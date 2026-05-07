"""``POST /api/agent/turn`` — the BFF agent-turn route.

This is the dashboard-facing half of the auth bridge laid out in
``docs/adr/0001-dashboard-auth-bridging.md``. The route is the cookie
session's only entry point into the agent's :class:`Orchestrator` and
the safety machinery that hangs off :class:`RequestContext`.

Pipeline:

  1. **Cookie → Session.** Resolve the session record from the
     HttpOnly cookie; reject early if the session is missing or has
     no ``fhir_user`` claim.
  2. **Session → OpenEMR identity** (cached per-access-token). The
     :class:`OpenEMRMeFetcher` calls the OpenEMR module's ``/me``
     endpoint with a sidecar-minted lookup JWT to resolve the OIDC
     UUID into the integer ``user_id`` + ``username`` + GACL group.
  3. **Identity + body → Internal JWT.** :class:`InternalJwtMinter`
     produces the same JWT shape the legacy PHP module mints. The
     route never sees ``RequestContext`` directly; the next step is
     what produces it.
  4. **Internal JWT → RequestContext.** The existing
     :class:`AuthGateway` validates the minted JWT exactly as it
     validates a legacy /turn request.
  5. **Orchestrator.** Same ``Orchestrator.turn()`` the legacy
     pipeline calls.

Step (4) is the architectural property the bridge exists to preserve:
the trust boundary stays a single chokepoint, even though the
identity surface fans out.

T38.10 scope: ``message + session_id + patient_id`` only. The W2
graph inputs (``document_id``, ``evidence_query``) and streaming
follow with the intake form / research-mode subtasks.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from agentforge.config import Settings
from agentforge.dashboard_auth.internal_jwt import (
    InternalJwtMintError,
    InternalJwtMinter,
)
from agentforge.dashboard_auth.openemr_me import (
    OpenEMRIdentity,
    OpenEMRMeFetcher,
    OpenEMRMeFetchError,
)
from agentforge.dashboard_auth.sessions import Session, SessionStore
from agentforge.gateway.auth_gateway import AuthGateway


class _OrchestratorProto:
    """Subset of :class:`Orchestrator` the route depends on.

    Typed as a ``Protocol``-style placeholder so the test suite can
    inject a stub without importing the orchestrator's heavy deps.
    """

    async def turn(  # pragma: no cover — protocol stub
        self,
        ctx: Any,
        user_message: str,
        *,
        session_id: str | None = None,
    ) -> str: ...


class AgentTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    patient_id: int
    session_id: str | None = None


class AgentTurnResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str


def _extract_user_uuid(fhir_user: str) -> str | None:
    """Pull the trailing UUID off a ``fhirUser`` claim.

    Accepts either the full URI form (``…/fhir/Practitioner/<uuid>``)
    OpenEMR emits in production, or the bare ``Practitioner/<uuid>``
    form some environments use. The trailing path segment is the UUID.
    """
    if not fhir_user:
        return None
    tail = fhir_user.rsplit("/", 1)[-1].strip()
    return tail or None


def make_agent_turn_router(
    *,
    settings: Settings,
    session_store: SessionStore,
    me_fetcher: OpenEMRMeFetcher,
    jwt_minter: InternalJwtMinter,
    auth_gateway: AuthGateway,
    orchestrator: _OrchestratorProto,
) -> APIRouter:
    """Build the BFF agent-turn router. Mounted at ``/api/agent``."""
    router = APIRouter(prefix="/api/agent", tags=["dashboard-agent"])

    # In-memory cache: access_token → resolved identity. Keyed by
    # access_token (not session_id) so an access-token rotation
    # invalidates the cached identity in lockstep.
    identity_cache: dict[str, OpenEMRIdentity] = {}

    async def _resolve_session(request: Request) -> Session:
        cookie = request.cookies.get(settings.dashboard_session_cookie_name)
        if not cookie:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
            )
        session = await session_store.get_session(cookie)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired",
            )
        return session

    async def _resolve_identity(session: Session) -> OpenEMRIdentity:
        user_uuid = _extract_user_uuid(session.fhir_user or "")
        if user_uuid is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session has no usable fhirUser identity",
            )
        cached = identity_cache.get(session.access_token)
        if cached is not None:
            return cached
        try:
            identity = await me_fetcher.fetch(user_uuid=user_uuid)
        except OpenEMRMeFetchError as exc:
            # 5xx / transport / 4xx from /me all surface as 502 to the
            # dashboard — the BFF itself is fine, but the upstream
            # bootstrap failed. The dashboard treats this as
            # "couldn't talk to OpenEMR; ask the user to retry".
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "error": "OpenEMR identity bootstrap failed.",
                    "upstream_status": exc.status_code,
                },
            ) from exc
        identity_cache[session.access_token] = identity
        return identity

    @router.post("/turn", response_model=AgentTurnResponse)
    async def agent_turn(
        request: Request,
        body: Annotated[AgentTurnRequest, Body()],
    ) -> AgentTurnResponse:
        if body.patient_id <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="patient_id must be positive",
            )

        session = await _resolve_session(request)
        identity = await _resolve_identity(session)

        try:
            internal_jwt = jwt_minter.mint(
                identity=identity,
                patient_id=body.patient_id,
            )
        except InternalJwtMintError as exc:
            # Defensive — InternalJwtMinter validates the same
            # invariants we already check here, but if it ever
            # diverges we want a clean 400 instead of a 500.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        ctx = await auth_gateway.validate_request(f"Bearer {internal_jwt}")
        reply = await orchestrator.turn(
            ctx,
            body.message,
            session_id=body.session_id,
        )
        return AgentTurnResponse(reply=reply)

    return router
