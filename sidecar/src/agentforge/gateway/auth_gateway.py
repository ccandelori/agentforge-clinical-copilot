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
from typing import Annotated, Protocol, cast

import jwt
import redis.exceptions
from fastapi import Depends, Header, HTTPException, Request, status

from agentforge.gateway.policy import RecordClassRule, SensitivityPolicy
from agentforge.gateway.policy_loader import POLICY_LOADED_KEY
from agentforge.gateway.policy_reader import fetch_sensitivity_rules

ISSUER = "openemr-agentforge"

# Alias for the canonical sentinel key the loader writes
# (``agentforge:policy:loaded``). Historically this module used a
# distinct ``POLICY_SENTINEL_KEY = "agentforge:policy:version"`` that
# nothing else in the codebase wrote — the mismatch was invisible
# while the auth gateway ran without a Redis client (tests mocked
# either key separately). Once Week 1 #2 wired the production
# auth_gateway to a real Redis, every /turn request hit the missing
# sentinel and 503'd. Re-aliased here so a single string flows from
# loader to reader; the old constant name is retained for any
# external reference.
POLICY_SENTINEL_KEY = POLICY_LOADED_KEY
ROLE_CLEARANCES_PREFIX = "agentforge:policy:role:"


class _RedisProto(Protocol):
    """The minimal async-Redis surface the gateway needs.

    Using a Protocol keeps the gateway compatible with the real
    `redis.asyncio.Redis` client AND with `unittest.mock.AsyncMock`
    fixtures in tests, without subclassing.
    """

    async def get(self, key: str) -> bytes | None: ...

    async def smembers(self, key: str) -> set[bytes]: ...

    async def keys(self, pattern: str) -> list[bytes] | list[str]: ...


@dataclass(frozen=True)
class RequestContext:
    """Immutable per-request authorization context produced by AuthGateway.

    Tools accept this as a typed parameter so they can't construct one
    themselves — the gateway is the single trust-boundary discipline
    from ARCHITECTURE.md §2.

    `username` and `breakglass_reason` extend the spec's set; they are
    needed downstream for sensitivity-policy lookup keying and
    audit-log routing respectively (the JWT carries them, so the
    context shouldn't drop them).
    """

    user_id: int
    patient_id: int
    username: str
    role: str | None
    breakglass_flag: bool
    breakglass_reason: str | None
    sensitivity_clearances: frozenset[str] = field(default_factory=frozenset)
    # Original bearer token, captured by the gateway. The orchestrator
    # forwards it to PHP internal endpoints (e.g. demographics) so they
    # can validate using the same JWT secret without re-minting.
    raw_token: str = ""


@dataclass(frozen=True)
class RecordMetadata:
    """Minimal structural classifiers a tool already has at hand from
    its query. Sufficient input for `check_record_visibility` —
    deliberately *not* the record body. ARCHITECTURE.md §2 forbids
    body inspection in sensitivity decisions.

    All fields default so a tool with no sensitivity-relevant metadata
    can call `RecordMetadata()` and get a default-allow result.
    """

    encounter_category: int | None = None
    note_title: str | None = None
    note_type: str | None = None
    attending_only: bool = False
    attending_user_id: int | None = None


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

        username = payload.get("username")
        if not isinstance(username, str) or username == "":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="username claim is required and must be a non-empty string",
            )

        breakglass_flag = bool(payload.get("breakglass_flag", False))
        breakglass_reason_raw = payload.get("breakglass_reason")
        breakglass_reason: str | None = (
            breakglass_reason_raw if isinstance(breakglass_reason_raw, str) else None
        )

        clearances = await self._load_clearances(role)

        return RequestContext(
            user_id=user_id,
            patient_id=patient_id,
            username=username,
            role=role,
            breakglass_flag=breakglass_flag,
            breakglass_reason=breakglass_reason,
            sensitivity_clearances=clearances,
            raw_token=token,
        )

    async def check_record_visibility(
        self,
        ctx: RequestContext,
        metadata: RecordMetadata,
    ) -> bool:
        """Decide whether `ctx`'s user can see a record with the given
        metadata. Metadata only — the record body is never read.

        Walks the rules in a fixed order
        (`behavioral_health` → `substance_abuse_cfr42` → `attending_only`)
        and short-circuits on the first deny. Default-allow when no rule
        fires; fail-closed when the policy isn't loaded or the metadata
        a fired rule needs is absent.

        Breakglass intent (`ctx.breakglass_reason`) is *not* a silent
        bypass: the visibility decision is unchanged. The audit-log
        layer (Task 34, future) is the consumer of breakglass intent;
        encoding a bypass here would let "I had a reason" gradually
        become "I always have a reason".
        """
        if self.redis is None:
            return False

        policy = await fetch_sensitivity_rules(self.redis)
        if policy is None:
            return False

        for class_name in self._rule_evaluation_order(policy):
            rule = policy.record_classes.get(class_name)
            if rule is None:
                continue
            if not self._rule_matches_metadata(rule, metadata):
                continue
            if not self._user_satisfies_rule(ctx, rule, metadata):
                return False
        return True

    @staticmethod
    def _rule_evaluation_order(policy: SensitivityPolicy) -> tuple[str, ...]:
        # Fixed walk order: keep `attending_only` last so the cheaper
        # category/title rules can short-circuit before we look at
        # supervisor overrides. Unknown rule names sort to the end so
        # adding a new class via YAML doesn't reorder the existing
        # checks.
        canonical = ("behavioral_health", "substance_abuse_cfr42", "attending_only")
        extras = tuple(sorted(set(policy.record_classes) - set(canonical)))
        return canonical + extras

    @staticmethod
    def _rule_matches_metadata(
        rule: RecordClassRule,
        metadata: RecordMetadata,
    ) -> bool:
        if (
            metadata.encounter_category is not None
            and metadata.encounter_category in rule.encounter_categories
        ):
            return True
        if metadata.note_title is not None and any(
            metadata.note_title.startswith(prefix) for prefix in rule.note_title_prefixes
        ):
            return True
        if metadata.note_type is not None and metadata.note_type in rule.note_types:
            return True
        return rule.attending_only and metadata.attending_only

    @staticmethod
    def _user_satisfies_rule(
        ctx: RequestContext,
        rule: RecordClassRule,
        metadata: RecordMetadata,
    ) -> bool:
        # Attending-only is a record-scoped predicate, not a clearance:
        # the attending of record passes by identity. Other users need
        # the configured override clearance.
        if rule.attending_only and metadata.attending_only:
            if metadata.attending_user_id is None:
                # Fail-closed: rule fired but the metadata didn't say
                # whose attending the record is. We can't safely
                # surface it.
                return False
            if metadata.attending_user_id == ctx.user_id:
                return True
            return all(c in ctx.sensitivity_clearances for c in rule.required_clearances)

        return all(c in ctx.sensitivity_clearances for c in rule.required_clearances)

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


# ---------- FastAPI dependency wiring ----------


def get_auth_gateway(request: Request) -> AuthGateway:
    """Read the AuthGateway from `request.app.state.auth_gateway`.

    The application's startup hook is responsible for constructing the
    gateway and assigning it; this getter simply exposes it as a FastAPI
    dependency so route handlers can `Depends(get_request_context)`
    without knowing about app state.
    """
    gateway = request.app.state.auth_gateway
    if not isinstance(gateway, AuthGateway):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AuthGateway is not configured on app.state",
        )
    return gateway


async def get_request_context(
    gateway: Annotated[AuthGateway, Depends(get_auth_gateway)],
    authorization: Annotated[str | None, Header()] = None,
) -> RequestContext:
    """FastAPI dependency: validate the Authorization header and return
    the immutable RequestContext. Use as
    `Annotated[RequestContext, Depends(get_request_context)]`
    on any agent route that requires an authenticated user/patient
    context."""
    return await gateway.validate_request(authorization)
