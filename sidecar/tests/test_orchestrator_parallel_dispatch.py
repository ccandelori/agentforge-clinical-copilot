"""Parallel tool dispatch (week1-gaps Task #5).

The current orchestrator dispatches the LLM's tool_calls one at a
time inside ``Orchestrator.turn``. With the catalogue's per-fetch
latency at 100-300ms each, a UC-1 chart overview that calls 8 tools
spends 1.5-2.4s in serial waits — a third of the total turn budget.

This file builds the parallelization in subtask order:

* 5.1 — ``_dispatch_batch`` runs a list of ``ToolCall`` concurrently
  via :func:`asyncio.gather` and returns results in input order.
* 5.2 — ``turn()`` consumes ``plan.parallel_batches`` to group the
  LLM's tool_calls and dispatch each batch through the new method.
* 5.3 — concurrent ``_dispatch`` is verified safe against the per-turn
  shared mutable state (``timed_out_tools``, the cache).
* 5.4 — Langfuse picks up batch-size and concurrent-dispatch counts.

Each subtask lands as its own TDD red→green pair so the integration
slices through ``Orchestrator.turn`` are reviewable independently and
the eval suite can re-confirm the 6-pass baseline between commits.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from agentforge.gateway.auth_gateway import RequestContext
from agentforge.llm.types import ToolCall
from agentforge.orchestrator import Orchestrator


def _ctx() -> RequestContext:
    return RequestContext(
        user_id=42,
        patient_id=8,
        username="test-user",
        role="clinician",
        breakglass_flag=False,
        breakglass_reason=None,
        sensitivity_clearances=frozenset(),
        raw_token="raw.jwt.token",
    )


def _build_orchestrator() -> Orchestrator:
    """Build an Orchestrator with mock fetchers; behavior here is
    irrelevant for the dispatch-batch tests because they stub the
    per-call dispatch directly. The full constructor signature has
    to satisfy mypy / runtime type checks though.
    """
    return Orchestrator(
        llm=AsyncMock(),
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
    )


def _tool_call(name: str, call_id: str = "t1") -> ToolCall:
    return ToolCall(id=call_id, name=name, input={})


class TestDispatchBatch:
    """Subtask 5.1 — ``_dispatch_batch`` runs calls in parallel."""

    async def test_dispatch_batch_runs_calls_concurrently(
        self, monkeypatch: Any
    ) -> None:
        """The headline contract: when given N tool calls,
        ``_dispatch_batch`` must run all N in parallel via
        ``asyncio.gather`` rather than serially. Verified by stubbing
        ``_dispatch`` with a 100ms sleep — a 3-call serial run would
        take ~300ms; the parallel run should finish in just over 100ms.
        """
        orch = _build_orchestrator()

        async def slow_dispatch(
            ctx: RequestContext,
            call: ToolCall,
            trace: Any,
            timed_out_tools: list[str],
        ) -> tuple[str, Any]:
            del ctx, trace, timed_out_tools
            await asyncio.sleep(0.1)
            return f'{{"name":"{call.name}"}}', None

        monkeypatch.setattr(orch, "_dispatch", slow_dispatch)

        calls = [
            _tool_call("get_demographics", "t1"),
            _tool_call("get_active_problems", "t2"),
            _tool_call("get_active_medications", "t3"),
        ]

        timed_out: list[str] = []
        start = time.perf_counter()
        results = await orch._dispatch_batch(_ctx(), calls, None, timed_out)
        elapsed = time.perf_counter() - start

        # Three 100ms calls in parallel should be well under 300ms.
        # Generous bound because GHA / cold-start variance is real;
        # the goal is "much less than serial," not a tight ceiling.
        assert elapsed < 0.25, (
            f"3 parallel 100ms dispatches took {elapsed:.3f}s — looks "
            "serial. Confirm asyncio.gather is in the implementation."
        )
        assert len(results) == 3

    async def test_dispatch_batch_preserves_input_order(
        self, monkeypatch: Any
    ) -> None:
        """``asyncio.gather`` preserves the input order of awaitables in
        its return value regardless of completion order. The orchestrator
        relies on this so each result lines up with the LLM's tool_call
        IDs when synthesizing the assistant message.
        """
        orch = _build_orchestrator()

        async def order_marker(
            ctx: RequestContext,
            call: ToolCall,
            trace: Any,
            timed_out_tools: list[str],
        ) -> tuple[str, Any]:
            del ctx, trace, timed_out_tools
            # Make the LATER ones in the input finish FIRST. Without
            # gather's ordering guarantee, the result list would come
            # back in completion order, not input order.
            if call.name == "get_demographics":
                await asyncio.sleep(0.05)
            return call.name, None

        monkeypatch.setattr(orch, "_dispatch", order_marker)

        calls = [
            _tool_call("get_demographics", "t1"),
            _tool_call("get_active_problems", "t2"),
            _tool_call("get_active_medications", "t3"),
        ]

        results = await orch._dispatch_batch(_ctx(), calls, None, [])

        names = [r[0] for r in results]
        assert names == [
            "get_demographics",
            "get_active_problems",
            "get_active_medications",
        ], f"_dispatch_batch returned out-of-order results: {names}"

    async def test_dispatch_batch_empty_list_is_noop(self) -> None:
        """An empty batch must not call ``asyncio.gather`` with no
        awaitables (which would also work, but be a hidden bug if
        callers expect a side-effect like a trace span). Empty input
        yields empty output.
        """
        orch = _build_orchestrator()
        result = await orch._dispatch_batch(_ctx(), [], None, [])
        assert result == []


# Marker — keeps the import set quiet against ruff's
# unused-import warning when test classes get reshuffled.
_ = (MagicMock,)
