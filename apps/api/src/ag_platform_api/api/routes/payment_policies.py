from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ag_platform_api.api.dependencies import Broker, CurrentUser, DatabaseSession
from ag_platform_api.api.routes.agents import owned_agent
from ag_platform_api.models import Agent, AgentPaymentPolicy, PaymentApprovalMode
from ag_platform_api.schemas import AgentPaymentPolicyRead, AgentPaymentPolicyUpdate

router = APIRouter(tags=["payment policies"])


async def _owned_policies_by_agent(
    db: DatabaseSession,
    owner_id: UUID,
    agent_ids: list[UUID],
) -> dict[UUID, AgentPaymentPolicy]:
    if not agent_ids:
        return {}
    policies = (
        await db.scalars(
            select(AgentPaymentPolicy).where(
                AgentPaymentPolicy.owner_id == owner_id,
                AgentPaymentPolicy.agent_id.in_(agent_ids),
            )
        )
    ).all()
    return {policy.agent_id: policy for policy in policies}


@router.get("/payment-policies", response_model=list[AgentPaymentPolicyRead])
async def list_payment_policies(
    user: CurrentUser,
    db: DatabaseSession,
) -> list[AgentPaymentPolicy]:
    agent_ids = list(
        (
            await db.scalars(
                select(Agent.id)
                .where(Agent.owner_id == user.id)
                .order_by(Agent.created_at.desc(), Agent.id)
            )
        ).all()
    )
    policies_by_agent = await _owned_policies_by_agent(db, user.id, agent_ids)
    missing_agent_ids = [agent_id for agent_id in agent_ids if agent_id not in policies_by_agent]

    if missing_agent_ids:
        db.add_all(
            AgentPaymentPolicy(
                owner_id=user.id,
                agent_id=agent_id,
                mode=PaymentApprovalMode.always,
            )
            for agent_id in missing_agent_ids
        )
        try:
            await db.commit()
        except IntegrityError:
            # Another request may have filled the same legacy policy concurrently.
            await db.rollback()
        policies_by_agent = await _owned_policies_by_agent(db, user.id, agent_ids)

    return [policies_by_agent[agent_id] for agent_id in agent_ids]


@router.patch(
    "/agents/{agent_id}/payment-policy",
    response_model=AgentPaymentPolicyRead,
)
async def update_payment_policy(
    agent_id: UUID,
    payload: AgentPaymentPolicyUpdate,
    user: CurrentUser,
    db: DatabaseSession,
    broker: Broker,
) -> AgentPaymentPolicy:
    await owned_agent(db, user.id, agent_id)
    policy = await db.scalar(
        select(AgentPaymentPolicy)
        .where(
            AgentPaymentPolicy.agent_id == agent_id,
            AgentPaymentPolicy.owner_id == user.id,
        )
        .with_for_update()
    )
    if policy is None:
        policy = AgentPaymentPolicy(owner_id=user.id, agent_id=agent_id)
        db.add(policy)

    policy.mode = payload.mode
    policy.threshold_amount = payload.threshold_amount
    policy.threshold_currency = payload.threshold_currency
    await db.commit()
    await db.refresh(policy)
    await broker.publish(
        "agent.payment_policy_updated",
        {
            "agent_id": agent_id,
            "owner_id": user.id,
            "mode": policy.mode.value,
            "threshold_amount": policy.threshold_amount,
            "threshold_currency": policy.threshold_currency,
        },
    )
    return policy
