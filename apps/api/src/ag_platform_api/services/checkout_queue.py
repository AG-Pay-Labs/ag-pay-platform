import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ag_platform_api.core.config import (
    LOCAL_DIRECT_CARD_PROVIDER,
    STRIPE_HOSTED_TEST_ADAPTER_KEY,
    Settings,
    normalize_checkout_origin,
)
from ag_platform_api.models import (
    AgentPaymentMethod,
    CartItem,
    CheckoutExecution,
    CheckoutExecutionStatus,
    CheckoutStatusTransition,
    PaymentMethod,
    PaymentMethodStatus,
    StoredCardCredential,
)
from ag_platform_api.services.checkout.errors import CheckoutError
from ag_platform_api.services.checkout.origins import validate_stripe_hosted_test_checkout_url
from ag_platform_api.services.checkout.types import decimal_to_minor, is_card_expired


@dataclass(frozen=True, slots=True)
class CheckoutQueueError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def checkout_url_origin(checkout_url: str) -> str:
    parsed = urlsplit(checkout_url)
    if parsed.scheme.lower() != "https" or parsed.hostname is None:
        raise CheckoutQueueError(
            "checkout_url_not_https", "Managed checkout requires an absolute HTTPS checkout URL"
        )
    if parsed.username or parsed.password:
        raise CheckoutQueueError(
            "checkout_url_has_credentials", "Checkout URLs cannot contain embedded credentials"
        )
    try:
        return normalize_checkout_origin(f"https://{parsed.netloc}")
    except ValueError as exc:
        raise CheckoutQueueError("checkout_url_invalid", "Checkout URL origin is invalid") from exc


async def queue_checkout_execution(
    db: AsyncSession,
    *,
    item: CartItem,
    payment_method: PaymentMethod,
    settings: Settings,
) -> CheckoutExecution | None:
    """Freeze an approved managed-checkout request into its durable execution row."""
    if item.checkout_adapter is None and item.checkout_url is None:
        return None
    if item.checkout_adapter is None or item.checkout_url is None:
        raise CheckoutQueueError(
            "checkout_spec_incomplete", "Managed checkout requires an adapter and checkout URL"
        )
    if not settings.checkout_enabled:
        raise CheckoutQueueError("checkout_disabled", "Managed checkout is not enabled")
    if item.billing_period is not None:
        raise CheckoutQueueError(
            "checkout_recurring_unsupported",
            "Managed checkout does not support recurring purchases",
        )
    try:
        decimal_to_minor(item.unit_price * item.quantity, item.currency)
    except CheckoutError as error:
        raise CheckoutQueueError(f"checkout_{error.code.value}", error.safe_message) from None
    if is_card_expired(payment_method.expiry_month, payment_method.expiry_year):
        raise CheckoutQueueError(
            "payment_method_expired",
            "The approved payment method has expired",
        )
    issuing_method = payment_method.provider == "stripe_issuing" and re.fullmatch(
        r"ic_[A-Za-z0-9]+", payment_method.provider_payment_method_id
    )
    link_method = (
        settings.environment.lower() in {"development", "test"}
        and settings.stripe_link_enabled
        and settings.stripe_link_test_mode
        and payment_method.provider == "stripe_link"
        and re.fullmatch(r"csmrpd_[A-Za-z0-9]+", payment_method.provider_payment_method_id)
    )
    demo_method = (
        settings.environment.lower() in {"development", "test"}
        and settings.checkout_demo_enabled
        and item.checkout_adapter
        in {
            settings.checkout_demo_adapter_key,
            *((STRIPE_HOSTED_TEST_ADAPTER_KEY,) if settings.checkout_hosted_demo_enabled else ()),
        }
        and payment_method.provider == "prototype-vault"
        and payment_method.provider_payment_method_id
        in {
            "pm_stripe_demo_success",
            "pm_stripe_demo_decline",
            "pm_stripe_demo_3ds",
        }
    )
    direct_card_method = (
        settings.environment.lower() in {"development", "test"}
        and settings.local_direct_card_enabled
        and payment_method.provider == LOCAL_DIRECT_CARD_PROVIDER
        and re.fullmatch(r"ldc_[A-Za-z0-9_-]+", payment_method.provider_payment_method_id)
    )
    if not issuing_method and not link_method and not demo_method and not direct_card_method:
        raise CheckoutQueueError(
            "checkout_provider_unsupported",
            "Managed checkout requires an assigned supported payment method",
        )
    unresolved_execution = await db.scalar(
        select(CheckoutExecution.id)
        .where(
            CheckoutExecution.owner_id == item.owner_id,
            CheckoutExecution.payment_method_id == payment_method.id,
            CheckoutExecution.status.in_(
                (
                    CheckoutExecutionStatus.action_required,
                    CheckoutExecutionStatus.outcome_unknown,
                )
            ),
        )
        .limit(1)
        .with_for_update()
    )
    if unresolved_execution is not None:
        raise CheckoutQueueError(
            "card_reconciliation_required",
            "The virtual card is quarantined until an unresolved checkout is reconciled.",
        )

    adapter = settings.checkout_adapters.get(item.checkout_adapter)
    if adapter is None:
        raise CheckoutQueueError(
            "checkout_adapter_unknown", "The requested checkout adapter is not configured"
        )
    if adapter.checkout_mode == "stripe_hosted_test" and not (demo_method or link_method):
        raise CheckoutQueueError(
            "checkout_provider_unsupported",
            "Stripe-hosted test checkout requires a configured demo payment method",
        )
    if direct_card_method and (
        adapter.checkout_mode != "direct"
        or adapter.payment_form_strategy != "browserbase_ai"
        or adapter.order_reference_selector is None
    ):
        raise CheckoutQueueError(
            "checkout_adapter_invalid",
            "Stored direct cards require an AI-mapped direct adapter with an order reference",
        )
    if direct_card_method:
        stored_credential = await db.scalar(
            select(StoredCardCredential.payment_method_id)
            .where(
                StoredCardCredential.payment_method_id == payment_method.id,
                StoredCardCredential.owner_id == item.owner_id,
            )
            .with_for_update()
        )
        if stored_credential is None:
            raise CheckoutQueueError(
                "checkout_provider_unsupported",
                "The stored direct-card credential is unavailable",
            )
    if adapter.checkout_mode == "stripe_hosted_test":
        try:
            validate_stripe_hosted_test_checkout_url(item.checkout_url)
        except CheckoutError:
            raise CheckoutQueueError(
                "checkout_url_invalid",
                "Stripe-hosted test checkout requires a concrete cs_test Checkout URL",
            ) from None
    origin = checkout_url_origin(item.checkout_url)
    if origin not in adapter.allowed_origins:
        raise CheckoutQueueError(
            "checkout_origin_not_allowed", "Checkout URL origin is not allowed for this adapter"
        )

    if (
        payment_method.owner_id != item.owner_id
        or payment_method.status is not PaymentMethodStatus.active
        or item.selected_payment_method_id != payment_method.id
    ):
        raise CheckoutQueueError(
            "payment_method_invalid", "Approved payment method is not active for this checkout"
        )
    assignment = await db.scalar(
        select(AgentPaymentMethod)
        .where(
            AgentPaymentMethod.agent_id == item.agent_id,
            AgentPaymentMethod.payment_method_id == payment_method.id,
        )
        .with_for_update()
    )
    if assignment is None:
        raise CheckoutQueueError(
            "payment_method_unassigned", "Approved payment method is not assigned to this agent"
        )

    existing = await db.scalar(
        select(CheckoutExecution).where(CheckoutExecution.cart_item_id == item.id).with_for_update()
    )
    if existing is not None:
        item.checkout_execution = existing
        return existing

    execution = CheckoutExecution(
        owner_id=item.owner_id,
        agent_id=item.agent_id,
        payment_method_id=payment_method.id,
        cart_item_id=item.id,
        adapter_key=item.checkout_adapter,
        adapter_config=adapter.model_dump(mode="json"),
        approved_amount=item.unit_price * item.quantity,
        currency=item.currency,
        checkout_origin=origin,
    )
    db.add(execution)
    await db.flush()
    db.add(
        CheckoutStatusTransition(
            execution_id=execution.id,
            status=CheckoutExecutionStatus.queued,
            attempt_count=execution.attempt_count,
        )
    )
    item.checkout_execution = execution
    return execution
