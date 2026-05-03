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

from dataclasses import dataclass

from agentforge.observability.hmac_hash import pseudonymize
from agentforge.observability.protocols import TraceHandle


@dataclass(frozen=True)
class _NullTraceHandle:
    trace_id: str | None = None


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
        return _NULL_TRACE

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

    def record_planner_decision(
        self,
        trace: TraceHandle,
        *,
        use_case: str,
        tool_count: int,
        batch_count: int,
    ) -> None:
        del trace, use_case, tool_count, batch_count

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
    ) -> None:
        del trace, claims_emitted, claims_rejected, by_category

    def flush(self) -> None:
        return None

    async def aclose(self) -> None:
        return None
