"""LangGraph supervisor skeleton for the W2 agent (Task 1, MR 1).

This module ships the StateGraph wiring + supervisor routing as
**dead code in production** — only exercised by tests. The old
``Orchestrator.turn()`` iterative loop in ``__init__.py`` remains
the production entrypoint.

Subsequent MRs:
  * MR 2 (Task 1.3-1.5) — wires real worker bodies into
    ``intake_extractor_node`` (VisionExtractor[IntakeFormExtraction])
    and ``synthesize_node`` (existing synthesis logic).
  * MR 3 (Task 1.6-1.8) — replaces the production entrypoint with
    ``graph.ainvoke()``, removes the old loop.

Routing (MR 1 placeholder, deepens in MR 2/3):
  * iteration >= MAX_ITERATIONS → SYNTHESIZE (hard stop)
  * Plan.use_case == FOLLOWUP   → SYNTHESIZE (no tools needed)
  * otherwise                   → INTAKE_EXTRACTOR (default)

The default-INTAKE_EXTRACTOR rule is intentionally dumb. Real
PDF-vs-evidence routing is not load-bearing in MR 1 because the
workers being routed to are pass-through stubs. Logged in
DEVIATIONS.md.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agentforge.orchestrator.planner import Plan, UseCase

MAX_ITERATIONS: int = 3


class RouteDecision(StrEnum):
    """Where the supervisor sends the turn next.

    String values are the LangGraph node names — used directly as
    targets in the conditional-edge map.
    """

    INTAKE_EXTRACTOR = "intake-extractor"
    EVIDENCE_RETRIEVER = "evidence-retriever"
    BOTH = "both"
    SYNTHESIZE = "synthesize"


class AgentState(TypedDict):
    """Shared state threaded through every node in the graph.

    All workers append to / read from this single dict. The
    ``route_decision`` field carries the supervisor's routing intent
    forward to the conditional edges.
    """

    messages: list[dict[str, Any]]
    tool_results: list[Any]
    route_decision: RouteDecision | None
    route_reason: str
    iteration: int
    extraction_result: Any | None
    evidence_chunks: list[Any]


class _PlannerLike(Protocol):
    """Subset of ``Planner`` consumed by the supervisor.

    Keeps tests stub-able without inheritance. The real ``Planner``
    satisfies this structurally.
    """

    async def plan(self, user_message: str) -> Plan: ...


def _last_user_message(state: AgentState) -> str:
    for msg in reversed(state["messages"]):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            return content if isinstance(content, str) else ""
    return ""


def _decide_route(plan: Plan, iteration: int) -> tuple[RouteDecision, str]:
    if iteration >= MAX_ITERATIONS:
        return RouteDecision.SYNTHESIZE, "iteration cap reached"
    if plan.use_case == UseCase.FOLLOWUP:
        return RouteDecision.SYNTHESIZE, "followup: no tools needed"
    return RouteDecision.INTAKE_EXTRACTOR, f"default routing for {plan.use_case.value}"


async def supervisor_node(
    state: AgentState,
    planner: _PlannerLike,
) -> dict[str, Any]:
    """Run the planner and emit a route decision.

    Pure routing — never mutates ``messages``, ``tool_results``,
    ``extraction_result``, or ``evidence_chunks``. Bumps ``iteration``
    so worker → supervisor loops eventually trip the cap.
    """
    plan = await planner.plan(_last_user_message(state))
    decision, reason = _decide_route(plan, state["iteration"])
    return {
        "route_decision": decision,
        "route_reason": reason,
        "iteration": state["iteration"] + 1,
    }


async def intake_extractor_node(state: AgentState) -> dict[str, Any]:
    """MR 1 stub. Real VisionExtractor[IntakeFormExtraction] in MR 2."""
    return {}


async def evidence_retriever_node(state: AgentState) -> dict[str, Any]:
    """MR 1 stub. Real EvidenceRetriever wiring in MR 2."""
    return {}


async def synthesize_node(state: AgentState) -> dict[str, Any]:
    """MR 1 stub. Real synthesis migration in MR 2."""
    return {}


async def terminal_node(state: AgentState) -> dict[str, Any]:
    """Terminal sink. Real StreamingVerifier wrapping in MR 3."""
    return {}


def _route_from_supervisor(state: AgentState) -> str:
    decision = state["route_decision"]
    if decision is None:
        # Defensive — supervisor must always set route_decision.
        # An unset value here means the supervisor wasn't run; route to
        # synthesize so the graph still terminates.
        return RouteDecision.SYNTHESIZE.value
    return decision.value


def build_graph(
    planner: _PlannerLike,
) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
    """Assemble + compile the supervisor graph.

    The Planner is injected so tests can pass a stub. Production
    callers (later MRs) will inject the real Planner constructed
    against the LLM client.
    """

    async def _supervisor(state: AgentState) -> dict[str, Any]:
        return await supervisor_node(state, planner)

    graph: StateGraph[AgentState, None, AgentState, AgentState] = StateGraph(AgentState)
    graph.add_node("supervisor", _supervisor)
    graph.add_node(RouteDecision.INTAKE_EXTRACTOR.value, intake_extractor_node)
    graph.add_node(RouteDecision.EVIDENCE_RETRIEVER.value, evidence_retriever_node)
    graph.add_node(RouteDecision.SYNTHESIZE.value, synthesize_node)
    graph.add_node("terminal", terminal_node)

    graph.set_entry_point("supervisor")
    graph.add_conditional_edges(
        "supervisor",
        _route_from_supervisor,
        {
            RouteDecision.INTAKE_EXTRACTOR.value: RouteDecision.INTAKE_EXTRACTOR.value,
            RouteDecision.EVIDENCE_RETRIEVER.value: RouteDecision.EVIDENCE_RETRIEVER.value,
            # MR 1: BOTH unwired (no parallel workers yet); collapses to
            # intake-extractor. Wired properly in MR 2.
            RouteDecision.BOTH.value: RouteDecision.INTAKE_EXTRACTOR.value,
            RouteDecision.SYNTHESIZE.value: RouteDecision.SYNTHESIZE.value,
        },
    )
    # Workers loop back to supervisor so the iteration cap engages.
    graph.add_edge(RouteDecision.INTAKE_EXTRACTOR.value, "supervisor")
    graph.add_edge(RouteDecision.EVIDENCE_RETRIEVER.value, "supervisor")
    # Synthesize → terminal → END (no loop).
    graph.add_edge(RouteDecision.SYNTHESIZE.value, "terminal")
    graph.add_edge("terminal", END)

    return graph.compile()
