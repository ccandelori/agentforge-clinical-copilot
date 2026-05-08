"""Tests for the W2 judge retry-with-fresh-seed path (Task 17.5).

The judge runs at temperature=0, so two calls on identical inputs
*should* produce the same verdict. When they don't (the rare path
where the model is genuinely on the boundary or the prompt is
ambiguous), the judge runs a third tiebreaker call and majority-votes.

The contract: ``grade_with_retry`` returns the same shape as
``grade``, but with three extra knobs surfaced so callers can detect
disagreement (and surface it in eval reports for prompt-tuning):

  * ``LLMJudgeOutcome.attempts`` — how many calls were issued.
  * ``LLMJudgeOutcome.tiebreaker_used`` — True when the third call ran.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from agentforge.llm.types import LLMResponse
from tests.eval.graders.llm_judge_w2 import (
    JudgeCategory,
    JudgeVerdict,
    LLMJudge,
)
from tests.eval.harness import EvalCase, EvalCategory


def _judge_response(verdict: str, rationale: str = "ok") -> LLMResponse:
    return LLMResponse(
        text=f"VERDICT: {verdict}\nRATIONALE: {rationale}",
        tool_calls=[],
        stop_reason="end_turn",
        input_tokens=120,
        output_tokens=15,
    )


def _case() -> EvalCase:
    return EvalCase(
        id="w2_retry",
        category=EvalCategory.HALLUCINATION,
        patient_id=8,
        query="q",
        expected_behavior="b",
    )


def _trace() -> Any:
    sentinel = MagicMock()
    sentinel.trace_id = "test-trace"
    return sentinel


class TestRetryAgreement:
    async def test_two_passes_in_a_row_skip_tiebreaker(self) -> None:
        llm = AsyncMock()
        llm.complete.side_effect = [
            _judge_response("PASS", "first"),
            _judge_response("PASS", "second"),
        ]
        judge = LLMJudge(
            llm=llm, langfuse=MagicMock(), model="claude-sonnet-4-6"
        )
        outcome = await judge.grade_with_retry(
            JudgeCategory.FACTUALLY_CONSISTENT,
            response_text="r",
            sources="s",
            case=_case(),
            trace=_trace(),
        )
        assert outcome.verdict is JudgeVerdict.PASS
        assert outcome.attempts == 2
        assert outcome.tiebreaker_used is False
        assert llm.complete.await_count == 2

    async def test_two_fails_in_a_row_skip_tiebreaker(self) -> None:
        llm = AsyncMock()
        llm.complete.side_effect = [
            _judge_response("FAIL"),
            _judge_response("FAIL"),
        ]
        judge = LLMJudge(
            llm=llm, langfuse=MagicMock(), model="claude-sonnet-4-6"
        )
        outcome = await judge.grade_with_retry(
            JudgeCategory.SAFE_REFUSAL,
            response_text="r",
            sources="",
            case=_case(),
            trace=_trace(),
        )
        assert outcome.verdict is JudgeVerdict.FAIL
        assert outcome.tiebreaker_used is False


class TestRetryDisagreement:
    async def test_disagreement_runs_tiebreaker(self) -> None:
        # PASS / FAIL → run tiebreaker. Tiebreaker = PASS → final PASS.
        llm = AsyncMock()
        llm.complete.side_effect = [
            _judge_response("PASS"),
            _judge_response("FAIL"),
            _judge_response("PASS"),
        ]
        judge = LLMJudge(
            llm=llm, langfuse=MagicMock(), model="claude-sonnet-4-6"
        )
        outcome = await judge.grade_with_retry(
            JudgeCategory.FACTUALLY_CONSISTENT,
            response_text="r",
            sources="s",
            case=_case(),
            trace=_trace(),
        )
        assert outcome.verdict is JudgeVerdict.PASS
        assert outcome.attempts == 3
        assert outcome.tiebreaker_used is True
        assert llm.complete.await_count == 3

    async def test_tiebreaker_breaks_to_fail_when_majority_fail(self) -> None:
        llm = AsyncMock()
        llm.complete.side_effect = [
            _judge_response("FAIL"),
            _judge_response("PASS"),
            _judge_response("FAIL"),
        ]
        judge = LLMJudge(
            llm=llm, langfuse=MagicMock(), model="claude-sonnet-4-6"
        )
        outcome = await judge.grade_with_retry(
            JudgeCategory.FACTUALLY_CONSISTENT,
            response_text="r",
            sources="s",
            case=_case(),
            trace=_trace(),
        )
        assert outcome.verdict is JudgeVerdict.FAIL
        assert outcome.tiebreaker_used is True


class TestRetryObservability:
    async def test_each_attempt_records_through_langfuse(self) -> None:
        # Three calls → three record_llm_call invocations so cost
        # aggregates correctly even on the disagreement path.
        llm = AsyncMock()
        llm.complete.side_effect = [
            _judge_response("PASS"),
            _judge_response("FAIL"),
            _judge_response("PASS"),
        ]
        langfuse = MagicMock()
        judge = LLMJudge(llm=llm, langfuse=langfuse, model="claude-sonnet-4-6")
        await judge.grade_with_retry(
            JudgeCategory.FACTUALLY_CONSISTENT,
            response_text="r",
            sources="s",
            case=_case(),
            trace=_trace(),
        )
        assert langfuse.record_llm_call.call_count == 3
