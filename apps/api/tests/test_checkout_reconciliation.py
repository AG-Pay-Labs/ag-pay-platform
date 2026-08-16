import json
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from ag_platform_api.services.checkout.reconciliation import (
    MAX_VERIFICATION_RESPONSE_BYTES,
    PAYMENT_VERIFICATION_ENDPOINT,
    LandingPaymentVerificationClient,
    ReconciliationError,
    ReconciliationErrorCode,
)
from ag_platform_api.services.checkout.repository import HostedReconciliationCandidate


def candidate() -> HostedReconciliationCandidate:
    return HostedReconciliationCandidate(
        execution_id=uuid4(),
        cart_item_id=uuid4(),
        owner_id=uuid4(),
        agent_id=uuid4(),
        payment_method_id=uuid4(),
        stripe_session_id="cs_test_fixed123",
        approved_title="Ship fuel",
        amount=Decimal("10.00"),
        amount_minor=1000,
        currency="EUR",
        receipt_url=("https://letyouragentspay.com/playground/success?session_id=cs_test_fixed123"),
        already_succeeded=False,
    )


def verified_payload(**overrides):
    payload = {
        "verified": True,
        "sessionId": "cs_test_fixed123",
        "orderReference": "cs_test_fixed123",
        "offer": {"slug": "ship-fuel", "name": "Ship fuel"},
        "amountMinor": 1000,
        "currency": "eur",
    }
    payload.update(overrides)
    return payload


async def test_landing_verifier_posts_exact_session_and_accepts_lowercase_currency() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Cache-Control": "no-store",
            },
            json=verified_payload(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        verifier = LandingPaymentVerificationClient(client)
        proof = await verifier.verify(candidate())

    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == PAYMENT_VERIFICATION_ENDPOINT
    assert json.loads(requests[0].content) == {"sessionId": "cs_test_fixed123"}
    assert proof.session_id == "cs_test_fixed123"
    assert proof.order_reference == "cs_test_fixed123"
    assert proof.offer_name == "Ship fuel"
    assert proof.amount_minor == 1000
    assert proof.currency == "eur"


@pytest.mark.parametrize(
    "payload",
    [
        verified_payload(sessionId="cs_test_other456"),
        verified_payload(orderReference="cs_test_other456"),
        verified_payload(offer={"slug": "other", "name": "Other offer"}),
        verified_payload(amountMinor=999),
        verified_payload(currency="usd"),
        {**verified_payload(), "unexpected": "field"},
        {**verified_payload(), "verified": "true"},
    ],
)
async def test_landing_verifier_rejects_any_unbound_or_non_strict_proof(payload) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json", "Cache-Control": "no-store"},
            json=payload,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        verifier = LandingPaymentVerificationClient(client)
        with pytest.raises(ReconciliationError) as caught:
            await verifier.verify(candidate())

    assert caught.value.code == ReconciliationErrorCode.not_verified


@pytest.mark.parametrize(
    ("status", "headers", "body", "expected"),
    [
        (404, {}, b'{"verified":false}', ReconciliationErrorCode.not_verified),
        (503, {}, b'{"verified":false}', ReconciliationErrorCode.unavailable),
        (
            200,
            {"Content-Type": "application/json"},
            json.dumps(verified_payload()).encode(),
            ReconciliationErrorCode.unavailable,
        ),
        (
            200,
            {"Content-Type": "application/json", "Cache-Control": "no-store"},
            b"x" * (MAX_VERIFICATION_RESPONSE_BYTES + 1),
            ReconciliationErrorCode.unavailable,
        ),
    ],
)
async def test_landing_verifier_fails_closed_for_untrusted_responses(
    status,
    headers,
    body,
    expected,
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        verifier = LandingPaymentVerificationClient(client)
        with pytest.raises(ReconciliationError) as caught:
            await verifier.verify(candidate())

    assert caught.value.code == expected


async def test_landing_verifier_never_follows_redirects() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://attacker.example/proof"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    ) as client:
        verifier = LandingPaymentVerificationClient(client)
        with pytest.raises(ReconciliationError) as caught:
            await verifier.verify(candidate())

    assert caught.value.code == ReconciliationErrorCode.not_verified
    assert len(requests) == 1
