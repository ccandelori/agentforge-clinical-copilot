"""No-op Langfuse client for tests and unconfigured deployments.

When ``LANGFUSE_HOST`` / ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY``
are not all set, the application wires :class:`NullLangfuseClient` onto
``app.state.langfuse`` instead of :class:`AgentLangfuse`. Every span
method becomes a no-op, every handle is the same shared sentinel, and
``aclose`` returns immediately. The orchestrator code stays identical
in both modes — it just calls into the Protocol.

The Null client still pseudonymises IDs through the same HMAC helper so
test code can assert against deterministic tokens without standing up a
real Langfuse instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from agentforge.observability.hmac_hash import pseudonymize
from agentforge.observability.protocols import RouteDecisionRecord, TraceHandle


@dataclass
class _NullTraceHandle:
    """Null-implementation handle.

    Carries the same ``route_decisions`` list and ``eval_outcome`` field
    as the real handle so call sites can mutate them unconditionally
    without an ``isinstance`` guard. ``trace_id`` is always ``None`` —
    the Null path doesn't talk to Langfuse, so there's no SDK
    identifier to surface.
    """

    trace_id: str | None = None
    route_decisions: list[RouteDecisionRecord] = field(default_factory=list)
    eval_outcome: str | None = None


# Module-level sentinel kept for legacy call sites that pre-existed the
# per-turn accumulator. Per-turn accumulation requires a fresh handle
# each call, so ``trace_turn`` no longer returns this constant — see
# :meth:`NullLangfuseClient.trace_turn`.
_NULL_TRACE: TraceHandle = _NullTraceHandle()


class NullLangfuseClient:
    """No-op implementation of :class:`LangfuseClient`."""

    def __init__(self, hmac_key: bytes | None = None) -> None:
        # Optional: if a key is supplied, pseudonymize_id still produces
        # real tokens so tests can verify the HMAC path end-to-end. If
        # absent, pseudonymize_id returns a deterministic placeholder so
        # callers don't crash on missing config in non-trace code paths.
        self._hmac_key = hmac_key

    def pseudonymize_id(self, raw_id: int | str) -> str:
        if self._hmac_key is None:
            return "anonymous"
        return pseudonymize(raw_id, self._hmac_key)

    def trace_turn(
        self,
        *,
        user_id: int | str,
        patient_id: int | str,
        breakglass_flag: bool,
        role: str | None,
    ) -> TraceHandle:
        del user_id, patient_id, breakglass_flag, role
        # Per-turn fresh handle — Task 27.3's accumulator semantics
        # require that ``route_decisions`` not bleed across turns.
        return _NullTraceHandle()

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
    ) -> None:
        del trace, tool_name, status, latency_ms, cache_hit, args_hash, result_hash

    def record_llm_call(
        self,
        trace: TraceHandle,
        *,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: int,
        cost_usd: float | None = None,
    ) -> None:
        del trace, model, prompt_tokens, completion_tokens, latency_ms, cost_usd

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
        del (
            trace,
            model,
            tool_name,
            input_tokens,
            output_tokens,
            latency_ms,
            schema_validation,
            page_count,
            unsupported_fields_count,
            extraction_confidence,
            cost_usd,
        )

    def record_planner_decision(
        self,
        trace: TraceHandle,
        *,
        use_case: str,
        tool_count: int,
        batch_count: int,
        latency_ms: int = 0,
    ) -> None:
        del trace, use_case, tool_count, batch_count, latency_ms

    def record_parallel_batch(
        self,
        trace: TraceHandle,
        *,
        batch_size: int,
        batch_duration_ms: int,
    ) -> None:
        del trace, batch_size, batch_duration_ms

    def record_verifier_decision(
        self,
        trace: TraceHandle,
        *,
        claims_emitted: int,
        claims_rejected: int,
        by_category: dict[str, int],
        latency_ms: int = 0,
    ) -> None:
        del trace, claims_emitted, claims_rejected, by_category, latency_ms

    def record_identity_guard_decision(
        self,
        trace: TraceHandle,
        *,
        is_valid: bool,
        matched_pattern: str | None,
        latency_ms: int = 0,
    ) -> None:
        del trace, is_valid, matched_pattern, latency_ms

    def record_verifier_span(
        self,
        trace: TraceHandle,
        *,
        latency_ms: int,
        claims_emitted: int,
        claims_rejected: int,
        by_category: dict[str, int],
    ) -> None:
        del trace, latency_ms, claims_emitted, claims_rejected, by_category

    def record_tool_failure_detail(
        self,
        trace: TraceHandle,
        *,
        tool_name: str,
        error_type: str,
        retry_attempts: int,
        final_outcome: str,
        latency_ms: int,
    ) -> None:
        del trace, tool_name, error_type, retry_attempts, final_outcome, latency_ms

    def record_data_quality_metrics(
        self,
        trace: TraceHandle,
        *,
        stale_labs_count: int,
        conflict_count: int,
    ) -> None:
        del trace, stale_labs_count, conflict_count

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
        del trace, from_node, to_node, route_decision, route_reason, iteration

    def record_retrieval_hits(
        self,
        trace: TraceHandle,
        *,
        bm25_count: int,
        dense_count: int,
        post_rerank_count: int,
    ) -> None:
        del trace, bm25_count, dense_count, post_rerank_count

    def record_extraction_confidence(
        self,
        trace: TraceHandle,
        *,
        confidence: float,
        unsupported_fields_count: int,
    ) -> None:
        del trace, confidence, unsupported_fields_count

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
        # Even the Null implementation must accumulate — eval harnesses
        # running without Langfuse still rely on ``trace.route_decisions``
        # to assert the routing path.
        if isinstance(trace, _NullTraceHandle):
            trace.route_decisions.append(
                RouteDecisionRecord(
                    decision=decision,
                    reason=reason,
                    from_node=from_node,
                    to_node=to_node,
                    iteration=iteration,
                )
            )

    def flush(self) -> None:
        return None

    async def aclose(self) -> None:
        return None
