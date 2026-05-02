"""Schema + fetcher for the ``get_recent_notes`` tool.

The first tool with per-record sensitivity gating: the PHP endpoint
returns full bodies for every note in scope, and the fetcher walks each
row's metadata through :class:`AuthGateway.check_record_visibility`
before emitting the typed payload. Rows the user lacks clearance for
are returned with ``permission_denied=True`` and stripped fields —
never silently dropped — so the model can still tell the user how
many restricted notes existed in the requested window.

The fetcher does the gating itself rather than letting the orchestrator
do it because:

  * The policy contract ("metadata-only, never body") needs to hold at
    the boundary the body crosses. Pushing the decision out to the
    orchestrator would mean the body briefly lived in an ungated
    ``ToolResult`` slot.
  * Per-row decisions need the row's own metadata. A bulk-allow/deny
    at the orchestrator level would require widening the result
    envelope to carry every row's metadata redundantly.

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
    """Subset of :class:`AuthGateway` the fetcher actually needs.

    Typed as a Protocol so tests can pass an ``AsyncMock`` straight
    through without inheriting from the real gateway.
    """

    async def check_record_visibility(
        self, ctx: RequestContext, metadata: RecordMetadata
    ) -> bool: ...


class NoteItem(BaseModel):
    """One note from pnotes or form_clinical_notes after sensitivity
    gating.

    On a permission-denied row, ``body``, ``title``, and ``author`` are
    cleared (those fields can leak the protected content) but ``id``,
    ``date``, ``source``, and ``note_type`` survive so the model can
    summarize what was withheld in aggregate.
    """

    model_config = ConfigDict(frozen=True)

    id: int
    source: str  # 'pnote' or 'clinical_note'
    date: str | None = None
    author: str | None = None
    title: str | None = None
    body: str | None = None
    note_type: str | None = None
    permission_denied: bool = False


class NotesPayload(BaseModel):
    """Recent notes for the bound patient, newest first, gated."""

    model_config = ConfigDict(frozen=True)

    notes: tuple[NoteItem, ...]


NotesResult = ToolResult[NotesPayload]


NOTES_TOOL_SPEC = ToolSpec(
    name="get_recent_notes",
    description=(
        "Look up the active patient's recent clinical notes (free-form "
        "pnotes plus structured encounter notes from the clinical-notes "
        "form). Returns date, author, title, body, source, and note_type "
        "for each note, newest first. Notes the calling user lacks "
        "clearance for come back with permission_denied=true and an "
        "empty body — surface them to the user as 'N restricted notes' "
        "rather than ignoring them silently."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "since_days": {
                "type": "integer",
                "description": (
                    "How many days back to look. Defaults to 90 if "
                    "omitted. The server caps the upper bound at 365."
                ),
                "minimum": 1,
                "maximum": 365,
            },
        },
        "required": [],
    },
)


class NotesFetcher:
    """HTTP fetcher + per-record sensitivity gating for recent_notes.

    Construction takes the gateway eagerly because gating is part of the
    fetcher's contract; a NotesFetcher with no gateway is a bug, not a
    degraded mode.
    """

    DEFAULT_SINCE_DAYS = 90

    def __init__(
        self,
        base_url: str,
        gateway: _VisibilityGate,
        http_client: httpx.AsyncClient | None = None,
        path: str = (
            "/interface/modules/custom_modules/oe-module-agentforge"
            "/public/internal/recent_notes.php"
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
        since_days: int | None = None,
    ) -> NotesResult:
        url = f"{self._base_url}{self._path}"
        effective_since = (
            since_days if since_days is not None else self.DEFAULT_SINCE_DAYS
        )
        response = await self._client.get(
            url,
            params={"pid": ctx.patient_id, "since_days": effective_since},
            headers={"Authorization": f"Bearer {ctx.raw_token}"},
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()

        gated_rows: list[NoteItem] = []
        any_denied = False
        for note in body.get("notes", []):
            metadata = RecordMetadata(
                note_type=note.get("note_type"),
                note_title=note.get("title"),
                # form_clinical_notes / pnotes don't surface attending_only
                # in the MVP payload, so the rule walks default-False.
                # If/when the PHP endpoint starts emitting attending data,
                # plumb it through here.
                attending_only=False,
            )
            allowed = await self._gateway.check_record_visibility(ctx, metadata)
            if not allowed:
                any_denied = True
                gated_rows.append(
                    NoteItem(
                        id=int(note["id"]),
                        source=str(note.get("source") or "pnote"),
                        date=note.get("date"),
                        # body / title / author can each leak the
                        # protected content — strip them. note_type and
                        # date stay so the model can describe what was
                        # withheld in aggregate.
                        author=None,
                        title=None,
                        body=None,
                        note_type=note.get("note_type"),
                        permission_denied=True,
                    )
                )
                continue
            gated_rows.append(
                NoteItem(
                    id=int(note["id"]),
                    source=str(note.get("source") or "pnote"),
                    date=note.get("date"),
                    author=note.get("author"),
                    title=note.get("title"),
                    body=note.get("body"),
                    note_type=note.get("note_type"),
                    permission_denied=False,
                )
            )

        payload = NotesPayload(notes=tuple(gated_rows))
        result_metadata = ToolResultMetadata(
            tool_name="get_recent_notes",
            fetched_at=datetime.now(UTC),
            data_freshness_seconds=self._cache_ttl,
            source="openemr.notes",
            redaction_applied=any_denied,
            redacted_fields=(
                ("body", "title", "author") if any_denied else ()
            ),
        )
        return NotesResult(metadata=result_metadata, payload=payload)

    async def aclose(self) -> None:
        await self._client.aclose()
