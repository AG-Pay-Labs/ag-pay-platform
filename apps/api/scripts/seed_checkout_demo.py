import argparse
import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from ag_platform_api.db.session import SessionFactory
from ag_platform_api.models import (
    Agent,
    AgentPaymentMethod,
    BillingProfileType,
    PaymentMethod,
    PaymentMethodStatus,
    User,
)

DEMO_METHODS = (
    ("Stripe demo · succeeds", "pm_stripe_demo_success", "4242"),
    ("Stripe demo · declines", "pm_stripe_demo_decline", "0002"),
    ("Stripe demo · 3DS", "pm_stripe_demo_3ds", "3220"),
)
DEMO_BILLING_DETAILS = {
    "type": "personal",
    "full_name": "AG Pay Demo Buyer",
    "email": "buyer@example.com",
    "phone": "+34910000000",
    "address": {
        "line1": "Calle de Serrano 55",
        "line2": None,
        "city": "Madrid",
        "region": "Madrid",
        "postal_code": "28006",
        "country": "ES",
    },
}


async def seed(username: str) -> tuple[int, int, int]:
    async with SessionFactory() as db, db.begin():
        owner = await db.scalar(select(User).where(User.username == username.strip().lower()))
        if owner is None:
            raise LookupError("Create the AG Pay user before seeding checkout demo methods")
        agents = list(await db.scalars(select(Agent).where(Agent.owner_id == owner.id)))
        if not agents:
            raise LookupError("Create and pair an agent before seeding checkout demo methods")
        created = 0
        assignments = 0
        profiles_updated = 0
        now = datetime.now(UTC)
        for display_name, reference, last4 in DEMO_METHODS:
            method = await db.scalar(
                select(PaymentMethod).where(
                    PaymentMethod.owner_id == owner.id,
                    PaymentMethod.provider == "prototype-vault",
                    PaymentMethod.provider_payment_method_id == reference,
                )
            )
            if method is None:
                method = PaymentMethod(
                    owner_id=owner.id,
                    display_name=display_name,
                    status=PaymentMethodStatus.active,
                    provider="prototype-vault",
                    provider_payment_method_id=reference,
                    card_brand="Visa test",
                    card_last4=last4,
                    expiry_month=12,
                    expiry_year=max(2034, now.year + 2),
                    billing_profile_type=BillingProfileType.personal,
                    billing_details=DEMO_BILLING_DETAILS,
                )
                db.add(method)
                await db.flush()
                created += 1
            elif not isinstance(method.billing_details, dict) or not method.billing_details.get(
                "email"
            ):
                method.billing_profile_type = BillingProfileType.personal
                method.billing_details = DEMO_BILLING_DETAILS
                profiles_updated += 1
            for agent in agents:
                existing = await db.get(
                    AgentPaymentMethod,
                    {"agent_id": agent.id, "payment_method_id": method.id},
                )
                if existing is None:
                    db.add(AgentPaymentMethod(agent_id=agent.id, payment_method_id=method.id))
                    assignments += 1
        return created, assignments, profiles_updated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    args = parser.parse_args()
    try:
        created, assignments, profiles_updated = asyncio.run(seed(args.username))
    except LookupError as error:
        raise SystemExit(str(error)) from None
    print(
        "Checkout demo ready: "
        f"{created} methods created, {assignments} assignments added, "
        f"{profiles_updated} billing profiles completed."
    )


if __name__ == "__main__":
    main()
