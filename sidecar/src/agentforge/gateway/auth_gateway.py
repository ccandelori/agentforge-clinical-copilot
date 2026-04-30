"""AuthGateway — the trust boundary between user-controlled input and
the agent's tool layer (ARCHITECTURE.md §2).

Every agent turn enters here. The gateway:
  1. Parses and verifies the JWT minted by the OpenEMR PHP module.
  2. Validates claim shapes that downstream code will rely on.
  3. Loads the user's sensitivity clearances from Redis (subtask 8.2).
  4. Returns an immutable RequestContext that the orchestrator and
     tool layer consume — they cannot construct one themselves, so
     the gateway is the single chokepoint for authorization decisions.

Sensitivity-clearance lookup is fail-closed: if Redis is unreachable
or the policy sentinel is missing, the gateway refuses the request
with HTTP 503. The cached clearance set has a configurable TTL
(default 60s) so steady-state requests don't hammer Redis.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, cast

import jwt
import redis.exceptions
from fastapi import HTTPException, status

ISSUER = "openemr-agentforge"

POLICY_SENTINEL_KEY = "agentforge:policy:version"
ROLE_CLEARANCES_PREFIX = "agentforge:policy:role:"


class _RedisProto(Protocol):
    """The minimal async-Redis surface the gateway needs.

    Using a Protocol keeps the gateway compatible with the real
    `redis.asyncio.Redis` client AND with `unittest.mock.AsyncMock`
    fixtures in tests, without subclassing.
    """

    async def get(self, key: str) -> bytes | None: ...

    async def smembers(self, key: str) -> set[bytes]: ...


@dataclass(frozen=True)
class RequestContext:
    """Immutable per-request authorization context produced by AuthGateway.

    Tools accept this as a typed parameter so they can't construct one
    themselves — the gateway is the single trust-boundary discipline
    from ARCHITECTURE.md §2.
    """

    user_id: int
    patient_id: int
    role: str | None
    breakglass_flag: bool
    sensitivity_clearances: frozenset[str] = field(default_factory=frozenset)


class AuthGateway:
    def __init__(
        self,
        jwt_secret: str,
        redis_client: _RedisProto | None = None,
        cache_ttl_seconds: int = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.jwt_secret = jwt_secret
        self.redis = redis_client
        self.cache_ttl = cache_ttl_seconds
        self._clock = clock
        self._clearance_cache: dict[str, tuple[frozenset[str], float]] = {}

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

        role_raw = payload.get("role")
        if role_raw is not None and not isinstance(role_raw, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="role claim must be a string or null",
            )
        role: str | None = role_raw

        breakglass_flag = bool(payload.get("breakglass_flag", False))

        clearances = await self._load_clearances(role)

        return RequestContext(
            user_id=user_id,
            patient_id=patient_id,
            role=role,
            breakglass_flag=breakglass_flag,
            sensitivity_clearances=clearances,
        )

    async def _load_clearances(self, role: str | None) -> frozenset[str]:
        if role is None:
            return frozenset()
        if self.redis is None:
            # No Redis configured — testing or pre-deployment configuration.
            return frozenset()

        cached = self._clearance_cache.get(role)
        now = self._clock()
        if cached is not None and cached[1] > now:
            return cached[0]

        # Sentinel check: if the policy YAML hasn't been loaded into
        # Redis, fail closed rather than treating "no key" as "no
        # clearances configured for this role."
        try:
            sentinel = await self.redis.get(POLICY_SENTINEL_KEY)
        except redis.exceptions.RedisError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authorization policy unavailable: Redis error",
            ) from exc

        if sentinel is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authorization policy not loaded; refusing requests until reload",
            )

        try:
            members = await self.redis.smembers(ROLE_CLEARANCES_PREFIX + role)
        except redis.exceptions.RedisError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authorization policy unavailable: Redis error",
            ) from exc

        clearances = frozenset(_decode_member(m) for m in members)
        self._clearance_cache[role] = (clearances, now + self.cache_ttl)
        return clearances

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
            payload = jwt.decode(
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
        return cast(dict[str, object], payload)


def _decode_member(member: bytes | str) -> str:
    """Redis members come back as bytes from the real client; tests may
    pass either bytes or strings. Normalise to str."""
    if isinstance(member, bytes):
        return member.decode("utf-8")
    return member
