"""Deterministic mock tool layer for eval / regression-lock tests.

The layer loads a JSON fixture file (default: ``tests/fixtures/agent_eval.json``)
and exposes one async method per real tool. Each method materializes the
typed Pydantic ``ToolResult`` envelope from the fixture row, so call
sites can swap mock-for-real without changing types.

Two patient phenotypes ship by default:

  * 100 — "Susan Underwood" (complex chronic): hypertension, T2DM,
    stage-3 CKD, two active medications, A1c + creatinine labs, vitals,
    a clinical note, and search-notes hits for "diabetes" and "renal".
  * 200 — "Alex Newman" (sparse): demographics only; every other tool
    returns an empty payload.

Spec reference: ARCHITECTURE.md S8 (eval) and the Task 38 fixture-pin
rationale. Fixtures here are hand-authored rather than captured from a
running demo DB — they pin to the *schemas*, not to a particular OE
docker SHA. Logged as a deviation; see docs/DEVIATIONS.md.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentforge.gateway.auth_gateway import RequestContext
from agentforge.tools.allergies import AllergiesPayload, AllergiesResult
from agentforge.tools.demographics import DemographicsPayload, DemographicsResult
from agentforge.tools.dtos import ToolResultMetadata
from agentforge.tools.encounters import (
    EncounterItem,
    EncountersPayload,
    EncountersResult,
)
from agentforge.tools.labs import LabsPayload, LabsResult
from agentforge.tools.medications import MedicationsPayload, MedicationsResult
from agentforge.tools.notes import NoteItem, NotesPayload, NotesResult
from agentforge.tools.problems import ProblemsPayload, ProblemsResult
from agentforge.tools.search_notes import (
    SearchHit,
    SearchNotesPayload,
    SearchNotesResult,
)
from agentforge.tools.vitals import VitalsPayload, VitalsResult

DEFAULT_FIXTURES_PATH: Path = (
    Path(__file__).resolve().parent.parent / "fixtures" / "agent_eval.json"
)


class FixtureMissingError(KeyError):
    """Raised when a patient (or required tool data) isn't in the fixture file.

    Distinguished from a generic KeyError so eval tests can fail with a
    clear "fixture coverage gap" diagnosis rather than tripping over a
    raw KeyError that looks like a programmer mistake.
    """


class MockToolLayer:
    """Loads fixtures and serves typed ToolResult payloads per patient.

    The layer is deterministic: the same ``ctx.patient_id`` always
    yields the same payload for the same tool. Search-notes uses the
    raw query string as its lookup key, so callers should pass exactly
    the query they put in their fixture entries.
    """

    def __init__(
        self,
        fixtures_path: Path | None = None,
        fixtures: dict[int, dict[str, Any]] | None = None,
    ) -> None:
        if fixtures is not None:
            self._fixtures = fixtures
        else:
            self._fixtures = self._load_fixtures(
                fixtures_path or DEFAULT_FIXTURES_PATH
            )

    @staticmethod
    def _load_fixtures(path: Path) -> dict[int, dict[str, Any]]:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        patients = raw.get("patients", {})
        # JSON keys are strings; convert to int for ergonomic lookup.
        return {int(pid): data for pid, data in patients.items()}

    def has_patient(self, patient_id: int) -> bool:
        return patient_id in self._fixtures

    @staticmethod
    def _meta(tool_name: str, source: str) -> ToolResultMetadata:
        # All mock results carry the same fetched_at — tests that care
        # about freshness should override; defaults to "now."
        return ToolResultMetadata(
            tool_name=tool_name,
            fetched_at=datetime.now(UTC),
            data_freshness_seconds=0,
            source=source,
        )

    def _patient_data(self, patient_id: int) -> dict[str, Any]:
        if patient_id not in self._fixtures:
            raise FixtureMissingError(
                f"No fixture for patient_id={patient_id}; available: "
                f"{sorted(self._fixtures)}"
            )
        return self._fixtures[patient_id]

    # ---------- Tool methods (mirror the real fetchers) ----------

    async def get_demographics(self, ctx: RequestContext) -> DemographicsResult:
        data = self._patient_data(ctx.patient_id)
        raw = data.get("demographics")
        if raw is None:
            # Demographics is the one tool with no clean empty representation
            # (the payload requires a name + DOB). Treat absence as a
            # fixture-coverage gap.
            raise FixtureMissingError(
                f"patient {ctx.patient_id} has no demographics fixture"
            )
        return DemographicsResult(
            metadata=self._meta("get_demographics", "openemr.demographics"),
            payload=DemographicsPayload.model_validate(raw),
        )

    async def get_active_problems(self, ctx: RequestContext) -> ProblemsResult:
        data = self._patient_data(ctx.patient_id)
        raw = data.get("problems", {"problems": []})
        return ProblemsResult(
            metadata=self._meta("get_active_problems", "openemr.problems"),
            payload=ProblemsPayload.model_validate(raw),
        )

    async def get_active_medications(
        self, ctx: RequestContext
    ) -> MedicationsResult:
        data = self._patient_data(ctx.patient_id)
        raw = data.get("medications", {"medications": []})
        return MedicationsResult(
            metadata=self._meta("get_active_medications", "openemr.medications"),
            payload=MedicationsPayload.model_validate(raw),
        )

    async def get_active_allergies(
        self, ctx: RequestContext
    ) -> AllergiesResult:
        data = self._patient_data(ctx.patient_id)
        raw = data.get("allergies", {"allergies": []})
        return AllergiesResult(
            metadata=self._meta("get_active_allergies", "openemr.allergies"),
            payload=AllergiesPayload.model_validate(raw),
        )

    async def get_recent_labs(
        self,
        ctx: RequestContext,
        since_days: int | None = None,
    ) -> LabsResult:
        data = self._patient_data(ctx.patient_id)
        raw = data.get("labs", {"labs": []})
        return LabsResult(
            metadata=self._meta("get_recent_labs", "openemr.labs"),
            payload=LabsPayload.model_validate(raw),
        )

    async def get_vitals_trend(
        self,
        ctx: RequestContext,
        since_days: int | None = None,
    ) -> VitalsResult:
        data = self._patient_data(ctx.patient_id)
        raw = data.get("vitals", {"vitals": []})
        return VitalsResult(
            metadata=self._meta("get_vitals_trend", "openemr.vitals"),
            payload=VitalsPayload.model_validate(raw),
        )

    async def get_recent_notes(
        self,
        ctx: RequestContext,
        since_days: int | None = None,
    ) -> NotesResult:
        data = self._patient_data(ctx.patient_id)
        raw = data.get("notes", {"notes": []})
        # Build NoteItem list manually to keep field defaults explicit.
        notes_raw = raw.get("notes", [])
        items = tuple(NoteItem.model_validate(n) for n in notes_raw)
        return NotesResult(
            metadata=self._meta("get_recent_notes", "openemr.notes"),
            payload=NotesPayload(notes=items),
        )

    async def get_recent_encounters(
        self,
        ctx: RequestContext,
        since_days: int | None = None,
    ) -> EncountersResult:
        data = self._patient_data(ctx.patient_id)
        raw = data.get("encounters", {"encounters": []})
        encs_raw = raw.get("encounters", [])
        items = tuple(EncounterItem.model_validate(e) for e in encs_raw)
        return EncountersResult(
            metadata=self._meta("get_recent_encounters", "openemr.encounters"),
            payload=EncountersPayload(encounters=items),
        )

    async def search_notes(
        self,
        ctx: RequestContext,
        query: str,
        limit: int | None = None,
        since_days: int | None = None,
    ) -> SearchNotesResult:
        data = self._patient_data(ctx.patient_id)
        search_dict = data.get("search_notes", {})
        # Exact-match query lookup; trim to mirror the real fetcher's
        # whitespace handling.
        rows = search_dict.get(query.strip(), [])
        items = tuple(SearchHit.model_validate(r) for r in rows)
        return SearchNotesResult(
            metadata=self._meta("search_notes", "openemr.notes_search"),
            payload=SearchNotesPayload(results=items),
        )
