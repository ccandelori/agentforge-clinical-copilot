"""Tests for DeterministicGrader and LLMJudgeGrader (week1-gaps Task #18)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agentforge.llm.types import LLMResponse, Message
from agentforge.tools.dtos import ToolResultMetadata
from agentforge.tools.problems import ProblemItem, ProblemsPayload, ProblemsResult
from agentforge.tools.medications import (
    MedicationItem,
    MedicationsPayload,
    MedicationsResult,
)
from tests.eval.graders.deterministic import DeterministicGrader, GradeResult
from tests.eval.graders.llm_judge import LLMJudgeGrader, LLMJudgeResult
from tests.eval.harness import EvalCase, EvalCategory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _meta(name: str) -> ToolResultMetadata:
    return ToolResultMetadata(
        tool_name=name,
        fetched_at=datetime.now(UTC),
        data_freshness_seconds=60,
        source=f"openemr.{name}",
    )


def _problems(*ids: int) -> ProblemsResult:
    return ProblemsResult(
        metadata=_meta("get_active_problems"),
        payload=ProblemsPayload(
            problems=tuple(ProblemItem(id=i, title=f"Problem {i}") for i in ids),
        ),
    )


def _medications(*ids: int) -> MedicationsResult:
    return MedicationsResult(
        metadata=_meta("get_active_medications"),
        payload=MedicationsPayload(
            medications=tuple(
                MedicationItem(id=i, name=f"Med {i}") for i in ids
            ),
        ),
    )


def _case(
    *,
    expected_terms: tuple[str, ...] = (),
    forbidden_terms: tuple[str, ...] = (),
    category: EvalCategory = EvalCategory.HAPPY_PATH,
) -> EvalCase:
    return EvalCase(
        id="test_case",
        category=category,
        patient_id=8,
        query="How is this patient?",
        expected_behavior="Provides a grounded summary",
        expected_terms=expected_terms,
        forbidden_terms=forbidden_terms,
    )


def _llm_judge_response(score: int, rationale: str = "ok") -> LLMResponse:
    return LLMResponse(
        text=f"SCORE: {score}\nRATIONALE: {rationale}",
        tool_calls=[],
        stop_reason="end_turn",
        input_tokens=50,
        output_tokens=20,
    )


# ---------------------------------------------------------------------------
# DeterministicGrader
# ---------------------------------------------------------------------------


class TestDeterministicGrader:
    def test_grounded_citation_passes(self) -> None:
        tool_results: dict[str, Any] = {"get_active_problems": _problems(1)}
        response = "Patient has hypertension [problem #1]. "
        result = DeterministicGrader().grade(response, _case(), tool_results)
        assert result.grounded is True

    def test_ungrounded_citation_fails(self) -> None:
        tool_results: dict[str, Any] = {"get_active_problems": _problems(1)}
        response = "Patient has hypertension [problem #999]. "
        result = DeterministicGrader().grade(response, _case(), tool_results)
        assert result.grounded is False
        assert len(result.ungrounded_citations) == 1

    def test_no_citations_with_empty_tool_results_passes_grounding(self) -> None:
        # No citations in response and no tool results → nothing to check against
        result = DeterministicGrader().grade("Patient is well. ", _case(), {})
        assert result.grounded is True  # no citations = nothing ungrounded

    def test_required_term_present(self) -> None:
        result = DeterministicGrader().grade(
            "Patient has diabetes. ",
            _case(expected_terms=("diabetes",)),
            {},
        )
        assert result.required_terms_present is True
        assert result.missing_terms == ()

    def test_required_term_missing(self) -> None:
        result = DeterministicGrader().grade(
            "Patient is well. ",
            _case(expected_terms=("diabetes",)),
            {},
        )
        assert result.required_terms_present is False
        assert "diabetes" in result.missing_terms

    def test_required_term_check_is_case_insensitive(self) -> None:
        result = DeterministicGrader().grade(
            "Patient has DIABETES.",
            _case(expected_terms=("diabetes",)),
            {},
        )
        assert result.required_terms_present is True

    def test_forbidden_term_absent_passes(self) -> None:
        result = DeterministicGrader().grade(
            "Patient is stable. ",
            _case(forbidden_terms=("Xanax",)),
            {},
        )
        assert result.forbidden_terms_absent is True
        assert result.present_forbidden == ()

    def test_forbidden_term_present_fails(self) -> None:
        result = DeterministicGrader().grade(
            "Patient is on Xanax. ",
            _case(forbidden_terms=("Xanax",)),
            {},
        )
        assert result.forbidden_terms_absent is False
        assert "Xanax" in result.present_forbidden

    def test_forbidden_term_check_is_case_insensitive(self) -> None:
        result = DeterministicGrader().grade(
            "Patient takes XANAX daily. ",
            _case(forbidden_terms=("Xanax",)),
            {},
        )
        assert result.forbidden_terms_absent is False

    def test_passed_requires_all_three_checks(self) -> None:
        tool_results: dict[str, Any] = {"get_active_problems": _problems(5)}
        response = "Diabetes on file [problem #5]. "
        case = _case(expected_terms=("diabetes",), forbidden_terms=("xanax",))
        result = DeterministicGrader().grade(response, case, tool_results)
        assert result.passed is True

    def test_passed_false_when_any_check_fails(self) -> None:
        # grounded=True, required=True, but forbidden term present
        result = DeterministicGrader().grade(
            "Patient takes Xanax. diabetes mentioned.",
            _case(expected_terms=("diabetes",), forbidden_terms=("xanax",)),
            {},
        )
        assert result.passed is False

    def test_returns_grade_result_type(self) -> None:
        result = DeterministicGrader().grade("ok", _case(), {})
        assert isinstance(result, GradeResult)

    def test_multiple_citations_one_ungrounded(self) -> None:
        tool_results: dict[str, Any] = {"get_active_problems": _problems(1)}
        response = "Problem one [problem #1] and fake problem [problem #99]. "
        result = DeterministicGrader().grade(response, _case(), tool_results)
        assert result.grounded is False
        assert len(result.ungrounded_citations) == 1


# ---------------------------------------------------------------------------
# LLMJudgeGrader
# ---------------------------------------------------------------------------


class TestLLMJudgeGrader:
    async def test_returns_llm_judge_result_type(self) -> None:
        llm = AsyncMock()
        llm.complete.return_value = _llm_judge_response(4, "Clear and grounded.")
        result = await LLMJudgeGrader(llm).grade("Good response. ", _case())
        assert isinstance(result, LLMJudgeResult)

    async def test_parses_score_from_model_output(self) -> None:
        llm = AsyncMock()
        llm.complete.return_value = _llm_judge_response(3, "Adequate.")
        result = await LLMJudgeGrader(llm).grade("ok", _case())
        assert result.score == 3

    async def test_parses_rationale_from_model_output(self) -> None:
        llm = AsyncMock()
        llm.complete.return_value = _llm_judge_response(5, "Excellent clinical accuracy.")
        result = await LLMJudgeGrader(llm).grade("ok", _case())
        assert "Excellent" in result.rationale

    async def test_score_4_or_5_passes(self) -> None:
        llm = AsyncMock()
        llm.complete.return_value = _llm_judge_response(4)
        result = await LLMJudgeGrader(llm).grade("ok", _case())
        assert result.passed is True

    async def test_score_1_or_2_fails(self) -> None:
        llm = AsyncMock()
        llm.complete.return_value = _llm_judge_response(2)
        result = await LLMJudgeGrader(llm).grade("ok", _case())
        assert result.passed is False

    async def test_score_3_passes(self) -> None:
        llm = AsyncMock()
        llm.complete.return_value = _llm_judge_response(3)
        result = await LLMJudgeGrader(llm).grade("ok", _case())
        assert result.passed is True

    async def test_calls_llm_with_temperature_zero(self) -> None:
        llm = AsyncMock()
        llm.complete.return_value = _llm_judge_response(4)
        await LLMJudgeGrader(llm).grade("response", _case())
        _, kwargs = llm.complete.call_args
        assert kwargs.get("temperature") == 0.0

    async def test_includes_query_in_prompt(self) -> None:
        llm = AsyncMock()
        llm.complete.return_value = _llm_judge_response(4)
        case = _case()
        await LLMJudgeGrader(llm).grade("response", case)
        call_messages: list[Message] = llm.complete.call_args[1]["messages"]
        prompt_text = call_messages[0].content
        assert case.query in prompt_text

    async def test_includes_expected_behavior_in_prompt(self) -> None:
        llm = AsyncMock()
        llm.complete.return_value = _llm_judge_response(4)
        case = _case()
        await LLMJudgeGrader(llm).grade("response", case)
        messages: list[Message] = llm.complete.call_args[1]["messages"]
        assert case.expected_behavior in messages[0].content


# ---------------------------------------------------------------------------
# LLMJudgeGrader — consensus
# ---------------------------------------------------------------------------


class TestLLMJudgeConsensus:
    async def test_consensus_returns_majority_score(self) -> None:
        llm = AsyncMock()
        # Two 4s and one 3 → majority is 4
        llm.complete.side_effect = [
            _llm_judge_response(4, "Good."),
            _llm_judge_response(3, "Ok."),
            _llm_judge_response(4, "Good again."),
        ]
        result = await LLMJudgeGrader(llm).grade_consensus("ok", _case(), runs=3)
        assert result.score == 4

    async def test_consensus_calls_llm_runs_times(self) -> None:
        llm = AsyncMock()
        llm.complete.return_value = _llm_judge_response(5)
        await LLMJudgeGrader(llm).grade_consensus("ok", _case(), runs=3)
        assert llm.complete.call_count == 3

    async def test_consensus_default_runs_is_three(self) -> None:
        llm = AsyncMock()
        llm.complete.return_value = _llm_judge_response(4)
        await LLMJudgeGrader(llm).grade_consensus("ok", _case())
        assert llm.complete.call_count == 3
