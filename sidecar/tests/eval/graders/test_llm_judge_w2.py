"""Tests for the W2 binary LLMJudge (Task 17.2).

The W2 judge differs from the W1 ``LLMJudgeGrader`` in three ways:

  * It emits a binary PASS/FAIL verdict (W1 was a 1-5 score).
  * It dispatches to a category-specific prompt loaded from the
    versioned prompt library — :class:`JudgeCategory` decides which one.
  * It records the call through the existing
    :class:`LangfuseClient.record_llm_call` surface so judge cost
    aggregates with the rest of the eval traffic in dashboards.

Tests pin the deterministic surface (parser, prompt routing,
temperature, seed plumbing) and only mock the LLM client + Langfuse
client. We never hit Anthropic.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentforge.llm.types import LLMResponse, Message
from tests.eval.graders.llm_judge_w2 import (
    JudgeCategory,
    JudgeVerdict,
    LLMJudge,
    LLMJudgeOutcome,
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


def _case(category: EvalCategory = EvalCategory.HALLUCINATION) -> EvalCase:
    return EvalCase(
        id="w2_judge_test",
        category=category,
        patient_id=8,
        query="Summarize the lab panel.",
        expected_behavior="Reports HbA1c with a citation.",
    )


@pytest.fixture
def llm() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def langfuse() -> MagicMock:
    """A non-async MagicMock — record_llm_call is sync per protocol."""
    return MagicMock()


@pytest.fixture
def trace_handle() -> Any:
    sentinel = MagicMock()
    sentinel.trace_id = "test-trace"
    return sentinel


# ---------------------------------------------------------------------------
# Verdict parsing
# ---------------------------------------------------------------------------


class TestVerdictParsing:
    async def test_pass_verdict_parses_to_pass(
        self, llm: AsyncMock, langfuse: MagicMock, trace_handle: Any
    ) -> None:
        llm.complete.return_value = _judge_response("PASS", "Claim matches source.")
        judge = LLMJudge(llm=llm, langfuse=langfuse, model="claude-sonnet-4-6")
        outcome = await judge.grade(
            JudgeCategory.FACTUALLY_CONSISTENT,
            response_text="Patient has HbA1c 7.2 [problem #5].",
            sources="HbA1c lab panel: 7.2",
            case=_case(),
            trace=trace_handle,
        )
        assert outcome.verdict is JudgeVerdict.PASS
        assert outcome.passed is True

    async def test_fail_verdict_parses_to_fail(
        self, llm: AsyncMock, langfuse: MagicMock, trace_handle: Any
    ) -> None:
        llm.complete.return_value = _judge_response("FAIL", "Claim contradicts source.")
        judge = LLMJudge(llm=llm, langfuse=langfuse, model="claude-sonnet-4-6")
        outcome = await judge.grade(
            JudgeCategory.FACTUALLY_CONSISTENT,
            response_text="Patient has HbA1c 4.0",
            sources="HbA1c lab panel: 7.2",
            case=_case(),
            trace=trace_handle,
        )
        assert outcome.verdict is JudgeVerdict.FAIL
        assert outcome.passed is False

    async def test_returns_outcome_type(
        self, llm: AsyncMock, langfuse: MagicMock, trace_handle: Any
    ) -> None:
        llm.complete.return_value = _judge_response("PASS")
        judge = LLMJudge(llm=llm, langfuse=langfuse, model="claude-sonnet-4-6")
        outcome = await judge.grade(
            JudgeCategory.SAFE_REFUSAL,
            response_text="I can't place orders.",
            sources="",
            case=_case(category=EvalCategory.REFUSAL),
            trace=trace_handle,
        )
        assert isinstance(outcome, LLMJudgeOutcome)
        assert "ok" in outcome.rationale or outcome.rationale == ""

    async def test_unparseable_response_treated_as_fail(
        self, llm: AsyncMock, langfuse: MagicMock, trace_handle: Any
    ) -> None:
        # The judge emitted free-form text without VERDICT — treated as
        # FAIL so a malformed judge doesn't silently bless a bad response.
        llm.complete.return_value = LLMResponse(
            text="The response seems okay.",
            tool_calls=[],
            stop_reason="end_turn",
            input_tokens=50,
            output_tokens=10,
        )
        judge = LLMJudge(llm=llm, langfuse=langfuse, model="claude-sonnet-4-6")
        outcome = await judge.grade(
            JudgeCategory.FACTUALLY_CONSISTENT,
            response_text="anything",
            sources="anything",
            case=_case(),
            trace=trace_handle,
        )
        assert outcome.verdict is JudgeVerdict.FAIL


# ---------------------------------------------------------------------------
# Prompt routing
# ---------------------------------------------------------------------------


class TestPromptRouting:
    async def test_factually_consistent_uses_consistency_prompt(
        self, llm: AsyncMock, langfuse: MagicMock, trace_handle: Any
    ) -> None:
        llm.complete.return_value = _judge_response("PASS")
        judge = LLMJudge(llm=llm, langfuse=langfuse, model="claude-sonnet-4-6")
        await judge.grade(
            JudgeCategory.FACTUALLY_CONSISTENT,
            response_text="r",
            sources="s",
            case=_case(),
            trace=trace_handle,
        )
        system_prompt: str = llm.complete.call_args.kwargs["system"]
        # The factual-consistency prompt mentions "claim" and "source".
        assert "claim" in system_prompt.lower()
        assert "source" in system_prompt.lower()

    async def test_safe_refusal_uses_refusal_prompt(
        self, llm: AsyncMock, langfuse: MagicMock, trace_handle: Any
    ) -> None:
        llm.complete.return_value = _judge_response("PASS")
        judge = LLMJudge(llm=llm, langfuse=langfuse, model="claude-sonnet-4-6")
        await judge.grade(
            JudgeCategory.SAFE_REFUSAL,
            response_text="I can't do that.",
            sources="",
            case=_case(category=EvalCategory.REFUSAL),
            trace=trace_handle,
        )
        system_prompt: str = llm.complete.call_args.kwargs["system"]
        assert "refus" in system_prompt.lower()


# ---------------------------------------------------------------------------
# Determinism plumbing
# ---------------------------------------------------------------------------


class TestDeterminism:
    async def test_calls_llm_at_temperature_zero(
        self, llm: AsyncMock, langfuse: MagicMock, trace_handle: Any
    ) -> None:
        llm.complete.return_value = _judge_response("PASS")
        judge = LLMJudge(llm=llm, langfuse=langfuse, model="claude-sonnet-4-6")
        await judge.grade(
            JudgeCategory.FACTUALLY_CONSISTENT,
            response_text="r",
            sources="s",
            case=_case(),
            trace=trace_handle,
        )
        assert llm.complete.call_args.kwargs["temperature"] == 0.0

    async def test_same_inputs_yield_same_verdict_across_runs(
        self, llm: AsyncMock, langfuse: MagicMock, trace_handle: Any
    ) -> None:
        # Mocked LLM — proves the judge is deterministic w.r.t. its inputs.
        llm.complete.return_value = _judge_response("PASS", "stable")
        judge = LLMJudge(llm=llm, langfuse=langfuse, model="claude-sonnet-4-6")
        outcome_a = await judge.grade(
            JudgeCategory.FACTUALLY_CONSISTENT,
            response_text="r",
            sources="s",
            case=_case(),
            trace=trace_handle,
        )
        outcome_b = await judge.grade(
            JudgeCategory.FACTUALLY_CONSISTENT,
            response_text="r",
            sources="s",
            case=_case(),
            trace=trace_handle,
        )
        assert outcome_a.verdict == outcome_b.verdict
        assert outcome_a.rationale == outcome_b.rationale


# ---------------------------------------------------------------------------
# Prompt body construction
# ---------------------------------------------------------------------------


class TestPromptBody:
    async def test_user_message_includes_response_and_sources(
        self, llm: AsyncMock, langfuse: MagicMock, trace_handle: Any
    ) -> None:
        llm.complete.return_value = _judge_response("PASS")
        judge = LLMJudge(llm=llm, langfuse=langfuse, model="claude-sonnet-4-6")
        await judge.grade(
            JudgeCategory.FACTUALLY_CONSISTENT,
            response_text="HbA1c is 7.2",
            sources="lab panel: HbA1c 7.2",
            case=_case(),
            trace=trace_handle,
        )
        messages: list[Message] = llm.complete.call_args.kwargs["messages"]
        assert len(messages) == 1
        body = messages[0].content
        assert "HbA1c is 7.2" in body
        assert "lab panel: HbA1c 7.2" in body


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


class TestObservability:
    async def test_records_llm_call_through_langfuse(
        self, llm: AsyncMock, langfuse: MagicMock, trace_handle: Any
    ) -> None:
        llm.complete.return_value = _judge_response("PASS")
        judge = LLMJudge(llm=llm, langfuse=langfuse, model="claude-sonnet-4-6")
        await judge.grade(
            JudgeCategory.FACTUALLY_CONSISTENT,
            response_text="r",
            sources="s",
            case=_case(),
            trace=trace_handle,
        )
        # Langfuse client recorded one llm call with the judge's token
        # counts and computed cost — proves the judge plugs into the
        # existing observability surface (no new tracking invented).
        assert langfuse.record_llm_call.call_count == 1
        kwargs = langfuse.record_llm_call.call_args.kwargs
        assert kwargs["model"] == "claude-sonnet-4-6"
        assert kwargs["prompt_tokens"] == 120
        assert kwargs["completion_tokens"] == 15
        # cost is positive for a known model
        assert kwargs["cost_usd"] is not None
        assert kwargs["cost_usd"] > 0.0

    async def test_records_under_provided_trace_handle(
        self, llm: AsyncMock, langfuse: MagicMock, trace_handle: Any
    ) -> None:
        llm.complete.return_value = _judge_response("PASS")
        judge = LLMJudge(llm=llm, langfuse=langfuse, model="claude-sonnet-4-6")
        await judge.grade(
            JudgeCategory.FACTUALLY_CONSISTENT,
            response_text="r",
            sources="s",
            case=_case(),
            trace=trace_handle,
        )
        # First positional arg to record_llm_call is the trace handle.
        assert langfuse.record_llm_call.call_args.args[0] is trace_handle
