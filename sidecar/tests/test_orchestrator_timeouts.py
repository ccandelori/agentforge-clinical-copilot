"""Latency-budget enforcement in Orchestrator.turn (week1-gaps Task #8).

Three budget levels are enforced:

  * ``total_turn`` (7s default) — wraps the entire ``turn()`` body via
    ``async with asyncio.timeout`` so a runaway turn surfaces as a
    generic graceful-degradation reply, never an unhandled cancellation.
  * ``synthesis_phase`` (5s default) — wraps each ``llm.complete`` call.
    On timeout, the error propagates to the outer ``total_turn`` handler.
  * ``tool_phase`` (4s default) — wraps each ``_dispatch_batch`` call.
    On timeout, all tools in the batch are marked timed-out and error
    payloads are synthesized so the loop can continue gracefully.

The tests use injected sleeps + tight policies so wall-clock time stays
short. ``asyncio.timeout`` runs in the same task as the caller, so
ContextVar mutations made before/after the timeout boundary are
preserved across it — that property is exercised here too because the
/turn endpoint reads ``get_turn_cost_usd()`` after this method returns.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from agentforge.gateway.auth_gateway import RequestContext
from agentforge.llm.types import LLMResponse, ToolCall
from agentforge.orchestrator import (
    _TURN_BUDGET_EXCEEDED_TEXT,
    Orchestrator,
    get_turn_cost_usd,
)
from agentforge.timeouts import TimeoutPolicy


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


def _build(
    *,
    llm: AsyncMock,
    timeout_policy: TimeoutPolicy,
    labs_fetcher: AsyncMock | None = None,
) -> Orchestrator:
    return Orchestrator(
        llm=llm,
        demographics_fetcher=AsyncMock(),
        medications_fetcher=AsyncMock(),
        problems_fetcher=AsyncMock(),
        allergies_fetcher=AsyncMock(),
        labs_fetcher=labs_fetcher or AsyncMock(),
        vitals_fetcher=AsyncMock(),
        notes_fetcher=AsyncMock(),
        search_notes_fetcher=AsyncMock(),
        encounters_fetcher=AsyncMock(),
        immunizations_fetcher=AsyncMock(),
        procedures_fetcher=AsyncMock(),
        timeout_policy=timeout_policy,
    )


class TestTotalTurnTimeout:
    async def test_returns_graceful_reply_when_total_turn_exceeded(
        self,
    ) -> None:
        # An LLM call that hangs forever is the canonical "runaway
        # turn" — the synthesis_phase + total_turn caps both fire,
        # but total_turn wins by being the outer envelope.
        async def hanging_complete(**kwargs: object) -> LLMResponse:
            await asyncio.sleep(10.0)  # >> any policy budget
            raise AssertionError("should be cancelled before this")

        llm = AsyncMock()
        llm.complete.side_effect = hanging_complete

        orch = _build(
            llm=llm,
            timeout_policy=TimeoutPolicy(
                per_tool=0.05,
                tool_phase=0.05,
                synthesis_phase=0.1,
                total_turn=0.1,
            ),
        )

        reply = await orch.turn(_ctx(), "summarize")

        assert reply == _TURN_BUDGET_EXCEEDED_TEXT

    async def test_does_not_raise_into_caller(self) -> None:
        # The /turn endpoint reads cost after turn() returns. A
        # CancelledError leaking out would crash that read; keep the
        # contract "always returns a string."
        async def hang(**kwargs: object) -> LLMResponse:
            await asyncio.sleep(10.0)
            raise AssertionError("unreachable")

        llm = AsyncMock()
        llm.complete.side_effect = hang

        orch = _build(
            llm=llm,
            timeout_policy=TimeoutPolicy(
                per_tool=0.05,
                tool_phase=0.05,
                synthesis_phase=0.1,
                total_turn=0.1,
            ),
        )

        # If this raises, the test fails. Returning a string is the
        # contract.
        reply = await orch.turn(_ctx(), "summarize")

        assert isinstance(reply, str)


class TestSynthesisPhaseTimeout:
    async def test_synthesis_timeout_bubbles_to_total_turn_handler(
        self,
    ) -> None:
        # A single LLM call slower than synthesis_phase but faster
        # than total_turn still times out — the synthesis cap is the
        # tightest cap that fires first.
        async def slow_complete(**kwargs: object) -> LLMResponse:
            await asyncio.sleep(0.5)
            return _final("done")

        llm = AsyncMock()
        llm.complete.side_effect = slow_complete

        orch = _build(
            llm=llm,
            timeout_policy=TimeoutPolicy(
                per_tool=0.05,
                tool_phase=0.05,
                synthesis_phase=0.05,  # 50ms — fires first
                total_turn=2.0,  # plenty of headroom
            ),
        )

        reply = await orch.turn(_ctx(), "summarize")

        # Synthesis timeout doesn't have its own message — it
        # propagates to the total_turn handler which returns the
        # generic budget-exceeded text.
        assert reply == _TURN_BUDGET_EXCEEDED_TEXT


class TestToolPhaseTimeoutCollectsPartialResults:
    async def test_all_batch_tools_marked_timed_out_on_phase_timeout(
        self,
    ) -> None:
        # Model issues two tool calls; both fetchers hang. Tool-phase
        # cap fires; both tools should land in timed_out_tools and
        # the model should see error payloads on the next iteration.
        async def hanging_fetch(**kwargs: object) -> object:
            await asyncio.sleep(10.0)
            raise AssertionError("unreachable")

        labs_fetcher = AsyncMock()
        labs_fetcher.fetch.side_effect = hanging_fetch

        first = LLMResponse(
            text="",
            tool_calls=[
                ToolCall(id="tc-1", name="get_recent_labs", input={}),
            ],
            stop_reason="tool_use",
            input_tokens=10,
            output_tokens=5,
        )
        # Second LLM call, after tool-phase timeout, returns final
        # text — confirms the loop continues past the timeout rather
        # than dead-ending.
        final = _final("Sorry, the lab service is slow.")

        llm = AsyncMock()
        llm.complete.side_effect = [first, final]

        orch = _build(
            llm=llm,
            labs_fetcher=labs_fetcher,
            timeout_policy=TimeoutPolicy(
                per_tool=0.05,
                tool_phase=0.05,  # fires before per_tool retries finish
                synthesis_phase=2.0,
                total_turn=5.0,
            ),
        )

        reply = await orch.turn(_ctx(), "What's my A1c?")

        # The graceful-degradation notice mentions the tool by name
        # — proves it landed in timed_out_tools.
        assert "Sorry, the lab service is slow." in reply
        assert "get_recent_labs" in reply
        assert "did not respond in time" in reply


class TestCostContextVarSurvivesTimeout:
    async def test_cost_var_zeroed_after_timeout(self) -> None:
        # Even on a runaway turn, the cost ContextVar starts at 0.0
        # for the next caller because turn() resets it at entry. The
        # /turn endpoint relies on this — it reads cost after the
        # call returns and surfaces the value as X-Agent-Cost-USD.
        async def hang(**kwargs: object) -> LLMResponse:
            await asyncio.sleep(10.0)
            raise AssertionError("unreachable")

        llm = AsyncMock()
        llm.complete.side_effect = hang

        orch = _build(
            llm=llm,
            timeout_policy=TimeoutPolicy(
                per_tool=0.05,
                tool_phase=0.05,
                synthesis_phase=0.05,
                total_turn=0.05,
            ),
        )

        await orch.turn(_ctx(), "summarize")

        # No LLM call landed _record_llm_call (timed out before
        # the SDK returned), so cost stays at the post-reset 0.0
        # value the /turn endpoint will read.
        assert get_turn_cost_usd() == 0.0


class TestTimeoutPolicyDefaults:
    def test_synthesis_phase_default_within_total_turn(self) -> None:
        # Sanity-check: default policy values shouldn't violate the
        # documented containment (synthesis_phase ≤ total_turn).
        policy = TimeoutPolicy()
        assert policy.synthesis_phase <= policy.total_turn
        assert policy.tool_phase <= policy.total_turn
        assert policy.per_tool <= policy.tool_phase
