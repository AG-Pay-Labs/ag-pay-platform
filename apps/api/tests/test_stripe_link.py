import json
from pathlib import Path
from uuid import uuid4

import pytest

from ag_platform_api.services.checkout.errors import CheckoutError, CheckoutErrorCode
from ag_platform_api.services.checkout.stripe_link import StripeLinkGateway
from ag_platform_api.services.checkout.types import ExpectedCardMetadata

FAKE_LINK_CLI = r"""#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path

root = Path(__file__).parent
args = sys.argv[1:]
if args == ["--version"]:
    print((root / "version.txt").read_text().strip())
    raise SystemExit(0)

with (root / "commands.jsonl").open("a") as output:
    output.write(json.dumps({
        "args": args,
        "link_access_token": "LINK_ACCESS_TOKEN" in os.environ,
        "node_options": "NODE_OPTIONS" in os.environ,
    }) + "\n")

if (root / "oversized").exists():
    sys.stdout.write("x" * (64 * 1024 + 1))
    raise SystemExit(0)

def value(flag):
    return args[args.index(flag) + 1]

if args[:2] == ["payment-methods", "list"]:
    method = {
        "id": "csmrpd_wallet123",
        "type": "card",
        "is_default": True,
        "card_details": {
            "brand": "visa",
            "last4": "4242",
            "exp_month": 12,
            "exp_year": 2030,
        },
    }
    if not (root / "no-capabilities").exists():
        method["capabilities"] = {
            "agentic_payments": {
                "eligible": not (root / "ineligible").exists(),
                "ineligibility_reasons": [],
            },
        }
    print(json.dumps([method]))
elif args[:2] == ["spend-request", "list"]:
    print("[]")
elif args[:2] == ["spend-request", "create"]:
    metadata = value("--metadata").split(":", 1)[1]
    record = {
        "id": "lsrq_request123",
        "status": "pending_approval",
        "payment_details": value("--payment-method-id"),
        "merchant_url": value("--merchant-url"),
        "amount": int(value("--amount")) + (1 if (root / "mismatch").exists() else 0),
        "currency": value("--currency"),
        "merchant_name": value("--merchant-name"),
        "context": value("--context"),
        "line_items": [{
            "name": value("--line-item").split(",")[0].split(":", 1)[1],
            "unit_amount": int(value("--line-item").split(",")[1].split(":", 1)[1]),
            "quantity": int(value("--line-item").split(",")[2].split(":", 1)[1]),
        }],
        "totals": [{"type": "total", "display_text": "Total", "amount": int(value("--amount"))}],
        "metadata": {"agpay_execution_id": metadata},
    }
    (root / "request.json").write_text(json.dumps(record))
    print(json.dumps(record))
elif args[:2] == ["spend-request", "retrieve"]:
    request_id = args[2]
    if "--output-file" in args:
        time.sleep(0.05)
        credential_path = Path(value("--output-file"))
        payload = {
            "spend_request_id": request_id,
            "card": {
                "number": (
                    "4242424242424242"
                    if (root / "live-card").exists()
                    else "4000009990001984"
                ),
                "cvc": "123",
                "exp_month": 12,
                "exp_year": 2030,
                "billing_address": {
                    "name": "Alex Example",
                    "line1": "1 Test Street",
                    "city": "Madrid",
                    "state": "Madrid",
                    "postal_code": "28001",
                    "country": "ES",
                },
            },
        }
        descriptor = os.open(credential_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump(payload, output)
    counter_path = root / "retrieve-count"
    count = int(counter_path.read_text()) + 1 if counter_path.exists() else 1
    counter_path.write_text(str(count))
    status = "approved" if "--output-file" in args or count >= 3 else "pending_approval"
    record_path = root / "request.json"
    record = json.loads(record_path.read_text())
    record["status"] = status
    if status == "approved" and (root / "mutate-after-create").exists():
        record["amount"] += 1
    print(json.dumps(record))
elif args[0] == "report":
    print("{}")
else:
    print(json.dumps({"code": "UNEXPECTED"}))
    raise SystemExit(1)
"""


def link_gateway(tmp_path: Path, *, version: str = "0.12.0") -> tuple[StripeLinkGateway, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    executable = tmp_path / "link-cli"
    executable.write_text(FAKE_LINK_CLI)
    executable.chmod(0o700)
    (tmp_path / "version.txt").write_text(version)
    auth_directory = tmp_path / "auth"
    auth_directory.mkdir(mode=0o700)
    owner_id = uuid4()
    auth_file = auth_directory / f"{owner_id}.json"
    auth_file.write_text("{}")
    auth_file.chmod(0o600)
    gateway = StripeLinkGateway(
        cli_path=str(executable),
        expected_cli_version="0.12.0",
        auth_directory=auth_directory,
        test_mode=True,
        approval_timeout_seconds=30,
        cli_timeout_seconds=5,
    )
    return gateway, owner_id


def billing_details() -> dict[str, object]:
    return {
        "type": "personal",
        "full_name": "Alex Example",
        "address": {
            "line1": "1 Test Street",
            "city": "Madrid",
            "region": "Madrid",
            "postal_code": "28001",
            "country": "ES",
        },
    }


async def test_link_cli_creates_approved_request_and_reads_delayed_card_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, owner_id = link_gateway(tmp_path)
    execution_id = uuid4()
    monkeypatch.setenv("LINK_ACCESS_TOKEN", "must-not-reach-child")
    monkeypatch.setenv("NODE_OPTIONS", "--require=/must/not/run.js")

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("ag_platform_api.services.checkout.stripe_link.asyncio.sleep", no_sleep)

    request = await gateway.ensure_spend_request(
        owner_id=owner_id,
        execution_id=execution_id,
        payment_method_id="csmrpd_wallet123",
        merchant_name="merchant.example.test",
        merchant_url="https://merchant.example.test",
        amount_minor=2500,
        currency="EUR",
        context=(
            "The user approved this exact one-time merchant purchase after reviewing the item, "
            "quantity, total, currency, and delivery details in AG Pay."
        ),
        existing_request_id=None,
        expected_card=ExpectedCardMetadata(owner_id, "4242", "Visa", 12, 2030),
        item_name="Shirt,unit_amount:1:override",
        item_quantity=2,
        unit_amount_minor=1250,
    )
    assert request.request_id == "lsrq_request123"
    assert request.status == "pending_approval"

    await gateway.wait_for_approval(owner_id=owner_id, request=request)
    secret = await gateway.retrieve_card(
        owner_id=owner_id,
        request=request,
        expected_billing_details=billing_details(),
    )
    assert repr(secret) == "IssuingCardSecret(<redacted>)"

    commands = [json.loads(line) for line in (tmp_path / "commands.jsonl").read_text().splitlines()]
    create = next(
        command for command in commands if command["args"][:2] == ["spend-request", "create"]
    )
    line_item = create["args"][create["args"].index("--line-item") + 1]
    assert line_item == "name:Shirt unit_amount 1 override,unit_amount:1250,quantity:2"
    assert "--request-approval" in create["args"]
    assert "--test" in create["args"]
    assert all(not command["link_access_token"] for command in commands)
    assert all(not command["node_options"] for command in commands)
    retrieve = next(
        command
        for command in commands
        if command["args"][:2] == ["spend-request", "retrieve"]
        and "--output-file" in command["args"]
    )
    credential_path = Path(retrieve["args"][retrieve["args"].index("--output-file") + 1])
    assert not await __import__("asyncio").to_thread(credential_path.exists)


async def test_link_rejects_ineligible_method_mismatched_request_and_live_card(
    tmp_path: Path,
) -> None:
    expected_context = "A" * 100

    async def create(gateway: StripeLinkGateway, owner_id) -> object:
        return await gateway.ensure_spend_request(
            owner_id=owner_id,
            execution_id=uuid4(),
            payment_method_id="csmrpd_wallet123",
            merchant_name="merchant.example.test",
            merchant_url="https://merchant.example.test",
            amount_minor=2500,
            currency="EUR",
            context=expected_context,
            existing_request_id=None,
            expected_card=ExpectedCardMetadata(owner_id, "4242", "Visa", 12, 2030),
            item_name="Approved item",
            item_quantity=1,
            unit_amount_minor=2500,
        )

    ineligible, owner_id = link_gateway(tmp_path / "ineligible-case")
    (tmp_path / "ineligible-case" / "ineligible").touch()
    with pytest.raises(CheckoutError) as rejected_method:
        await create(ineligible, owner_id)
    assert rejected_method.value.code == CheckoutErrorCode.card_unavailable

    mismatch, owner_id = link_gateway(tmp_path / "mismatch-case")
    (tmp_path / "mismatch-case" / "mismatch").touch()
    with pytest.raises(CheckoutError) as rejected_request:
        await create(mismatch, owner_id)
    assert rejected_request.value.code == CheckoutErrorCode.card_reconciliation_required

    live, owner_id = link_gateway(tmp_path / "live-card-case")
    (tmp_path / "live-card-case" / "live-card").touch()
    request = await create(live, owner_id)
    with pytest.raises(CheckoutError) as rejected_card:
        await live.retrieve_card(
            owner_id=owner_id,
            request=request,
            expected_billing_details=billing_details(),
        )
    assert rejected_card.value.code == CheckoutErrorCode.card_unavailable


async def test_link_accepts_card_when_optional_capabilities_are_absent(tmp_path: Path) -> None:
    gateway, owner_id = link_gateway(tmp_path)
    (tmp_path / "no-capabilities").touch()

    request = await gateway.ensure_spend_request(
        owner_id=owner_id,
        execution_id=uuid4(),
        payment_method_id="csmrpd_wallet123",
        merchant_name="merchant.example.test",
        merchant_url="https://merchant.example.test",
        amount_minor=2500,
        currency="EUR",
        context="A" * 100,
        existing_request_id=None,
        expected_card=ExpectedCardMetadata(owner_id, "4242", "Visa", 12, 2030),
        item_name="Approved item",
        item_quantity=1,
        unit_amount_minor=2500,
    )

    assert request.request_id == "lsrq_request123"


async def test_link_cli_version_and_owner_session_fail_closed(tmp_path: Path) -> None:
    gateway, owner_id = link_gateway(tmp_path, version="9.9.9")
    with pytest.raises(CheckoutError) as incompatible:
        await gateway.ensure_spend_request(
            owner_id=owner_id,
            execution_id=uuid4(),
            payment_method_id="csmrpd_wallet123",
            merchant_name="merchant.example.test",
            merchant_url="https://merchant.example.test",
            amount_minor=2500,
            currency="EUR",
            context="A" * 100,
            existing_request_id=None,
            expected_card=ExpectedCardMetadata(owner_id, "4242", "Visa", 12, 2030),
            item_name="Approved item",
            item_quantity=1,
            unit_amount_minor=2500,
        )
    assert incompatible.value.code == CheckoutErrorCode.provider_unsupported

    compatible, compatible_owner = link_gateway(tmp_path / "other")
    request = await compatible.ensure_spend_request(
        owner_id=compatible_owner,
        execution_id=uuid4(),
        payment_method_id="csmrpd_wallet123",
        merchant_name="merchant.example.test",
        merchant_url="https://merchant.example.test",
        amount_minor=2500,
        currency="EUR",
        context="A" * 100,
        existing_request_id=None,
        expected_card=ExpectedCardMetadata(compatible_owner, "4242", "Visa", 12, 2030),
        item_name="Approved item",
        item_quantity=1,
        unit_amount_minor=2500,
    )
    with pytest.raises(CheckoutError) as missing_owner:
        await compatible.retrieve_card(
            owner_id=uuid4(),
            request=request,
            expected_billing_details=billing_details(),
        )
    assert missing_owner.value.code == CheckoutErrorCode.card_unavailable


async def test_link_revalidates_approved_request_and_caps_cli_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, owner_id = link_gateway(tmp_path / "mutation-case")
    execution_id = uuid4()
    request = await gateway.ensure_spend_request(
        owner_id=owner_id,
        execution_id=execution_id,
        payment_method_id="csmrpd_wallet123",
        merchant_name="merchant.example.test",
        merchant_url="https://merchant.example.test",
        amount_minor=2500,
        currency="EUR",
        context="A" * 100,
        existing_request_id=None,
        expected_card=ExpectedCardMetadata(owner_id, "4242", "Visa", 12, 2030),
        item_name="Approved item",
        item_quantity=1,
        unit_amount_minor=2500,
    )
    (tmp_path / "mutation-case" / "mutate-after-create").touch()

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("ag_platform_api.services.checkout.stripe_link.asyncio.sleep", no_sleep)
    with pytest.raises(CheckoutError) as changed_request:
        await gateway.wait_for_approval(owner_id=owner_id, request=request)
    assert changed_request.value.code == CheckoutErrorCode.card_reconciliation_required
    with pytest.raises(CheckoutError) as changed_credential_request:
        await gateway.retrieve_card(
            owner_id=owner_id,
            request=request,
            expected_billing_details=billing_details(),
        )
    assert changed_credential_request.value.code == CheckoutErrorCode.card_reconciliation_required

    oversized, oversized_owner = link_gateway(tmp_path / "oversized-case")
    (tmp_path / "oversized-case" / "oversized").touch()
    with pytest.raises(CheckoutError) as oversized_output:
        await oversized.ensure_spend_request(
            owner_id=oversized_owner,
            execution_id=uuid4(),
            payment_method_id="csmrpd_wallet123",
            merchant_name="merchant.example.test",
            merchant_url="https://merchant.example.test",
            amount_minor=2500,
            currency="EUR",
            context="A" * 100,
            existing_request_id=None,
            expected_card=ExpectedCardMetadata(oversized_owner, "4242", "Visa", 12, 2030),
            item_name="Approved item",
            item_quantity=1,
            unit_amount_minor=2500,
        )
    assert oversized_output.value.code == CheckoutErrorCode.card_unavailable
