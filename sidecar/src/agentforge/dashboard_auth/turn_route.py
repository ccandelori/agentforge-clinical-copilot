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

import logging
from typing import Annotated, Any

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
from agentforge.orchestrator import get_turn_citation_index
from agentforge.verifier.cache import CitationIndex
from agentforge.verifier.citation import find_citations


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
    # FHIR Patient resource UUID — what the dashboard knows. The route
    # resolves it server-side via OpenEMRPatientPidFetcher; the agent
    # JWT carries the integer ``patient_data.pid`` the resolver
    # returns. See ADR-0001 §5.
    patient_uuid: str = Field(min_length=1)
    session_id: str | None = None


class AgentTurnCitation(BaseModel):
    """One citation surfaced to the dashboard chat UI.

    Shape matches what the vue-ui ``CitationPill`` / ``CitationsPane``
    components expect. Sourced by parsing the orchestrator's reply text
    for the W1 ``[record_type #id]`` grammar via
    :func:`agentforge.verifier.citation.find_citations`.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    excerpt: str
    date: str
    kind: str
    provenance: str | None = None


class AgentTurnResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str
    citations: list[AgentTurnCitation] = Field(default_factory=list)


# Map verifier record types → frontend kind enum
# ('note' | 'lab' | 'med' | 'problem' | 'allergy'). Unknown record
# types fall back to 'note' so the citation still surfaces as a pill.
_KIND_BY_RECORD_TYPE: dict[str, str] = {
    "note": "note",
    "encounter": "note",
    "visit": "note",
    "problem": "problem",
    "condition": "problem",
    "diagnosis": "problem",
    "allergy": "allergy",
    "med": "med",
    "medication": "med",
    "rx": "med",
    "lab": "lab",
    "lab_result": "lab",
    "observation": "lab",
}


_NARRATIVE_FIELDS: tuple[str, ...] = (
    # Note bodies, lab freetext — long-form clinical content
    "text", "note", "narrative", "summary", "body", "content",
)

_LABEL_FIELDS: tuple[str, ...] = (
    "description", "name", "title", "label", "display", "subject",
)

_VALUE_FIELDS: tuple[str, ...] = (
    "value", "result", "result_text", "code", "icd10", "rxnorm", "loinc",
)

_DATE_FIELD_PRIORITY: tuple[str, ...] = (
    "date", "effective_date", "onset_date", "recorded_date",
    "performed_date", "issued", "started", "created_at",
)

# Per-record-type bullet schemas for synthesized excerpts when the record
# has no narrative field. The model already saw these structured fields
# in the tool result; surfacing them as a "Label: value · Label: value"
# line is the difference between a useful pill and a bare token.
_METADATA_BY_KIND: dict[str, tuple[tuple[str, str], ...]] = {
    "encounter": (
        ("Type", "type"),
        ("Reason", "reason"),
        ("Provider", "provider"),
        ("Provider", "provider_name"),
        ("Status", "status"),
    ),
    "visit": (
        ("Type", "type"),
        ("Reason", "reason"),
        ("Provider", "provider"),
    ),
    "problem": (
        ("Description", "description"),
        ("ICD-10", "icd10"),
        ("Status", "status"),
        ("Severity", "severity"),
    ),
    "condition": (
        ("Description", "description"),
        ("ICD-10", "icd10"),
        ("Status", "status"),
    ),
    "diagnosis": (
        ("Description", "description"),
        ("ICD-10", "icd10"),
    ),
    "medication": (
        ("Drug", "name"),
        ("Drug", "drug"),
        ("Dose", "dose"),
        ("Route", "route"),
        ("Frequency", "frequency"),
        ("Status", "status"),
    ),
    "med": (
        ("Drug", "name"),
        ("Dose", "dose"),
        ("Route", "route"),
        ("Frequency", "frequency"),
    ),
    "rx": (
        ("Drug", "name"),
        ("Dose", "dose"),
    ),
    "allergy": (
        ("Substance", "substance"),
        ("Substance", "name"),
        ("Reaction", "reaction"),
        ("Severity", "severity"),
        ("Type", "type"),
    ),
    "lab": (
        ("Test", "name"),
        ("Test", "test"),
        ("Value", "value"),
        ("Units", "units"),
        ("Reference", "reference_range"),
        ("Flag", "flag"),
    ),
    "lab_result": (
        ("Test", "name"),
        ("Value", "value"),
        ("Units", "units"),
        ("Flag", "flag"),
    ),
    "observation": (
        ("Code", "code"),
        ("Value", "value"),
        ("Units", "units"),
    ),
    "vitals": (
        ("Type", "type"),
        ("Value", "value"),
        ("Units", "units"),
    ),
}

# Cap excerpts to bound payload size — full chart notes can be many KB
# of free text and the chat doesn't need every word. The client clamps
# its display further (line-clamp-3 by default) and reveals more on the
# "View source" expand toggle, so 4 KB is plenty of headroom.
_EXCERPT_MAX_LEN = 4000


def _truncate(s: str) -> str:
    s = s.strip()
    if len(s) > _EXCERPT_MAX_LEN:
        return s[: _EXCERPT_MAX_LEN - 1].rstrip() + "…"
    return s


def _pick_excerpt(record: dict[str, Any], record_type: str, fallback: str) -> str:
    """Build the best excerpt available for a record.

    Strategy:
      1. Narrative wins — note bodies, summaries, free-text fields.
      2. Else synthesize a "Label: value · Label: value" line using
         the kind-specific metadata schema. Encounters / problems /
         meds / allergies / labs all have useful structured fields
         the model already saw; surfacing them is the difference
         between a useful pill and a bare token.
      3. Generic label/value fallback.
      4. Raw `[type #id]` token as last resort.
    """
    # 1. Narrative field.
    for field_name in _NARRATIVE_FIELDS:
        v = record.get(field_name)
        if isinstance(v, str) and v.strip():
            return _truncate(v)

    # 2. Kind-specific metadata.
    fields = _METADATA_BY_KIND.get(record_type.lower())
    if fields:
        seen_labels: set[str] = set()
        parts: list[str] = []
        for label, field_name in fields:
            if label in seen_labels:
                continue
            v = record.get(field_name)
            if v is None or v == "":
                continue
            seen_labels.add(label)
            parts.append(f"{label}: {v}")
        if parts:
            return _truncate(" · ".join(parts))

    # 3. Generic label / value field.
    for field_name in _LABEL_FIELDS + _VALUE_FIELDS:
        v = record.get(field_name)
        if isinstance(v, str) and v.strip():
            return _truncate(v)

    # 4. Raw token.
    return fallback


def _pick_date(record: dict[str, Any]) -> str:
    """Pick the most-likely date field; empty string if none matches."""
    for field_name in _DATE_FIELD_PRIORITY:
        v = record.get(field_name)
        if isinstance(v, str) and v.strip():
            # Trim time portion for compact pill display.
            return v.split("T", 1)[0]
    return ""


def _build_citations(reply: str) -> list[AgentTurnCitation]:
    """Extract inline ``[type #id]`` citations from a reply for the UI.

    Excerpts and dates are resolved against the per-turn
    :class:`CitationIndex` stashed by the orchestrator — this gives the
    chat pills the actual record text instead of the raw ``[note #116]``
    token. When the index is unavailable (e.g. unit-test stub or a turn
    that didn't go through the verifier), we fall back to the raw token.
    """
    index: CitationIndex | None = get_turn_citation_index()
    out: list[AgentTurnCitation] = []
    seen: set[str] = set()
    for c in find_citations(reply):
        kind = _KIND_BY_RECORD_TYPE.get(c.record_type.lower(), "note")
        cid = f"{c.record_type}-{c.record_id}"
        if cid in seen:
            continue
        seen.add(cid)
        record = (
            index.get(c.record_type, c.record_id) if index is not None else None
        )
        source = f"{c.record_type.title()} {c.record_id}"
        if record is not None:
            excerpt = _pick_excerpt(record, c.record_type, fallback=c.raw)
            date = _pick_date(record)
        else:
            excerpt = c.raw
            date = ""
        out.append(
            AgentTurnCitation(
                id=cid,
                source=source,
                excerpt=excerpt,
                date=date,
                kind=kind,
                provenance=c.raw,
            )
        )
    return out


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
    patient_pid_fetcher: OpenEMRPatientPidFetcher,
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
    # Patient-UUID → integer pid resolution. Cheaper than identity
    # (no role / GACL lookup), but also cheap enough that a missing
    # cache turns into an extra OpenEMR round-trip per turn — worth
    # caching across turns for the same patient.
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
            # 5xx / transport / 4xx from /me all surface as 502 to the
            # dashboard — the BFF itself is fine, but the upstream
            # bootstrap failed. Log the upstream status + UUID we
            # tried so failures are diagnosable from the sidecar log.
            _log.warning(
                "Bridge /me failed: upstream_status=%s user_uuid=%s err=%s",
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
                "Bridge /patient_pid failed: upstream_status=%s patient_uuid=%s err=%s",
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

    @router.post("/turn", response_model=AgentTurnResponse)
    async def agent_turn(
        request: Request,
        body: Annotated[AgentTurnRequest, Body()],
    ) -> AgentTurnResponse:
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

        ctx = await auth_gateway.validate_request(f"Bearer {internal_jwt}")
        reply = await orchestrator.turn(
            ctx,
            body.message,
            session_id=body.session_id,
        )
        return AgentTurnResponse(
            reply=reply,
            citations=_build_citations(reply),
        )

    return router
