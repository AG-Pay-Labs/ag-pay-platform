from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ag_platform_api.api.dependencies import AppSettings, Broker, CurrentUser, DatabaseSession
from ag_platform_api.core.security import hash_opaque_token, new_opaque_token
from ag_platform_api.models import Agent, AgentPaymentPolicy, AgentStatus
from ag_platform_api.schemas import (
    AgentCreate,
    AgentCreated,
    AgentRead,
    Message,
    PairingTokenResponse,
)
from ag_platform_api.services.serializers import agent_read

router = APIRouter(prefix="/agents", tags=["agents"])


async def owned_agent(db: DatabaseSession, owner_id: UUID, agent_id: UUID) -> Agent:
    agent = await db.scalar(select(Agent).where(Agent.id == agent_id, Agent.owner_id == owner_id))
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.post("", response_model=AgentCreated, status_code=status.HTTP_201_CREATED)
async def create_agent(
    payload: AgentCreate,
    user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
    broker: Broker,
) -> AgentCreated:
    token = new_opaque_token("pair")
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.pairing_token_expire_minutes)
    agent = Agent(
        owner_id=user.id,
        name=payload.name,
        description=payload.description,
        pairing_token_hash=hash_opaque_token(token, settings),
        pairing_expires_at=expires_at,
    )
    db.add(agent)
    await db.flush()
    db.add(AgentPaymentPolicy(owner_id=user.id, agent_id=agent.id))
    await db.commit()
    await db.refresh(agent)
    await broker.publish("agent.created", {"agent_id": agent.id, "owner_id": user.id})
    return AgentCreated(
        **agent_read(agent, settings).model_dump(),
        pairing_token=token,
        pairing_expires_at=expires_at,
    )


@router.get("", response_model=list[AgentRead])
async def list_agents(
    user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> list[AgentRead]:
    agents = (
        await db.scalars(
            select(Agent).where(Agent.owner_id == user.id).order_by(Agent.created_at.desc())
        )
    ).all()
    return [agent_read(agent, settings) for agent in agents]


@router.get("/{agent_id}", response_model=AgentRead)
async def get_agent(
    agent_id: UUID,
    user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> AgentRead:
    return agent_read(await owned_agent(db, user.id, agent_id), settings)


@router.post("/{agent_id}/pairing-token", response_model=PairingTokenResponse)
async def rotate_pairing_token(
    agent_id: UUID,
    user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
    broker: Broker,
) -> PairingTokenResponse:
    agent = await owned_agent(db, user.id, agent_id)
    if agent.status is AgentStatus.revoked:
        raise HTTPException(status_code=409, detail="Revoked agents cannot be paired")
    token = new_opaque_token("pair")
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.pairing_token_expire_minutes)
    agent.status = AgentStatus.pending
    agent.pairing_token_hash = hash_opaque_token(token, settings)
    agent.pairing_expires_at = expires_at
    agent.api_key_hash = None
    agent.api_key_expires_at = None
    agent.last_seen_at = None
    await db.commit()
    await broker.publish("agent.pairing_rotated", {"agent_id": agent.id})
    return PairingTokenResponse(pairing_token=token, pairing_expires_at=expires_at)


@router.delete("/{agent_id}", response_model=Message)
async def revoke_agent(
    agent_id: UUID,
    user: CurrentUser,
    db: DatabaseSession,
    broker: Broker,
) -> Message:
    agent = await owned_agent(db, user.id, agent_id)
    if agent.status is not AgentStatus.revoked:
        agent.status = AgentStatus.revoked
        agent.revoked_at = datetime.now(UTC)
        agent.api_key_hash = None
        agent.api_key_expires_at = None
        agent.pairing_token_hash = None
        await db.commit()
        await broker.publish("agent.revoked", {"agent_id": agent.id})
    return Message(message="Agent revoked")
