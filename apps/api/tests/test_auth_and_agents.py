import jwt
from conftest import FakeBroker
from helpers import API, PASSWORD, bearer, connect_agent, create_agent, register_user
from httpx import AsyncClient


async def test_validation_error_does_not_echo_rejected_password_or_extra_body_values(
    client: AsyncClient,
) -> None:
    response = await client.post(
        f"{API}/auth/register",
        json={
            "username": "ab",
            "password": "pwSENT!",
            "https://secret.example.test/4242424242424242": "cvc-sentinel-987",
        },
    )

    assert response.status_code == 422
    assert "pwSENT!" not in response.text
    assert "4242424242424242" not in response.text
    assert "cvc-sentinel-987" not in response.text
    assert all(set(error) == {"type", "loc", "msg"} for error in response.json()["detail"])


async def test_registration_login_and_current_user(client: AsyncClient) -> None:
    token = await register_user(client, "  OWNER  ")

    me = await client.get(f"{API}/auth/me", headers=bearer(token))
    assert me.status_code == 200
    assert me.json()["username"] == "owner"
    assert me.json()["is_active"] is True

    duplicate = await client.post(
        f"{API}/auth/register",
        json={"username": "owner", "password": PASSWORD},
    )
    assert duplicate.status_code == 409

    wrong_password = await client.post(
        f"{API}/auth/login",
        json={"username": "owner", "password": "definitely-not-the-password"},
    )
    assert wrong_password.status_code == 401

    login = await client.post(
        f"{API}/auth/login",
        json={"username": "OWNER", "password": PASSWORD},
    )
    assert login.status_code == 200
    assert login.json()["token_type"] == "bearer"
    logged_in_me = await client.get(f"{API}/auth/me", headers=bearer(login.json()["access_token"]))
    assert logged_in_me.status_code == 200
    assert logged_in_me.json()["id"] == me.json()["id"]

    unauthenticated = await client.get(f"{API}/auth/me")
    assert unauthenticated.status_code == 401


async def test_agent_handshake_is_one_time_and_tokens_are_role_scoped(
    client: AsyncClient,
    broker: FakeBroker,
) -> None:
    user_token = await register_user(client, "agent-owner")
    created = await create_agent(client, user_token)
    assert created["status"] == "pending"
    assert created["connection_state"] == "pending"
    assert created["pairing_token"].startswith("pair_")

    invalid = await client.post(
        f"{API}/agent/handshake",
        json={
            "pairing_token": "pair_00000000000000000000000000000000",
            "instance_id": "intruder",
        },
    )
    assert invalid.status_code == 401

    handshake = await connect_agent(client, created["pairing_token"])
    agent_token = handshake["agent_access_token"]
    assert handshake["agent_id"] == created["id"]
    assert agent_token.startswith("agt_")

    replay = await client.post(
        f"{API}/agent/handshake",
        json={
            "pairing_token": created["pairing_token"],
            "instance_id": "second-instance",
        },
    )
    assert replay.status_code == 401

    user_token_as_agent = await client.post(
        f"{API}/agent/heartbeat",
        headers=bearer(user_token),
    )
    assert user_token_as_agent.status_code == 401

    agent_token_as_user = await client.get(
        f"{API}/auth/me",
        headers=bearer(agent_token),
    )
    assert agent_token_as_user.status_code == 401

    heartbeat = await client.post(
        f"{API}/agent/heartbeat",
        headers=bearer(agent_token),
    )
    assert heartbeat.status_code == 200
    assert heartbeat.json()["connection_state"] == "online"
    assert created["id"] in broker.online_agents

    agent = await client.get(f"{API}/agents/{created['id']}", headers=bearer(user_token))
    assert agent.status_code == 200
    assert agent.json()["status"] == "active"
    assert agent.json()["connection_state"] == "online"
    assert agent.json()["capabilities"] == ["shopping", "receipts"]
    assert "pairing_token" not in agent.json()

    revoked = await client.delete(
        f"{API}/agents/{created['id']}",
        headers=bearer(user_token),
    )
    assert revoked.status_code == 200
    rejected_after_revoke = await client.post(
        f"{API}/agent/heartbeat",
        headers=bearer(agent_token),
    )
    assert rejected_after_revoke.status_code == 401

    # Agent keys are opaque random values, not decodable owner JWTs.
    try:
        jwt.decode(agent_token, options={"verify_signature": False})
    except jwt.DecodeError:
        pass
    else:  # pragma: no cover - explicitly documents the security boundary
        raise AssertionError("agent access token unexpectedly has JWT structure")
