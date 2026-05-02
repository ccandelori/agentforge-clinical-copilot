"""Schema + fetcher for the ``search_notes`` tool.

FULLTEXT-relevance search over pnotes + form_clinical_notes, scoped to
the active patient. Calls the OpenEMR notes_search endpoint (Task 25),
which runs a UNION of MATCH(...) AGAINST(... IN NATURAL LANGUAGE MODE)
projections and returns up to 10 ranked hits with truncated snippets.

Like :mod:`agentforge.tools.notes`, this fetcher does per-record
sensitivity gating: each hit's title is walked through
``AuthGateway.check_record_visibility`` and the snippet+title are
cleared on deny. Score, id, source, and date survive so the model can
describe what was withheld ("3 restricted matches") without seeing
the protected text.

Limitation worth knowing: the PHP search response surfaces title +
snippet but not ``note_type`` or attending-of-record metadata. The
gateway's ``RecordMetadata`` only carries the title for these rows,
so title-prefix rules in the sensitivity policy fire, but note-type
and attending-only rules will be best-effort allow until the search
endpoint is extended (open follow-up).

See ARCHITECTURE.md S2 (sensitivity policy) and S4 (tools).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict

from agentforge.gateway.auth_gateway import RecordMetadata, RequestContext
from agentforge.llm.types import ToolSpec
from agentforge.tools.dtos import ToolResult, ToolResultMetadata


class _VisibilityGate(Protocol):
    """Subset of :class:`AuthGateway` the fetcher needs."""

    async def check_record_visibility(
        self, ctx: RequestContext, metadata: RecordMetadata
    ) -> bool: ...


class SearchHit(BaseModel):
    """One ranked search hit. ``title`` and ``snippet`` are cleared on
    a permission-denied row; ``id``, ``source``, ``date``, and ``score``
    remain so the model can summarize aggregates without seeing the
    protected text.
    """

    model_config = ConfigDict(frozen=True)

    id: int
    source: str  # 'pnote' or 'clinical_note'
    date: str | None = None
    title: str | None = None
    snippet: str | None = None
    score: float | None = None
    permission_denied: bool = False


class SearchNotesPayload(BaseModel):
    """Ranked search results for the bound patient, highest score first."""

    model_config = ConfigDict(frozen=True)

    results: tuple[SearchHit, ...]


SearchNotesResult = ToolResult[SearchNotesPayload]


SEARCH_NOTES_TOOL_SPEC = ToolSpec(
    name="search_notes",
    description=(
        "Full-text search over the active patient's clinical notes "
        "(pnotes + form_clinical_notes). Returns up to 10 relevance-"
        "ranked snippets with date, title, and a 200-character preview. "
        "Use when the user asks 'has the patient ever mentioned X?' or "
        "is looking for prior documentation of a specific symptom or "
        "intervention. Hits the user lacks clearance for come back with "
        "permission_denied=true and an empty snippet — surface as "
        "'N restricted matches' rather than ignoring."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "The search phrase (natural-language; not boolean "
                    "syntax). Empty / whitespace-only queries return no "
                    "results without hitting the database."
                ),
            },
            "limit": {
                "type": "integer",
                "description": (
                    "Max number of hits to return. Defaults to 5; "
                    "the server caps the upper bound at 10."
                ),
                "minimum": 1,
                "maximum": 10,
            },
            "since_days": {
                "type": "integer",
                "description": (
                    "How many days back to search. Defaults to 365; "
                    "the server caps the upper bound at 365."
                ),
                "minimum": 1,
                "maximum": 365,
            },
        },
        "required": ["query"],
    },
)


class SearchNotesFetcher:
    """HTTP fetcher + per-record sensitivity gating for search_notes."""

    DEFAULT_LIMIT = 5
    DEFAULT_SINCE_DAYS = 365

    def __init__(
        self,
        base_url: str,
        gateway: _VisibilityGate,
        http_client: httpx.AsyncClient | None = None,
        path: str = (
            "/interface/modules/custom_modules/oe-module-agentforge"
            "/public/internal/notes_search.php"
        ),
        cache_ttl_seconds: int = 60,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._path = path
        self._gateway = gateway
        self._client = http_client or httpx.AsyncClient(timeout=5.0)
        self._cache_ttl = cache_ttl_seconds

    async def fetch(
        self,
        ctx: RequestContext,
        query: str,
        limit: int | None = None,
        since_days: int | None = None,
    ) -> SearchNotesResult:
        trimmed = query.strip()
        effective_limit = (
            limit if limit is not None else self.DEFAULT_LIMIT
        )
        effective_since = (
            since_days if since_days is not None else self.DEFAULT_SINCE_DAYS
        )

        if trimmed == "":
            # Empty/whitespace queries don't go to the wire — PHP would
            # 400 anyway and the round trip is wasted.
            empty_metadata = ToolResultMetadata(
                tool_name="search_notes",
                fetched_at=datetime.now(UTC),
                data_freshness_seconds=self._cache_ttl,
                source="openemr.notes_search",
                redaction_applied=False,
                redacted_fields=(),
            )
            return SearchNotesResult(
                metadata=empty_metadata,
                payload=SearchNotesPayload(results=()),
            )

        url = f"{self._base_url}{self._path}"
        response = await self._client.get(
            url,
            params={
                "pid": ctx.patient_id,
                "q": trimmed,
                "limit": effective_limit,
                "since_days": effective_since,
            },
            headers={"Authorization": f"Bearer {ctx.raw_token}"},
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()

        gated_rows: list[SearchHit] = []
        any_denied = False
        for hit in body.get("results", []):
            metadata = RecordMetadata(
                # Search response carries the title but not note_type or
                # attending_only — title-prefix rules apply, the others
                # default to False/None and rely on the policy's
                # default-allow when its match metadata is absent.
                note_title=hit.get("title"),
                note_type=None,
                attending_only=False,
            )
            allowed = await self._gateway.check_record_visibility(ctx, metadata)
            score_raw = hit.get("score")
            score = (
                float(score_raw)
                if isinstance(score_raw, (int, float))
                else None
            )
            if not allowed:
                any_denied = True
                gated_rows.append(
                    SearchHit(
                        id=int(hit["id"]),
                        source=str(hit.get("source") or "pnote"),
                        date=hit.get("date"),
                        # Title and snippet leak the protected text; clear
                        # both. Date and score survive — they describe
                        # the match without exposing it.
                        title=None,
                        snippet=None,
                        score=score,
                        permission_denied=True,
                    )
                )
                continue
            gated_rows.append(
                SearchHit(
                    id=int(hit["id"]),
                    source=str(hit.get("source") or "pnote"),
                    date=hit.get("date"),
                    title=hit.get("title"),
                    snippet=hit.get("snippet"),
                    score=score,
                    permission_denied=False,
                )
            )

        payload = SearchNotesPayload(results=tuple(gated_rows))
        result_metadata = ToolResultMetadata(
            tool_name="search_notes",
            fetched_at=datetime.now(UTC),
            data_freshness_seconds=self._cache_ttl,
            source="openemr.notes_search",
            redaction_applied=any_denied,
            redacted_fields=(
                ("title", "snippet") if any_denied else ()
            ),
        )
        return SearchNotesResult(metadata=result_metadata, payload=payload)

    async def aclose(self) -> None:
        await self._client.aclose()
