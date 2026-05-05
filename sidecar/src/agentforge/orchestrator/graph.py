"""LangGraph supervisor for the W2 agent (Task 1).

This module ships the StateGraph wiring, supervisor routing, and
real worker bodies for the intake extractor and evidence retriever.
The graph remains **dead code in production** — only exercised by
tests — until MR 3 cuts over the production entrypoint from the
old ``Orchestrator.turn()`` iterative loop.

MR layering:
  * MR 1 (Task 1.1-1.2) — StateGraph skeleton + supervisor routing
    against ``Planner.plan()`` + iteration cap.
  * MR 2 (Task 1.3-1.4, this MR) — wires the real
    ``VisionExtractor[IntakeFormExtraction]`` into
    ``intake_extractor_node`` and ``EvidenceRetriever`` into
    ``evidence_retriever_node``. ``synthesize_node`` and
    ``terminal_node`` stay as stubs (deferred to MR 3 since real
    synthesis overlaps with the ``_turn_inner`` cutover).
  * MR 3 (Task 1.5-1.8) — migrates synthesis, wraps StreamingVerifier
    as the terminal node, wires Langfuse spans + DataQuality
    warnings, and replaces the production entrypoint with
    ``graph.ainvoke()``.

Routing (still the MR 1 placeholder until MR 3):
  * iteration >= MAX_ITERATIONS → SYNTHESIZE (hard stop)
  * Plan.use_case == FOLLOWUP   → SYNTHESIZE (no tools needed)
  * otherwise                   → INTAKE_EXTRACTOR (default)

Worker idempotency keeps the placeholder routing safe under
loop-back. Each worker checks state for prior output and no-ops if
already done — so the supervisor's loop-back may re-enter a node
multiple times without paying for the same Anthropic / retrieval
call twice. Real PDF-vs-evidence-vs-both routing logic lands in
MR 3 alongside cutover.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agentforge.orchestrator.planner import Plan, UseCase
from agentforge.rag.types import RetrievalResult
from agentforge.schemas.intake import IntakeFormExtraction
from agentforge.tools.attach_and_extract import (
    RenderedPage,
    VisionExtractionResult,
)

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

    Worker-input fields (``document_id``, ``patient_id``,
    ``pdf_pages``, ``query``) are populated by the entrypoint that
    constructs the starter state from a request. The intake extractor
    consumes the PDF triple; the evidence retriever consumes
    ``query``.

    Worker-output fields (``extraction_result``, ``evidence_chunks``)
    are populated by the workers themselves and consumed downstream
    by the synthesizer. Workers are idempotent — once an output
    field is populated, the worker no-ops if the supervisor routes
    back to it.
    """

    messages: list[dict[str, Any]]
    tool_results: list[Any]
    route_decision: RouteDecision | None
    route_reason: str
    iteration: int
    extraction_result: IntakeFormExtraction | None
    evidence_chunks: list[RetrievalResult]
    document_id: int | None
    patient_id: int | None
    pdf_pages: list[RenderedPage]
    query: str


class _PlannerLike(Protocol):
    """Subset of ``Planner`` consumed by the supervisor.

    Keeps tests stub-able without inheritance. The real ``Planner``
    satisfies this structurally.
    """

    async def plan(self, user_message: str) -> Plan: ...


class _VisionExtractorLike(Protocol):
    """Subset of ``VisionExtractor[IntakeFormExtraction]`` consumed
    by ``intake_extractor_node``."""

    async def extract(
        self,
        *,
        pages: list[RenderedPage],
        document_id: int,
        patient_id: int,
    ) -> VisionExtractionResult[IntakeFormExtraction]: ...


class _EvidenceRetrieverLike(Protocol):
    """Subset of ``EvidenceRetriever`` consumed by
    ``evidence_retriever_node``."""

    async def retrieve(
        self, query: str, *, top_k: int = 5
    ) -> list[RetrievalResult]: ...


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


async def intake_extractor_node(
    state: AgentState,
    extractor: _VisionExtractorLike,
) -> dict[str, Any]:
    """Drive the intake-form vision extraction.

    Hands the rendered pages + identifier triple to
    ``VisionExtractor.extract`` and writes the validated
    ``IntakeFormExtraction`` into ``state["extraction_result"]``.

    Idempotent: returns an empty update without calling the extractor
    when (a) extraction has already run this turn, (b) no pages are
    attached, or (c) document_id / patient_id are missing. The
    supervisor's loop-back means a worker can be re-entered up to
    MAX_ITERATIONS times, so we must not duplicate the (expensive)
    Anthropic call.
    """
    if state["extraction_result"] is not None:
        return {}
    if not state["pdf_pages"]:
        return {}
    document_id = state["document_id"]
    patient_id = state["patient_id"]
    if document_id is None or patient_id is None:
        return {}

    result = await extractor.extract(
        pages=state["pdf_pages"],
        document_id=document_id,
        patient_id=patient_id,
    )
    return {"extraction_result": result.extraction}


async def evidence_retriever_node(
    state: AgentState,
    retriever: _EvidenceRetrieverLike,
) -> dict[str, Any]:
    """Drive guideline retrieval against ``state["query"]``.

    Idempotent in the same way as ``intake_extractor_node`` — once
    ``evidence_chunks`` is populated, re-entry under the supervisor's
    loop-back is a no-op. An empty ``query`` also no-ops; we never
    call the retriever with an empty string.
    """
    if state["evidence_chunks"]:
        return {}
    query = state["query"]
    if not query:
        return {}
    results = await retriever.retrieve(query)
    return {"evidence_chunks": results}


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
    *,
    vision_extractor: _VisionExtractorLike | None = None,
    evidence_retriever: _EvidenceRetrieverLike | None = None,
) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
    """Assemble + compile the supervisor graph.

    Workers are injected so tests can pass stubs. Production callers
    (MR 3 cutover) will inject real instances. ``vision_extractor``
    and ``evidence_retriever`` are optional — when None, the
    corresponding worker is wired as a no-op pass-through (the
    supervisor will still route to it under the placeholder rule, but
    it produces no output).
    """

    async def _supervisor(state: AgentState) -> dict[str, Any]:
        return await supervisor_node(state, planner)

    async def _intake_extractor(state: AgentState) -> dict[str, Any]:
        if vision_extractor is None:
            return {}
        return await intake_extractor_node(state, vision_extractor)

    async def _evidence_retriever(state: AgentState) -> dict[str, Any]:
        if evidence_retriever is None:
            return {}
        return await evidence_retriever_node(state, evidence_retriever)

    graph: StateGraph[AgentState, None, AgentState, AgentState] = StateGraph(AgentState)
    graph.add_node("supervisor", _supervisor)
    graph.add_node(RouteDecision.INTAKE_EXTRACTOR.value, _intake_extractor)
    graph.add_node(RouteDecision.EVIDENCE_RETRIEVER.value, _evidence_retriever)
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
