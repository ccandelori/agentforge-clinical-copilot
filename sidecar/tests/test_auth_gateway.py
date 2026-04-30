"""Behavior tests for AuthGateway — the trust boundary between user input
and the agent's tool layer. ARCHITECTURE.md §2.

Subtask 8.1 covers JWT validation: parse the Authorization header, decode
and verify the token, validate claim shapes, and return a partial
RequestContext (sensitivity_clearances stays empty until subtask 8.2
loads them from Redis).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from fastapi import HTTPException

from agentforge.gateway.auth_gateway import AuthGateway, RequestContext

JWT_SECRET = "test-secret-32-bytes-or-more-padding"
ISSUER = "openemr-agentforge"


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
    assert ctx.role == "Physicians"
    assert ctx.breakglass_flag is False
    assert ctx.sensitivity_clearances == frozenset()


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
