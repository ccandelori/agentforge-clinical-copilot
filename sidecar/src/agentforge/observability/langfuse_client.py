"""Production Langfuse client for AgentForge engineering traces.

This module is the only place that talks to the Langfuse SDK. Callers
above it work with :class:`agentforge.observability.protocols.LangfuseClient`,
so swapping in the Null implementation (or any future backend) is purely
a wiring change in :func:`agentforge.main.create_app`.

Boundary discipline (ARCHITECTURE.md S7.3):
  * No raw patient/user IDs cross into the SDK — they are HMAC-pseudonymised
    here before any ``user_id`` / ``session_id`` parameter is set.
  * No raw tool args, tool results, prompts, or completions cross into
    the SDK — callers compute hashes via
    :mod:`agentforge.observability.hmac_hash` and pass digests in.
  * The Langfuse secret key is held only in memory; this module never
    logs it, formats it into messages, or includes it in span metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentforge.observability.hmac_hash import pseudonymize
from agentforge.observability.protocols import TraceHandle

if TYPE_CHECKING:
    # Imported only for type checking so the Null path can run even when
    # the langfuse package is absent (e.g. CI without the SDK installed).
    from langfuse import Langfuse


@dataclass(frozen=True)
class _LangfuseTraceHandle:
    """Concrete :class:`TraceHandle` carrying the Langfuse root span.

    The ``span`` attribute holds the SDK's root observation object. The
    Protocol accessor ``trace_id`` exposes the Langfuse trace identifier
    so callers can correlate logs without reaching into the SDK type.
    """

    trace_id: str | None
    span: Any  # langfuse._client.span.LangfuseSpan; opaque to callers


class AgentLangfuse:
    """Concrete :class:`LangfuseClient` backed by the Langfuse 4.x SDK."""

    def __init__(
        self,
        *,
        host: str,
        public_key: str,
        secret_key: str,
        hmac_key: bytes,
        environment: str | None = None,
    ) -> None:
        if not host:
            raise ValueError("Langfuse host must be a non-empty URL")
        if not public_key:
            raise ValueError("Langfuse public_key is required")
        if not secret_key:
            raise ValueError("Langfuse secret_key is required")
        if not hmac_key:
            raise ValueError("hmac_key must be non-empty bytes")

        # Imported here (not at module top) so an unconfigured deployment
        # using NullLangfuseClient never imports the SDK at all.
        from langfuse import Langfuse

        self._hmac_key = hmac_key
        self._langfuse: Langfuse = Langfuse(
            host=host,
            public_key=public_key,
            secret_key=secret_key,
            environment=environment,
        )

    def pseudonymize_id(self, raw_id: int | str) -> str:
        return pseudonymize(raw_id, self._hmac_key)

    def trace_turn(
        self,
        *,
        user_id: int | str,
        patient_id: int | str,
        breakglass_flag: bool,
        role: str | None,
    ) -> TraceHandle:
        pseudo_user = self.pseudonymize_id(user_id)
        pseudo_patient = self.pseudonymize_id(patient_id)

        # Trace-level user_id / session_id wiring lives in the SDK's
        # current-trace context, which our flat (non-context-manager) API
        # doesn't enter. Lifting the pseudonyms into span metadata still
        # gets them into Langfuse and keeps them filterable; entering the
        # current-trace context to set them as first-class trace fields
        # is a follow-up for the orchestrator-instrumentation task.
        span = self._langfuse.start_observation(
            name="agent_turn",
            as_type="agent",
            metadata={
                "user_id_pseudonym": pseudo_user,
                "patient_id_pseudonym": pseudo_patient,
                "breakglass_flag": breakglass_flag,
                "role": role,
            },
        )

        trace_id = getattr(span, "trace_id", None)
        return _LangfuseTraceHandle(trace_id=trace_id, span=span)

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
        parent = self._parent_span(trace)
        if parent is None:
            return

        span = parent.start_observation(
            name=f"tool:{tool_name}",
            as_type="tool",
            metadata={
                "tool_name": tool_name,
                "status": status,
                "latency_ms": latency_ms,
                "cache_hit": cache_hit,
                "args_hash": args_hash,
                "result_hash": result_hash,
            },
        )
        span.end()

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
        parent = self._parent_span(trace)
        if parent is None:
            return

        # Cost lives in span metadata (not usage_details) because the
        # Langfuse SDK reserves usage_details for raw token counts —
        # cost aggregation in the UI uses its own pricing math when
        # not passed in directly. Omit the key entirely when callers
        # don't supply it so legacy traces stay visually clean.
        metadata: dict[str, float | int] = {"latency_ms": latency_ms}
        if cost_usd is not None:
            metadata["cost_usd"] = cost_usd

        span = parent.start_observation(
            name=f"llm:{model}",
            as_type="generation",
            model=model,
            usage_details={
                "input": prompt_tokens,
                "output": completion_tokens,
            },
            metadata=metadata,
        )
        span.end()

    def record_planner_decision(
        self,
        trace: TraceHandle,
        *,
        use_case: str,
        tool_count: int,
        batch_count: int,
    ) -> None:
        """Emit an evaluator-style span describing the planner output.

        The values logged here are PHI-safe by construction:
        ``use_case`` is from the four-element closed enum (see
        :class:`agentforge.orchestrator.planner.UseCase`) and the counts
        describe dispatch shape, not patient content.
        """
        parent = self._parent_span(trace)
        if parent is None:
            return

        span = parent.start_observation(
            name="planner",
            as_type="evaluator",
            metadata={
                "use_case": use_case,
                "tool_count": tool_count,
                "batch_count": batch_count,
            },
        )
        span.end()

    def record_parallel_batch(
        self,
        trace: TraceHandle,
        *,
        batch_size: int,
        batch_duration_ms: int,
    ) -> None:
        """Emit a span describing one parallel-dispatch batch.

        ``batch_size`` is the number of tool calls dispatched in
        parallel and ``batch_duration_ms`` is the wall-clock time
        the ``asyncio.gather`` call took (~max(per-tool-latency)).

        Sequential-equivalent timing is intentionally NOT recorded
        here — the per-tool ``record_tool_call`` spans already carry
        each tool's latency, and the dashboard sums them to get the
        sequential estimate. Savings = sum(per-tool latencies) -
        batch_duration_ms.
        """
        parent = self._parent_span(trace)
        if parent is None:
            return

        span = parent.start_observation(
            name="parallel_batch",
            as_type="span",
            metadata={
                "batch_size": batch_size,
                "batch_duration_ms": batch_duration_ms,
            },
        )
        span.end()

    def record_verifier_decision(
        self,
        trace: TraceHandle,
        *,
        claims_emitted: int,
        claims_rejected: int,
        by_category: dict[str, int],
    ) -> None:
        parent = self._parent_span(trace)
        if parent is None:
            return

        span = parent.start_observation(
            name="verifier",
            as_type="evaluator",
            metadata={
                "claims_emitted": claims_emitted,
                "claims_rejected": claims_rejected,
                "by_category": dict(by_category),
            },
        )
        span.end()

    def flush(self) -> None:
        self._langfuse.flush()

    async def aclose(self) -> None:
        # Langfuse's shutdown flushes pending events and tears down the
        # OTel exporter thread. Wrapping in an async method matches the
        # FastAPI lifespan idiom even though the SDK call itself is sync.
        self._langfuse.shutdown()

    @staticmethod
    def _parent_span(trace: TraceHandle) -> Any:
        if isinstance(trace, _LangfuseTraceHandle):
            return trace.span
        return None
