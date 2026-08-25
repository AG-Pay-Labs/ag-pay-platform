from collections import defaultdict
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ag_platform_api.api.dependencies import Broker, CurrentUser, DatabaseSession
from ag_platform_api.models import Agent, PaymentRuleSet
from ag_platform_api.schemas import PaymentRuleSetRead, PaymentRuleSetWrite

router = APIRouter(tags=["payment rule sets"])


def _read_rule_set(
    rule_set: PaymentRuleSet,
    assigned_agent_ids: list[UUID],
) -> PaymentRuleSetRead:
    return PaymentRuleSetRead(
        id=rule_set.id,
        name=rule_set.name,
        mode=rule_set.mode,
        threshold_amount=rule_set.threshold_amount,
        threshold_currency=rule_set.threshold_currency,
        assigned_agent_ids=assigned_agent_ids,
        created_at=rule_set.created_at,
        updated_at=rule_set.updated_at,
    )


async def _owned_agents(
    db: DatabaseSession,
    owner_id: UUID,
    agent_ids: list[UUID],
) -> list[Agent]:
    if not agent_ids:
        return []
    agents = list(
        (
            await db.scalars(
                select(Agent)
                .where(Agent.owner_id == owner_id, Agent.id.in_(agent_ids))
                .with_for_update()
            )
        ).all()
    )
    if len(agents) != len(agent_ids):
        raise HTTPException(status_code=404, detail="One or more agents were not found.")
    return agents


async def _apply_assignments(
    db: DatabaseSession,
    rule_set: PaymentRuleSet,
    requested_agent_ids: list[UUID],
) -> None:
    requested_agents = await _owned_agents(db, rule_set.owner_id, requested_agent_ids)
    current_agents = list(
        (
            await db.scalars(
                select(Agent)
                .where(
                    Agent.owner_id == rule_set.owner_id,
                    Agent.payment_rule_set_id == rule_set.id,
                )
                .with_for_update()
            )
        ).all()
    )
    requested_ids = {agent.id for agent in requested_agents}
    for agent in current_agents:
        if agent.id not in requested_ids:
            agent.payment_rule_set_id = None
    for agent in requested_agents:
        agent.payment_rule_set_id = rule_set.id


@router.get("/payment-rule-sets", response_model=list[PaymentRuleSetRead])
async def list_payment_rule_sets(
    user: CurrentUser,
    db: DatabaseSession,
) -> list[PaymentRuleSetRead]:
    rule_sets = list(
        (
            await db.scalars(
                select(PaymentRuleSet)
                .where(PaymentRuleSet.owner_id == user.id)
                .order_by(PaymentRuleSet.updated_at.desc(), PaymentRuleSet.id)
            )
        ).all()
    )
    assignments: dict[UUID, list[UUID]] = defaultdict(list)
    if rule_sets:
        rows = (
            await db.execute(
                select(Agent.payment_rule_set_id, Agent.id).where(
                    Agent.owner_id == user.id,
                    Agent.payment_rule_set_id.in_([rule_set.id for rule_set in rule_sets]),
                )
            )
        ).all()
        for rule_set_id, agent_id in rows:
            if rule_set_id is not None:
                assignments[rule_set_id].append(agent_id)
    return [_read_rule_set(rule_set, assignments[rule_set.id]) for rule_set in rule_sets]


@router.post(
    "/payment-rule-sets",
    response_model=PaymentRuleSetRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_payment_rule_set(
    payload: PaymentRuleSetWrite,
    user: CurrentUser,
    db: DatabaseSession,
    broker: Broker,
) -> PaymentRuleSetRead:
    rule_set = PaymentRuleSet(
        owner_id=user.id,
        name=payload.name,
        mode=payload.mode,
        threshold_amount=payload.threshold_amount,
        threshold_currency=payload.threshold_currency,
    )
    db.add(rule_set)
    try:
        await db.flush()
        await _apply_assignments(db, rule_set, payload.agent_ids)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A rule set with this name already exists.",
        ) from exc
    await db.refresh(rule_set)
    await broker.publish(
        "payment_rule_set.created",
        {"rule_set_id": rule_set.id, "owner_id": user.id, "agent_ids": payload.agent_ids},
    )
    return _read_rule_set(rule_set, payload.agent_ids)


@router.patch("/payment-rule-sets/{rule_set_id}", response_model=PaymentRuleSetRead)
async def update_payment_rule_set(
    rule_set_id: UUID,
    payload: PaymentRuleSetWrite,
    user: CurrentUser,
    db: DatabaseSession,
    broker: Broker,
) -> PaymentRuleSetRead:
    rule_set = await db.scalar(
        select(PaymentRuleSet)
        .where(PaymentRuleSet.id == rule_set_id, PaymentRuleSet.owner_id == user.id)
        .with_for_update()
    )
    if rule_set is None:
        raise HTTPException(status_code=404, detail="Rule set not found.")
    rule_set.name = payload.name
    rule_set.mode = payload.mode
    rule_set.threshold_amount = payload.threshold_amount
    rule_set.threshold_currency = payload.threshold_currency
    await _apply_assignments(db, rule_set, payload.agent_ids)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A rule set with this name already exists.",
        ) from exc
    await db.refresh(rule_set)
    await broker.publish(
        "payment_rule_set.updated",
        {"rule_set_id": rule_set.id, "owner_id": user.id, "agent_ids": payload.agent_ids},
    )
    return _read_rule_set(rule_set, payload.agent_ids)
