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

from fastapi import APIRouter, Body, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

_log = logging.getLogger(__name__)

from agentforge.config import Settings
from agentforge.dashboard_auth.internal_jwt import (
    InternalJwtMinter,
    InternalJwtMintError,
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
from agentforge.orchestrator import (
    get_last_persisted_handle,
    get_last_turn_extraction,
    get_turn_citation_index,
)
from agentforge.orchestrator.graph import DocumentType
from agentforge.schemas.citation import SourceType
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
        pdf_pages: list[Any] | None = None,
        document_id: int | None = None,
        doc_type: DocumentType | None = None,
        evidence_query: str = "",
    ) -> str: ...


class _DocumentBytesProto:
    """Subset of :class:`DocumentBytesFetcher` the route depends on.

    Carrying this as a Protocol-style stub keeps the heavy ``httpx``
    + ``DocumentBytesFetchError`` machinery out of unit-test imports.
    The route narrows the failure shape via duck-typed ``status_code``
    on the raised exception (mirroring what
    :class:`DocumentUploadError` does in upload_route).
    """

    async def fetch(  # pragma: no cover — protocol stub
        self, *, document_id: int, raw_token: str
    ) -> Any: ...


class _PdfRendererProto:
    """Subset of :class:`PdfRenderer` the route depends on."""

    def render_pages(self, pdf_bytes: bytes) -> list[Any]:  # pragma: no cover
        ...


class AgentTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    # FHIR Patient resource UUID — what the dashboard knows. The route
    # resolves it server-side via OpenEMRPatientPidFetcher; the agent
    # JWT carries the integer ``patient_data.pid`` the resolver
    # returns. See ADR-0001 §5.
    patient_uuid: str = Field(min_length=1)
    session_id: str | None = None
    # T38.15: optional pointer to an OpenEMR document the user just
    # uploaded via /api/agent/upload. When supplied, the route fetches
    # the bytes via DocumentBytesFetcher, renders to per-page PNGs via
    # PdfRenderer, and forwards ``pdf_pages`` + ``document_id`` to the
    # orchestrator. The orchestrator then routes through the W2
    # supervisor + extractor + verifier rather than the W1 chart-
    # question loop. Modeled as ``str`` (not ``int``) so the dashboard
    # can carry the value as a string in JSON without lossy precision
    # at very large ids; we parse to int at the boundary.
    document_id: str | None = None
    # P1.2: optional vision contract dispatch. ``intake_form`` (default
    # when None) routes through ``INTAKE_CONTRACT``; ``lab_pdf`` routes
    # through ``LAB_CONTRACT``. Pydantic validates against the
    # ``DocumentType`` enum's closed value set so a bogus payload is a
    # 422 at the BFF, never a misrouted call into the wrong extractor.
    # The dashboard UI work to actually send this field is the next-wave
    # follow-up; the API surface is plumbed through here so callers can
    # opt in without another schema change.
    doc_type: DocumentType | None = None
    # P1.3: free-text guideline question. Forwarded to the orchestrator
    # verbatim — the W2 graph routes to the evidence retriever node
    # when it is non-empty. Either ``evidence_query``, ``document_id``,
    # both, or neither may be set; the orchestrator falls back to the
    # W1 iterative loop when neither is present so chart-question turns
    # are unaffected. Mirrors the legacy /turn route's ``evidence_query``
    # field (see main.py).
    evidence_query: str = ""


class AgentTurnCitation(BaseModel):
    """One citation surfaced to the dashboard chat UI — W2 wire shape.

    The dashboard contract per W2_ARCHITECTURE.md §2.2: every clinical
    claim carries a machine-readable citation a reader can trace back
    to its source. The fields mirror :class:`agentforge.schemas.citation.Citation`
    on purpose — that is the canonical structured form, this is the
    BFF response surface that flattens it for the chat UI. Note that
    ``source_type`` is a free string rather than the schema's
    :class:`SourceType` enum so this model can also carry the synthetic
    ``OPENEMR_RECORD`` source we mint for W1 chart records (see
    :func:`_build_citations`); the value space is still constrained
    to the ``SourceType`` enum at construction time.

    Sourced by parsing the orchestrator's reply text for the
    ``[record_type #id]`` grammar via
    :func:`agentforge.verifier.citation.find_citations`, then enriched
    against the per-turn :class:`CitationIndex` so each pill carries
    the rich locator + verbatim quote the W2 contract asks for.

    Shape compatibility with W1 callers is intentionally NOT preserved
    — the dashboard is the only consumer and is updated in lockstep.
    """

    model_config = ConfigDict(extra="forbid")

    source_type: str
    """One of :class:`SourceType` values: ``openemr_record``,
    ``guideline``, ``intake_form``, ``lab_pdf``."""

    source_id: str
    """Stable handle for the source: FHIR/OpenEMR record id, guideline
    document id, or scanned-document id."""

    page_or_section: str | None = None
    """Human-readable locator: ``"page 2"`` for documents, ``"Section
    4.1"`` for guideline chunks, ``None`` for chart-resident records
    that have no page/section concept."""

    field_or_chunk_id: str | None = None
    """Stable inner handle: ``"<record_type>/<record_id>"`` for chart
    records, retrieval ``chunk_id`` for guideline chunks, extraction
    field key for scanned documents. ``None`` only for the rare
    fallback path where the verifier returned no record context."""

    quote_or_value: str | None = None
    """The literal extracted value or quoted text the claim is grounded
    in. Capped at :data:`_QUOTE_MAX_LEN` to bound payload size; the
    client truncates further for compact display. ``None`` only when
    the verifier returned no record context AND no raw token was
    captured (extremely unlikely — we always have at least the
    bracket-tag string)."""


class AgentTurnResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str
    citations: list[AgentTurnCitation] = Field(default_factory=list)
    # Per-turn structured extraction snapshot from the W2 INTAKE flow
    # (T38.12). Populated by ``Orchestrator._run_graph_turn`` via the
    # ``_TURN_EXTRACTION_VAR`` ContextVar; the route reads it through
    # :func:`get_last_turn_extraction` after ``orchestrator.turn()``
    # returns. ``None`` for chart-question turns and any path that did
    # not produce an extraction (evidence-only, W1 fall-through). The
    # shape is intentionally opaque on the wire — the dashboard drawer
    # types it client-side and renders an extraction-review panel
    # below the assistant bubble when non-null.
    extraction: dict[str, Any] | None = None
    # Per-turn persisted-resource handle (P1.1). Set when the post-
    # extract persist call to OpenEMR succeeded; ``None`` for any of:
    # chart-question turns (no extraction), evidence-only turns,
    # persister not wired, document_id missing, persistence failure
    # (logged but swallowed — the synthesis turn still surfaces the
    # model's reply). The dashboard's confirm-panel uses this to
    # route a follow-up "open this resource" action without a second
    # round-trip to look up the just-persisted handle.
    persisted_resource_id: str | None = None


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

# Cap quote_or_value to bound payload size — full chart notes can be many
# KB of free text and the chat doesn't need every word. The client clamps
# its display further (line-clamp-3 by default) and reveals more on the
# "View source" expand toggle, so 4 KB is plenty of headroom.
_QUOTE_MAX_LEN = 4000


def _truncate(s: str) -> str:
    s = s.strip()
    if len(s) > _QUOTE_MAX_LEN:
        return s[: _QUOTE_MAX_LEN - 1].rstrip() + "…"
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


def _date_to_section(record: dict[str, Any]) -> str | None:
    """Pick the most-likely date field for ``page_or_section``.

    Chart-resident records (notes, encounters, labs) have no page or
    formal section — but they DO have a clinical date that gives the
    citation pill a useful temporal locator (``"Recorded 2026-04-12"``).
    Returns ``None`` when no date field matched, so the wire shape
    omits ``page_or_section`` cleanly rather than emitting an empty
    string.
    """
    for field_name in _DATE_FIELD_PRIORITY:
        v = record.get(field_name)
        if isinstance(v, str) and v.strip():
            # Trim time portion for compact pill display.
            return v.split("T", 1)[0]
    return None


def _quote_from_w2_record(record: dict[str, Any], fallback: str) -> str:
    """Pick the verbatim quote from a W2-shaped record dict.

    W2 records (guideline chunks, intake/lab extractions) carry the
    canonical literal in ``quote_or_value``. Truncate so the wire
    payload stays bounded — guideline chunks routinely exceed
    :data:`_QUOTE_MAX_LEN` of body text.
    """
    quote = record.get("quote_or_value")
    if isinstance(quote, str) and quote.strip():
        return _truncate(quote)
    return fallback


def _w2_citation_from_record(
    record_type: str,
    record_id: str,
    record: dict[str, Any],
    fallback_quote: str,
) -> AgentTurnCitation:
    """Build a wire citation from a W2-shaped indexed record.

    The W2 graph stashes ``Citation.model_dump()`` into the per-turn
    index for guideline chunks (``source_type=guideline``) and intake
    /lab extraction fields. Those dicts already carry the canonical
    fields; we just normalize types and bound the quote length.
    """
    source_type_raw = record.get("source_type")
    if not isinstance(source_type_raw, str) or not source_type_raw:
        source_type_raw = SourceType.OPENEMR_RECORD.value

    source_id_raw = record.get("source_id")
    if isinstance(source_id_raw, str) and source_id_raw:
        source_id = source_id_raw
    else:
        source_id = record_id

    page_or_section = record.get("page_or_section")
    if not isinstance(page_or_section, str) or not page_or_section:
        page_or_section = None

    field_or_chunk_id_raw = record.get("field_or_chunk_id")
    if isinstance(field_or_chunk_id_raw, str) and field_or_chunk_id_raw:
        field_or_chunk_id = field_or_chunk_id_raw
    else:
        # Fall back to a deterministic synthetic key. The bracket-tag
        # parser already pinned (record_type, record_id), so reusing
        # them keeps the dashboard's pill identity stable.
        field_or_chunk_id = f"{record_type}/{record_id}"

    return AgentTurnCitation(
        source_type=source_type_raw,
        source_id=source_id,
        page_or_section=page_or_section,
        field_or_chunk_id=field_or_chunk_id,
        quote_or_value=_quote_from_w2_record(record, fallback_quote),
    )


def _w2_citation_from_w1_record(
    record_type: str,
    record_id: str,
    record: dict[str, Any],
    fallback_quote: str,
) -> AgentTurnCitation:
    """Build a W2 wire citation from a W1-shaped chart record.

    Chart records (the ones returned by the eleven W1 chart tools —
    problems, meds, allergies, labs, vitals, encounters, notes,
    immunizations, procedures, demographics) don't carry a structured
    citation; they're raw row dicts. We project them into the W2
    contract by minting an ``OPENEMR_RECORD`` source with:

    * ``source_id``  = the bracket-tag ``record_id`` (verifier already
      validated this against the per-turn cache, so it's a real id).
    * ``page_or_section`` = the row's date field if present (gives the
      pill a temporal locator), else ``None``.
    * ``field_or_chunk_id`` = ``"<record_type>/<record_id>"`` — the
      composite key that uniquely identifies this row within the turn.
    * ``quote_or_value`` = the rich excerpt :func:`_pick_excerpt`
      already produces (narrative when available, else the kind-specific
      "Label: value · Label: value" synthesis).
    """
    return AgentTurnCitation(
        source_type=SourceType.OPENEMR_RECORD.value,
        source_id=record_id,
        page_or_section=_date_to_section(record),
        field_or_chunk_id=f"{record_type}/{record_id}",
        quote_or_value=_pick_excerpt(record, record_type, fallback=fallback_quote),
    )


def _build_citations(reply: str) -> list[AgentTurnCitation]:
    """Extract inline ``[type #id]`` citations from a reply, in W2 wire shape.

    Bridge contract:

    1. The synthesizer emits bracket-tag citations in its reply text
       (the W1 grammar — ``[type #id]``). The bracket grammar is the
       LLM-friendly form; the W2 wire shape is the dashboard contract.
       Mapping between the two happens here.
    2. :func:`find_citations` parses the bracket tags into a
       ``(record_type, record_id)`` pair plus the raw token.
    3. Each pair is looked up in the per-turn :class:`CitationIndex`
       stashed by the orchestrator. The index records carry either:

       * **W2 shape** (key has ``source_type``) — guideline chunks
         registered by ``build_w2_citation_index`` and intake/lab
         extraction citations. Fields are copied through directly.
       * **W1 shape** (raw row dicts) — chart records keyed by their
         tool's row id. Projected to W2 via
         :func:`_w2_citation_from_w1_record`: ``source_type`` becomes
         ``OPENEMR_RECORD``; ``page_or_section`` becomes the row's date
         (when present); ``quote_or_value`` becomes the same rich
         excerpt the W1 surface used to render.

    4. When the per-turn index is unavailable (unit-test stub, or a
       turn that didn't pass through the verifier), we still emit a
       valid W2 citation — the bracket-tag carries (record_type,
       record_id) so the wire shape can be populated; only the
       ``quote_or_value`` falls back to the raw token.

    Dedup keys on ``(source_type, field_or_chunk_id)`` — the same key
    the underlying :class:`CitationIndex` uses, so duplicate bracket
    tags collapse into one pill.
    """
    index: CitationIndex | None = get_turn_citation_index()
    out: list[AgentTurnCitation] = []
    seen: set[tuple[str, str]] = set()
    for c in find_citations(reply):
        record = (
            index.get(c.record_type, c.record_id) if index is not None else None
        )

        if record is None:
            citation = AgentTurnCitation(
                source_type=SourceType.OPENEMR_RECORD.value,
                source_id=c.record_id,
                page_or_section=None,
                field_or_chunk_id=f"{c.record_type}/{c.record_id}",
                quote_or_value=c.raw,
            )
        elif "source_type" in record:
            # W2-shaped record: ``Citation.model_dump()`` from
            # build_w2_citation_index. Copy fields through.
            citation = _w2_citation_from_record(
                c.record_type, c.record_id, record, fallback_quote=c.raw
            )
        else:
            # W1-shaped record: raw chart row from build_citation_index.
            citation = _w2_citation_from_w1_record(
                c.record_type, c.record_id, record, fallback_quote=c.raw
            )

        # Dedup on the W2 key. ``field_or_chunk_id`` is non-None for the
        # W2 record path and for the W1 projection (we synthesize
        # ``"<record_type>/<record_id>"`` there); for the no-index
        # fallback we synthesize the same composite, so the dedup key
        # is always populated.
        dedup_key = (
            citation.source_type,
            citation.field_or_chunk_id or f"{c.record_type}/{c.record_id}",
        )
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        out.append(citation)
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
    document_bytes_fetcher: _DocumentBytesProto | None = None,
    pdf_renderer: _PdfRendererProto | None = None,
) -> APIRouter:
    """Build the BFF agent-turn router. Mounted at ``/api/agent``.

    ``document_bytes_fetcher`` + ``pdf_renderer`` are required when
    callers send ``document_id`` on the turn request body (T38.15).
    Both are kept optional on the constructor so test setups that
    never exercise the W2 path don't have to wire them; the route
    handler raises a clear 500 if a request arrives with
    ``document_id`` but the helpers are missing.
    """
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
        # Parse document_id at the boundary (string in JSON → int).
        # Reject non-positive values up front rather than letting the
        # PHP side return 400 — the panel can act on the response
        # status uniformly that way.
        document_id_int: int | None = None
        if body.document_id is not None:
            try:
                document_id_int = int(body.document_id)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="document_id must parse as a positive integer.",
                ) from exc
            if document_id_int <= 0:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="document_id must be a positive integer.",
                )

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

        # Resolve document_id → pdf_pages (T38.15). Mirror the legacy
        # main.py /turn path: 503 on transport failure, 502 on upstream
        # error, 422 on non-pdf mimetype or unrenderable bytes.
        pdf_pages: list[Any] | None = None
        if document_id_int is not None:
            if document_bytes_fetcher is None or pdf_renderer is None:
                # Misconfiguration: caller passed document_id but the
                # router was mounted without the W2 helpers. Fail loud
                # instead of silently dropping the input.
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        "document_id supplied but the BFF router has no "
                        "document fetcher / PDF renderer wired."
                    ),
                )
            try:
                document = await document_bytes_fetcher.fetch(
                    document_id=document_id_int,
                    raw_token=ctx.raw_token,
                )
            except Exception as exc:  # narrowed by status_code attr below
                # Duck-typed: DocumentBytesFetchError carries
                # ``status_code``. Anything else propagates.
                upstream_status = getattr(exc, "status_code", None)
                if not isinstance(upstream_status, int):
                    raise
                if upstream_status == 0:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Document fetch failed: OpenEMR unreachable.",
                    ) from exc
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={
                        "error": "Document fetch failed.",
                        "sidecar_upstream_status": upstream_status,
                    },
                ) from exc

            mimetype = getattr(document, "mimetype", "")
            content = getattr(document, "content", b"")
            if mimetype != "application/pdf":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=(
                        f"Document {document_id_int} is not a PDF "
                        f"(mimetype: {mimetype})."
                    ),
                )

            try:
                pdf_pages = pdf_renderer.render_pages(content)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=(
                        f"Document {document_id_int} could not be "
                        f"rendered as a PDF."
                    ),
                ) from exc

        reply = await orchestrator.turn(
            ctx,
            body.message,
            session_id=body.session_id,
            pdf_pages=pdf_pages,
            document_id=document_id_int,
            doc_type=body.doc_type,
            evidence_query=body.evidence_query,
        )
        # Read the extraction snapshot AFTER ``turn`` returns — same
        # ContextVar isolation contract as ``get_turn_citation_index``
        # (see orchestrator/__init__.py). ``None`` for non-INTAKE turns.
        extraction = get_last_turn_extraction()
        # Same contract for the persisted-resource handle (P1.1).
        persisted = get_last_persisted_handle()
        return AgentTurnResponse(
            reply=reply,
            citations=_build_citations(reply),
            extraction=extraction,
            persisted_resource_id=persisted.resource_id if persisted else None,
        )

    return router
