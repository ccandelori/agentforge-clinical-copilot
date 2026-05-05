"""LangGraph supervisor skeleton (Task 1, MR 1).

This MR ships the StateGraph wiring + supervisor routing as dead code
in production — only exercised by tests. Workers are stubs; real
worker bodies arrive in MR 2 (Task 1.3-1.5). The old
``Orchestrator.turn()`` loop is untouched.

Tests use a stub ``Planner``-shaped object so no LLM is involved.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentforge.orchestrator.graph import (
    MAX_ITERATIONS,
    AgentState,
    RouteDecision,
    build_graph,
    supervisor_node,
)
from agentforge.orchestrator.planner import Plan, UseCase


class StubPlanner:
    """Test stand-in for ``Planner``.

    Returns a pre-canned ``Plan`` from ``plan()``. The real ``Planner``
    is duck-typed by the supervisor (only ``plan(user_message)`` is
    used), so this stub is sufficient for graph-level tests.
    """

    def __init__(self, plan: Plan) -> None:
        self._plan = plan
        self.calls: list[str] = []

    async def plan(self, user_message: str) -> Plan:
        self.calls.append(user_message)
        return self._plan


def _empty_followup_plan() -> Plan:
    return Plan(
        use_case=UseCase.FOLLOWUP,
        tool_calls=(),
        parallel_batches=(),
    )


def _starter_state(user_message: str = "hello") -> AgentState:
    return AgentState(
        messages=[{"role": "user", "content": user_message}],
        tool_results=[],
        route_decision=None,
        route_reason="",
        iteration=0,
        extraction_result=None,
        evidence_chunks=[],
    )


# ---------------------------------------------------------------------------
# Cycle 1 — tracer bullet: graph compiles + invokes
# ---------------------------------------------------------------------------


class TestGraphTracer:
    @pytest.mark.asyncio
    async def test_build_graph_returns_invocable_graph(self) -> None:
        planner = StubPlanner(_empty_followup_plan())

        graph = build_graph(planner)

        # Compiled langgraph exposes ``ainvoke`` for async invocation.
        # We don't assert on the *result* yet — that's later cycles.
        # Tracer just proves the graph compiles and runs to completion
        # without raising.
        result: dict[str, Any] = await graph.ainvoke(_starter_state())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Cycle 2 — supervisor consults Planner and records the routing decision
# ---------------------------------------------------------------------------


def _admit_synthesis_plan() -> Plan:
    return Plan(
        use_case=UseCase.ADMIT_SYNTHESIS,
        tool_calls=(),
        parallel_batches=(),
    )


class TestSupervisorPlannerWiring:
    @pytest.mark.asyncio
    async def test_supervisor_invokes_planner_with_user_message(self) -> None:
        planner = StubPlanner(_admit_synthesis_plan())

        graph = build_graph(planner)
        await graph.ainvoke(_starter_state(user_message="why was the patient admitted?"))

        # Supervisor must consult the planner with the user's message.
        # The supervisor runs once per iteration (so several times when
        # workers loop back), but every call must use the same user
        # message — supervisor is stateless w.r.t. the message stream.
        assert planner.calls
        assert all(msg == "why was the patient admitted?" for msg in planner.calls)

    @pytest.mark.asyncio
    async def test_supervisor_writes_route_decision_and_reason(self) -> None:
        planner = StubPlanner(_admit_synthesis_plan())

        graph = build_graph(planner)
        result = await graph.ainvoke(_starter_state())

        # ADMIT_SYNTHESIS is not FOLLOWUP and iteration starts at 0,
        # so MR 1's placeholder routing yields INTAKE_EXTRACTOR.
        # On the second supervisor pass (after the worker loop-back
        # bumps iteration to 1, then 2), the supervisor still picks
        # INTAKE_EXTRACTOR until the cap. The final supervisor pass
        # at iteration >= 3 flips to SYNTHESIZE — that's what we
        # actually observe at graph termination.
        assert result["route_decision"] == RouteDecision.SYNTHESIZE
        assert result["route_reason"]  # non-empty reason recorded


# ---------------------------------------------------------------------------
# Cycle 3 — FOLLOWUP plans short-circuit to SYNTHESIZE
# ---------------------------------------------------------------------------


class TestSupervisorFollowupRouting:
    @pytest.mark.asyncio
    async def test_followup_plan_routes_to_synthesize(self) -> None:
        # Direct supervisor_node invocation isolates the routing rule
        # from graph traversal. With a FOLLOWUP plan and iteration 0,
        # the supervisor must short-circuit to SYNTHESIZE — pure
        # follow-ups don't need tool calls.
        planner = StubPlanner(_empty_followup_plan())
        state = _starter_state()

        update = await supervisor_node(state, planner)

        assert update["route_decision"] == RouteDecision.SYNTHESIZE
        assert "followup" in update["route_reason"].lower()


# ---------------------------------------------------------------------------
# Cycle 4 — iteration cap forces SYNTHESIZE regardless of plan
# ---------------------------------------------------------------------------


class TestSupervisorIterationCap:
    @pytest.mark.asyncio
    async def test_at_cap_routes_to_synthesize_even_for_admit(self) -> None:
        # ADMIT_SYNTHESIS would normally route to INTAKE_EXTRACTOR
        # under MR 1's placeholder rule. The cap must override.
        planner = StubPlanner(_admit_synthesis_plan())
        state = _starter_state()
        state["iteration"] = MAX_ITERATIONS

        update = await supervisor_node(state, planner)

        assert update["route_decision"] == RouteDecision.SYNTHESIZE
        assert "cap" in update["route_reason"].lower()

    @pytest.mark.asyncio
    async def test_just_below_cap_still_routes_to_worker(self) -> None:
        # Sanity counterpart — at iteration MAX-1 the cap has not yet
        # tripped, so the placeholder routing rule still applies.
        planner = StubPlanner(_admit_synthesis_plan())
        state = _starter_state()
        state["iteration"] = MAX_ITERATIONS - 1

        update = await supervisor_node(state, planner)

        assert update["route_decision"] == RouteDecision.INTAKE_EXTRACTOR


# ---------------------------------------------------------------------------
# Cycle 5 — conditional edges dispatch to the node named by route_decision
# ---------------------------------------------------------------------------


class TestConditionalRouting:
    @pytest.mark.asyncio
    async def test_followup_path_visits_synthesize_not_intake(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Spy on the stubbed worker bodies. The graph must skip
        # intake_extractor entirely when supervisor routes to
        # synthesize.
        from agentforge.orchestrator import graph as graph_module

        intake_calls = 0
        synthesize_calls = 0

        async def spy_intake(state: AgentState) -> dict[str, Any]:
            nonlocal intake_calls
            intake_calls += 1
            return {}

        async def spy_synthesize(state: AgentState) -> dict[str, Any]:
            nonlocal synthesize_calls
            synthesize_calls += 1
            return {}

        monkeypatch.setattr(graph_module, "intake_extractor_node", spy_intake)
        monkeypatch.setattr(graph_module, "synthesize_node", spy_synthesize)

        planner = StubPlanner(_empty_followup_plan())
        graph = build_graph(planner)
        await graph.ainvoke(_starter_state())

        assert intake_calls == 0
        assert synthesize_calls == 1

    @pytest.mark.asyncio
    async def test_admit_path_visits_intake_at_least_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Sanity counterpart: ADMIT_SYNTHESIS routes to intake-extractor
        # before the iteration cap eventually trips and forces synthesize.
        from agentforge.orchestrator import graph as graph_module

        intake_calls = 0

        async def spy_intake(state: AgentState) -> dict[str, Any]:
            nonlocal intake_calls
            intake_calls += 1
            return {}

        monkeypatch.setattr(graph_module, "intake_extractor_node", spy_intake)

        planner = StubPlanner(_admit_synthesis_plan())
        graph = build_graph(planner)
        await graph.ainvoke(_starter_state())

        assert intake_calls >= 1


# ---------------------------------------------------------------------------
# Cycle 6 — workers loop back to supervisor; cap engages at MAX_ITERATIONS
# ---------------------------------------------------------------------------


class TestIterationCapEndToEnd:
    @pytest.mark.asyncio
    async def test_admit_loops_until_cap_then_synthesizes(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # ADMIT_SYNTHESIS keeps routing to INTAKE_EXTRACTOR until the
        # iteration cap trips and forces SYNTHESIZE. Therefore the
        # intake stub runs exactly MAX_ITERATIONS times before the
        # graph terminates — this proves the worker→supervisor
        # loop-back edge is wired and the cap engages cleanly.
        from agentforge.orchestrator import graph as graph_module

        intake_calls = 0
        synthesize_calls = 0

        async def spy_intake(state: AgentState) -> dict[str, Any]:
            nonlocal intake_calls
            intake_calls += 1
            return {}

        async def spy_synthesize(state: AgentState) -> dict[str, Any]:
            nonlocal synthesize_calls
            synthesize_calls += 1
            return {}

        monkeypatch.setattr(graph_module, "intake_extractor_node", spy_intake)
        monkeypatch.setattr(graph_module, "synthesize_node", spy_synthesize)

        planner = StubPlanner(_admit_synthesis_plan())
        graph = build_graph(planner)
        result = await graph.ainvoke(_starter_state())

        assert intake_calls == MAX_ITERATIONS
        assert synthesize_calls == 1
        assert result["route_decision"] == RouteDecision.SYNTHESIZE
        assert "cap" in result["route_reason"].lower()
