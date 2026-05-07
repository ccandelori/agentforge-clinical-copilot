"""Mints internal AGENTFORGE JWTs for the dashboard auth bridge.

ADR-0001 §4 mandates that the dashboard's session must produce a
``RequestContext`` through the existing :class:`AuthGateway`. This
module is the half of the bridge that emits a JWT carrying the
legacy claim shape (the half that *resolves* identity from a session
lives in :mod:`agentforge.dashboard_auth.openemr_me`).

The minter intentionally mirrors the OpenEMR PHP module's
``AgentJwtService`` claim shape line-for-line. If the two ever drift,
``test_minted_token_validates_through_real_auth_gateway`` is the
load-bearing test that catches it.
"""

from __future__ import annotations

import datetime as dt
from typing import Protocol

import jwt

from agentforge.dashboard_auth.openemr_me import OpenEMRIdentity

JWT_ISSUER = "openemr-agentforge"
INTERNAL_TOKEN_TTL_SECONDS = 300  # 5 minutes — same as legacy AgentJwtService


class _ClockProto(Protocol):
    def now(self) -> dt.datetime: ...


class InternalJwtMintError(ValueError):
    """Raised when the inputs would produce a JWT the gateway rejects.

    Failing fast in the minter — rather than waiting for AuthGateway
    to refuse the token at request time — keeps the error site close
    to the bug.
    """


class InternalJwtMinter:
    def __init__(self, *, jwt_secret: str, clock: _ClockProto) -> None:
        self._jwt_secret = jwt_secret
        self._clock = clock

    def mint(
        self,
        *,
        identity: OpenEMRIdentity,
        patient_id: int,
        breakglass_flag: bool = False,
        breakglass_reason: str | None = None,
    ) -> str:
        if patient_id <= 0:
            raise InternalJwtMintError(
                f"patient_id must be positive (got {patient_id})",
            )
        if identity.user_id <= 0:
            raise InternalJwtMintError(
                f"identity.user_id must be positive (got {identity.user_id})",
            )
        if identity.username == "":
            raise InternalJwtMintError("identity.username must be non-empty")

        now = self._clock.now()
        iat = int(now.timestamp())
        payload: dict[str, object] = {
            "iss": JWT_ISSUER,
            "iat": iat,
            "exp": iat + INTERNAL_TOKEN_TTL_SECONDS,
            "sub": str(identity.user_id),
            "patient_id": patient_id,
            "username": identity.username,
            "role": identity.role,
            "breakglass_flag": breakglass_flag,
            "breakglass_reason": breakglass_reason,
        }
        return jwt.encode(payload, self._jwt_secret, algorithm="HS256")
