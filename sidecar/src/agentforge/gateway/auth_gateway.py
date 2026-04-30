"""AuthGateway — the trust boundary between user-controlled input and
the agent's tool layer (ARCHITECTURE.md §2).

Every agent turn enters here. The gateway:
  1. Parses and verifies the JWT minted by the OpenEMR PHP module.
  2. Validates claim shapes that downstream code will rely on.
  3. Loads the user's sensitivity clearances from Redis (subtask 8.2).
  4. Returns an immutable RequestContext that the orchestrator and
     tool layer consume — they cannot construct one themselves, so
     the gateway is the single chokepoint for authorization decisions.

Subtask 8.1 covers JWT validation and partial RequestContext
construction; sensitivity_clearances stays empty until 8.2 lands.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import jwt
from fastapi import HTTPException, status

ISSUER = "openemr-agentforge"


@dataclass(frozen=True)
class RequestContext:
    """Immutable per-request authorization context produced by AuthGateway.

    Tools accept this as a typed parameter so they can't construct one
    themselves — that's the single trust-boundary discipline from
    ARCHITECTURE.md §2.
    """

    user_id: int
    patient_id: int
    role: str | None
    breakglass_flag: bool
    sensitivity_clearances: frozenset[str] = field(default_factory=frozenset)


class AuthGateway:
    def __init__(self, jwt_secret: str) -> None:
        self.jwt_secret = jwt_secret

    async def validate_request(self, authorization: str | None) -> RequestContext:
        token = self._parse_bearer(authorization)
        payload = self._decode_token(token)

        patient_id = payload.get("patient_id")
        if not isinstance(patient_id, int) or patient_id <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="patient_id claim is required and must be a positive integer",
            )

        sub = payload.get("sub")
        if not isinstance(sub, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="sub claim must be a string",
            )
        try:
            user_id = int(sub)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="sub claim must be a stringified integer user id",
            ) from exc

        if user_id <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="user_id must be positive",
            )

        role = payload.get("role")
        if role is not None and not isinstance(role, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="role claim must be a string or null",
            )

        breakglass_flag = bool(payload.get("breakglass_flag", False))

        return RequestContext(
            user_id=user_id,
            patient_id=patient_id,
            role=role,
            breakglass_flag=breakglass_flag,
        )

    @staticmethod
    def _parse_bearer(authorization: str | None) -> str:
        if not authorization:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authorization header is required",
            )
        prefix = "Bearer "
        if not authorization.startswith(prefix):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authorization scheme must be Bearer",
            )
        return authorization[len(prefix):]

    def _decode_token(self, token: str) -> dict[str, object]:
        try:
            return jwt.decode(
                token,
                self.jwt_secret,
                algorithms=["HS256"],
                issuer=ISSUER,
            )
        except jwt.ExpiredSignatureError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired",
            ) from exc
        except jwt.InvalidIssuerError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has invalid issuer",
            ) from exc
        except jwt.InvalidTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Token is invalid: {exc}",
            ) from exc
