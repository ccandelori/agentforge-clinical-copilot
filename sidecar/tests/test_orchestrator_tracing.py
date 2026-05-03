"""Langfuse tracing wiring inside the orchestrator turn loop.

Every turn opens a trace; every LLM completion and tool dispatch
attaches a span; the verifier's decision counts ride on a final span.
The orchestrator only ever passes hashed identifiers and content
digests — never raw payloads — so PHI cannot reach the trace store
by construction. See ARCHITECTURE.md S7.3.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from agentforge.gateway.auth_gateway import RequestContext
from agentforge.llm.types import LLMResponse, ToolCall
from agentforge.orchestrator import Orchestrator
from agentforge.tools.demographics import DemographicsPayload, DemographicsResult
from agentforge.tools.dtos import ToolResultMetadata
from agentforge.tools.problems import ProblemItem, ProblemsPayload, ProblemsResult


def _ctx(*, patient_id: int = 7, user_id: int = 42) -> RequestContext:
    return RequestContext(
        user_id=user_id,
        patient_id=patient_id,
        username="test-user",
        role="clinician",
        breakglass_flag=False,
        breakglass_reason=None,
        sensitivity_clearances=frozenset(),
        raw_token="raw.jwt.token",
    )


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


def _demographics() -> DemographicsResult:
    return DemographicsResult(
        metadata=_meta("get_demographics"),
        payload=DemographicsPayload(
            patient_id=7,
            given_name="Jane",
            family_name="Doe",
            date_of_birth=date(1980, 5, 1),
        ),
    )


def _llm_with(*responses: LLMResponse) -> AsyncMock:
    mock = AsyncMock()
    mock.complete.side_effect = list(responses)
    return mock


def _fetcher(result: Any) -> AsyncMock:
    mock = AsyncMock()
    mock.fetch.return_value = result
    return mock


def _make_langfuse_mock() -> MagicMock:
    """Returns a MagicMock satisfying the LangfuseClient surface."""
    mock = MagicMock()
    mock.trace_turn.return_value = MagicMock(trace_id="trace-test-1")
    mock.aclose = AsyncMock()
    return mock


def _build(
    *,
    llm: AsyncMock,
    langfuse: MagicMock | None = None,
    hmac_key: bytes | None = b"test-key",
    problems: AsyncMock | None = None,
    demographics: AsyncMock | None = None,
    verifier_enabled: bool = False,
) -> Orchestrator:
    return Orchestrator(
        llm=llm,
        demographics_fetcher=demographics or AsyncMock(),
        medications_fetcher=AsyncMock(),
        problems_fetcher=problems or AsyncMock(),
        allergies_fetcher=AsyncMock(),
        labs_fetcher=AsyncMock(),
        vitals_fetcher=AsyncMock(),
        notes_fetcher=AsyncMock(),
        search_notes_fetcher=AsyncMock(),
        encounters_fetcher=AsyncMock(),
        immunizations_fetcher=AsyncMock(),
        procedures_fetcher=AsyncMock(),
        verifier_enabled=verifier_enabled,
        langfuse=langfuse,
        hmac_key=hmac_key,
    )


class TestTraceLifecycle:
    async def test_opens_trace_on_turn_with_user_and_patient_id(self) -> None:
        llm = _llm_with(
            LLMResponse(
                text="ok",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=5,
                output_tokens=2,
            )
        )
        langfuse = _make_langfuse_mock()
        orch = _build(llm=llm, langfuse=langfuse)
        await orch.turn(_ctx(user_id=42, patient_id=7), "hi")

        langfuse.trace_turn.assert_called_once()
        kwargs = langfuse.trace_turn.call_args.kwargs
        assert kwargs["user_id"] == 42
        assert kwargs["patient_id"] == 7
        assert kwargs["breakglass_flag"] is False
        assert kwargs["role"] == "clinician"

    async def test_no_trace_when_langfuse_not_configured(self) -> None:
        # The default-off path must not require a Langfuse instance —
        # NullLangfuseClient is the implicit default. Verified here by
        # checking the turn just runs to completion.
        llm = _llm_with(
            LLMResponse(
                text="ok",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=5,
                output_tokens=2,
            )
        )
        orch = _build(llm=llm, langfuse=None)
        reply = await orch.turn(_ctx(), "hi")
        assert reply == "ok"


class TestLLMSpans:
    async def test_records_llm_call_per_completion(self) -> None:
        llm = _llm_with(
            LLMResponse(
                text="hello",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=12,
                output_tokens=4,
            )
        )
        langfuse = _make_langfuse_mock()
        orch = _build(llm=llm, langfuse=langfuse)
        await orch.turn(_ctx(), "hi")

        langfuse.record_llm_call.assert_called_once()
        kwargs = langfuse.record_llm_call.call_args.kwargs
        assert kwargs["prompt_tokens"] == 12
        assert kwargs["completion_tokens"] == 4
        assert kwargs["model"] != ""
        assert kwargs["latency_ms"] >= 0

    async def test_records_two_llm_calls_for_tool_use_loop(self) -> None:
        llm = _llm_with(
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(id="t1", name="get_demographics", input={}),
                ],
                stop_reason="tool_use",
                input_tokens=10,
                output_tokens=3,
            ),
            LLMResponse(
                text="Patient is Jane Doe.",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=20,
                output_tokens=8,
            ),
        )
        langfuse = _make_langfuse_mock()
        orch = _build(
            llm=llm,
            langfuse=langfuse,
            demographics=_fetcher(_demographics()),
        )
        await orch.turn(_ctx(), "name?")
        assert langfuse.record_llm_call.call_count == 2


class TestToolSpans:
    async def test_records_tool_call_with_hashed_args_and_result(self) -> None:
        llm = _llm_with(
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(id="t1", name="get_active_problems", input={}),
                ],
                stop_reason="tool_use",
                input_tokens=10,
                output_tokens=3,
            ),
            LLMResponse(
                text="ok.",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=20,
                output_tokens=4,
            ),
        )
        langfuse = _make_langfuse_mock()
        orch = _build(
            llm=llm, langfuse=langfuse, problems=_fetcher(_problems(1))
        )
        await orch.turn(_ctx(), "problems?")

        langfuse.record_tool_call.assert_called_once()
        kwargs = langfuse.record_tool_call.call_args.kwargs
        assert kwargs["tool_name"] == "get_active_problems"
        assert kwargs["status"] == "ok"
        assert kwargs["cache_hit"] is False
        # Hashed payloads, never raw — non-empty hex strings.
        assert isinstance(kwargs["args_hash"], str) and kwargs["args_hash"]
        assert isinstance(kwargs["result_hash"], str) and kwargs["result_hash"]
        assert kwargs["latency_ms"] >= 0

    async def test_tool_failure_records_error_status(self) -> None:
        llm = _llm_with(
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(id="t1", name="get_active_problems", input={}),
                ],
                stop_reason="tool_use",
                input_tokens=10,
                output_tokens=3,
            ),
            LLMResponse(
                text="failed.",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=20,
                output_tokens=4,
            ),
        )
        broken = AsyncMock()
        broken.fetch.side_effect = RuntimeError("backend down")
        langfuse = _make_langfuse_mock()
        orch = _build(llm=llm, langfuse=langfuse, problems=broken)
        await orch.turn(_ctx(), "?")

        kwargs = langfuse.record_tool_call.call_args.kwargs
        assert kwargs["status"] == "error"
        # Errors don't ride a result hash — there's no payload to digest.
        assert kwargs["result_hash"] is None


class TestVerifierDecisionSpan:
    async def test_records_verifier_decision_with_pass_and_fail_counts(
        self,
    ) -> None:
        llm = _llm_with(
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(id="t1", name="get_active_problems", input={}),
                ],
                stop_reason="tool_use",
                input_tokens=10,
                output_tokens=3,
            ),
            LLMResponse(
                text=(
                    "Real claim [problem #1]. "
                    "Made-up claim with no citation. "
                ),
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=30,
                output_tokens=15,
            ),
        )
        langfuse = _make_langfuse_mock()
        orch = _build(
            llm=llm,
            langfuse=langfuse,
            problems=_fetcher(_problems(1)),
            verifier_enabled=True,
        )
        await orch.turn(_ctx(), "summary?")

        langfuse.record_verifier_decision.assert_called_once()
        kwargs = langfuse.record_verifier_decision.call_args.kwargs
        assert kwargs["claims_emitted"] == 2
        assert kwargs["claims_rejected"] == 1
        assert "no_citation" in kwargs["by_category"]
        assert kwargs["by_category"]["no_citation"] == 1

    async def test_no_verifier_span_when_verifier_disabled(self) -> None:
        llm = _llm_with(
            LLMResponse(
                text="hello",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=5,
                output_tokens=2,
            )
        )
        langfuse = _make_langfuse_mock()
        orch = _build(llm=llm, langfuse=langfuse, verifier_enabled=False)
        await orch.turn(_ctx(), "hi")
        langfuse.record_verifier_decision.assert_not_called()


class TestCostAccounting:
    """Per-turn LLM cost lands in the ContextVar AND on the Langfuse
    generation span (Week 1 Task #14). Calculated from token counts +
    the static pricing table; aggregated across all LLM calls in the
    turn so the /turn endpoint can surface a single X-Agent-Cost-USD
    header per response."""

    async def test_single_call_cost_lands_in_contextvar(self) -> None:
        from agentforge.observability.cost import calculate_cost
        from agentforge.orchestrator import _TRACE_MODEL, get_turn_cost_usd

        llm = _llm_with(
            LLMResponse(
                text="ok",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=1000,
                output_tokens=500,
            )
        )
        orch = _build(llm=llm, langfuse=None)
        await orch.turn(_ctx(), "hi")

        expected = calculate_cost(_TRACE_MODEL, 1000, 500)
        assert get_turn_cost_usd() == expected

    async def test_tool_use_loop_accumulates_cost(self) -> None:
        from agentforge.observability.cost import calculate_cost
        from agentforge.orchestrator import _TRACE_MODEL, get_turn_cost_usd

        llm = _llm_with(
            LLMResponse(
                text="",
                tool_calls=[ToolCall(id="t1", name="get_demographics", input={})],
                stop_reason="tool_use",
                input_tokens=100,
                output_tokens=50,
            ),
            LLMResponse(
                text="Patient is Jane Doe.",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=200,
                output_tokens=80,
            ),
        )
        orch = _build(
            llm=llm,
            langfuse=None,
            demographics=_fetcher(_demographics()),
        )
        await orch.turn(_ctx(), "name?")

        expected = (
            calculate_cost(_TRACE_MODEL, 100, 50)
            + calculate_cost(_TRACE_MODEL, 200, 80)
        )
        assert get_turn_cost_usd() == expected

    async def test_cost_resets_between_turns(self) -> None:
        """A second turn on the same orchestrator must not inherit
        accumulated cost from the prior turn — turn() resets the
        ContextVar before running anything."""
        from agentforge.observability.cost import calculate_cost
        from agentforge.orchestrator import _TRACE_MODEL, get_turn_cost_usd

        llm1 = _llm_with(
            LLMResponse(
                text="first",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=100,
                output_tokens=20,
            )
        )
        orch1 = _build(llm=llm1, langfuse=None)
        await orch1.turn(_ctx(), "hi")

        llm2 = _llm_with(
            LLMResponse(
                text="second",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=300,
                output_tokens=60,
            )
        )
        orch2 = _build(llm=llm2, langfuse=None)
        await orch2.turn(_ctx(), "again")

        # Second turn's accumulator only reflects the second call,
        # not the first — proves the reset at turn() entry works.
        assert get_turn_cost_usd() == calculate_cost(_TRACE_MODEL, 300, 60)

    async def test_record_llm_call_passes_cost_to_langfuse(self) -> None:
        """Cost is forwarded to the Langfuse generation span so the
        trace store gets per-call dollar metadata, not just tokens."""
        llm = _llm_with(
            LLMResponse(
                text="ok",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=1000,
                output_tokens=500,
            )
        )
        langfuse = _make_langfuse_mock()
        orch = _build(llm=llm, langfuse=langfuse)
        await orch.turn(_ctx(), "hi")

        langfuse.record_llm_call.assert_called_once()
        kwargs = langfuse.record_llm_call.call_args.kwargs
        assert "cost_usd" in kwargs
        assert kwargs["cost_usd"] > 0.0
