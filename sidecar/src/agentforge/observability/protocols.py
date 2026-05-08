"""Public surface for Langfuse-backed observability.

Two protocols, one shape:

  * ``LangfuseClient`` — what callers (orchestrator, verifier, future
    instrumentation) hold. Exposes ``trace_turn`` plus span helpers.
  * ``TraceHandle`` — what ``trace_turn`` returns. Carries enough context
    to attach child spans without leaking the underlying SDK type.

Both the real :class:`AgentLangfuse` and the no-op
:class:`NullLangfuseClient` satisfy this contract. The Protocol is
deliberately narrow: callers cannot pass raw payloads anywhere — they
must hash them at the boundary using
:mod:`agentforge.observability.hmac_hash` first. This is the structural
guarantee that PHI cannot reach the trace store. See ARCHITECTURE.md
S7.3 ("What is never logged").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable


@dataclass(frozen=True)
class RouteDecisionRecord:
    """One supervisor routing decision — frozen so the trace handle's
    accumulated trail is read-only after append.

    Mirrors the ``record_handoff_span`` field set so dashboards can
    cross-reference span events against the in-memory trail without
    field-name drift.

    All five fields are PHI-safe by construction: closed enums or
    bounded strings (``decision``, ``from_node``, ``to_node``), the
    bounded-vocabulary ``reason``, and the integer ``iteration``
    capped by ``MAX_ITERATIONS``.
    """

    decision: str
    reason: str
    from_node: str
    to_node: str
    iteration: int


@runtime_checkable
class TraceHandle(Protocol):
    """Opaque handle returned by :meth:`LangfuseClient.trace_turn`.

    Concrete implementations may carry an SDK trace object, a span
    context, or nothing at all (Null implementation). Callers should
    not introspect the handle — pass it back to the client's span
    methods to attach child observations.

    Per-turn accumulators:

    * ``route_decisions`` — every supervisor → worker handoff this
      turn, appended in order by :meth:`LangfuseClient.record_route_decision`.
      Eval runs read this trail to assert the routing path without
      walking the Langfuse span store.
    * ``eval_outcome`` — eval-run case verdict (``"pass"`` / ``"fail"``
      / ``"refused"`` / etc.); ``None`` for production turns. Mutable
      so eval harnesses can stamp the verdict after the run completes.
    """

    @property
    def trace_id(self) -> str | None:
        """Stable trace identifier, or ``None`` for the Null implementation."""
        ...

    @property
    def route_decisions(self) -> list[RouteDecisionRecord]:
        """The ordered routing trail for this turn (mutated by append)."""
        ...

    eval_outcome: str | None


@runtime_checkable
class LangfuseClient(Protocol):
    """Boundary protocol for engineering-trace observability.

    All methods accept already-hashed identifiers and content digests.
    No method takes a raw patient ID, raw arg payload, or raw completion
    text — that's enforced by the type signatures below.
    """

    def pseudonymize_id(self, raw_id: int | str) -> str: ...

    def trace_turn(
        self,
        *,
        user_id: int | str,
        patient_id: int | str,
        breakglass_flag: bool,
        role: str | None,
    ) -> TraceHandle: ...

    def record_tool_call(
        self,
        trace: TraceHandle,
        *,
        tool_name: str,
        status: str,
        latency_ms: int,
        cache_hit: bool,
        args_hash: str | None,
        result_hash: str | None,
    ) -> None: ...

    def record_llm_call(
        self,
        trace: TraceHandle,
        *,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: int,
        cost_usd: float | None = None,
    ) -> None: ...

    def record_extraction_call(
        self,
        trace: TraceHandle,
        *,
        model: str,
        tool_name: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        schema_validation: Literal["pass", "fail"],
        page_count: int,
        unsupported_fields_count: int,
        extraction_confidence: float | None = None,
        cost_usd: float | None = None,
    ) -> None:
        """Record one vision-extraction call (Task 11/13 ``attach_and_extract``).

        Like :meth:`record_llm_call`, this method is **structurally
        PHI-safe**: it accepts only the call's metadata shape, never
        the prompt body, the rendered images, or the extracted fields'
        text. The four extraction-specific fields beyond the LLM-call
        baseline are all bounded:

        * ``tool_name`` is the closed two-element set
          ``"emit_lab_pdf_extraction"`` / ``"emit_intake_form_extraction"``
          (one per :class:`VisionContract`); not patient data.
        * ``schema_validation`` is the closed literal pair
          ``"pass"`` / ``"fail"`` reflecting whether
          :meth:`pydantic.BaseModel.model_validate` succeeded on the
          tool_use payload.
        * ``page_count`` is the number of rendered PDF pages, not
          their content.
        * ``unsupported_fields_count`` is the **length** of the
          extraction's ``unsupported_fields`` list — not the field
          names themselves, which could leak intent (e.g. "syphilis"
          if the form had a positive test the model couldn't
          confidently localize).
        * ``extraction_confidence`` is the worker's overall
          self-rating in [0, 1]; ``None`` when validation failed
          before a confidence number existed.
        """
        ...

    def record_planner_decision(
        self,
        trace: TraceHandle,
        *,
        use_case: str,
        tool_count: int,
        batch_count: int,
        latency_ms: int = 0,
    ) -> None: ...

    def record_parallel_batch(
        self,
        trace: TraceHandle,
        *,
        batch_size: int,
        batch_duration_ms: int,
    ) -> None: ...

    def record_verifier_decision(
        self,
        trace: TraceHandle,
        *,
        claims_emitted: int,
        claims_rejected: int,
        by_category: dict[str, int],
        latency_ms: int = 0,
    ) -> None: ...

    def record_identity_guard_decision(
        self,
        trace: TraceHandle,
        *,
        is_valid: bool,
        matched_pattern: str | None,
        latency_ms: int = 0,
    ) -> None: ...

    def record_verifier_span(
        self,
        trace: TraceHandle,
        *,
        latency_ms: int,
        claims_emitted: int,
        claims_rejected: int,
        by_category: dict[str, int],
    ) -> None: ...

    def record_tool_failure_detail(
        self,
        trace: TraceHandle,
        *,
        tool_name: str,
        error_type: str,
        retry_attempts: int,
        final_outcome: str,
        latency_ms: int,
    ) -> None: ...

    def record_data_quality_metrics(
        self,
        trace: TraceHandle,
        *,
        stale_labs_count: int,
        conflict_count: int,
    ) -> None: ...

    def record_route_decision(
        self,
        trace: TraceHandle,
        *,
        decision: str,
        reason: str,
        from_node: str,
        to_node: str,
        iteration: int,
    ) -> None:
        """Record one supervisor routing decision.

        Distinct from :meth:`record_handoff_span` in two ways:

        * **Appends** the decision to ``trace.route_decisions``, building
          a per-turn routing trail the eval harness can introspect
          without walking the Langfuse span store.
        * Emits as ``span`` (not ``evaluator``) and uses the
          ``route_decision:from->to`` naming so dashboards can isolate
          routing-trail spans from older handoff spans.

        All five fields are PHI-safe by construction (see the
        ``RouteDecisionRecord`` docstring).
        """
        ...

    def record_extraction_confidence(
        self,
        trace: TraceHandle,
        *,
        confidence: float,
        unsupported_fields_count: int,
    ) -> None:
        """Record the worker's self-rated extraction confidence.

        Companion to :meth:`record_extraction_call` for callers that
        observe the confidence signal *outside* the call site (e.g. a
        post-validation hook that flags low-confidence extractions for
        downstream review). The two fields are PHI-safe by construction:
        ``confidence`` is a float in [0, 1]; ``unsupported_fields_count``
        is the **length** of the worker's ``unsupported_fields`` list,
        not the field names themselves.
        """
        ...

    def record_retrieval_hits(
        self,
        trace: TraceHandle,
        *,
        bm25_count: int,
        dense_count: int,
        post_rerank_count: int,
    ) -> None:
        """Record one evidence-retrieval call's per-stage counts.

        Emitted by the W2 ``evidence_retriever_node`` whenever the RAG
        pipeline runs (W2_ARCHITECTURE.md §7 — ``retrieval_hits``).
        The three counts are PHI-safe by construction: they're sizes
        of the BM25 / dense / final-result lists, not the chunks
        themselves. Dashboards roll these up to surface "did the
        reranker actually move the needle on this turn?" without
        cracking open the chunk text.

        * ``bm25_count`` — candidates BM25 contributed (after its
          internal zero-score drop).
        * ``dense_count`` — candidates the dense retriever contributed.
        * ``post_rerank_count`` — candidates that survived rerank into
          the final result list (equals ``len(results)``).
        """
        ...

    def record_handoff_span(
        self,
        trace: TraceHandle,
        *,
        from_node: str,
        to_node: str,
        route_decision: str,
        route_reason: str,
        iteration: int,
    ) -> None:
        """Record one supervisor → worker (or supervisor → synthesize) handoff.

        Emitted by the W2 LangGraph supervisor each time it picks a
        ``RouteDecision`` for the next iteration. The five fields capture
        the graph's routing semantics without leaking PHI:

        * ``from_node`` — the node whose output triggered this routing
          decision (``"start"`` for the first supervisor pass).
        * ``to_node`` — the worker about to run, or ``"synthesize"`` /
          ``"terminal"`` on the terminating handoff.
        * ``route_decision`` — the ``RouteDecision`` value (a
          closed enum so PHI-safe by construction).
        * ``route_reason`` — short string describing the supervisor's
          rationale (e.g. ``"iteration cap reached"``,
          ``"followup: no tools needed"``). Bounded vocabulary set in
          ``orchestrator.graph._decide_route``.
        * ``iteration`` — the supervisor's iteration counter at the
          moment of the decision, bounded by ``MAX_ITERATIONS``.
        """
        ...

    def flush(self) -> None: ...

    async def aclose(self) -> None: ...
