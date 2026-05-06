"""LangGraph supervisor for the W2 agent (Task 1).

This module ships the StateGraph wiring, supervisor routing, and
real worker bodies for the intake extractor and evidence retriever.
The graph remains **dead code in production** — only exercised by
tests — until MR 3 cuts over the production entrypoint from the
old ``Orchestrator.turn()`` iterative loop.

MR layering:
  * MR 1 (Task 1.1-1.2) — StateGraph skeleton + supervisor routing
    against ``Planner.plan()`` + iteration cap.
  * MR 2 (Task 1.3-1.4) — wires the real
    ``VisionExtractor[IntakeFormExtraction]`` into
    ``intake_extractor_node`` and ``EvidenceRetriever`` into
    ``evidence_retriever_node``.
  * MR 3 (Task 1.5, this MR) — wires the real synthesizer LLM
    into ``synthesize_node``. Builds a context block from
    ``extraction_result`` + ``evidence_chunks`` and asks the model
    for a final answer. ``terminal_node`` stays as a stub.
  * MR 4 (Task 1.6) — wraps ``StreamingVerifier`` as
    ``terminal_node``; adds the W2 citation-index builder.
  * MR 5 (Task 1.7-1.8) — wires ``SynthesisInputTruncator``,
    ``DataQualityChecker`` warnings, and Langfuse spans per handoff.
  * MR 6 — production cutover: replace ``Orchestrator.turn()``
    callers with ``graph.ainvoke()``.

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

from agentforge.llm.types import LLMResponse, Message, ToolSpec
from agentforge.orchestrator.planner import Plan, UseCase
from agentforge.rag.types import RetrievalResult
from agentforge.schemas.intake import IntakeFormExtraction
from agentforge.tools.attach_and_extract import (
    RenderedPage,
    VisionExtractionResult,
)

SYNTHESIS_SYSTEM_PROMPT: str = (
    "You are a clinical co-pilot. Given the user's question, any "
    "structured data extracted from uploaded documents, and the "
    "retrieved guideline evidence, synthesize a concise answer for "
    "the clinician. Cite extracted values and guideline chunks "
    "explicitly so a reader can trace every clinical claim back to "
    "its source.\n\n"
    "If neither extracted data nor evidence is available, answer "
    "from the conversation itself — do not invent values."
)

SYNTHESIS_MAX_TOKENS: int = 2048

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


class _SynthesisLLMLike(Protocol):
    """Subset of ``LLMClient`` consumed by ``synthesize_node``.

    The synthesizer only needs ``complete()`` (one-shot synthesis;
    streaming integration with the verifier lives in the terminal
    node). Narrowing to a Protocol keeps test stubs from having to
    implement the full ``LLMClient`` Protocol's ``stream()`` surface.
    """

    async def complete(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
        temperature: float = 1.0,
    ) -> LLMResponse: ...


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


def _state_messages_to_llm_messages(
    state_messages: list[dict[str, Any]],
) -> list[Message]:
    """Convert wire-format dict messages to ``Message`` objects.

    State carries messages as plain dicts so the FastAPI request body
    can be mapped directly into starter state without an extra
    parse step. The LLM client wants typed ``Message`` instances.
    """
    typed: list[Message] = []
    for raw in state_messages:
        role = raw.get("role")
        content = raw.get("content", "")
        if role not in {"user", "assistant", "tool"}:
            continue
        if not isinstance(content, str):
            continue
        typed.append(Message(role=role, content=content))
    return typed


def _build_synthesis_context_block(state: AgentState) -> str | None:
    """Render extraction + evidence into a single context block.

    Returns ``None`` when the turn carries no synthesis context — pure
    follow-up turns answer from the conversation alone, no need for a
    placeholder block.
    """
    sections: list[str] = []

    extraction = state["extraction_result"]
    if extraction is not None:
        sections.append(
            "EXTRACTED INTAKE DATA:\n" + extraction.model_dump_json(indent=2)
        )

    chunks = state["evidence_chunks"]
    if chunks:
        chunk_lines: list[str] = []
        for result in chunks:
            chunk = result.chunk
            citation_tag = (
                f"[guideline:{chunk.doc_id}#{chunk.chunk_id}]"
            )
            chunk_lines.append(f"{citation_tag}\n{chunk.text}")
        sections.append("RETRIEVED EVIDENCE:\n" + "\n\n".join(chunk_lines))

    if not sections:
        return None
    return "\n\n".join(sections)


async def synthesize_node(
    state: AgentState,
    llm: _SynthesisLLMLike,
) -> dict[str, Any]:
    """Compose a final answer from accumulated turn state.

    Calls the LLM once with the conversation messages, the
    ``SYNTHESIS_SYSTEM_PROMPT``, and (in later MRs) a context block
    derived from ``extraction_result`` + ``evidence_chunks``. Appends
    the response text as an assistant message.

    Idempotent: if the last message in state is already an assistant
    turn, the synthesizer has already run and we no-op. Same contract
    as the other workers under the supervisor's loop-back.
    """
    if state["messages"] and state["messages"][-1].get("role") == "assistant":
        return {}
    messages = _state_messages_to_llm_messages(state["messages"])
    context_block = _build_synthesis_context_block(state)
    if context_block is not None:
        # Inject as a user-role message so the LLM treats it as part
        # of the conversation. Going via the system prompt would mix
        # turn-specific content with timeless instructions; a chat
        # message is the cleaner seam.
        messages.append(Message(role="user", content=context_block))
    response = await llm.complete(
        system=SYNTHESIS_SYSTEM_PROMPT,
        messages=messages,
        max_tokens=SYNTHESIS_MAX_TOKENS,
    )
    new_message: dict[str, Any] = {
        "role": "assistant",
        "content": response.text,
    }
    return {"messages": [*state["messages"], new_message]}


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
    synthesis_llm: _SynthesisLLMLike | None = None,
) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
    """Assemble + compile the supervisor graph.

    Workers are injected so tests can pass stubs. Production callers
    (cutover MR) will inject real instances. Each worker dependency
    is optional — when None, the corresponding worker is wired as a
    no-op pass-through. This preserves earlier MR behavior for
    callers that haven't wired the new dependency yet.
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

    async def _synthesize(state: AgentState) -> dict[str, Any]:
        if synthesis_llm is None:
            return {}
        return await synthesize_node(state, synthesis_llm)

    graph: StateGraph[AgentState, None, AgentState, AgentState] = StateGraph(AgentState)
    graph.add_node("supervisor", _supervisor)
    graph.add_node(RouteDecision.INTAKE_EXTRACTOR.value, _intake_extractor)
    graph.add_node(RouteDecision.EVIDENCE_RETRIEVER.value, _evidence_retriever)
    graph.add_node(RouteDecision.SYNTHESIZE.value, _synthesize)
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
