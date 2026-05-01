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

from typing import Protocol, runtime_checkable


@runtime_checkable
class TraceHandle(Protocol):
    """Opaque handle returned by :meth:`LangfuseClient.trace_turn`.

    Concrete implementations may carry an SDK trace object, a span
    context, or nothing at all (Null implementation). Callers should
    not introspect the handle — pass it back to the client's span
    methods to attach child observations.
    """

    @property
    def trace_id(self) -> str | None:
        """Stable trace identifier, or ``None`` for the Null implementation."""
        ...


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
    ) -> None: ...

    def record_verifier_decision(
        self,
        trace: TraceHandle,
        *,
        claims_emitted: int,
        claims_rejected: int,
        by_category: dict[str, int],
    ) -> None: ...

    def flush(self) -> None: ...

    async def aclose(self) -> None: ...
