"""Tests for the W2 eval-gate runner (Task 18.2 / 18.3).

The runner loads the 50 W2 cases, invokes a supervisor-graph callable
per case (mockable for CI determinism, real for baseline regen), grades
each via :class:`EvalHarnessW2`, and aggregates per-category pass
rates.

The runner is *not* the gate. It produces the per-category pass-rate
JSON the gate consumes (Task 18.4).

Tests cover:

  * Case loading — all 50 W2 cases reach the runner.
  * Per-case dispatch — a mock supervisor runs once per case.
  * Adapter behaviour — the supervisor's structured result becomes
    the harness inputs (response, sources, structured_citation_payload,
    structured_citations, logs).
  * Aggregation — per-category pass-rate output (Task 18.3 reduce step).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from agentforge.llm.types import LLMResponse
from agentforge.schemas.citation import (
    Citation,
    PageBBox,
    SourceType,
)
from tests.eval.gate.runner_w2 import (
    SupervisorOutput,
    W2RunnerResult,
    load_week2_cases,
    run_week2_suite,
)
from tests.eval.graders.llm_judge_w2 import LLMJudge
from tests.eval.harness import EvalCase, EvalCategory
from tests.eval.harness_w2 import EvalHarnessW2


def _good_payload() -> dict[str, Any]:
    return {
        "source_type": "openemr_record",
        "source_id": "5",
        "page_or_section": "problem #5",
        "field_or_chunk_id": "title",
        "quote_or_value": "Hypertension",
    }


def _intake_citation() -> Citation:
    return Citation(
        source_type=SourceType.INTAKE_FORM,
        source_id="doc-1",
        page_or_section="page 1",
        field_or_chunk_id="primary_complaint",
        quote_or_value="chest pain on exertion",
        page_bbox=PageBBox(
            page=1, x0=0.1, y0=0.1, x1=0.4, y1=0.2, bbox_confidence=0.9
        ),
    )


def _passing_output(_case: EvalCase) -> SupervisorOutput:
    """Supervisor output every case-grading layer accepts as a pass."""
    return SupervisorOutput(
        response="The chief complaint is chest pain [problem #5].",
        sources="patient record: hypertension",
        structured_citation_payload=_good_payload(),
        structured_citations=(_intake_citation(),),
        logs=("clean trace line",),
    )


def _judge_response(verdict: str) -> LLMResponse:
    return LLMResponse(
        text=f"VERDICT: {verdict}\nRATIONALE: stub",
        tool_calls=[],
        stop_reason="end_turn",
        input_tokens=120,
        output_tokens=15,
    )


def _build_harness(*, judge_verdict: str = "PASS") -> EvalHarnessW2:
    llm = AsyncMock()
    llm.complete.return_value = _judge_response(judge_verdict)
    langfuse = MagicMock()
    judge = LLMJudge(llm=llm, langfuse=langfuse, model="claude-sonnet-4-6")
    trace = MagicMock()
    trace.trace_id = "test-trace"
    return EvalHarnessW2(judge=judge, trace=trace)


# ---------------------------------------------------------------------------
# Case loading
# ---------------------------------------------------------------------------


class TestLoadWeek2Cases:
    def test_loads_all_fifty_cases(self) -> None:
        cases = load_week2_cases()
        assert len(cases) == 50

    def test_each_case_has_a_w2_category(self) -> None:
        cases = load_week2_cases()
        valid = {
            EvalCategory.EXTRACTION,
            EvalCategory.EVIDENCE_RETRIEVAL,
            EvalCategory.CITATIONS,
            EvalCategory.REFUSAL,
            EvalCategory.MISSING_DATA,
        }
        for case in cases:
            assert case.category in valid, (
                f"{case.id} has non-W2 category {case.category}"
            )

    def test_explicit_directory_override_works(self, tmp_path: Path) -> None:
        # Override with empty dir → returns empty list (and does not
        # raise).  Lets the gate logic reuse the loader for synthetic
        # test fixtures without poking at the real cases directory.
        empty = tmp_path / "empty"
        empty.mkdir()
        assert load_week2_cases(directory=empty) == []


# ---------------------------------------------------------------------------
# Suite execution
# ---------------------------------------------------------------------------


class TestRunWeek2Suite:
    async def test_invokes_supervisor_once_per_case(self) -> None:
        cases = load_week2_cases()
        harness = _build_harness(judge_verdict="PASS")

        call_log: list[str] = []

        def supervisor(case: EvalCase) -> SupervisorOutput:
            call_log.append(case.id)
            return _passing_output(case)

        results = await run_week2_suite(
            cases=cases, supervisor=supervisor, harness=harness
        )

        assert len(call_log) == len(cases)
        assert sorted(call_log) == sorted(c.id for c in cases)
        assert all(isinstance(r, W2RunnerResult) for r in results)

    async def test_returns_one_result_per_case_in_order(self) -> None:
        cases = load_week2_cases()[:5]
        harness = _build_harness(judge_verdict="PASS")
        results = await run_week2_suite(
            cases=cases,
            supervisor=lambda c: _passing_output(c),
            harness=harness,
        )
        assert [r.case.id for r in results] == [c.id for c in cases]

    async def test_supports_async_supervisor(self) -> None:
        cases = load_week2_cases()[:3]
        harness = _build_harness(judge_verdict="PASS")

        async def supervisor(case: EvalCase) -> SupervisorOutput:
            return _passing_output(case)

        results = await run_week2_suite(
            cases=cases, supervisor=supervisor, harness=harness
        )
        assert len(results) == 3
        assert all(r.eval_result.passed for r in results)

    async def test_failing_supervisor_output_propagates_to_eval_result(
        self,
    ) -> None:
        # No-citation response → programmatic citation_present check
        # should fail; eval_result.passed is False.
        cases = load_week2_cases()[:1]
        harness = _build_harness(judge_verdict="PASS")

        def supervisor(_case: EvalCase) -> SupervisorOutput:
            return SupervisorOutput(
                response="No citation here.",
                sources="",
                structured_citation_payload=_good_payload(),
                structured_citations=(),
                logs=(),
            )

        results = await run_week2_suite(
            cases=cases, supervisor=supervisor, harness=harness
        )
        assert results[0].eval_result.passed is False


