"""Tests for the W2 per-category pass-rate aggregator (Task 18.3).

The aggregator is the runner's reduce step: a list of
:class:`W2RunnerResult` becomes ``{category_name: pass_rate}``.

The gate logic (18.4) consumes this dict.  Keeping the reducer in its
own module so the gate's tests can build synthetic ``W2RunnerResult``
objects without spinning the supervisor mock.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

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
from tests.eval.gate.scoring import summarize_by_category
from tests.eval.graders.llm_judge_w2 import LLMJudge
from tests.eval.graders.programmatic import (
    CheckResult,
    CitationCheckResult,
    PhiCheckResult,
    ProgrammaticChecks,
)
from tests.eval.harness import EvalCase, EvalCategory
from tests.eval.harness_w2 import EvalHarnessW2, W2EvalResult


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


def _passing_output() -> SupervisorOutput:
    return SupervisorOutput(
        response="Chief complaint: chest pain [problem #5].",
        sources="patient record: hypertension",
        structured_citation_payload=_good_payload(),
        structured_citations=(_intake_citation(),),
        logs=("clean log",),
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


def _synthetic_result(
    *, case_id: str, category: EvalCategory, passed: bool
) -> W2RunnerResult:
    """Build a runner result without touching the harness or supervisor.

    Used by tests that just want to verify the reducer's arithmetic
    without the cost of running the full pipeline.
    """
    case = EvalCase(
        id=case_id,
        category=category,
        patient_id=1,
        query="stub",
        expected_behavior="stub",
    )
    citation_check = CitationCheckResult(
        name="citation_present",
        passed=passed,
        citation_count=1 if passed else 0,
    )
    programmatic = ProgrammaticChecks(
        schema_valid=CheckResult(name="schema_valid", passed=True),
        citation_present=citation_check,
        no_phi_in_logs=PhiCheckResult(
            name="no_phi_in_logs", passed=True, matches=()
        ),
    )
    return W2RunnerResult(
        case=case,
        eval_result=W2EvalResult(
            case_id=case_id,
            programmatic=programmatic,
            judge_outcome=None,
        ),
    )


# ---------------------------------------------------------------------------
# Synthetic-result arithmetic — verifies the reduce shape
# ---------------------------------------------------------------------------


class TestSummarizeByCategoryArithmetic:
    def test_empty_results_returns_empty_dict(self) -> None:
        assert summarize_by_category([]) == {}

    def test_single_passing_case_returns_one_for_its_category(self) -> None:
        results = [
            _synthetic_result(
                case_id="ext-1",
                category=EvalCategory.EXTRACTION,
                passed=True,
            )
        ]
        rates = summarize_by_category(results)
        assert rates == {"extraction": 1.0}

    def test_single_failing_case_returns_zero_for_its_category(self) -> None:
        results = [
            _synthetic_result(
                case_id="ext-1",
                category=EvalCategory.EXTRACTION,
                passed=False,
            )
        ]
        rates = summarize_by_category(results)
        assert rates == {"extraction": 0.0}

    def test_mixed_pass_fail_within_category(self) -> None:
        results = [
            _synthetic_result(
                case_id="ext-1", category=EvalCategory.EXTRACTION, passed=True
            ),
            _synthetic_result(
                case_id="ext-2", category=EvalCategory.EXTRACTION, passed=True
            ),
            _synthetic_result(
                case_id="ext-3", category=EvalCategory.EXTRACTION, passed=False
            ),
            _synthetic_result(
                case_id="ext-4", category=EvalCategory.EXTRACTION, passed=False
            ),
        ]
        rates = summarize_by_category(results)
        assert rates == {"extraction": 0.5}

    def test_multiple_categories_aggregate_independently(self) -> None:
        results = [
            _synthetic_result(
                case_id="ext-1", category=EvalCategory.EXTRACTION, passed=True
            ),
            _synthetic_result(
                case_id="ref-1", category=EvalCategory.REFUSAL, passed=True
            ),
            _synthetic_result(
                case_id="ref-2", category=EvalCategory.REFUSAL, passed=False
            ),
            _synthetic_result(
                case_id="cit-1", category=EvalCategory.CITATIONS, passed=False
            ),
        ]
        rates = summarize_by_category(results)
        assert rates == {
            "extraction": 1.0,
            "refusal": pytest.approx(0.5),
            "citations": 0.0,
        }


# ---------------------------------------------------------------------------
# End-to-end — runner produces results the reducer can summarize
# ---------------------------------------------------------------------------


class TestSummarizeByCategoryEndToEnd:
    async def test_all_cases_passing_yields_one_per_category(self) -> None:
        cases = load_week2_cases()
        harness = _build_harness(judge_verdict="PASS")
        results = await run_week2_suite(
            cases=cases,
            supervisor=lambda _c: _passing_output(),
            harness=harness,
        )
        rates = summarize_by_category(results)
        for category in (
            "extraction",
            "evidence_retrieval",
            "citations",
            "refusal",
            "missing_data",
        ):
            assert category in rates
            assert rates[category] == pytest.approx(1.0)
