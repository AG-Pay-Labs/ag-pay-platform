from __future__ import annotations

import argparse
import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from ag_platform_api.core.config import get_settings
from ag_platform_api.core.security import encrypt_secret
from ag_platform_api.db.session import SessionFactory
from ag_platform_api.models import (
    Agent,
    AgentPaymentMethod,
    AgentPaymentPolicy,
    AgentStatus,
    BillingPeriod,
    BillingProfileType,
    CartItem,
    CartItemStatus,
    PaymentApprovalMode,
    PaymentMethod,
    PaymentMethodStatus,
    Purchase,
    PurchaseCredential,
    PurchaseStatus,
    Subscription,
    SubscriptionStatus,
    User,
)

QONTO_CARD_REFERENCE = "6ca8f8b6-c0a7-4c72-9d70-60728a04ee35"
LEGACY_QONTO_CARD_REFERENCE = "pm_qonto_demo_virtual_2048"
SEEDED_MERCHANT_PASSWORD_PREFIX = "agpay-seeded-merchant-credential"

LEGACY_APPROVAL_URLS = {
    "design-asset-license": "https://design.example.com/demo/agent-illustrations",
    "research-database": "https://research.example.com/demo/model-database",
    "travel-adapter": "https://shop.example.com/demo/travel-adapter",
}


@dataclass(frozen=True, slots=True)
class AgentSeed:
    slug: str
    name: str
    runtime: str
    description: str
    capabilities: tuple[str, ...]
    last_seen_seconds_ago: int
    created_days_ago: int


@dataclass(frozen=True, slots=True)
class PurchaseSeed:
    slug: str
    title: str
    description: str
    product_url: str
    login_url: str
    merchant: str
    reason: str
    amount: Decimal
    agent_slug: str
    purchased_days_ago: int
    currency: str = "EUR"
    billing_period: BillingPeriod | None = None
    next_billing_days: int | None = None


@dataclass(frozen=True, slots=True)
class ApprovalSeed:
    slug: str
    title: str
    description: str
    product_url: str
    login_url: str
    merchant: str
    reason: str
    amount: Decimal
    currency: str
    agent_slug: str
    submitted_hours_ago: int
    billing_period: BillingPeriod | None = None
    approved: bool = False


@dataclass(frozen=True, slots=True)
class PolicySeed:
    agent_slug: str
    mode: PaymentApprovalMode
    threshold_amount: Decimal | None = None
    threshold_currency: str | None = None


AGENT_SEEDS = (
    AgentSeed(
        slug="openclaw-atlas",
        name="OpenClaw Atlas",
        runtime="openclaw",
        description="Primary procurement agent for tools, APIs, and infrastructure.",
        capabilities=("web_search", "purchase_proposal", "subscription_tracking"),
        last_seen_seconds_ago=20,
        created_days_ago=54,
    ),
    AgentSeed(
        slug="openclaw-scout",
        name="OpenClaw Scout",
        runtime="openclaw",
        description="Finds low-cost products and compares merchant offers.",
        capabilities=("web_search", "price_comparison", "purchase_proposal"),
        last_seen_seconds_ago=45,
        created_days_ago=47,
    ),
    AgentSeed(
        slug="openclaw-penny",
        name="OpenClaw Penny",
        runtime="openclaw",
        description="Budget-conscious agent for everyday purchases under strict limits.",
        capabilities=("budget_policy", "purchase_proposal"),
        last_seen_seconds_ago=90,
        created_days_ago=39,
    ),
    AgentSeed(
        slug="openclaw-curator",
        name="OpenClaw Curator",
        runtime="openclaw",
        description="Curates software services and recurring developer tooling.",
        capabilities=("vendor_research", "subscription_tracking", "purchase_proposal"),
        last_seen_seconds_ago=420,
        created_days_ago=31,
    ),
    AgentSeed(
        slug="openclaw-orbit",
        name="OpenClaw Orbit",
        runtime="openclaw",
        description="Handles cloud-platform research and operational errands.",
        capabilities=("cloud_research", "purchase_proposal"),
        last_seen_seconds_ago=86_400,
        created_days_ago=24,
    ),
    AgentSeed(
        slug="hermes-research",
        name="Hermes Research",
        runtime="hermes",
        description="Hermes agent dedicated to model and API research.",
        capabilities=("model_research", "web_search", "purchase_proposal"),
        last_seen_seconds_ago=30,
        created_days_ago=18,
    ),
    AgentSeed(
        slug="hermes-errands",
        name="Hermes Errands",
        runtime="hermes",
        description="Hermes agent for small office and personal errands.",
        capabilities=("shopping", "purchase_proposal"),
        last_seen_seconds_ago=240,
        created_days_ago=12,
    ),
)


PURCHASE_SEEDS = (
    PurchaseSeed(
        slug="render-service",
        title="Render Web Service",
        description="Monthly web-service allocation for the AG Pay API.",
        product_url="https://render.com/",
        login_url="https://dashboard.render.com/",
        merchant="Render.com",
        reason="Keeps the prototype API reachable for development checks.",
        amount=Decimal("25.00"),
        agent_slug="openclaw-orbit",
        purchased_days_ago=26,
        currency="USD",
        billing_period=BillingPeriod.monthly,
        next_billing_days=5,
    ),
    PurchaseSeed(
        slug="anthropic-api",
        title="Anthropic API Credits",
        description="Monthly API budget for Anthropic model experiments.",
        product_url="https://console.anthropic.com/",
        login_url="https://console.anthropic.com/login",
        merchant="Anthropic",
        reason="Supports comparative reasoning tests for connected agents.",
        amount=Decimal("45.00"),
        agent_slug="hermes-research",
        purchased_days_ago=22,
        currency="USD",
        billing_period=BillingPeriod.monthly,
        next_billing_days=9,
    ),
    PurchaseSeed(
        slug="openai-api",
        title="OpenAI API Credits",
        description="Monthly API budget for OpenAI model experiments.",
        product_url="https://platform.openai.com/",
        login_url="https://platform.openai.com/login",
        merchant="OpenAI",
        reason="Provides model access for supervised agent workflows.",
        amount=Decimal("50.00"),
        agent_slug="openclaw-atlas",
        purchased_days_ago=19,
        currency="USD",
        billing_period=BillingPeriod.monthly,
        next_billing_days=12,
    ),
    PurchaseSeed(
        slug="kimi-api",
        title="Kimi API Credits",
        description="Monthly API budget for Kimi model experiments.",
        product_url="https://platform.moonshot.ai/",
        login_url="https://platform.moonshot.ai/console",
        merchant="Kimi",
        reason="Adds another model provider to the evaluation mix.",
        amount=Decimal("30.00"),
        agent_slug="openclaw-curator",
        purchased_days_ago=15,
        currency="USD",
        billing_period=BillingPeriod.monthly,
        next_billing_days=16,
    ),
    PurchaseSeed(
        slug="cotton-socks",
        title="Blue Cotton Socks",
        description="One pair of lightweight blue cotton socks.",
        product_url="https://www.primark.com/en-es/c/men/clothing/underwear-and-socks/socks",
        login_url="https://www.primark.com/en-es",
        merchant="Primark",
        reason="Replaced a worn pair while staying below the small-purchase limit.",
        amount=Decimal("1.99"),
        agent_slug="hermes-errands",
        purchased_days_ago=11,
    ),
    PurchaseSeed(
        slug="pocket-notebook",
        title="Pocket Notebook",
        description="A small recycled-paper notebook for field notes.",
        product_url="https://www.muji.eu/stationery/notebooks-and-paper/plain-notebooks",
        login_url="https://www.muji.eu/account/login",
        merchant="MUJI",
        reason="Needed a compact offline backup for research notes.",
        amount=Decimal("1.45"),
        agent_slug="openclaw-scout",
        purchased_days_ago=9,
    ),
    PurchaseSeed(
        slug="cable-organizer",
        title="Cable Organizer",
        description="Reusable hook-and-loop cable organizer for a desk charger.",
        product_url="https://www.ikea.com/es/en/cat/cable-management-accessories-16195/",
        login_url="https://www.ikea.com/es/en/profile/login/",
        merchant="IKEA",
        reason="Reduces cable clutter around the development workstation.",
        amount=Decimal("0.89"),
        agent_slug="openclaw-penny",
        purchased_days_ago=7,
    ),
    PurchaseSeed(
        slug="cleaning-cloth",
        title="Screen Cleaning Cloth",
        description="One washable microfiber cloth for laptop screens.",
        product_url="https://www.ikea.com/es/en/cat/cleaning-accessories-20659/",
        login_url="https://www.ikea.com/es/en/profile/login/",
        merchant="IKEA",
        reason="Keeps shared display equipment clean without disposable wipes.",
        amount=Decimal("1.20"),
        agent_slug="openclaw-scout",
        purchased_days_ago=5,
    ),
    PurchaseSeed(
        slug="espresso",
        title="Espresso",
        description="A single espresso purchased during a research session.",
        product_url="https://www.starbucks.es/menu",
        login_url="https://www.starbucks.es/account/signin",
        merchant="Starbucks",
        reason="A small refreshment during a long supervised test run.",
        amount=Decimal("1.60"),
        agent_slug="openclaw-atlas",
        purchased_days_ago=3,
    ),
    PurchaseSeed(
        slug="pencil-set",
        title="Two-Pencil Set",
        description="Two basic graphite pencils for the pocket notebook.",
        product_url="https://spain.muji.eu/stationery/pens-and-pencils/pencils",
        login_url="https://spain.muji.eu/account/login",
        merchant="MUJI",
        reason="Completes the field-note kit at minimal cost.",
        amount=Decimal("0.75"),
        agent_slug="hermes-errands",
        purchased_days_ago=1,
    ),
)


APPROVAL_SEEDS = (
    ApprovalSeed(
        slug="sentry-team-plan",
        title="Sentry Team Plan",
        description="Monthly error monitoring for the agent API and management dashboard.",
        product_url="https://sentry.io/pricing/",
        login_url="https://sentry.io/auth/login/",
        merchant="Sentry",
        reason="Adds production-grade error visibility before the next release review.",
        amount=Decimal("26.00"),
        currency="USD",
        agent_slug="openclaw-orbit",
        submitted_hours_ago=2,
        billing_period=BillingPeriod.monthly,
    ),
    ApprovalSeed(
        slug="design-asset-license",
        title="Streamline Illustration License",
        description="A commercial license for interface illustrations used in agent workflows.",
        product_url="https://www.streamlinehq.com/illustrations",
        login_url="https://app.streamlinehq.com/",
        merchant="Streamline",
        reason="The selected pack matches the dashboard palette and has the clearest licensing.",
        amount=Decimal("34.00"),
        currency="USD",
        agent_slug="openclaw-scout",
        submitted_hours_ago=5,
    ),
    ApprovalSeed(
        slug="research-database",
        title="Hugging Face PRO",
        description="Monthly access to expanded model hosting and developer features.",
        product_url="https://huggingface.co/pricing",
        login_url="https://huggingface.co/login",
        merchant="Hugging Face",
        reason="Supports recurring model comparisons and hosted evaluation workflows.",
        amount=Decimal("9.00"),
        currency="USD",
        agent_slug="hermes-research",
        submitted_hours_ago=8,
        billing_period=BillingPeriod.monthly,
    ),
    ApprovalSeed(
        slug="travel-adapter",
        title="Anker 511 Charger (Nano 3, 30W)",
        description="A compact 30 W USB-C charger for the workstation travel bag.",
        product_url="https://www.anker.com/products/a2147",
        login_url="https://www.anker.com/account/login",
        merchant="Anker",
        reason="Meets the requested size and power requirements at the lowest compared price.",
        amount=Decimal("28.00"),
        currency="USD",
        agent_slug="openclaw-atlas",
        submitted_hours_ago=20,
        approved=True,
    ),
)


POLICY_SEEDS = (
    PolicySeed(
        agent_slug="openclaw-atlas",
        mode=PaymentApprovalMode.subscriptions_or_above_amount,
        threshold_amount=Decimal("20.00"),
        threshold_currency="USD",
    ),
    PolicySeed(
        agent_slug="openclaw-scout",
        mode=PaymentApprovalMode.above_amount,
        threshold_amount=Decimal("20.00"),
        threshold_currency="USD",
    ),
    PolicySeed(
        agent_slug="openclaw-penny",
        mode=PaymentApprovalMode.above_amount,
        threshold_amount=Decimal("20.00"),
        threshold_currency="USD",
    ),
    PolicySeed(
        agent_slug="openclaw-curator",
        mode=PaymentApprovalMode.subscriptions_only,
    ),
    PolicySeed(agent_slug="openclaw-orbit", mode=PaymentApprovalMode.always),
    PolicySeed(
        agent_slug="hermes-research",
        mode=PaymentApprovalMode.subscriptions_only,
    ),
    PolicySeed(agent_slug="hermes-errands", mode=PaymentApprovalMode.never),
)


def _api_key_hash(owner_id: UUID, slug: str) -> str:
    return hashlib.sha256(f"agpay-agent:{owner_id}:{slug}".encode()).hexdigest()


def _instance_id(seed: AgentSeed) -> str:
    role = seed.slug.removeprefix(f"{seed.runtime}-")
    runtime = "ocw" if seed.runtime == "openclaw" else "hermes"
    return f"{runtime}-eu-west-1-{role}-01"


def _purchase_reference(seed: PurchaseSeed) -> str:
    return str(uuid5(NAMESPACE_URL, f"https://qonto.com/transactions/ag-pay/{seed.slug}"))


async def _seed_agent(db: Any, owner: User, seed: AgentSeed, now: datetime) -> Agent:
    instance_id = _instance_id(seed)
    legacy_instance_id = f"demo-{seed.slug}"
    agent = await db.scalar(
        select(Agent).where(
            Agent.owner_id == owner.id,
            Agent.instance_id.in_((instance_id, legacy_instance_id)),
        )
    )
    connected_at = now - timedelta(days=max(seed.created_days_ago - 1, 1))
    last_seen_at = now - timedelta(seconds=seed.last_seen_seconds_ago)

    if agent is None:
        agent = Agent(
            owner_id=owner.id,
            instance_id=instance_id,
            created_at=now - timedelta(days=seed.created_days_ago),
            updated_at=last_seen_at,
        )
        db.add(agent)

    agent.name = seed.name
    agent.instance_id = instance_id
    agent.description = seed.description
    agent.status = AgentStatus.active
    agent.pairing_token_hash = None
    agent.pairing_expires_at = None
    agent.api_key_hash = _api_key_hash(owner.id, seed.slug)
    agent.api_key_expires_at = now + timedelta(days=365)
    agent.software_version = "2026.7.2" if seed.runtime == "openclaw" else "0.20.0"
    agent.capabilities = list(seed.capabilities)
    agent.connected_at = connected_at
    agent.last_seen_at = last_seen_at
    agent.revoked_at = None
    return agent


async def _seed_card(db: Any, owner: User, now: datetime) -> PaymentMethod:
    card = await db.scalar(
        select(PaymentMethod).where(
            PaymentMethod.owner_id == owner.id,
            PaymentMethod.provider == "qonto",
            PaymentMethod.provider_payment_method_id.in_(
                (QONTO_CARD_REFERENCE, LEGACY_QONTO_CARD_REFERENCE)
            ),
        )
    )
    billing_details = {
        "type": "business",
        "legal_name": "AG Pay Labs S.L.",
        "vat_number": "ESB87942136",
        "registration_number": "B-87942136",
        "contact_name": "Vitaly Bulyzhyn",
        "email": owner.username,
        "phone": None,
        "address": {
            "line1": "Calle de la Innovación 12",
            "line2": None,
            "city": "Madrid",
            "region": "Madrid",
            "postal_code": "28010",
            "country": "ES",
        },
    }

    if card is None:
        card = PaymentMethod(
            owner_id=owner.id,
            provider="qonto",
            provider_payment_method_id=QONTO_CARD_REFERENCE,
            created_at=now - timedelta(days=60),
            updated_at=now - timedelta(days=60),
        )
        db.add(card)

    card.display_name = "Qonto Virtual Card"
    card.provider_payment_method_id = QONTO_CARD_REFERENCE
    card.status = PaymentMethodStatus.active
    card.card_brand = "Mastercard"
    card.card_last4 = "2048"
    card.expiry_month = 12
    card.expiry_year = max(now.year + 3, 2030)
    card.billing_profile_type = BillingProfileType.business
    card.billing_details = billing_details
    return card


async def _seed_purchase(
    db: Any,
    owner: User,
    card: PaymentMethod,
    agents: dict[str, Agent],
    seed: PurchaseSeed,
    now: datetime,
) -> Purchase:
    provider_reference = _purchase_reference(seed)
    legacy_provider_reference = f"qonto_demo_{seed.slug}_v1"
    existing = await db.scalar(
        select(Purchase)
        .options(
            selectinload(Purchase.cart_item).selectinload(CartItem.credential),
            selectinload(Purchase.subscription),
        )
        .where(
            Purchase.payment_method_id == card.id,
            Purchase.provider_reference.in_((provider_reference, legacy_provider_reference)),
        )
    )
    if existing is not None:
        agent = agents[seed.agent_slug]
        existing.owner_id = owner.id
        existing.agent_id = agent.id
        existing.payment_method_id = card.id
        existing.status = PurchaseStatus.completed
        existing.amount = seed.amount
        existing.currency = seed.currency
        existing.provider_reference = provider_reference

        item = existing.cart_item
        item.owner_id = owner.id
        item.agent_id = agent.id
        item.selected_payment_method_id = card.id
        item.title = seed.title
        item.description = seed.description
        item.product_url = seed.product_url
        item.merchant = seed.merchant
        item.reason = seed.reason
        item.quantity = 1
        item.unit_price = seed.amount
        item.currency = seed.currency
        item.billing_period = seed.billing_period
        item.status = CartItemStatus.purchased
        item.decision_note = "Approved under the configured payment policy."
        item.credential.owner_id = owner.id
        item.credential.agent_id = agent.id
        item.credential.email = owner.username
        item.credential.login_url = seed.login_url

        if seed.billing_period is not None:
            subscription = existing.subscription
            if subscription is None:
                db.add(
                    Subscription(
                        owner_id=owner.id,
                        agent_id=existing.agent_id,
                        purchase_id=existing.id,
                        billing_period=seed.billing_period,
                        status=SubscriptionStatus.active,
                        next_billing_at=now + timedelta(days=seed.next_billing_days or 30),
                    )
                )
            else:
                subscription.owner_id = owner.id
                subscription.agent_id = agent.id
                subscription.billing_period = seed.billing_period
                subscription.status = SubscriptionStatus.active
                subscription.next_billing_at = now + timedelta(days=seed.next_billing_days or 30)
        return existing

    agent = agents[seed.agent_slug]
    purchased_at = now - timedelta(days=seed.purchased_days_ago)
    proposed_at = purchased_at - timedelta(hours=2)
    approved_at = purchased_at - timedelta(minutes=25)

    credential = PurchaseCredential(
        owner_id=owner.id,
        agent_id=agent.id,
        email=owner.username,
        encrypted_password=encrypt_secret(
            f"{SEEDED_MERCHANT_PASSWORD_PREFIX}-{seed.slug}", get_settings()
        ),
        login_url=seed.login_url,
        created_at=proposed_at,
        updated_at=proposed_at,
    )
    db.add(credential)
    await db.flush()

    cart_item = CartItem(
        owner_id=owner.id,
        agent_id=agent.id,
        credential_id=credential.id,
        selected_payment_method_id=card.id,
        title=seed.title,
        description=seed.description,
        product_url=seed.product_url,
        merchant=seed.merchant,
        reason=seed.reason,
        quantity=1,
        unit_price=seed.amount,
        currency=seed.currency,
        billing_period=seed.billing_period,
        status=CartItemStatus.purchased,
        decision_note="Approved under the configured payment policy.",
        approved_at=approved_at,
        created_at=proposed_at,
        updated_at=purchased_at,
    )
    db.add(cart_item)
    await db.flush()

    purchase = Purchase(
        owner_id=owner.id,
        agent_id=agent.id,
        payment_method_id=card.id,
        cart_item_id=cart_item.id,
        status=PurchaseStatus.completed,
        amount=seed.amount,
        currency=seed.currency,
        provider_reference=provider_reference,
        receipt_url=None,
        purchased_at=purchased_at,
        created_at=purchased_at,
        updated_at=purchased_at,
    )
    db.add(purchase)
    await db.flush()

    if seed.billing_period is not None:
        db.add(
            Subscription(
                owner_id=owner.id,
                agent_id=agent.id,
                purchase_id=purchase.id,
                billing_period=seed.billing_period,
                status=SubscriptionStatus.active,
                next_billing_at=now + timedelta(days=seed.next_billing_days or 30),
                created_at=purchased_at,
                updated_at=purchased_at,
            )
        )
    return purchase


async def _seed_approval(
    db: Any,
    owner: User,
    card: PaymentMethod,
    agents: dict[str, Agent],
    seed: ApprovalSeed,
    now: datetime,
) -> CartItem:
    legacy_url = LEGACY_APPROVAL_URLS.get(seed.slug)
    candidate_urls = tuple(url for url in (seed.product_url, legacy_url) if url is not None)
    existing = await db.scalar(
        select(CartItem)
        .options(selectinload(CartItem.credential))
        .where(
            CartItem.owner_id == owner.id,
            or_(CartItem.product_url.in_(candidate_urls), CartItem.title == seed.title),
        )
    )
    if existing is not None:
        agent = agents[seed.agent_slug]
        existing.agent_id = agent.id
        existing.title = seed.title
        existing.description = seed.description
        existing.product_url = seed.product_url
        existing.merchant = seed.merchant
        existing.reason = seed.reason
        existing.quantity = 1
        existing.unit_price = seed.amount
        existing.currency = seed.currency
        existing.billing_period = seed.billing_period
        if existing.decision_note == "Approved for the supervised demo.":
            existing.decision_note = "Approved under the configured payment policy."
        if existing.credential is not None:
            existing.credential.owner_id = owner.id
            existing.credential.agent_id = agent.id
            existing.credential.email = owner.username
            existing.credential.login_url = seed.login_url
        return existing

    agent = agents[seed.agent_slug]
    proposed_at = now - timedelta(hours=seed.submitted_hours_ago)
    credential = PurchaseCredential(
        owner_id=owner.id,
        agent_id=agent.id,
        email=owner.username,
        encrypted_password=encrypt_secret(
            f"{SEEDED_MERCHANT_PASSWORD_PREFIX}-approval-{seed.slug}", get_settings()
        ),
        login_url=seed.login_url,
        created_at=proposed_at,
        updated_at=proposed_at,
    )
    db.add(credential)
    await db.flush()

    item = CartItem(
        owner_id=owner.id,
        agent_id=agent.id,
        credential_id=credential.id,
        selected_payment_method_id=card.id if seed.approved else None,
        title=seed.title,
        description=seed.description,
        product_url=seed.product_url,
        merchant=seed.merchant,
        reason=seed.reason,
        quantity=1,
        unit_price=seed.amount,
        currency=seed.currency,
        billing_period=seed.billing_period,
        status=CartItemStatus.approved if seed.approved else CartItemStatus.proposed,
        decision_note="Approved under the configured payment policy." if seed.approved else None,
        approved_at=proposed_at + timedelta(minutes=35) if seed.approved else None,
        created_at=proposed_at,
        updated_at=proposed_at + timedelta(minutes=35) if seed.approved else proposed_at,
    )
    db.add(item)
    return item


async def _seed_policy(
    db: Any,
    owner: User,
    agents: dict[str, Agent],
    seed: PolicySeed,
) -> AgentPaymentPolicy:
    agent = agents[seed.agent_slug]
    policy = await db.scalar(
        select(AgentPaymentPolicy).where(
            AgentPaymentPolicy.owner_id == owner.id,
            AgentPaymentPolicy.agent_id == agent.id,
        )
    )
    if policy is None:
        policy = AgentPaymentPolicy(owner_id=owner.id, agent_id=agent.id)
        db.add(policy)

    policy.mode = seed.mode
    policy.threshold_amount = seed.threshold_amount
    policy.threshold_currency = seed.threshold_currency
    return policy


async def seed_demo_data(username: str) -> dict[str, int]:
    now = datetime.now(UTC).replace(microsecond=0)
    normalized_username = username.strip().lower()

    async with SessionFactory() as db:
        owner = await db.scalar(select(User).where(User.username == normalized_username))
        if owner is None:
            raise LookupError(f"No user exists with username {normalized_username!r}")

        agents: dict[str, Agent] = {}
        for seed in AGENT_SEEDS:
            agents[seed.slug] = await _seed_agent(db, owner, seed, now)
        await db.flush()

        card = await _seed_card(db, owner, now)
        await db.flush()

        for agent in agents.values():
            assignment = await db.get(
                AgentPaymentMethod,
                {"agent_id": agent.id, "payment_method_id": card.id},
            )
            if assignment is None:
                db.add(AgentPaymentMethod(agent_id=agent.id, payment_method_id=card.id))
        await db.flush()

        for seed in POLICY_SEEDS:
            await _seed_policy(db, owner, agents, seed)
        await db.flush()

        for seed in PURCHASE_SEEDS:
            await _seed_purchase(db, owner, card, agents, seed, now)
        await db.flush()

        for seed in APPROVAL_SEEDS:
            await _seed_approval(db, owner, card, agents, seed, now)
        await db.flush()

        summary = {
            "agents": await db.scalar(
                select(func.count()).select_from(Agent).where(Agent.owner_id == owner.id)
            ),
            "payment_methods": await db.scalar(
                select(func.count())
                .select_from(PaymentMethod)
                .where(PaymentMethod.owner_id == owner.id)
            ),
            "payment_policies": await db.scalar(
                select(func.count())
                .select_from(AgentPaymentPolicy)
                .where(AgentPaymentPolicy.owner_id == owner.id)
            ),
            "purchases": await db.scalar(
                select(func.count()).select_from(Purchase).where(Purchase.owner_id == owner.id)
            ),
            "subscriptions": await db.scalar(
                select(func.count())
                .select_from(Subscription)
                .where(Subscription.owner_id == owner.id)
            ),
            "pending_approvals": await db.scalar(
                select(func.count())
                .select_from(CartItem)
                .where(
                    CartItem.owner_id == owner.id,
                    CartItem.status == CartItemStatus.proposed,
                )
            ),
        }
        await db.commit()
        return {key: int(value or 0) for key, value in summary.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed repeatable AG Pay demo data.")
    parser.add_argument("--username", required=True, help="Existing platform username/email")
    args = parser.parse_args()

    try:
        summary = asyncio.run(seed_demo_data(args.username))
    except LookupError as exc:
        raise SystemExit(str(exc)) from exc

    print(f"Seeded demo data for {args.username.strip().lower()}:")
    for entity, count in summary.items():
        print(f"  {entity}: {count}")


if __name__ == "__main__":
    main()
