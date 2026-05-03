"""DataQualityChecker integration into the orchestrator (week1-gaps Task #7).

The checker exists as a tested unit in
:mod:`agentforge.verifier.data_quality`. What we exercise here is the
wiring:

  * ``Orchestrator`` accepts ``data_quality``; default is None so
    existing fixtures keep passing unchanged.
  * After the tool loop produces a final assistant text (and the
    verifier, if enabled), the orchestrator runs the stale-lab and
    problem/note-conflict heuristics over the per-turn
    ``tool_results`` and appends compact warnings to the user-visible
    output under a ``Data quality notes:`` header.
  * Counts (stale_labs, conflicts) ride on the trace via
    ``record_data_quality_metrics``.

The append-after-final-text placement is a deliberate deviation from
"before final LLM call" in the task spec — the iterative tool-use loop
in this orchestrator has no separate synthesis-input seam, mirroring
the truncator deferral documented in DEVIATIONS.md 2026-05-02.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

from agentforge.gateway.auth_gateway import RequestContext
from agentforge.llm.types import LLMResponse
from agentforge.orchestrator import Orchestrator
from agentforge.tools.dtos import ToolResultMetadata
from agentforge.tools.labs import LabResultItem, LabsPayload, LabsResult
from agentforge.tools.notes import NoteItem, NotesPayload, NotesResult
from agentforge.tools.problems import (
    ProblemItem,
    ProblemsPayload,
    ProblemsResult,
)
from agentforge.verifier.data_quality import DataQualityChecker


def _ctx() -> RequestContext:
    return RequestContext(
        user_id=42,
        patient_id=8,
        username="dr.smith",
        role="clinician",
        breakglass_flag=False,
        breakglass_reason=None,
        sensitivity_clearances=frozenset(),
        raw_token="raw.jwt.token",
    )


def _final(text: str) -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_calls=[],
        stop_reason="end_turn",
        input_tokens=10,
        output_tokens=5,
    )


def _tool_metadata(tool_name: str) -> ToolResultMetadata:
    return ToolResultMetadata(
        tool_name=tool_name,
        fetched_at=datetime(2026, 5, 2, tzinfo=UTC),
        data_freshness_seconds=60,
        source=f"openemr.{tool_name}",
    )


def _frozen_clock(today: date) -> DataQualityChecker:
    return DataQualityChecker(
        now=lambda: datetime(today.year, today.month, today.day, tzinfo=UTC),
        stale_lab_threshold_days=30,
    )


def _lab(*, date: date | None) -> LabResultItem:
    """Minimal :class:`LabResultItem` carrying only the fields the
    DataQualityChecker reads (date, test_name, value). Other required
    fields get filler ints so pydantic stays happy.
    """
    return LabResultItem(
        id=1,
        order_id=1,
        report_id=1,
        test_name="HbA1c",
        value="7.5",
        units="%",
        date=date,
    )


def _build(
    *,
    llm_text: str,
    data_quality: DataQualityChecker | None,
    langfuse: MagicMock | None = None,
) -> Orchestrator:
    llm = AsyncMock()
    llm.complete.return_value = _final(llm_text)
    return Orchestrator(
        llm=llm,
        demographics_fetcher=AsyncMock(),
        medications_fetcher=AsyncMock(),
        problems_fetcher=AsyncMock(),
        allergies_fetcher=AsyncMock(),
        labs_fetcher=AsyncMock(),
        vitals_fetcher=AsyncMock(),
        notes_fetcher=AsyncMock(),
        search_notes_fetcher=AsyncMock(),
        encounters_fetcher=AsyncMock(),
        immunizations_fetcher=AsyncMock(),
        procedures_fetcher=AsyncMock(),
        data_quality=data_quality,
        langfuse=langfuse,
    )


# Most tests below exercise ``_apply_data_quality`` directly — it's the
# narrow orchestrator-internal seam that runs the checker. The full
# turn() integration is covered by one end-to-end test that drives a
# real labs fetcher result through the loop. This split keeps the
# wiring tests fast (no fake LLM cycles) while still proving the loop
# integration works.


class TestDataQualityAppendsWarnings:
    def test_stale_lab_produces_inline_flag_in_final_text(self) -> None:
        # Lab from 45 days ago, threshold 30 — stale.
        lab = _lab(date=date(2026, 3, 15))  # ~48 days before today
        labs_result = LabsResult(
            metadata=_tool_metadata("get_recent_labs"),
            payload=LabsPayload(labs=(lab,)),
        )

        orch = _build(
            llm_text="answer",
            data_quality=_frozen_clock(date(2026, 5, 2)),
        )

        out = orch._apply_data_quality(
            "answer",
            {"get_recent_labs": labs_result},
            trace=None,
        )

        assert "Data quality notes:" in out
        assert "2026-03-15" in out

    def test_problem_note_conflict_appends_warning(self) -> None:
        problem = ProblemItem(id=1, title="Hypertension")
        note = NoteItem(
            id=10,
            source="pnote",
            date="2026-05-01",
            body="Hypertension resolved per BP readings this month.",
        )
        problems_result = ProblemsResult(
            metadata=_tool_metadata("get_active_problems"),
            payload=ProblemsPayload(problems=(problem,)),
        )
        notes_result = NotesResult(
            metadata=_tool_metadata("get_recent_notes"),
            payload=NotesPayload(notes=(note,)),
        )

        orch = _build(
            llm_text="answer",
            data_quality=_frozen_clock(date(2026, 5, 2)),
        )

        out = orch._apply_data_quality(
            "answer",
            {
                "get_active_problems": problems_result,
                "get_recent_notes": notes_result,
            },
            trace=None,
        )

        assert "Data quality notes:" in out
        assert "Hypertension" in out
        assert "resolved" in out


class TestDataQualityNoOps:
    def test_returns_text_unchanged_when_checker_is_none(self) -> None:
        orch = _build(llm_text="answer", data_quality=None)
        out = orch._apply_data_quality(
            "answer", {}, trace=None
        )
        assert out == "answer"

    def test_returns_text_unchanged_when_no_relevant_results(self) -> None:
        orch = _build(
            llm_text="answer",
            data_quality=_frozen_clock(date(2026, 5, 2)),
        )
        # No labs, no problems, no notes.
        out = orch._apply_data_quality(
            "answer", {}, trace=None
        )
        assert out == "answer"

    def test_fresh_lab_does_not_trigger_warning(self) -> None:
        lab = _lab(date=date(2026, 5, 1))  # 1 day before today
        labs_result = LabsResult(
            metadata=_tool_metadata("get_recent_labs"),
            payload=LabsPayload(labs=(lab,)),
        )

        orch = _build(
            llm_text="answer",
            data_quality=_frozen_clock(date(2026, 5, 2)),
        )

        out = orch._apply_data_quality(
            "answer",
            {"get_recent_labs": labs_result},
            trace=None,
        )

        assert out == "answer"


class TestDataQualityTelemetry:
    def test_records_zero_counts_when_all_clean(self) -> None:
        langfuse = MagicMock()
        trace = MagicMock(trace_id="t-1")
        orch = _build(
            llm_text="answer",
            data_quality=_frozen_clock(date(2026, 5, 2)),
            langfuse=langfuse,
        )

        orch._apply_data_quality(
            "answer", {}, trace=trace
        )

        langfuse.record_data_quality_metrics.assert_called_once()
        kwargs = langfuse.record_data_quality_metrics.call_args.kwargs
        assert kwargs["stale_labs_count"] == 0
        assert kwargs["conflict_count"] == 0

    def test_records_counts_when_warnings_fire(self) -> None:
        langfuse = MagicMock()
        trace = MagicMock(trace_id="t-1")

        lab = _lab(date=date(2026, 3, 1))  # well past 30-day threshold
        labs_result = LabsResult(
            metadata=_tool_metadata("get_recent_labs"),
            payload=LabsPayload(labs=(lab,)),
        )

        orch = _build(
            llm_text="answer",
            data_quality=_frozen_clock(date(2026, 5, 2)),
            langfuse=langfuse,
        )

        orch._apply_data_quality(
            "answer",
            {"get_recent_labs": labs_result},
            trace=trace,
        )

        kwargs = langfuse.record_data_quality_metrics.call_args.kwargs
        assert kwargs["stale_labs_count"] == 1
        assert kwargs["conflict_count"] == 0


class TestDataQualityFullTurnIntegration:
    """One end-to-end test that drives the full ``turn()`` so we know
    the orchestrator actually invokes ``_apply_data_quality`` on the
    success path. Smaller wiring tests above use the helper directly.
    """

    async def test_warnings_appear_in_turn_output(self) -> None:
        # Stub a tool loop where the model issues get_recent_labs once,
        # then returns final text. The fetcher returns a stale lab,
        # which the data quality checker should flag.
        from agentforge.llm.types import ToolCall

        lab = _lab(date=date(2026, 3, 1))
        labs_result = LabsResult(
            metadata=_tool_metadata("get_recent_labs"),
            payload=LabsPayload(labs=(lab,)),
        )

        labs_fetcher = AsyncMock()
        labs_fetcher.fetch.return_value = labs_result

        first_call = LLMResponse(
            text="",
            tool_calls=[
                ToolCall(
                    id="tc-1",
                    name="get_recent_labs",
                    input={},
                )
            ],
            stop_reason="tool_use",
            input_tokens=10,
            output_tokens=5,
        )
        final_call = _final("Latest HbA1c is 7.5%.")

        llm = AsyncMock()
        llm.complete.side_effect = [first_call, final_call]

        orch = Orchestrator(
            llm=llm,
            demographics_fetcher=AsyncMock(),
            medications_fetcher=AsyncMock(),
            problems_fetcher=AsyncMock(),
            allergies_fetcher=AsyncMock(),
            labs_fetcher=labs_fetcher,
            vitals_fetcher=AsyncMock(),
            notes_fetcher=AsyncMock(),
            search_notes_fetcher=AsyncMock(),
            encounters_fetcher=AsyncMock(),
            immunizations_fetcher=AsyncMock(),
            procedures_fetcher=AsyncMock(),
            data_quality=_frozen_clock(date(2026, 5, 2)),
        )

        reply = await orch.turn(_ctx(), "What's the latest A1c?")

        assert "Latest HbA1c" in reply
        assert "Data quality notes:" in reply
        assert "2026-03-01" in reply
