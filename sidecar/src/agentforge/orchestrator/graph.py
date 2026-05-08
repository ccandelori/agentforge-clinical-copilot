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
  * MR 3 (Task 1.5) — wires the real synthesizer LLM into
    ``synthesize_node``. Builds a context block from
    ``extraction_result`` + ``evidence_chunks`` and asks the model
    for a final answer.
  * MR 4 (Task 1.6, this MR) — adds ``build_w2_citation_index`` and
    wraps ``StreamingVerifier`` as ``terminal_node``. The
    synthesizer's evidence-tag format is updated to be
    parser-compatible (``[guideline #chunk_id]``) so cited claims
    actually round-trip through verification.
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

from collections.abc import AsyncIterator, Iterable
from enum import StrEnum
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agentforge.llm.types import LLMResponse, Message, ToolSpec
from agentforge.observability.protocols import LangfuseClient, TraceHandle
from agentforge.orchestrator.planner import Plan, UseCase
from agentforge.prompts import load_prompt
from agentforge.rag.evidence_retriever import RetrievalStats
from agentforge.rag.types import RetrievalResult
from agentforge.schemas.citation import Citation as W2Citation
from agentforge.schemas.intake import IntakeFormExtraction
from agentforge.tools.attach_and_extract import (
    RenderedPage,
    VisionExtractionResult,
)
from agentforge.tools.dtos import ToolResult
from agentforge.verifier.cache import CitationIndex, CitationKey, build_citation_index
from agentforge.verifier.data_quality import DataQualityChecker
from agentforge.verifier.protocols import DomainConstraintChecker
from agentforge.verifier.streaming_verifier import StreamingVerifier

# Loaded once from prompts/<active>/graph_synthesizer.md. Distinct from
# the W1 ``synthesizer`` component (used by ``Orchestrator.SYSTEM_PROMPT``)
# because the W2 graph's synthesize step receives extraction + evidence,
# not tool_use results, and so needs different grounding rules. Both
# components coexist until the MR 6 cutover retires the W1 path.
SYNTHESIS_SYSTEM_PROMPT: str = load_prompt("graph_synthesizer")

SYNTHESIS_MAX_TOKENS: int = 2048

# Token budget for the synthesizer's input edge (messages + tool_results).
# Matches the W1 ``TimeoutPolicy.synthesis_input_cap`` default so the
# truncator behaves identically across both code paths.
SYNTHESIS_INPUT_CAP_TOKENS: int = 12_000

MAX_ITERATIONS: int = 3

# Marker used as ``from_node`` on the supervisor's first handoff span,
# before any worker has run. Kept as a module-level constant so dashboards
# / queries can pin it without hunting for a magic string.
HANDOFF_START_NODE: str = "start"


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

    Cross-cutting fields:

    * ``tool_results`` — W1-shaped ``dict[str, ToolResult[Any]]`` so
      the MR 6 cutover bridge can drop W1 callers' results into the
      graph state without re-shaping. The W2 worker bodies don't
      populate it today; the synthesizer's truncator + DataQuality
      hook still operate on it (no-ops on an empty dict).
    * ``langfuse_trace`` — opaque per-turn handle for observability
      spans. Populated by the entrypoint that opens the turn's trace;
      ``None`` when Langfuse is not configured (NullClient path).
    * ``last_node`` — node name of the most recent worker exit, used
      by the supervisor to populate the ``from_node`` field on each
      handoff span. Initialized to ``HANDOFF_START_NODE`` on the
      starter state.
    """

    messages: list[dict[str, Any]]
    tool_results: dict[str, ToolResult[Any]]
    route_decision: RouteDecision | None
    route_reason: str
    iteration: int
    extraction_result: IntakeFormExtraction | None
    evidence_chunks: list[RetrievalResult]
    document_id: int | None
    patient_id: int | None
    pdf_pages: list[RenderedPage]
    query: str
    langfuse_trace: TraceHandle | None
    last_node: str


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
    ``evidence_retriever_node``.

    The node calls :meth:`retrieve_with_stats` so the
    ``retrieval_hits`` Langfuse span can carry the per-stage counts
    alongside the retrieved chunks. :meth:`retrieve` stays in the
    contract for legacy callers (W1 fallback) but the node itself
    reads the stats path.
    """

    async def retrieve(
        self, query: str, *, top_k: int = 5
    ) -> list[RetrievalResult]: ...

    async def retrieve_with_stats(
        self, query: str, *, top_k: int = 5
    ) -> RetrievalStats: ...


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


class _TruncatorLike(Protocol):
    """Subset of ``SynthesisInputTruncator`` consumed by ``synthesize_node``.

    Just the one method we exercise. Promotes test stubs that don't
    need the full tiktoken-backed implementation while keeping the
    real ``SynthesisInputTruncator`` structurally compatible.
    """

    def truncate(
        self,
        results: dict[str, ToolResult[Any]],
        max_tokens: int,
    ) -> dict[str, ToolResult[Any]]: ...


def _last_user_message(state: AgentState) -> str:
    for msg in reversed(state["messages"]):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            return content if isinstance(content, str) else ""
    return ""


def _decide_route(plan: Plan, state: AgentState) -> tuple[RouteDecision, str]:
    """Pick the next node based on plan + per-turn worker progress.

    Hard-stop at the iteration cap → SYNTHESIZE. Pure follow-up →
    SYNTHESIZE (no tools needed). Otherwise consult W2 inputs:

    * ``pdf_pages`` non-empty AND no ``extraction_result`` →
      INTAKE_EXTRACTOR.
    * ``query`` non-empty AND no ``evidence_chunks`` → EVIDENCE_RETRIEVER.
    * Both workers complete (or had no input to run on) → SYNTHESIZE.

    The supervisor loop-back drives this through one worker per
    iteration; intake first when both are pending. Workers are
    idempotent so a re-entry under a tighter cap is cheap.
    """
    iteration = state["iteration"]
    if iteration >= MAX_ITERATIONS:
        return RouteDecision.SYNTHESIZE, "iteration cap reached"
    if plan.use_case == UseCase.FOLLOWUP:
        return RouteDecision.SYNTHESIZE, "followup: no tools needed"

    has_pdf = bool(state["pdf_pages"])
    has_query = bool(state["query"])
    has_extraction = state["extraction_result"] is not None
    has_evidence = bool(state["evidence_chunks"])

    intake_pending = has_pdf and not has_extraction
    evidence_pending = has_query and not has_evidence

    if intake_pending:
        return RouteDecision.INTAKE_EXTRACTOR, "intake PDF awaits extraction"
    if evidence_pending:
        return (
            RouteDecision.EVIDENCE_RETRIEVER,
            "evidence query awaits retrieval",
        )

    # Either both workers ran already or there was nothing for them
    # to do. Either way the synthesizer has everything it'll get.
    return (
        RouteDecision.SYNTHESIZE,
        f"all W2 workers complete for {plan.use_case.value}",
    )


async def supervisor_node(
    state: AgentState,
    planner: _PlannerLike,
    *,
    langfuse: LangfuseClient | None = None,
) -> dict[str, Any]:
    """Run the planner and emit a route decision.

    Pure routing — never mutates ``messages``, ``tool_results``,
    ``extraction_result``, or ``evidence_chunks``. Bumps ``iteration``
    so worker → supervisor loops eventually trip the cap.

    When ``langfuse`` is provided AND ``state["langfuse_trace"]`` is a
    non-Null handle, emits one ``handoff`` span per routing decision
    capturing the from/to node pair, the route decision + reason, and
    the post-bump iteration counter. The Null path skips the call
    entirely so test stubs that don't implement Langfuse are never
    forced to.
    """
    plan = await planner.plan(_last_user_message(state))
    decision, reason = _decide_route(plan, state)

    next_iteration = state["iteration"] + 1
    _maybe_record_handoff(
        langfuse,
        state.get("langfuse_trace"),
        from_node=state.get("last_node") or HANDOFF_START_NODE,
        to_node=decision.value,
        route_decision=decision.value,
        route_reason=reason,
        iteration=next_iteration,
    )

    return {
        "route_decision": decision,
        "route_reason": reason,
        "iteration": next_iteration,
    }


def _maybe_record_handoff(
    langfuse: LangfuseClient | None,
    trace: TraceHandle | None,
    *,
    from_node: str,
    to_node: str,
    route_decision: str,
    route_reason: str,
    iteration: int,
) -> None:
    """Forward a handoff span to Langfuse when both client and trace are wired.

    Either argument missing → no-op. Keeps the call sites in worker
    bodies free of the boilerplate ``if langfuse is None or trace is
    None`` guard while preserving the structural property that we never
    call into the Null implementation with a fake trace.
    """
    if langfuse is None or trace is None:
        return
    langfuse.record_handoff_span(
        trace,
        from_node=from_node,
        to_node=to_node,
        route_decision=route_decision,
        route_reason=route_reason,
        iteration=iteration,
    )


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

    Always stamps ``last_node`` so the next supervisor pass can populate
    ``from_node`` on its handoff span — even on the no-op short-circuit
    paths, the conditional edge fired.
    """
    last_node_update: dict[str, Any] = {
        "last_node": RouteDecision.INTAKE_EXTRACTOR.value
    }
    if state["extraction_result"] is not None:
        return last_node_update
    if not state["pdf_pages"]:
        return last_node_update
    document_id = state["document_id"]
    patient_id = state["patient_id"]
    if document_id is None or patient_id is None:
        return last_node_update

    result = await extractor.extract(
        pages=state["pdf_pages"],
        document_id=document_id,
        patient_id=patient_id,
    )
    return {**last_node_update, "extraction_result": result.extraction}


async def evidence_retriever_node(
    state: AgentState,
    retriever: _EvidenceRetrieverLike,
    *,
    langfuse: LangfuseClient | None = None,
) -> dict[str, Any]:
    """Drive guideline retrieval against ``state["query"]``.

    Idempotent in the same way as ``intake_extractor_node`` — once
    ``evidence_chunks`` is populated, re-entry under the supervisor's
    loop-back is a no-op. An empty ``query`` also no-ops; we never
    call the retriever with an empty string. Stamps ``last_node`` so
    the next supervisor handoff span knows where the loop-back came
    from.

    When ``langfuse`` is provided alongside a non-Null
    ``state["langfuse_trace"]``, emits one ``retrieval_hits`` span per
    successful retrieval call carrying ``bm25_count``, ``dense_count``,
    and ``post_rerank_count`` (the post-RRF, pre-rerank count is
    captured under ``dense_count + bm25_count`` shape per
    W2_ARCHITECTURE.md §7). The same Null-trace guard the supervisor
    uses for handoff spans applies here — without a trace handle, the
    span is suppressed so the dashboard's trace_id never flips to
    ``None`` from a misrouted Null call.
    """
    last_node_update: dict[str, Any] = {
        "last_node": RouteDecision.EVIDENCE_RETRIEVER.value
    }
    if state["evidence_chunks"]:
        return last_node_update
    query = state["query"]
    if not query:
        return last_node_update
    stats = await retriever.retrieve_with_stats(query)
    _maybe_record_retrieval_hits(
        langfuse,
        state.get("langfuse_trace"),
        bm25_count=stats.bm25_count,
        dense_count=stats.dense_count,
        post_rerank_count=stats.post_rerank_count,
    )
    return {**last_node_update, "evidence_chunks": stats.results}


def _maybe_record_retrieval_hits(
    langfuse: LangfuseClient | None,
    trace: TraceHandle | None,
    *,
    bm25_count: int,
    dense_count: int,
    post_rerank_count: int,
) -> None:
    """Forward the per-stage counts to Langfuse when both client and trace are wired.

    Mirrors :func:`_maybe_record_handoff` — either argument missing →
    no-op. Keeps the node body free of the ``if langfuse is None or
    trace is None`` guard while preserving the structural property
    that we never call into the Null implementation with a fake trace.
    """
    if langfuse is None or trace is None:
        return
    langfuse.record_retrieval_hits(
        trace,
        bm25_count=bm25_count,
        dense_count=dense_count,
        post_rerank_count=post_rerank_count,
    )


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
            # Format matches the W1 verifier's citation grammar
            # (`[<type> #<id>]`) so the model's mirrored tags parse
            # cleanly at terminal-node verification time. The doc_id
            # is shown in the body so the model can reference both
            # without baking a composite tag the verifier would
            # need to deconstruct.
            citation_tag = f"[guideline #{chunk.chunk_id}]"
            chunk_lines.append(
                f"{citation_tag} ({chunk.doc_id})\n{chunk.text}"
            )
        sections.append("RETRIEVED EVIDENCE:\n" + "\n\n".join(chunk_lines))

    if not sections:
        return None
    return "\n\n".join(sections)


async def synthesize_node(
    state: AgentState,
    llm: _SynthesisLLMLike,
    *,
    truncator: _TruncatorLike | None = None,
    max_synthesis_tokens: int = SYNTHESIS_INPUT_CAP_TOKENS,
    data_quality_checker: DataQualityChecker | None = None,
    langfuse: LangfuseClient | None = None,
) -> dict[str, Any]:
    """Compose a final answer from accumulated turn state.

    Calls the LLM once with the conversation messages, the
    ``SYNTHESIS_SYSTEM_PROMPT``, and a context block derived from
    ``extraction_result`` + ``evidence_chunks``. Appends the response
    text as an assistant message.

    Cross-cutting hooks (all optional):

    * **Truncator.** When ``truncator`` is wired and ``state["tool_results"]``
      is non-empty, the dict is capped at ``max_synthesis_tokens`` before
      anything is rendered. This is dead-code wiring on a pure W2 turn
      (no tool_results) but lights up under the MR 6 W1 bridge.
    * **DataQualityChecker.** When ``data_quality_checker`` is wired,
      W1-shaped tool_results are run through the stale-lab and
      problem/note-conflict heuristics. Any warnings prepend the
      synthesizer's system prompt as a ``<system_reminder>`` block so
      the LLM has a chance to surface them inline. ``langfuse``, when
      supplied alongside, receives a ``record_data_quality_metrics``
      span carrying only the warning counts (the strings stay local).

    Idempotent: if the last message in state is already an assistant
    turn, the synthesizer has already run and we no-op. Same contract
    as the other workers under the supervisor's loop-back.
    """
    last_node_update: dict[str, Any] = {
        "last_node": RouteDecision.SYNTHESIZE.value
    }
    if state["messages"] and state["messages"][-1].get("role") == "assistant":
        return last_node_update

    tool_results = state["tool_results"]
    if truncator is not None and tool_results:
        tool_results = truncator.truncate(tool_results, max_synthesis_tokens)

    dq_warnings = _collect_data_quality_warnings(
        tool_results,
        data_quality_checker,
        langfuse=langfuse,
        trace=state.get("langfuse_trace"),
    )
    system_prompt = _compose_system_prompt(SYNTHESIS_SYSTEM_PROMPT, dq_warnings)

    messages = _state_messages_to_llm_messages(state["messages"])
    context_block = _build_synthesis_context_block(state)
    if context_block is not None:
        # Inject as a user-role message so the LLM treats it as part
        # of the conversation. Going via the system prompt would mix
        # turn-specific content with timeless instructions; a chat
        # message is the cleaner seam.
        messages.append(Message(role="user", content=context_block))
    response = await llm.complete(
        system=system_prompt,
        messages=messages,
        max_tokens=SYNTHESIS_MAX_TOKENS,
    )
    new_message: dict[str, Any] = {
        "role": "assistant",
        "content": response.text,
    }
    return {
        **last_node_update,
        "messages": [*state["messages"], new_message],
    }


def _compose_system_prompt(base: str, dq_warnings: list[str]) -> str:
    """Return ``base`` optionally prefixed by a data-quality reminder.

    Empty warning list → unchanged base prompt. Non-empty list →
    ``<system_reminder>`` block with one bullet per warning, prepended
    above the base prompt with a blank line separator. The reminder
    block is intentionally bracketed so the model can reliably ignore
    it when answering questions unrelated to the flagged data.
    """
    if not dq_warnings:
        return base
    body = "\n".join(f"- {w}" for w in dq_warnings)
    reminder = (
        "<system_reminder>\n"
        "Data quality flags for this turn:\n"
        f"{body}\n"
        "</system_reminder>"
    )
    return f"{reminder}\n\n{base}"


def _collect_data_quality_warnings(
    tool_results: dict[str, ToolResult[Any]],
    checker: DataQualityChecker | None,
    *,
    langfuse: LangfuseClient | None,
    trace: TraceHandle | None,
) -> list[str]:
    """Run the stale-lab + problem/note conflict checks over W1 tool results.

    Mirrors :meth:`Orchestrator._data_quality_suffix` so warnings are
    consistent across the W1 iterative path and the W2 graph path.
    Returns an empty list when (a) no checker is wired, (b) no
    tool_results match the W1 keys, or (c) no warnings fire.

    Records the warning counts (NOT the strings) onto the trace via
    ``record_data_quality_metrics`` whenever the checker ran, so
    dashboards can roll up per-turn DQ activity even when no warning
    surfaced. Skips the metric record entirely when the checker is
    None — there's nothing to report.
    """
    if checker is None:
        return []

    warnings: list[str] = []
    stale_count = 0
    conflict_count = 0

    labs_result = tool_results.get("get_recent_labs")
    if labs_result is not None and hasattr(labs_result.payload, "labs"):
        for lab in labs_result.payload.labs:
            flag = checker.check_stale_labs(lab)
            if flag is not None:
                warnings.append(flag)
                stale_count += 1

    problems_result = tool_results.get("get_active_problems")
    notes_result = tool_results.get("get_recent_notes")
    if (
        problems_result is not None
        and notes_result is not None
        and hasattr(problems_result.payload, "problems")
        and hasattr(notes_result.payload, "notes")
    ):
        conflicts = checker.check_conflicting_sources(
            problems_result.payload.problems,
            notes_result.payload.notes,
        )
        warnings.extend(conflicts)
        conflict_count = len(conflicts)

    if langfuse is not None and trace is not None:
        langfuse.record_data_quality_metrics(
            trace,
            stale_labs_count=stale_count,
            conflict_count=conflict_count,
        )

    # Dedupe identical warnings — multiple labs from the same date
    # produce the same string, so without dedup a chart with several
    # labs from one collection date floods the prompt with copies of one
    # notice. Preserve insertion order so the most-recently-fired check
    # surfaces first.
    seen: set[str] = set()
    deduped: list[str] = []
    for warning in warnings:
        if warning in seen:
            continue
        seen.add(warning)
        deduped.append(warning)
    return deduped


def build_w2_citation_index(state: AgentState) -> CitationIndex:
    """Build a per-turn CitationIndex from every cite-bearing slice of state.

    Three sources merge into one index:

    * ``state["tool_results"]`` — W1-shaped ``dict[str, ToolResult]`` walked
      via the existing :func:`build_citation_index`. This is the MR 6
      bridge: when a turn carries W1 tool results (chart-question turns
      after the cutover), their ``[problem #N]`` / ``[lab_result #N]`` /
      etc. tags resolve in the same index that resolves W2 tags.
    * ``state["evidence_chunks"]`` — each retrieved guideline chunk's W2
      citation.
    * ``state["extraction_result"]`` — chief-concern + per-list citations
      from the intake-form extraction.

    All three contribute under the same ``(record_type, record_id)``
    key shape that the W1 verifier already understands. The W1 walk
    runs first so W2 records can override on key collision (extremely
    unlikely in practice — W1 record types are nouns like ``"problem"``;
    W2 source types are sources like ``"guideline"``, ``"intake_form"``).
    """
    w1_index = build_citation_index(state["tool_results"])
    records: dict[CitationKey, dict[str, Any]] = dict(w1_index.records)
    for result in state["evidence_chunks"]:
        _register_w2_citation(records, result.chunk.citation)
    extraction = state["extraction_result"]
    if extraction is not None:
        for citation in _walk_extraction_citations(extraction):
            _register_w2_citation(records, citation)
    return CitationIndex(records=records)


def _register_w2_citation(
    records: dict[CitationKey, dict[str, Any]],
    citation: W2Citation,
) -> None:
    key: CitationKey = (citation.source_type.value, citation.field_or_chunk_id)
    records[key] = citation.model_dump()


def _walk_extraction_citations(
    extraction: IntakeFormExtraction,
) -> Iterable[W2Citation]:
    if extraction.chief_concern_citation is not None:
        yield extraction.chief_concern_citation
    for demo in extraction.demographics:
        yield demo.citation
    for med in extraction.medications:
        yield med.citation
    for allergy in extraction.allergies:
        yield allergy.citation
    for fh in extraction.family_history:
        yield fh.citation


async def terminal_node(
    state: AgentState,
    *,
    domain_checker: DomainConstraintChecker | None = None,
) -> dict[str, Any]:
    """Verify the assistant's final answer against the per-turn citation index.

    Builds a ``CitationIndex`` from state's W2 sources, instantiates
    a ``StreamingVerifier``, and runs the last assistant message
    through it. Each parsed citation must resolve in the index;
    citations that don't resolve cause their containing claim to be
    replaced with the rejection marker.

    No-ops on the messages field when no assistant message exists
    (early termination paths) but always stamps ``last_node`` so any
    downstream observer sees the graph terminated here.
    """
    last_node_update: dict[str, Any] = {"last_node": "terminal"}
    last = _last_assistant_message_index(state["messages"])
    if last is None:
        return last_node_update

    raw_text = state["messages"][last].get("content", "")
    if not isinstance(raw_text, str) or not raw_text:
        return last_node_update

    index = build_w2_citation_index(state)
    verifier = StreamingVerifier(index, domain_checker)
    verified_text = await _verify_text(verifier, raw_text)

    new_messages = list(state["messages"])
    new_messages[last] = {
        **state["messages"][last],
        "content": verified_text,
    }
    return {**last_node_update, "messages": new_messages}


def _last_assistant_message_index(
    messages: list[dict[str, Any]],
) -> int | None:
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "assistant":
            return i
    return None


async def _verify_text(verifier: StreamingVerifier, text: str) -> str:
    """Run a complete text through ``verify_stream`` as a single chunk.

    The streaming API takes an ``AsyncIterator[str]``; we wrap the
    full assistant text in a one-shot generator and concatenate the
    yielded ``VerifiedChunk.text`` values to reconstruct the final
    string with rejection markers swapped in for unverified claims.
    """

    async def _yield_once() -> AsyncIterator[str]:
        yield text

    parts: list[str] = []
    async for chunk in verifier.verify_stream(_yield_once()):
        parts.append(chunk.text)
    return "".join(parts)


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
    domain_checker: DomainConstraintChecker | None = None,
    truncator: _TruncatorLike | None = None,
    max_synthesis_tokens: int = SYNTHESIS_INPUT_CAP_TOKENS,
    data_quality_checker: DataQualityChecker | None = None,
    langfuse: LangfuseClient | None = None,
) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
    """Assemble + compile the supervisor graph.

    Workers are injected so tests can pass stubs. Production callers
    (cutover MR) will inject real instances. Each worker dependency
    is optional — when None, the corresponding worker is wired as a
    no-op pass-through. This preserves earlier MR behavior for
    callers that haven't wired the new dependency yet.

    ``domain_checker`` is consumed by ``terminal_node``'s embedded
    ``StreamingVerifier``. When None, the verifier passes
    cited-and-resolved claims unchanged (only structural citation
    resolution is enforced).

    Cross-cutting deps (all optional, default off):

    * ``truncator`` — caps ``state["tool_results"]`` at
      ``max_synthesis_tokens`` before the synthesizer's LLM call.
    * ``data_quality_checker`` — runs DQ heuristics over W1-shaped
      tool_results; warnings prepend the synthesizer's system prompt.
    * ``langfuse`` — receives ``record_handoff_span`` per supervisor
      decision and ``record_data_quality_metrics`` per synthesis call.
      The per-turn ``TraceHandle`` lives in ``state["langfuse_trace"]``.

    All four cross-cutting hooks no-op when their dependency is None
    OR when the relevant state slice is empty, so a graph built without
    them behaves exactly like the MR 4 graph.
    """

    async def _supervisor(state: AgentState) -> dict[str, Any]:
        return await supervisor_node(state, planner, langfuse=langfuse)

    async def _intake_extractor(state: AgentState) -> dict[str, Any]:
        if vision_extractor is None:
            return {"last_node": RouteDecision.INTAKE_EXTRACTOR.value}
        return await intake_extractor_node(state, vision_extractor)

    async def _evidence_retriever(state: AgentState) -> dict[str, Any]:
        if evidence_retriever is None:
            return {"last_node": RouteDecision.EVIDENCE_RETRIEVER.value}
        return await evidence_retriever_node(
            state, evidence_retriever, langfuse=langfuse
        )

    async def _synthesize(state: AgentState) -> dict[str, Any]:
        if synthesis_llm is None:
            return {"last_node": RouteDecision.SYNTHESIZE.value}
        return await synthesize_node(
            state,
            synthesis_llm,
            truncator=truncator,
            max_synthesis_tokens=max_synthesis_tokens,
            data_quality_checker=data_quality_checker,
            langfuse=langfuse,
        )

    async def _terminal(state: AgentState) -> dict[str, Any]:
        return await terminal_node(state, domain_checker=domain_checker)

    graph: StateGraph[AgentState, None, AgentState, AgentState] = StateGraph(AgentState)
    graph.add_node("supervisor", _supervisor)
    graph.add_node(RouteDecision.INTAKE_EXTRACTOR.value, _intake_extractor)
    graph.add_node(RouteDecision.EVIDENCE_RETRIEVER.value, _evidence_retriever)
    graph.add_node(RouteDecision.SYNTHESIZE.value, _synthesize)
    graph.add_node("terminal", _terminal)

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
