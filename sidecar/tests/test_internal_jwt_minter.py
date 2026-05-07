"""Tests for ``agentforge.dashboard_auth.internal_jwt.InternalJwtMinter``.

The minter produces an AGENTFORGE JWT with the same claim shape the
legacy OpenEMR PHP module mints, so the sidecar's :class:`AuthGateway`
can validate it unchanged. See ADR-0001 §4 for the architectural
contract.

The single end-to-end correctness test composes the minter with a
real :class:`AuthGateway`: a JWT minted from a session+identity must
validate cleanly and produce a :class:`RequestContext` with the same
fields. That property is the whole point of the bridge.
"""

from __future__ import annotations

import datetime as dt

import jwt
import pytest

from agentforge.dashboard_auth.internal_jwt import (
    InternalJwtMinter,
    InternalJwtMintError,
)
from agentforge.dashboard_auth.openemr_me import OpenEMRIdentity
from agentforge.gateway.auth_gateway import AuthGateway

SECRET = "a-very-long-test-secret-that-is-at-least-32b"


class _FrozenClock:
    def __init__(self, now: dt.datetime) -> None:
        self._now = now

    def now(self) -> dt.datetime:
        return self._now

    def monotonic(self) -> float:
        # AuthGateway uses a Callable[[], float] for its monotonic clock.
        return self._now.timestamp()


def _make_minter(clock: _FrozenClock | None = None) -> InternalJwtMinter:
    return InternalJwtMinter(
        jwt_secret=SECRET,
        clock=clock or _FrozenClock(dt.datetime.now(dt.UTC)),
    )


def test_mint_produces_a_token_decodable_with_the_shared_secret() -> None:
    minter = _make_minter()
    identity = OpenEMRIdentity(user_id=17, username="admin", role="Administrators")

    token = minter.mint(identity=identity, patient_id=42)

    decoded = jwt.decode(
        token,
        SECRET,
        algorithms=["HS256"],
        issuer="openemr-agentforge",
    )
    assert decoded["sub"] == "17"
    assert decoded["patient_id"] == 42
    assert decoded["username"] == "admin"
    assert decoded["role"] == "Administrators"
    assert decoded["breakglass_flag"] is False
    assert decoded["breakglass_reason"] is None


def test_mint_emits_short_lived_exp_window() -> None:
    now = dt.datetime(2026, 5, 6, 12, 0, tzinfo=dt.UTC)
    minter = _make_minter(clock=_FrozenClock(now))

    token = minter.mint(
        identity=OpenEMRIdentity(user_id=1, username="u", role=None),
        patient_id=1,
    )

    decoded = jwt.decode(
        token,
        SECRET,
        algorithms=["HS256"],
        issuer="openemr-agentforge",
        options={"verify_exp": False},
    )
    assert decoded["iat"] == int(now.timestamp())
    # 5-minute lifetime mirrors the legacy AgentJwtService default.
    assert decoded["exp"] == int(now.timestamp()) + 300


def test_mint_rejects_non_positive_patient_id() -> None:
    minter = _make_minter()
    identity = OpenEMRIdentity(user_id=1, username="u", role=None)

    with pytest.raises(InternalJwtMintError):
        minter.mint(identity=identity, patient_id=0)
    with pytest.raises(InternalJwtMintError):
        minter.mint(identity=identity, patient_id=-1)


def test_mint_rejects_non_positive_user_id() -> None:
    minter = _make_minter()
    identity = OpenEMRIdentity(user_id=0, username="u", role=None)

    with pytest.raises(InternalJwtMintError):
        minter.mint(identity=identity, patient_id=1)


def test_mint_rejects_empty_username() -> None:
    minter = _make_minter()
    identity = OpenEMRIdentity(user_id=1, username="", role=None)

    with pytest.raises(InternalJwtMintError):
        minter.mint(identity=identity, patient_id=1)


def test_mint_emits_role_null_when_identity_has_no_group() -> None:
    minter = _make_minter()
    identity = OpenEMRIdentity(user_id=22, username="orphan", role=None)

    token = minter.mint(identity=identity, patient_id=99)

    decoded = jwt.decode(
        token,
        SECRET,
        algorithms=["HS256"],
        issuer="openemr-agentforge",
    )
    assert decoded["role"] is None


@pytest.mark.asyncio
async def test_minted_token_validates_through_real_auth_gateway() -> None:
    """End-to-end: a minted token must round-trip through AuthGateway.

    This is the load-bearing test for the bridge. If it fails the
    legacy /turn pipeline will reject the dashboard's session-derived
    requests at the trust boundary.
    """
    minter = _make_minter()
    gateway = AuthGateway(jwt_secret=SECRET, redis_client=None)

    token = minter.mint(
        identity=OpenEMRIdentity(user_id=17, username="admin", role="Administrators"),
        patient_id=42,
    )
    ctx = await gateway.validate_request(f"Bearer {token}")

    assert ctx.user_id == 17
    assert ctx.patient_id == 42
    assert ctx.username == "admin"
    assert ctx.role == "Administrators"
    assert ctx.breakglass_flag is False
    assert ctx.breakglass_reason is None
    # raw_token roundtrips so downstream tools can call legacy
    # /internal/* endpoints without re-minting.
    assert ctx.raw_token == token
