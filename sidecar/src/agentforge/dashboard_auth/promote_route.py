"""``POST /api/agent/promote/intake`` — BFF intake-promotion route (Gap 2).

The clinician-approved chart-write that closes the loop on the W2
brief's "persist derived facts as appropriate FHIR resources or
OpenEMR records" requirement. Same auth pipeline as
:mod:`agentforge.dashboard_auth.upload_route` and
:mod:`agentforge.dashboard_auth.turn_route`:

  1. **Cookie → Session.** Resolve the session record from the
     HttpOnly cookie; reject early if the session is missing or has
     no ``fhir_user`` claim.
  2. **Session → OpenEMR identity** (cached per-access-token).
  3. **Identity + patient_uuid → Internal JWT.**
     :class:`InternalJwtMinter` produces the same JWT shape the
     legacy PHP module mints.
  4. **Internal JWT → RequestContext.** :class:`AuthGateway` validates
     the minted JWT exactly as it validates a /turn or /upload
     request — keeps the trust chokepoint per ADR-0001 a single
     surface.
  5. **IntakePromoteWriter.** Forwards the user-bound JWT plus the
     accepted-items body to the OpenEMR PHP endpoint, which writes
     one ``lists`` row per accepted item.

The body shape is::

    {
      "patient_uuid": "...",            # FHIR Patient resource UUID
      "questionnaire_response_id": "...",  # optional — for audit/lineage
      "document_id": "...",                # optional — for audit/lineage
      "items": [
        {"kind": "allergy", "title": "Penicillin", "details": "rash"},
        {"kind": "medical_problem", "title": "Type 2 diabetes"},
        ...
      ]
    }

The route validates the body shape and the patient-scope mapping
(``patient_uuid`` → ``pid`` via :class:`OpenEMRPatientPidFetcher`)
before forwarding to PHP. The PHP side enforces the JWT-vs-body
patient-scope check independently — the BFF validation is for early,
clean error reporting; the PHP check is the load-bearing one.

Why per-row checkboxes are load-bearing
---------------------------------------

The W2 brief asks for "derived facts persisted as appropriate FHIR
resources or OpenEMR records". The original safety defense (defense-
qa-w2.md Q1 + Q7) said promotion was out of scope because OCR
mistakes shouldn't auto-write to the chart. This route reverses
that scope decision but preserves the safety property in a different
shape: the clinician reviews each extracted row on the dashboard's
:class:`ExtractionPanel`, ticks/un-ticks per-row checkboxes, and
clicks an explicit "Commit selected to chart" button. Promotion is
a deliberate human action, not an auto-write.

The BFF doesn't enforce this safety property — that lives in the
Vue UI — but the route is structured so a future flag-driven
"auto-promote on extract" would be a one-liner if the safety story
ever changes (and would be a deliberate, reviewable change rather
than an emergent one).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal, Protocol

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

_log = logging.getLogger(__name__)

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
from agentforge.dashboard_auth.openemr_patient_pid import (
    OpenEMRPatientPidFetcher,
    OpenEMRPatientPidFetchError,
)
from agentforge.dashboard_auth.sessions import Session, SessionStore
from agentforge.gateway.auth_gateway import AuthGateway
from agentforge.tools.intake_promote import IntakePromoteError


# Closed set of accepted item kinds — must mirror the PHP side's
# ``InternalIntakePromoteController::ALLOWED_KINDS`` and the writer's
# class constants. Adding a kind is a coordinated change across
# three files (Vue UI, this route, the PHP controller + writer).
PromoteItemKind = Literal[
    "allergy",
    "medical_problem",
    "medication",
    "family_history",
]


# Keep these caps in sync with the PHP controller. The duplication is
# defense in depth: the BFF rejects oversize batches early so they
# don't burn an OpenEMR round-trip; the PHP controller is still the
# load-bearing check on the database side.
_MAX_ITEMS = 100
_MAX_TITLE_LEN = 255
_MAX_DETAILS_LEN = 1024


class PromoteItem(BaseModel):
    """One accepted intake-form row the clinician approved.

    Validation here is structural — the controller rejects empty
    titles, oversize fields, and wrong ``kind`` values up front so
    the writer can trust shape correctness. Cross-row checks (no
    duplicates, etc.) are NOT enforced; a clinician committing two
    "Penicillin" allergy rows is a UX choice, not a contract
    violation.
    """

    model_config = ConfigDict(extra="forbid")

    kind: PromoteItemKind
    title: str = Field(min_length=1, max_length=_MAX_TITLE_LEN)
    details: str | None = Field(default=None, max_length=_MAX_DETAILS_LEN)


class PromoteIntakeRequest(BaseModel):
    """Body for ``POST /api/agent/promote/intake``.

    ``patient_uuid`` is the FHIR Patient resource UUID; the BFF maps
    it to the integer ``patient_data.pid`` via the same fetcher the
    /turn and /upload routes use. The PHP side then re-checks the
    JWT's ``patientId`` claim against this resolved pid as the
    load-bearing scope check.

    ``questionnaire_response_id`` and ``document_id`` are optional
    audit/lineage hints — they land in the chart row's
    ``lists.comments`` so a clinician can trace any AI-promoted row
    back to the upstream extraction. The PHP side does not enforce
    that these reference real records; they're documentary.
    """

    model_config = ConfigDict(extra="forbid")

    patient_uuid: str = Field(min_length=1)
    items: list[PromoteItem] = Field(min_length=1, max_length=_MAX_ITEMS)
    questionnaire_response_id: str | None = None
    document_id: str | None = None


class _PromoteWriterProto(Protocol):
    """Subset of :class:`IntakePromoteWriter` the route depends on.

    Typed as a Protocol so the test suite can inject a stub without
    constructing an httpx client.
    """

    async def promote(  # pragma: no cover — protocol stub
        self,
        *,
        jwt: str,
        body: dict[str, Any],
    ) -> dict[str, Any]: ...


def _extract_user_uuid(fhir_user: str) -> str | None:
    """Pull the trailing UUID off a ``fhirUser`` claim.

    Mirrors the helper in :mod:`turn_route` and :mod:`upload_route`.
    """
    if not fhir_user:
        return None
    tail = fhir_user.rsplit("/", 1)[-1].strip()
    return tail or None


def make_agent_promote_router(
    *,
    settings: Settings,
    session_store: SessionStore,
    me_fetcher: OpenEMRMeFetcher,
    patient_pid_fetcher: OpenEMRPatientPidFetcher,
    jwt_minter: InternalJwtMinter,
    auth_gateway: AuthGateway,
    promote_writer: _PromoteWriterProto,
) -> APIRouter:
    """Build the BFF intake-promotion router. Mounted at ``/api/agent``."""
    router = APIRouter(prefix="/api/agent", tags=["dashboard-agent"])

    # Same per-process caches the upload + turn routes use. See
    # upload_route's docblock for why these don't share dicts across
    # routers (boundary clarity > 100% cache hit ratio).
    identity_cache: dict[str, OpenEMRIdentity] = {}
    patient_pid_cache: dict[str, int] = {}

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
            _log.warning(
                "Promote bridge /me failed: upstream_status=%s user_uuid=%s err=%s",
                exc.status_code,
                user_uuid,
                exc,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "error": "OpenEMR identity bootstrap failed.",
                    "upstream_status": exc.status_code,
                    "stage": "me",
                },
            ) from exc
        identity_cache[session.access_token] = identity
        return identity

    async def _resolve_patient_pid(patient_uuid: str) -> int:
        cached = patient_pid_cache.get(patient_uuid)
        if cached is not None:
            return cached
        try:
            pid = await patient_pid_fetcher.fetch(patient_uuid=patient_uuid)
        except OpenEMRPatientPidFetchError as exc:
            _log.warning(
                "Promote bridge /patient_pid failed: upstream_status=%s "
                "patient_uuid=%s err=%s",
                exc.status_code,
                patient_uuid,
                exc,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "error": "OpenEMR patient bootstrap failed.",
                    "upstream_status": exc.status_code,
                    "stage": "patient_pid",
                },
            ) from exc
        patient_pid_cache[patient_uuid] = pid
        return pid

    @router.post("/promote/intake")
    async def promote_intake(
        request: Request,
        body: Annotated[PromoteIntakeRequest, Body()],
    ) -> dict[str, Any]:
        # ---- Auth pipeline (401 / 502 paths from the resolvers) ----
        session = await _resolve_session(request)
        identity = await _resolve_identity(session)
        patient_id = await _resolve_patient_pid(body.patient_uuid)

        try:
            internal_jwt = jwt_minter.mint(
                identity=identity,
                patient_id=patient_id,
            )
        except InternalJwtMintError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        # AuthGateway validation is the trust chokepoint per
        # ADR-0001: we go through it even though we know the JWT is
        # well-formed (we just minted it) so a future change to the
        # gateway's validation rules picks up all three routes
        # uniformly.
        await auth_gateway.validate_request(f"Bearer {internal_jwt}")

        # ---- Build the PHP body ----
        # The PHP controller takes ``patient_id`` (not patient_uuid)
        # — it does the JWT-vs-payload check on the integer pid we
        # just resolved. Forwarding the items verbatim preserves the
        # clinician's per-row approval set.
        php_body: dict[str, Any] = {
            "patient_id": patient_id,
            "items": [
                {
                    "kind": item.kind,
                    "title": item.title,
                    **({"details": item.details} if item.details is not None else {}),
                }
                for item in body.items
            ],
        }
        if body.questionnaire_response_id is not None:
            php_body["questionnaire_response_id"] = body.questionnaire_response_id
        if body.document_id is not None:
            # Try parsing — the PHP side accepts string-as-int but the
            # BFF can give a cleaner error on garbage input.
            try:
                php_body["document_id"] = int(body.document_id)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="document_id must parse as a positive integer.",
                ) from exc

        # ---- Forward to OpenEMR via the JWT-authed PHP endpoint ----
        try:
            receipt = await promote_writer.promote(
                jwt=internal_jwt,
                body=php_body,
            )
        except IntakePromoteError as exc:
            if exc.status_code == 0:
                # Transport failure — same shape as the upload route's
                # 503 path so the dashboard can react uniformly.
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Intake promotion failed: OpenEMR unreachable.",
                ) from exc
            # Upstream returned an error (401 / 403 / 400 / 5xx).
            # Surface the upstream status so the panel can decide
            # whether to retry, surface to the user, or page the
            # operator.
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "error": "Intake promotion failed.",
                    "sidecar_upstream_status": exc.status_code,
                },
            ) from exc

        # The PHP receipt is already shaped for the client
        # ({promoted: [...], count: N}); pass through verbatim.
        return receipt

    return router


# Re-export so callers can wire ``Depends(make_agent_promote_router)``
# without importing both modules. Mirrors the upload_route surface.
__all__ = [
    "PromoteIntakeRequest",
    "PromoteItem",
    "PromoteItemKind",
    "make_agent_promote_router",
]


# Suppress unused-import warning — Depends is part of the public
# surface for future deps wiring even though the current router
# doesn't use it directly.
_ = Depends
