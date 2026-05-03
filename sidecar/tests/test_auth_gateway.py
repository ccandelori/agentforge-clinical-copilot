"""Behavior tests for AuthGateway — the trust boundary between user input
and the agent's tool layer. ARCHITECTURE.md §2.

Subtask 8.1 covers JWT validation: parse the Authorization header, decode
and verify the token, validate claim shapes, and return a partial
RequestContext (sensitivity_clearances stays empty until subtask 8.2
loads them from Redis).
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from unittest.mock import AsyncMock

import jwt
import pytest
import redis.exceptions
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from agentforge.gateway.auth_gateway import (
    AuthGateway,
    RequestContext,
    get_auth_gateway,
    get_request_context,
)

JWT_SECRET = "test-secret-32-bytes-or-more-padding"
ISSUER = "openemr-agentforge"


def make_redis_mock(
    *,
    policy_loaded: bool = True,
    role_clearances: dict[str, set[bytes]] | None = None,
    raise_on_get: bool = False,
    raise_on_smembers: bool = False,
) -> AsyncMock:
    """Build an async Redis mock prepopulated with policy state.

    `policy_loaded` controls the sentinel ``agentforge:policy:loaded``
    (the canonical key the policy loader writes; historical callers
    used ``agentforge:policy:version`` but that string was never set
    by the loader — the mismatch is fixed in auth_gateway.py).
    `role_clearances` maps role names to the byte-encoded clearance set
    Redis would return.
    """
    redis_mock = AsyncMock()

    async def get(key: str) -> bytes | None:
        if raise_on_get:
            raise redis.exceptions.ConnectionError("simulated outage")
        if key == "agentforge:policy:loaded":
            return b"1" if policy_loaded else None
        return None

    async def smembers(key: str) -> set[bytes]:
        if raise_on_smembers:
            raise redis.exceptions.ConnectionError("simulated outage")
        prefix = "agentforge:policy:role:"
        if not key.startswith(prefix):
            return set()
        role = key[len(prefix):]
        return (role_clearances or {}).get(role, set())

    redis_mock.get.side_effect = get
    redis_mock.smembers.side_effect = smembers
    return redis_mock


def make_token(secret: str = JWT_SECRET, **overrides: Any) -> str:
    """Build a JWT with sane defaults; pass kwargs to override individual claims."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "iss": ISSUER,
        "sub": "42",
        "patient_id": 123,
        "username": "jpatel",
        "role": "Physicians",
        "breakglass_flag": False,
        "breakglass_reason": None,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    payload.update(overrides)
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture
def gateway() -> AuthGateway:
    return AuthGateway(jwt_secret=JWT_SECRET)


# ---------- Happy path ----------


async def test_validate_request_returns_request_context_for_valid_token(
    gateway: AuthGateway,
) -> None:
    token = make_token()

    ctx = await gateway.validate_request(f"Bearer {token}")

    assert isinstance(ctx, RequestContext)
    assert ctx.user_id == 42
    assert ctx.patient_id == 123
    assert ctx.username == "jpatel"
    assert ctx.role == "Physicians"
    assert ctx.breakglass_flag is False
    assert ctx.breakglass_reason is None
    assert ctx.sensitivity_clearances == frozenset()


async def test_validate_request_propagates_breakglass_reason_when_flag_is_set(
    gateway: AuthGateway,
) -> None:
    token = make_token(
        breakglass_flag=True,
        breakglass_reason="After-hours admit; PCP unreachable.",
    )

    ctx = await gateway.validate_request(f"Bearer {token}")

    assert ctx.breakglass_flag is True
    assert ctx.breakglass_reason == "After-hours admit; PCP unreachable."


async def test_validate_request_role_can_be_null_for_users_with_no_gacl_group(
    gateway: AuthGateway,
) -> None:
    # AgentJwtService emits role=None when the user has no GACL membership;
    # the gateway should accept that and let a downstream policy refuse if
    # the absence of role is fatal for this request.
    token = make_token(role=None)

    ctx = await gateway.validate_request(f"Bearer {token}")

    assert ctx.role is None


# ---------- Authorization header parsing ----------


async def test_validate_request_raises_401_when_authorization_header_is_none(
    gateway: AuthGateway,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await gateway.validate_request(None)

    assert exc_info.value.status_code == 401


async def test_validate_request_raises_401_when_authorization_header_is_empty(
    gateway: AuthGateway,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await gateway.validate_request("")

    assert exc_info.value.status_code == 401


async def test_validate_request_raises_401_when_scheme_is_not_bearer(
    gateway: AuthGateway,
) -> None:
    token = make_token()

    with pytest.raises(HTTPException) as exc_info:
        await gateway.validate_request(f"Basic {token}")

    assert exc_info.value.status_code == 401


# ---------- JWT signature / expiry / issuer ----------


async def test_validate_request_raises_401_when_signature_is_invalid(
    gateway: AuthGateway,
) -> None:
    token = make_token(secret="completely-different-secret-padding-")

    with pytest.raises(HTTPException) as exc_info:
        await gateway.validate_request(f"Bearer {token}")

    assert exc_info.value.status_code == 401


async def test_validate_request_raises_401_when_token_is_expired(
    gateway: AuthGateway,
) -> None:
    expired_at = datetime.now(UTC) - timedelta(minutes=1)
    token = make_token(exp=expired_at)

    with pytest.raises(HTTPException) as exc_info:
        await gateway.validate_request(f"Bearer {token}")

    assert exc_info.value.status_code == 401


async def test_validate_request_raises_401_when_issuer_is_wrong(
    gateway: AuthGateway,
) -> None:
    token = make_token(iss="not-openemr-agentforge")

    with pytest.raises(HTTPException) as exc_info:
        await gateway.validate_request(f"Bearer {token}")

    assert exc_info.value.status_code == 401


# ---------- Claim shape validation ----------


async def test_validate_request_raises_400_when_patient_id_is_missing(
    gateway: AuthGateway,
) -> None:
    token = make_token(patient_id=None)

    with pytest.raises(HTTPException) as exc_info:
        await gateway.validate_request(f"Bearer {token}")

    assert exc_info.value.status_code == 400


async def test_validate_request_raises_400_when_patient_id_is_zero(
    gateway: AuthGateway,
) -> None:
    # pid=0 is OpenEMR's "no patient" sentinel; matches the PHP
    # controller's 400 behaviour.
    token = make_token(patient_id=0)

    with pytest.raises(HTTPException) as exc_info:
        await gateway.validate_request(f"Bearer {token}")

    assert exc_info.value.status_code == 400


async def test_validate_request_raises_400_when_sub_is_not_a_numeric_string(
    gateway: AuthGateway,
) -> None:
    token = make_token(sub="not-a-number")

    with pytest.raises(HTTPException) as exc_info:
        await gateway.validate_request(f"Bearer {token}")

    assert exc_info.value.status_code == 400


# ---------- Sensitivity clearance loading from Redis (subtask 8.2) ----------


async def test_validate_request_loads_clearances_from_redis_for_role() -> None:
    redis_mock = make_redis_mock(
        role_clearances={
            "Physicians": {b"mental_health_authorized", b"cfr42_authorized"},
        },
    )
    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=redis_mock)

    ctx = await gateway.validate_request(f"Bearer {make_token(role='Physicians')}")

    assert ctx.sensitivity_clearances == frozenset(
        {"mental_health_authorized", "cfr42_authorized"}
    )


async def test_validate_request_returns_empty_clearances_when_role_has_none() -> None:
    # Role exists but has no special clearances in the policy — this is a
    # valid configuration, distinct from "policy not loaded".
    redis_mock = make_redis_mock(role_clearances={"Receptionists": set()})
    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=redis_mock)

    ctx = await gateway.validate_request(f"Bearer {make_token(role='Receptionists')}")

    assert ctx.sensitivity_clearances == frozenset()


async def test_validate_request_does_not_hit_redis_when_role_is_null() -> None:
    redis_mock = make_redis_mock()
    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=redis_mock)

    ctx = await gateway.validate_request(f"Bearer {make_token(role=None)}")

    assert ctx.sensitivity_clearances == frozenset()
    redis_mock.get.assert_not_called()
    redis_mock.smembers.assert_not_called()


async def test_validate_request_raises_503_when_policy_sentinel_missing() -> None:
    # Fail-closed: if the sentinel key isn't present, the policy YAML
    # hasn't been loaded into Redis yet (or the keys were evicted).
    # Refuse requests until the policy reload succeeds.
    redis_mock = make_redis_mock(policy_loaded=False)
    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=redis_mock)

    with pytest.raises(HTTPException) as exc_info:
        await gateway.validate_request(f"Bearer {make_token()}")

    assert exc_info.value.status_code == 503


async def test_validate_request_raises_503_when_redis_get_fails() -> None:
    redis_mock = make_redis_mock(raise_on_get=True)
    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=redis_mock)

    with pytest.raises(HTTPException) as exc_info:
        await gateway.validate_request(f"Bearer {make_token()}")

    assert exc_info.value.status_code == 503


async def test_validate_request_raises_503_when_redis_smembers_fails() -> None:
    redis_mock = make_redis_mock(raise_on_smembers=True)
    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=redis_mock)

    with pytest.raises(HTTPException) as exc_info:
        await gateway.validate_request(f"Bearer {make_token()}")

    assert exc_info.value.status_code == 503


async def test_validate_request_caches_clearances_within_ttl() -> None:
    # Two requests for the same role within the cache TTL should hit
    # Redis exactly once.
    redis_mock = make_redis_mock(
        role_clearances={"Physicians": {b"mental_health_authorized"}},
    )

    # Inject a fixed clock so the cache never expires during the test.
    monotonic_value = [1000.0]

    def fake_clock() -> float:
        return monotonic_value[0]

    gateway = AuthGateway(
        jwt_secret=JWT_SECRET,
        redis_client=redis_mock,
        cache_ttl_seconds=60,
        clock=fake_clock,
    )

    await gateway.validate_request(f"Bearer {make_token()}")
    await gateway.validate_request(f"Bearer {make_token()}")

    assert redis_mock.smembers.call_count == 1


# ---------- RequestContext immutability + FastAPI dependency (subtask 8.3) ----------


def test_request_context_is_frozen_dataclass() -> None:
    # Documents the invariant: tools cannot mutate the auth context they
    # receive. Removing frozen=True breaks this test.
    ctx = RequestContext(
        user_id=1,
        patient_id=1,
        username="x",
        role=None,
        breakglass_flag=False,
        breakglass_reason=None,
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.user_id = 2  # type: ignore[misc]


async def test_get_request_context_dependency_resolves_authorization_header() -> None:
    # End-to-end wiring: a FastAPI app with the gateway in app.state
    # exposes get_request_context as a Depends(); the dependency reads
    # the Authorization header and returns a populated context.
    app = FastAPI()
    redis_mock = make_redis_mock(role_clearances={"Physicians": {b"mh"}})
    app.state.auth_gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=redis_mock)

    @app.post("/echo")
    async def echo(
        ctx: Annotated[RequestContext, Depends(get_request_context)],
    ) -> dict[str, Any]:
        return {
            "user_id": ctx.user_id,
            "patient_id": ctx.patient_id,
            "username": ctx.username,
            "clearances": sorted(ctx.sensitivity_clearances),
        }

    _ = echo  # silence ruff unused-function

    client = TestClient(app)
    token = make_token()
    response = client.post("/echo", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {
        "user_id": 42,
        "patient_id": 123,
        "username": "jpatel",
        "clearances": ["mh"],
    }


async def test_get_request_context_dependency_returns_401_without_authorization_header() -> None:
    app = FastAPI()
    app.state.auth_gateway = AuthGateway(jwt_secret=JWT_SECRET)

    @app.post("/echo")
    async def echo(
        ctx: Annotated[RequestContext, Depends(get_request_context)],
    ) -> dict[str, int]:
        return {"user_id": ctx.user_id}

    _ = echo  # silence ruff unused-function

    client = TestClient(app)
    response = client.post("/echo")

    assert response.status_code == 401


def test_get_auth_gateway_returns_gateway_from_app_state() -> None:
    app = FastAPI()
    sentinel = AuthGateway(jwt_secret=JWT_SECRET)
    app.state.auth_gateway = sentinel

    # Synthesise a Request whose .app.state has the gateway.
    from starlette.requests import Request

    scope = {"type": "http", "app": app, "headers": []}
    request = Request(scope)

    assert get_auth_gateway(request) is sentinel


async def test_validate_request_refetches_after_cache_ttl_expires() -> None:
    redis_mock = make_redis_mock(
        role_clearances={"Physicians": {b"mental_health_authorized"}},
    )

    monotonic_value = [1000.0]

    def fake_clock() -> float:
        return monotonic_value[0]

    gateway = AuthGateway(
        jwt_secret=JWT_SECRET,
        redis_client=redis_mock,
        cache_ttl_seconds=60,
        clock=fake_clock,
    )

    await gateway.validate_request(f"Bearer {make_token()}")

    # Advance clock past TTL.
    monotonic_value[0] = 1100.0

    await gateway.validate_request(f"Bearer {make_token()}")

    assert redis_mock.smembers.call_count == 2
