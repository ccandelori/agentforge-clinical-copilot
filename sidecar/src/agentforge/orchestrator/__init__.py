"""Tool-calling orchestrator with optional verifier wiring.

The MVP loop is a tight tool-use → tool_result cycle: ask the model,
dispatch any tool calls, feed the results back, repeat until the model
emits a final text response. See ARCHITECTURE.md §3 for the full
Planner / ToolDispatcher / Synthesizer / Verifier vision; what's here
is the "everything in one node" simplification.

When ``verifier_enabled=True`` the final assistant text is gated through
:class:`StreamingVerifier` (Task 28) before being returned. The
verifier builds its per-turn citation cache from the structured
``ToolResult`` objects this loop collected, then redacts any sentence
that doesn't ground in that cache. Substance checks plug in via the
:class:`DomainConstraintChecker` from Task 29.

Production wiring keeps the verifier on; the default-off flag preserves
the legacy behavior the test suite was originally written against.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import ContextVar
from typing import Any, Final

from agentforge.breakglass import BreakglassAuditTool
from agentforge.gateway.auth_gateway import RequestContext
from agentforge.llm.client import LLMClient
from agentforge.llm.types import (
    LLMResponse,
    Message,
    StreamEvent,
    StreamFinal,
    StreamTextDelta,
    ToolCall,
)
from agentforge.observability.cost import calculate_cost
from agentforge.observability.hmac_hash import hash_payload
from agentforge.observability.protocols import LangfuseClient, TraceHandle
from agentforge.orchestrator.identity_guard import IdentityGuard
from agentforge.orchestrator.memory import HARD_CAP, ConversationMemory
from agentforge.orchestrator.planner import Planner
from agentforge.orchestrator.truncation import SynthesisInputTruncator
from agentforge.prompts import load_prompt
from agentforge.storage.redis_client import AgentRedisClient
from agentforge.timeouts import (
    GracefulDegradation,
    RetryPolicy,
    TimeoutPolicy,
    classify_http_error,
    retry_with_policy,
)
from agentforge.tools.allergies import (
    ALLERGIES_TOOL_SPEC,
    AllergiesFetcher,
    AllergiesResult,
)
from agentforge.tools.demographics import (
    DEMOGRAPHICS_TOOL_SPEC,
    DemographicsFetcher,
    DemographicsResult,
)
from agentforge.tools.dtos import ToolResult
from agentforge.tools.encounters import (
    ENCOUNTERS_TOOL_SPEC,
    EncountersFetcher,
    EncountersResult,
)
from agentforge.tools.immunizations import (
    IMMUNIZATIONS_TOOL_SPEC,
    ImmunizationsFetcher,
    ImmunizationsResult,
)
from agentforge.tools.labs import LABS_TOOL_SPEC, LabsFetcher, LabsResult
from agentforge.tools.medications import (
    MEDICATIONS_TOOL_SPEC,
    MedicationsFetcher,
    MedicationsResult,
)
from agentforge.tools.notes import NOTES_TOOL_SPEC, NotesFetcher, NotesResult
from agentforge.tools.problems import (
    PROBLEMS_TOOL_SPEC,
    ProblemsFetcher,
    ProblemsResult,
)
from agentforge.tools.procedures import (
    PROCEDURES_TOOL_SPEC,
    ProceduresFetcher,
    ProceduresResult,
)
from agentforge.tools.search_notes import (
    SEARCH_NOTES_TOOL_SPEC,
    SearchNotesFetcher,
    SearchNotesResult,
)
from agentforge.tools.vitals import VITALS_TOOL_SPEC, VitalsFetcher, VitalsResult
from agentforge.verifier import (
    DomainConstraintChecker,
    NullDomainConstraintChecker,
    StreamingVerifier,
    build_citation_index,
)
from agentforge.verifier.data_quality import DataQualityChecker

# The model name we report on Langfuse spans. The actual SDK model
# string is decided by the LLMClient implementation; surfacing a
# stable label here keeps trace dashboards readable across provider
# swaps without hard-coding the SDK constant.
_TRACE_MODEL: Final[str] = "claude-sonnet-4-5"

# Returned to the user when a session has hit HARD_CAP. The reply is
# clinical-neutral and instructs the user how to recover (start a new
# encounter session) without leaking the session_id or any prior PHI.
_SESSION_REFUSAL_TEXT: Final[str] = (
    "This conversation has reached its session length limit. "
    "Please start a new chat session for further questions about this patient."
)

# Returned when the per-turn ``total_turn`` budget elapses (week1-gaps
# Task #8). The reply is intentionally generic — a more diagnostic
# message ("we got 3 of 5 tool results before the timer fired") could
# leak which tools the orchestrator chose to dispatch, which is a
# weak side channel onto the agent's planner. Better to keep the text
# stable and rely on Langfuse traces for the per-tool postmortem.
_TURN_BUDGET_EXCEEDED_TEXT: Final[str] = (
    "This response is taking longer than expected. "
    "Please try again or simplify your question."
)

# Loaded once from prompts/<active>/synthesizer.md. The body lives in
# the versioned prompt library at the repo root so future edits land as
# reviewable text diffs; see prompts/README.md.
SYSTEM_PROMPT: Final[str] = load_prompt("synthesizer")

MAX_TOOL_ITERATIONS: Final[int] = 4

# Per-turn USD cost accumulator. Populated by ``_record_llm_call`` and
# read by the /turn endpoint to set the X-Agent-Cost-USD response
# header. Stored as a :class:`contextvars.ContextVar` so concurrent
# /turn requests on the same orchestrator instance don't clobber each
# other's totals — each asyncio task gets its own value via PEP 567
# context propagation. ``Orchestrator.turn`` resets to 0.0 at the
# start of every call.
_TURN_COST_VAR: ContextVar[float] = ContextVar(
    "agentforge_turn_cost_usd", default=0.0
)


def get_turn_cost_usd() -> float:
    """Return the accumulated LLM cost for the current asyncio task.

    Resets to 0.0 at the start of each :meth:`Orchestrator.turn`
    invocation, so callers should read this AFTER ``turn`` returns
    and BEFORE awaiting any other code that might issue a new turn
    in the same task.
    """
    return _TURN_COST_VAR.get()


class Orchestrator:
    def __init__(
        self,
        llm: LLMClient,
        demographics_fetcher: DemographicsFetcher,
        medications_fetcher: MedicationsFetcher,
        problems_fetcher: ProblemsFetcher,
        allergies_fetcher: AllergiesFetcher,
        labs_fetcher: LabsFetcher,
        vitals_fetcher: VitalsFetcher,
        notes_fetcher: NotesFetcher,
        search_notes_fetcher: SearchNotesFetcher,
        encounters_fetcher: EncountersFetcher,
        immunizations_fetcher: ImmunizationsFetcher,
        procedures_fetcher: ProceduresFetcher,
        *,
        domain_constraints: DomainConstraintChecker | None = None,
        verifier_enabled: bool = False,
        langfuse: LangfuseClient | None = None,
        hmac_key: bytes | None = None,
        redis_storage: AgentRedisClient | None = None,
        memory: ConversationMemory | None = None,
        timeout_policy: TimeoutPolicy | None = None,
        retry_policy: RetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        breakglass_audit: BreakglassAuditTool | None = None,
        planner: Planner | None = None,
        truncator: SynthesisInputTruncator | None = None,
        data_quality: DataQualityChecker | None = None,
        identity_guard_enabled: bool = False,
    ) -> None:
        self._llm = llm
        self._demographics = demographics_fetcher
        self._medications = medications_fetcher
        self._problems = problems_fetcher
        self._allergies = allergies_fetcher
        self._labs = labs_fetcher
        self._vitals = vitals_fetcher
        self._notes = notes_fetcher
        self._search_notes = search_notes_fetcher
        self._encounters = encounters_fetcher
        self._immunizations = immunizations_fetcher
        self._procedures = procedures_fetcher
        self._domain_constraints = (
            domain_constraints or NullDomainConstraintChecker()
        )
        self._verifier_enabled = verifier_enabled
        self._langfuse = langfuse
        self._hmac_key = hmac_key
        self._redis_storage = redis_storage
        self._memory = memory
        self._timeout_policy = timeout_policy or TimeoutPolicy()
        self._retry_policy = retry_policy or RetryPolicy()
        self._sleep = sleep
        self._breakglass_audit = breakglass_audit
        # Optional planner. When set, ``turn()`` (subtask 4.3) calls
        # ``planner.plan(user_message)`` before the tool loop and uses
        # the resulting ``Plan`` to seed dispatch. ``None`` keeps the
        # legacy "let the model pick tools as it goes" path intact.
        self._planner = planner

        # Optional synthesis-input truncator. Wired here so
        # collaborators can inject one (week1-gaps #6); behavioral
        # integration is intentionally deferred — the iterative
        # tool-use loop has no separate "synthesis input" boundary
        # for the truncator to gate. Behavioral effect comes online
        # with the streaming refactor (#11/#13) where the synthesis
        # call separates from the tool loop. See DEVIATIONS.md.
        self._truncator = truncator

        # Optional data-quality checker (week1-gaps #7). When set,
        # ``turn()`` runs stale-lab + problem/note-conflict heuristics
        # over the per-turn ``tool_results`` after the model's final
        # response and appends compact warnings to the user-visible
        # text. The orchestrator's iterative tool-use loop has no
        # separate "synthesis input" seam, so the checks run
        # post-final-text rather than the planner-driven "before final
        # LLM call" placement in ARCHITECTURE.md §3 — see DEVIATIONS.md
        # 2026-05-02 for the same reason the truncator deferred.
        self._data_quality = data_quality

        # IdentityGuard wiring toggle (week1-gaps #7). When True,
        # ``turn()`` synchronously fetches demographics before the
        # tool loop, constructs an :class:`IdentityGuard` bound to
        # the chart owner's name + MRN-fallback, and checks the user
        # message. A cross-patient reference short-circuits the turn
        # with the guard's refusal text. The pre-fetched demographics
        # are stashed in the per-turn cache so the model's first
        # iteration sees a cache hit if it asks for the same tool.
        # Disabled by default so the existing test fixtures (which
        # don't stub demographics) keep working unchanged.
        self._identity_guard_enabled = identity_guard_enabled

    async def turn(
        self,
        ctx: RequestContext,
        user_message: str,
        *,
        session_id: str | None = None,
    ) -> str:
        """Run one user turn through the model + tools, return final text.

        ``session_id`` is consulted only when a :class:`ConversationMemory`
        is configured. When given, prior turns are loaded as message
        history and the user/assistant pair is persisted after the
        model finishes. The hard cap from memory.py becomes a refusal
        before the model is invoked at all.
        """
        # Reset the per-turn cost accumulator so callers reading
        # ``get_turn_cost_usd()`` after this turn returns see only the
        # USD spent in THIS turn, not lingering value from a prior
        # turn that ran on the same asyncio task. Set unconditionally
        # — refusals (HARD_CAP, etc) cost zero by default.
        _TURN_COST_VAR.set(0.0)

        # Total-turn budget enforcement (week1-gaps Task #8). We use
        # ``asyncio.timeout`` (Python 3.11+) rather than wrapping
        # ``_turn_inner`` in ``asyncio.wait_for`` so the call runs in
        # the SAME asyncio task — that keeps the per-turn cost
        # ``ContextVar`` mutations visible to the /turn endpoint
        # reading ``get_turn_cost_usd()`` after we return. Catching
        # the TimeoutError here means a runaway turn surfaces as a
        # generic graceful-degradation reply, never an unhandled
        # cancellation back through FastAPI.
        try:
            async with asyncio.timeout(self._timeout_policy.total_turn):
                return await self._turn_inner(
                    ctx, user_message, session_id=session_id
                )
        except TimeoutError:
            return _TURN_BUDGET_EXCEEDED_TEXT

    async def _turn_inner(
        self,
        ctx: RequestContext,
        user_message: str,
        *,
        session_id: str | None,
    ) -> str:
        """Body of :meth:`turn` minus the total-turn timeout wrapper.

        Pulled out so the outer wrapper stays tight and so future
        callers (e.g. a streaming variant) can reuse the same
        per-turn machinery without re-implementing the timeout
        envelope.
        """
        # Reject up front when the session has already hit its hard cap;
        # we never call the model on a refused turn.
        if self._memory is not None and session_id is not None:
            existing = await self._memory.get_memory(session_id)
            if len(existing) // 2 >= HARD_CAP:
                return _SESSION_REFUSAL_TEXT

        # Breakglass audit fires once per session (the tool dedups
        # internally). Best-effort: outcome is logged inside the tool;
        # we don't gate the turn on its result.
        if self._breakglass_audit is not None:
            await self._breakglass_audit.log_breakglass_access(
                ctx, session_id=session_id
            )

        # Planner runs ONCE per turn, before the tool loop. The agent
        # loop below still does its own tool selection — the plan's
        # use_case rides on the trace (this subtask) and #5 will
        # consume ``plan.parallel_batches`` to seed dispatch.
        # Skipped entirely when no planner is wired (the legacy path
        # the test suite was originally written against).
        #
        # Cost gap (carryforward): this LLM call is NOT yet routed
        # through ``_record_llm_call``. The Planner consumes its own
        # LLMClient and doesn't surface token counts, so the per-turn
        # cost ContextVar undercounts by the planner's contribution
        # (small system prompt + 1024-cap output, ~$0.005 per turn
        # with claude-sonnet-4-5). Address before #20 enables the
        # planner by default — otherwise dashboards understate cost.
        plan = await self._planner.plan(user_message) if self._planner else None

        # Per-turn tool-result accumulator (declared here so the
        # IdentityGuard pre-fetch below can pre-populate it before the
        # main loop runs). Keyed by tool name; later iterations of the
        # same tool overwrite — acceptable for MVP because the
        # catalogue is idempotent reads.
        tool_results: dict[str, ToolResult[Any]] = {}
        trace = self._open_trace(ctx)

        # Planner ran above; surface its classification on the trace so
        # cohort filters in Langfuse can split metrics by use_case
        # (admit_synthesis vs contraindication etc) without mining
        # turn payloads. tool_count + batch_count describe dispatch
        # shape only — no PHI leaves this call.
        if plan is not None:
            self._record_planner_decision(
                trace,
                use_case=plan.use_case.value,
                tool_count=len(plan.tool_calls),
                batch_count=len(plan.parallel_batches),
            )

        # IdentityGuard (week1-gaps #7). Demographics-first: fetch
        # synchronously so we have the chart-owner's name to bind the
        # guard to (Option B, see task spec). Fail-skip if demographics
        # are unavailable — the real auth boundary is the tool layer
        # (RequestContext.patient_id is bound), and IdentityGuard's
        # own docstring positions it as a usability layer, not a
        # security one.
        if self._identity_guard_enabled:
            demo_result = await self._safe_fetch_demographics(ctx)
            if demo_result is not None:
                # Stash the pre-fetch in per-turn tool_results so the
                # verifier's citation index sees it AND prime the redis
                # cache so a redundant model-issued get_demographics
                # short-circuits to a cache hit.
                tool_results["get_demographics"] = demo_result
                await self._maybe_cache_set(
                    ctx,
                    "get_demographics",
                    self._hash_args({}),
                    demo_result,
                )

                guard = self._build_identity_guard(ctx, demo_result)
                check = guard.check_message(user_message)
                self._record_identity_guard_decision(
                    trace,
                    is_valid=check.is_valid,
                    matched_pattern=check.matched_pattern,
                )
                if not check.is_valid:
                    # check.refusal_reason is non-None when is_valid is
                    # False; assert it explicitly for the type checker
                    # rather than reaching for a cast.
                    assert check.refusal_reason is not None
                    await self._maybe_persist_turn(
                        session_id, user_message, check.refusal_reason
                    )
                    return check.refusal_reason

        messages: list[Message] = []
        if self._memory is not None and session_id is not None:
            for entry in await self._memory.get_memory(session_id):
                role_raw = entry.get("role")
                content_raw = entry.get("content")
                role = role_raw if isinstance(role_raw, str) else "user"
                content = content_raw if isinstance(content_raw, str) else ""
                # Persisted memory only contains user/assistant turns —
                # tool_use/tool_result frames are intentionally not
                # rehydrated; the model re-fetches as needed for the
                # new question.
                if role in ("user", "assistant"):
                    messages.append(Message(role=role, content=content))  # type: ignore[arg-type]

        messages.append(Message(role="user", content=user_message))
        tools = [
            DEMOGRAPHICS_TOOL_SPEC,
            PROBLEMS_TOOL_SPEC,
            MEDICATIONS_TOOL_SPEC,
            ALLERGIES_TOOL_SPEC,
            LABS_TOOL_SPEC,
            VITALS_TOOL_SPEC,
            NOTES_TOOL_SPEC,
            SEARCH_NOTES_TOOL_SPEC,
            ENCOUNTERS_TOOL_SPEC,
            IMMUNIZATIONS_TOOL_SPEC,
            PROCEDURES_TOOL_SPEC,
        ]
        # Names of tools whose retries all timed out this turn. Surface
        # at the end as a graceful-degradation notice so the user
        # knows the response is incomplete.
        timed_out_tools: list[str] = []

        for _ in range(MAX_TOOL_ITERATIONS):
            llm_start = time.perf_counter()
            # Synthesis-phase budget (week1-gaps Task #8). A runaway
            # streaming response or a single very-large completion
            # mustn't burn the whole total_turn budget on one
            # iteration. On timeout we let the error propagate to the
            # outer ``async with asyncio.timeout(total_turn)`` handler
            # in ``turn()`` rather than handle it locally — the cost
            # of an in-flight LLM call going past synthesis_phase is
            # almost always close to total_turn anyway, and a generic
            # "taking too long" reply is more honest than partial
            # output.
            async with asyncio.timeout(self._timeout_policy.synthesis_phase):
                response = await self._llm.complete(
                    system=SYSTEM_PROMPT,
                    messages=messages,
                    tools=tools,
                    max_tokens=1024,
                )
            self._record_llm_call(
                trace,
                latency_ms=_elapsed_ms(llm_start),
                prompt_tokens=response.input_tokens,
                completion_tokens=response.output_tokens,
            )

            messages.append(
                Message(
                    role="assistant",
                    content=response.text,
                    tool_calls=response.tool_calls if response.tool_calls else None,
                )
            )

            if response.stop_reason != "tool_use" or not response.tool_calls:
                if not response.text:
                    return "(no response)"
                if not self._verifier_enabled:
                    final_text = response.text
                else:
                    final_text = await self._verify_response(
                        response.text, tool_results, trace
                    )
                # Data quality runs AFTER the verifier (when on) so its
                # appended warnings aren't redacted for missing
                # citations — the warnings are orchestrator-emitted
                # meta-content, not model claims that need grounding.
                final_text = self._apply_data_quality(
                    final_text, tool_results, trace
                )
                final_text = self._append_degradation_notice(
                    final_text, timed_out_tools
                )
                await self._maybe_persist_turn(
                    session_id, user_message, final_text
                )
                return final_text

            # Dispatch every tool the LLM asked for in this iteration
            # concurrently. The previous implementation looped
            # _dispatch one-at-a-time, which spent
            # sum(per-tool-latency) waiting on independent fetches
            # whose dependencies were already resolved (the LLM
            # decided to call them all in one shot, so it considers
            # them mutually independent). Using _dispatch_batch
            # collapses that wait to max(per-tool-latency).
            #
            # Result list comes back in input order (asyncio.gather
            # guarantee), so zipping with response.tool_calls is safe.
            #
            # Tool-phase budget (week1-gaps Task #8). On batch
            # timeout, all in-flight per-tool calls are cancelled by
            # the wrapping ``asyncio.timeout`` and we synthesize
            # ``tool_phase_timeout`` error payloads in input order.
            # Each tool name lands in ``timed_out_tools`` so the
            # final reply carries a graceful-degradation notice.
            batch_start = time.perf_counter()
            try:
                async with asyncio.timeout(self._timeout_policy.tool_phase):
                    batch_results = await self._dispatch_batch(
                        ctx, list(response.tool_calls), trace, timed_out_tools
                    )
            except TimeoutError:
                batch_results = []
                for call in response.tool_calls:
                    if call.name not in timed_out_tools:
                        timed_out_tools.append(call.name)
                    self._record_tool_call(
                        trace,
                        tool_name=call.name,
                        status="tool_phase_timeout",
                        latency_ms=_elapsed_ms(batch_start),
                        cache_hit=False,
                        args_hash=self._hash_args(call.input),
                        result_hash=None,
                    )
                    batch_results.append(
                        (
                            json.dumps(
                                {
                                    "error": "tool_phase_timeout",
                                    "tool": call.name,
                                }
                            ),
                            None,
                        )
                    )
            self._record_parallel_batch(
                trace,
                batch_size=len(response.tool_calls),
                batch_duration_ms=_elapsed_ms(batch_start),
            )
            for call, (content_json, result) in zip(
                response.tool_calls, batch_results, strict=True
            ):
                if result is not None:
                    tool_results[call.name] = result
                messages.append(
                    Message(
                        role="tool",
                        tool_call_id=call.id,
                        content=content_json,
                    )
                )

        return "(orchestrator hit max tool iterations without a final answer)"

    # ----------- Streaming variant -----------

    async def stream_turn(
        self,
        ctx: RequestContext,
        user_message: str,
        *,
        session_id: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Streaming variant of :meth:`turn`.

        Yields :class:`StreamTextDelta` events as the model emits text
        and exactly one terminal :class:`StreamFinal` carrying the
        fully-assembled :class:`LLMResponse`. Wire-format choice (SSE,
        WebSocket, gRPC) is the caller's job; this method speaks
        provider-agnostic events.

        Mirrors :meth:`turn` for safety features (hard-cap session
        refusal, breakglass audit, planner classification,
        IdentityGuard, DataQuality warnings, total_turn / synthesis_phase
        / tool_phase budget enforcement) — only the synthesis surface
        differs. The verifier-before-emit gate (week1-gaps Task #13)
        wraps the deltas BEFORE they reach the consumer; until that
        ships, the ``streaming_enabled`` setting in
        :class:`agentforge.config.Settings` stays off so unverified
        clinical text never reaches the wire in production.

        Same-task ``asyncio.timeout`` keeps the per-turn cost
        ContextVar visible to /turn after the iterator drains.
        """
        # Reset cost var unconditionally — refusals / budget overruns
        # cost zero by default. Done OUTSIDE the timeout block so the
        # zero shows even when the body raises immediately.
        _TURN_COST_VAR.set(0.0)

        try:
            async with asyncio.timeout(self._timeout_policy.total_turn):
                async for event in self._stream_turn_inner(
                    ctx, user_message, session_id=session_id
                ):
                    yield event
        except TimeoutError:
            # Outer envelope fired. Yield a synthetic terminal pair so
            # SSE consumers see one delta + one final regardless of
            # whether the inner loop had already streamed anything.
            # The wire shape stays "deltas... then exactly one final."
            yield StreamTextDelta(text=_TURN_BUDGET_EXCEEDED_TEXT)
            yield StreamFinal(
                response=LLMResponse(
                    text=_TURN_BUDGET_EXCEEDED_TEXT,
                    tool_calls=[],
                    stop_reason="budget_exceeded",
                    input_tokens=0,
                    output_tokens=0,
                )
            )

    async def _stream_turn_inner(
        self,
        ctx: RequestContext,
        user_message: str,
        *,
        session_id: str | None,
    ) -> AsyncIterator[StreamEvent]:
        """Body of :meth:`stream_turn` minus the total-turn timeout
        wrapper. Same-task generator so cost/trace ContextVars stay
        consistent with :meth:`_turn_inner`.
        """
        # Hard-cap session refusal — single delta + final.
        if self._memory is not None and session_id is not None:
            existing = await self._memory.get_memory(session_id)
            if len(existing) // 2 >= HARD_CAP:
                yield StreamTextDelta(text=_SESSION_REFUSAL_TEXT)
                yield StreamFinal(
                    response=LLMResponse(
                        text=_SESSION_REFUSAL_TEXT,
                        tool_calls=[],
                        stop_reason="session_refusal",
                        input_tokens=0,
                        output_tokens=0,
                    )
                )
                return

        if self._breakglass_audit is not None:
            await self._breakglass_audit.log_breakglass_access(
                ctx, session_id=session_id
            )

        plan = await self._planner.plan(user_message) if self._planner else None

        tool_results: dict[str, ToolResult[Any]] = {}
        trace = self._open_trace(ctx)

        if plan is not None:
            self._record_planner_decision(
                trace,
                use_case=plan.use_case.value,
                tool_count=len(plan.tool_calls),
                batch_count=len(plan.parallel_batches),
            )

        if self._identity_guard_enabled:
            demo_result = await self._safe_fetch_demographics(ctx)
            if demo_result is not None:
                tool_results["get_demographics"] = demo_result
                await self._maybe_cache_set(
                    ctx,
                    "get_demographics",
                    self._hash_args({}),
                    demo_result,
                )

                guard = self._build_identity_guard(ctx, demo_result)
                check = guard.check_message(user_message)
                self._record_identity_guard_decision(
                    trace,
                    is_valid=check.is_valid,
                    matched_pattern=check.matched_pattern,
                )
                if not check.is_valid:
                    assert check.refusal_reason is not None
                    await self._maybe_persist_turn(
                        session_id, user_message, check.refusal_reason
                    )
                    yield StreamTextDelta(text=check.refusal_reason)
                    yield StreamFinal(
                        response=LLMResponse(
                            text=check.refusal_reason,
                            tool_calls=[],
                            stop_reason="identity_guard_refusal",
                            input_tokens=0,
                            output_tokens=0,
                        )
                    )
                    return

        messages: list[Message] = []
        if self._memory is not None and session_id is not None:
            for entry in await self._memory.get_memory(session_id):
                role_raw = entry.get("role")
                content_raw = entry.get("content")
                role = role_raw if isinstance(role_raw, str) else "user"
                content = content_raw if isinstance(content_raw, str) else ""
                if role in ("user", "assistant"):
                    messages.append(Message(role=role, content=content))  # type: ignore[arg-type]

        messages.append(Message(role="user", content=user_message))
        tools = [
            DEMOGRAPHICS_TOOL_SPEC,
            PROBLEMS_TOOL_SPEC,
            MEDICATIONS_TOOL_SPEC,
            ALLERGIES_TOOL_SPEC,
            LABS_TOOL_SPEC,
            VITALS_TOOL_SPEC,
            NOTES_TOOL_SPEC,
            SEARCH_NOTES_TOOL_SPEC,
            ENCOUNTERS_TOOL_SPEC,
            IMMUNIZATIONS_TOOL_SPEC,
            PROCEDURES_TOOL_SPEC,
        ]
        timed_out_tools: list[str] = []

        for _ in range(MAX_TOOL_ITERATIONS):
            llm_start = time.perf_counter()
            iter_response: LLMResponse | None = None
            iter_text_buffer: list[str] = []

            # Synthesis-phase budget per iteration. On timeout the
            # inner stream's tasks are cancelled; the outer
            # total_turn handler in :meth:`stream_turn` catches
            # ``TimeoutError`` and emits the budget-exceeded events.
            async with asyncio.timeout(self._timeout_policy.synthesis_phase):
                if self._verifier_enabled:
                    # Verifier-before-emit gate (week1-gaps Task #13).
                    # Tokens are piped through StreamingVerifier before
                    # reaching the consumer.  Each VerifiedChunk is either
                    # the original sentence (verified) or REJECTION_MARKER
                    # (ungrounded).  The citation index is built from the
                    # tool_results collected so far this turn so synthesis
                    # sentences can only cite records the model actually
                    # fetched.
                    _final_holder: list[LLMResponse] = []

                    async def _token_source() -> AsyncIterator[str]:
                        async for event in self._llm.stream(
                            system=SYSTEM_PROMPT,
                            messages=messages,
                            tools=tools,
                            max_tokens=1024,
                        ):
                            if isinstance(event, StreamTextDelta):
                                iter_text_buffer.append(event.text)
                                yield event.text
                            elif isinstance(event, StreamFinal):
                                _final_holder.append(event.response)

                    _index = build_citation_index(tool_results)
                    _verifier = StreamingVerifier(
                        citation_index=_index,
                        domain_checker=self._domain_constraints,
                    )
                    _verified_parts: list[str] = []
                    async for _chunk in _verifier.verify_stream(_token_source()):
                        _verified_parts.append(_chunk.text)
                        yield StreamTextDelta(text=_chunk.text)

                    if _final_holder:
                        _raw = _final_holder[0]
                        iter_response = (
                            _raw.model_copy(
                                update={"text": "".join(_verified_parts)}
                            )
                            if _verified_parts
                            else _raw
                        )
                else:
                    async for event in self._llm.stream(
                        system=SYSTEM_PROMPT,
                        messages=messages,
                        tools=tools,
                        max_tokens=1024,
                    ):
                        if isinstance(event, StreamTextDelta):
                            iter_text_buffer.append(event.text)
                            yield event
                        elif isinstance(event, StreamFinal):
                            iter_response = event.response

            if iter_response is None:
                # Defensive: LLM.stream() contract guarantees one
                # StreamFinal. If the implementation drops it (or the
                # stream was cancelled mid-flight), surface what we
                # have rather than dangling.
                yield StreamFinal(
                    response=LLMResponse(
                        text="".join(iter_text_buffer),
                        tool_calls=[],
                        stop_reason="incomplete_stream",
                        input_tokens=0,
                        output_tokens=0,
                    )
                )
                return

            self._record_llm_call(
                trace,
                latency_ms=_elapsed_ms(llm_start),
                prompt_tokens=iter_response.input_tokens,
                completion_tokens=iter_response.output_tokens,
            )

            messages.append(
                Message(
                    role="assistant",
                    content=iter_response.text,
                    tool_calls=iter_response.tool_calls
                    if iter_response.tool_calls
                    else None,
                )
            )

            if (
                iter_response.stop_reason != "tool_use"
                or not iter_response.tool_calls
            ):
                # Final iteration. Append data-quality warnings + any
                # graceful-degradation notice as additional deltas so
                # the consumer sees the full assistant text inline.
                final_text = iter_response.text

                dq_suffix = self._data_quality_suffix(tool_results, trace)
                if dq_suffix:
                    yield StreamTextDelta(text=dq_suffix)
                    final_text += dq_suffix

                degradation = GracefulDegradation.format_degradation_notice(
                    timed_out_tools
                )
                if degradation:
                    notice = f"\n\n{degradation}"
                    yield StreamTextDelta(text=notice)
                    final_text += notice

                await self._maybe_persist_turn(
                    session_id, user_message, final_text
                )

                yield StreamFinal(
                    response=LLMResponse(
                        text=final_text,
                        tool_calls=iter_response.tool_calls,
                        stop_reason=iter_response.stop_reason,
                        input_tokens=iter_response.input_tokens,
                        output_tokens=iter_response.output_tokens,
                    )
                )
                return

            # Tool dispatch (non-streaming). Same shape as
            # :meth:`_turn_inner` including the tool-phase timeout +
            # partial-result fallback.
            batch_start = time.perf_counter()
            try:
                async with asyncio.timeout(self._timeout_policy.tool_phase):
                    batch_results = await self._dispatch_batch(
                        ctx,
                        list(iter_response.tool_calls),
                        trace,
                        timed_out_tools,
                    )
            except TimeoutError:
                batch_results = []
                for call in iter_response.tool_calls:
                    if call.name not in timed_out_tools:
                        timed_out_tools.append(call.name)
                    self._record_tool_call(
                        trace,
                        tool_name=call.name,
                        status="tool_phase_timeout",
                        latency_ms=_elapsed_ms(batch_start),
                        cache_hit=False,
                        args_hash=self._hash_args(call.input),
                        result_hash=None,
                    )
                    batch_results.append(
                        (
                            json.dumps(
                                {
                                    "error": "tool_phase_timeout",
                                    "tool": call.name,
                                }
                            ),
                            None,
                        )
                    )
            self._record_parallel_batch(
                trace,
                batch_size=len(iter_response.tool_calls),
                batch_duration_ms=_elapsed_ms(batch_start),
            )
            for call, (content_json, result) in zip(
                iter_response.tool_calls, batch_results, strict=True
            ):
                if result is not None:
                    tool_results[call.name] = result
                messages.append(
                    Message(
                        role="tool",
                        tool_call_id=call.id,
                        content=content_json,
                    )
                )

        # Max-iterations exhausted without a non-tool-use response.
        # Mirror :meth:`_turn_inner`'s degenerate-error message but in
        # streaming shape.
        max_iter_text = (
            "(orchestrator hit max tool iterations without a final answer)"
        )
        yield StreamTextDelta(text=max_iter_text)
        yield StreamFinal(
            response=LLMResponse(
                text=max_iter_text,
                tool_calls=[],
                stop_reason="max_iterations",
                input_tokens=0,
                output_tokens=0,
            )
        )

    async def _verify_response(
        self,
        text: str,
        tool_results: dict[str, ToolResult[Any]],
        trace: TraceHandle | None,
    ) -> str:
        """Run StreamingVerifier over ``text`` and return the gated reply."""
        index = build_citation_index(tool_results)
        verifier = StreamingVerifier(
            citation_index=index, domain_checker=self._domain_constraints
        )

        async def _stream() -> Any:
            yield text

        chunks: list[str] = []
        emitted = 0
        rejected = 0
        by_category: Counter[str] = Counter()
        async for chunk in verifier.verify_stream(_stream()):
            chunks.append(chunk.text)
            emitted += 1
            if not chunk.verified:
                rejected += 1
                by_category[chunk.rejection_reason or "unknown"] += 1
        self._record_verifier_decision(
            trace,
            claims_emitted=emitted,
            claims_rejected=rejected,
            by_category=dict(by_category),
        )
        return "".join(chunks)

    @staticmethod
    def _append_degradation_notice(
        text: str, timed_out_tools: list[str]
    ) -> str:
        notice = GracefulDegradation.format_degradation_notice(timed_out_tools)
        if not notice:
            return text
        return f"{text}\n\n{notice}"

    async def _call_with_retry(
        self,
        fetcher_call: Callable[[], Awaitable[ToolResult[Any]]],
    ) -> ToolResult[Any]:
        """Run ``fetcher_call`` under the configured retry + per-tool budget.

        Centralized so each tool branch in :meth:`_dispatch` stays a
        one-liner and the policy is consistent across every fetcher.
        """
        return await retry_with_policy(
            fetcher_call,
            policy=self._retry_policy,
            total_budget=self._timeout_policy.per_tool,
            sleep=self._sleep,
        )

    async def _dispatch_batch(
        self,
        ctx: RequestContext,
        calls: list[ToolCall],
        trace: TraceHandle | None,
        timed_out_tools: list[str],
    ) -> list[tuple[str, ToolResult[Any] | None]]:
        """Run a list of tool calls in parallel and return results
        in input order.

        Each call goes through the same ``_dispatch`` path the
        sequential loop uses — same retry policy, same cache, same
        trace spans — so per-tool behavior is unchanged. The win is
        that wall-clock time becomes ``max(latency)`` rather than
        ``sum(latency)`` across the batch.

        Empty batches short-circuit without calling
        :func:`asyncio.gather` so a no-op batch can never accidentally
        emit a span or trip a side-effect.
        """
        if not calls:
            return []
        return await asyncio.gather(
            *(
                self._dispatch(ctx, call, trace, timed_out_tools)
                for call in calls
            )
        )

    async def _dispatch(
        self,
        ctx: RequestContext,
        call: ToolCall,
        trace: TraceHandle | None,
        timed_out_tools: list[str],
    ) -> tuple[str, ToolResult[Any] | None]:
        """Run one tool call.

        Returns ``(json_for_model, result_or_none)``: the JSON the
        assistant message will carry, plus the typed ``ToolResult`` so
        the caller can stash it in the per-turn citation cache. On
        error the second element is ``None`` and the model sees a
        structured error payload.
        """
        tool_name = call.name
        args_hash = self._hash_args(call.input)
        start = time.perf_counter()

        # Try the per-turn Redis cache first when configured. A hit
        # short-circuits the fetcher entirely; the cached payload still
        # rides the model history identically.
        cache_hit_result = await self._maybe_cache_get(
            ctx, tool_name, args_hash
        )
        if cache_hit_result is not None:
            payload_json = cache_hit_result.model_dump_json()
            self._record_tool_call(
                trace,
                tool_name=tool_name,
                status="ok",
                latency_ms=_elapsed_ms(start),
                cache_hit=True,
                args_hash=args_hash,
                result_hash=self._hash_payload_str(payload_json),
            )
            return payload_json, cache_hit_result

        try:
            result: ToolResult[Any]
            if tool_name == "get_demographics":
                result = await self._call_with_retry(
                    lambda: self._demographics.fetch(
                        patient_id=ctx.patient_id, raw_token=ctx.raw_token
                    )
                )
            elif tool_name == "get_active_medications":
                result = await self._call_with_retry(
                    lambda: self._medications.fetch(
                        patient_id=ctx.patient_id, raw_token=ctx.raw_token
                    )
                )
            elif tool_name == "get_active_problems":
                result = await self._call_with_retry(
                    lambda: self._problems.fetch(
                        patient_id=ctx.patient_id, raw_token=ctx.raw_token
                    )
                )
            elif tool_name == "get_active_allergies":
                result = await self._call_with_retry(
                    lambda: self._allergies.fetch(
                        patient_id=ctx.patient_id, raw_token=ctx.raw_token
                    )
                )
            elif tool_name == "get_immunizations":
                result = await self._call_with_retry(
                    lambda: self._immunizations.fetch(
                        patient_id=ctx.patient_id, raw_token=ctx.raw_token
                    )
                )
            elif tool_name == "get_procedures":
                raw_since = call.input.get("since_days")
                since_days = raw_since if isinstance(raw_since, int) else None
                result = await self._call_with_retry(
                    lambda: self._procedures.fetch(
                        patient_id=ctx.patient_id,
                        raw_token=ctx.raw_token,
                        since_days=since_days,
                    )
                )
            elif tool_name == "get_recent_labs":
                # since_days is optional; only forward when the model
                # actually picked one so the PHP default applies otherwise.
                raw_since = call.input.get("since_days")
                since_days = raw_since if isinstance(raw_since, int) else None
                result = await self._call_with_retry(
                    lambda: self._labs.fetch(
                        patient_id=ctx.patient_id,
                        raw_token=ctx.raw_token,
                        since_days=since_days,
                    )
                )
            elif tool_name == "get_vitals_trend":
                raw_since = call.input.get("since_days")
                since_days = raw_since if isinstance(raw_since, int) else None
                result = await self._call_with_retry(
                    lambda: self._vitals.fetch(
                        patient_id=ctx.patient_id,
                        raw_token=ctx.raw_token,
                        since_days=since_days,
                    )
                )
            elif tool_name == "get_recent_notes":
                # Notes do per-record sensitivity gating internally —
                # the fetcher takes the full ctx (not just raw_token)
                # and the gateway it was constructed with.
                raw_since = call.input.get("since_days")
                since_days = raw_since if isinstance(raw_since, int) else None
                result = await self._call_with_retry(
                    lambda: self._notes.fetch(
                        ctx=ctx,
                        since_days=since_days,
                    )
                )
            elif tool_name == "get_recent_encounters":
                # Encounters do per-record sensitivity gating internally
                # (encounter category + sensitivity marker), like notes.
                raw_since = call.input.get("since_days")
                since_days = raw_since if isinstance(raw_since, int) else None
                result = await self._call_with_retry(
                    lambda: self._encounters.fetch(
                        ctx=ctx,
                        since_days=since_days,
                    )
                )
            elif tool_name == "search_notes":
                raw_query = call.input.get("query")
                if not isinstance(raw_query, str):
                    return (
                        json.dumps(
                            {
                                "error": "tool_invalid_input",
                                "tool": tool_name,
                                "detail": "query is required and must be a string",
                            }
                        ),
                        None,
                    )
                raw_limit = call.input.get("limit")
                limit = raw_limit if isinstance(raw_limit, int) else None
                raw_since = call.input.get("since_days")
                since_days = raw_since if isinstance(raw_since, int) else None
                # Bind validated locals so the lambda captures them
                # rather than the loose ``raw_*`` names.
                query_str: str = raw_query
                result = await self._call_with_retry(
                    lambda: self._search_notes.fetch(
                        ctx=ctx,
                        query=query_str,
                        limit=limit,
                        since_days=since_days,
                    )
                )
            else:
                self._record_tool_call(
                    trace,
                    tool_name=tool_name,
                    status="not_implemented",
                    latency_ms=_elapsed_ms(start),
                    cache_hit=False,
                    args_hash=args_hash,
                    result_hash=None,
                )
                return (
                    json.dumps(
                        {"error": "tool_not_implemented", "tool": tool_name}
                    ),
                    None,
                )
            payload_json = result.model_dump_json()
            await self._maybe_cache_set(ctx, tool_name, args_hash, result)
            self._record_tool_call(
                trace,
                tool_name=tool_name,
                status="ok",
                latency_ms=_elapsed_ms(start),
                cache_hit=False,
                args_hash=args_hash,
                result_hash=self._hash_payload_str(payload_json),
            )
            return payload_json, result
        except Exception as exc:
            # Surface tool errors back to the model as structured payloads
            # rather than letting them crash the turn — the model can then
            # tell the user honestly that the lookup failed.
            #
            # Persistent timeouts also land in ``timed_out_tools`` so the
            # orchestrator can append a degradation notice to the final
            # reply. Any retry attempts are already done at this point —
            # we're only here on a final failure.
            error_type = classify_http_error(exc)
            if error_type == "timeout" and tool_name not in timed_out_tools:
                timed_out_tools.append(tool_name)
            self._record_tool_call(
                trace,
                tool_name=tool_name,
                status="error",
                latency_ms=_elapsed_ms(start),
                cache_hit=False,
                args_hash=args_hash,
                result_hash=None,
            )
            return (
                json.dumps(
                    {"error": "tool_fetch_failed", "tool": tool_name, "detail": str(exc)}
                ),
                None,
            )

    # ----------- Observability helpers -----------

    def _open_trace(self, ctx: RequestContext) -> TraceHandle | None:
        if self._langfuse is None:
            return None
        return self._langfuse.trace_turn(
            user_id=ctx.user_id,
            patient_id=ctx.patient_id,
            breakglass_flag=ctx.breakglass_flag,
            role=ctx.role,
        )

    def _record_llm_call(
        self,
        trace: TraceHandle | None,
        *,
        latency_ms: int,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        # Accumulate USD cost on the per-turn ContextVar regardless of
        # whether Langfuse is wired — the /turn endpoint reads this to
        # set the X-Agent-Cost-USD header and operators want that
        # signal even when traces are off (e.g. Null client in dev).
        cost = calculate_cost(
            _TRACE_MODEL, prompt_tokens, completion_tokens
        )
        _TURN_COST_VAR.set(_TURN_COST_VAR.get() + cost)

        if self._langfuse is None or trace is None:
            return
        self._langfuse.record_llm_call(
            trace,
            model=_TRACE_MODEL,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            cost_usd=cost,
        )

    def _record_tool_call(
        self,
        trace: TraceHandle | None,
        *,
        tool_name: str,
        status: str,
        latency_ms: int,
        cache_hit: bool,
        args_hash: str | None,
        result_hash: str | None,
    ) -> None:
        if self._langfuse is None or trace is None:
            return
        self._langfuse.record_tool_call(
            trace,
            tool_name=tool_name,
            status=status,
            latency_ms=latency_ms,
            cache_hit=cache_hit,
            args_hash=args_hash,
            result_hash=result_hash,
        )

    def _record_planner_decision(
        self,
        trace: TraceHandle | None,
        *,
        use_case: str,
        tool_count: int,
        batch_count: int,
    ) -> None:
        if self._langfuse is None or trace is None:
            return
        self._langfuse.record_planner_decision(
            trace,
            use_case=use_case,
            tool_count=tool_count,
            batch_count=batch_count,
        )

    def _record_parallel_batch(
        self,
        trace: TraceHandle | None,
        *,
        batch_size: int,
        batch_duration_ms: int,
    ) -> None:
        if self._langfuse is None or trace is None:
            return
        self._langfuse.record_parallel_batch(
            trace,
            batch_size=batch_size,
            batch_duration_ms=batch_duration_ms,
        )

    def _record_verifier_decision(
        self,
        trace: TraceHandle | None,
        *,
        claims_emitted: int,
        claims_rejected: int,
        by_category: dict[str, int],
    ) -> None:
        if self._langfuse is None or trace is None:
            return
        self._langfuse.record_verifier_decision(
            trace,
            claims_emitted=claims_emitted,
            claims_rejected=claims_rejected,
            by_category=by_category,
        )

    def _record_identity_guard_decision(
        self,
        trace: TraceHandle | None,
        *,
        is_valid: bool,
        matched_pattern: str | None,
    ) -> None:
        if self._langfuse is None or trace is None:
            return
        self._langfuse.record_identity_guard_decision(
            trace,
            is_valid=is_valid,
            matched_pattern=matched_pattern,
        )

    def _record_data_quality_metrics(
        self,
        trace: TraceHandle | None,
        *,
        stale_labs_count: int,
        conflict_count: int,
    ) -> None:
        if self._langfuse is None or trace is None:
            return
        self._langfuse.record_data_quality_metrics(
            trace,
            stale_labs_count=stale_labs_count,
            conflict_count=conflict_count,
        )

    # ----------- IdentityGuard helpers -----------

    async def _safe_fetch_demographics(
        self, ctx: RequestContext
    ) -> DemographicsResult | None:
        """Fetch demographics for IdentityGuard, returning None on failure.

        We need the chart-owner's name to bind the guard. If the
        demographics endpoint is down or the JWT is rejected, log
        nothing and skip the guard rather than refuse the turn — the
        real auth boundary is the tool layer, and a 5xx on demographics
        shouldn't black out the whole agent. The guard is a usability
        layer, not a security one (see identity_guard.py docstring).
        """
        try:
            return await self._demographics.fetch(
                patient_id=ctx.patient_id, raw_token=ctx.raw_token
            )
        except Exception:
            return None

    @staticmethod
    def _build_identity_guard(
        ctx: RequestContext, demo_result: DemographicsResult
    ) -> IdentityGuard:
        """Construct an IdentityGuard from the chart owner's demographics.

        ``DemographicsPayload`` doesn't currently include MRN, so we
        fall back to ``patient_id`` for the MRN check (Option B per
        the task spec). When :file:`demographics.php` is extended to
        return ``pubpid``, the construction below switches over without
        callers changing.
        """
        payload = demo_result.payload
        patient_name = f"{payload.given_name} {payload.family_name}".strip()
        # MRN fallback: patient_id-as-string until DemographicsPayload
        # learns about pubpid. The IdentityGuard's MRN check is
        # case-insensitive substring against this value, so the digits
        # of patient_id work as a placeholder.
        patient_mrn = str(ctx.patient_id)
        return IdentityGuard(
            current_patient_name=patient_name,
            current_mrn=patient_mrn,
        )

    # ----------- DataQuality helpers -----------

    def _apply_data_quality(
        self,
        text: str,
        tool_results: dict[str, ToolResult[Any]],
        trace: TraceHandle | None,
    ) -> str:
        """Append data-quality warnings to ``text`` and emit telemetry.

        Thin wrapper over :meth:`_data_quality_suffix` that concatenates
        the warnings block onto ``text``. Streaming callers (Task #10)
        prefer :meth:`_data_quality_suffix` directly so they can yield
        the suffix as a separate SSE delta after the model's text has
        already streamed.
        """
        suffix = self._data_quality_suffix(tool_results, trace)
        return text + suffix if suffix else text

    def _data_quality_suffix(
        self,
        tool_results: dict[str, ToolResult[Any]],
        trace: TraceHandle | None,
    ) -> str:
        """Compute the data-quality warnings block (or ``""``).

        Runs the stale-lab heuristic over ``get_recent_labs`` results
        and the problem/note conflict heuristic when both
        ``get_active_problems`` and ``get_recent_notes`` were
        collected. Counts are reported on the trace; the warnings
        themselves are returned as a block prefixed by a blank line +
        ``Data quality notes:`` header.

        Returns ``""`` when the checker isn't configured or no
        warnings fire.
        """
        if self._data_quality is None:
            return ""

        warnings: list[str] = []
        stale_count = 0
        conflict_count = 0

        labs_result = tool_results.get("get_recent_labs")
        if labs_result is not None and hasattr(labs_result.payload, "labs"):
            for lab in labs_result.payload.labs:
                flag = self._data_quality.check_stale_labs(lab)
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
            conflicts = self._data_quality.check_conflicting_sources(
                problems_result.payload.problems,
                notes_result.payload.notes,
            )
            warnings.extend(conflicts)
            conflict_count = len(conflicts)

        self._record_data_quality_metrics(
            trace,
            stale_labs_count=stale_count,
            conflict_count=conflict_count,
        )

        if not warnings:
            return ""

        # Compact header so the warnings are visually distinct from
        # the main answer without taking over the response. The header
        # text is identical regardless of warning category — the
        # individual lines explain themselves.
        body = "\n".join(f"- {w}" for w in warnings)
        return f"\n\nData quality notes:\n{body}"

    async def _maybe_persist_turn(
        self,
        session_id: str | None,
        user_message: str,
        agent_response: str,
    ) -> None:
        """Append this turn to the session memory if both are configured.

        suggest_reset / refuse_turn are intentionally swallowed at the
        orchestrator level for MVP — refuse_turn is already prevented
        by the pre-call hard-cap check in :meth:`turn`, and the soft
        cap hint is a UX surface that hasn't shipped yet.
        """
        if self._memory is None or session_id is None:
            return
        await self._memory.add_turn(
            session_id=session_id,
            user_message=user_message,
            agent_response=agent_response,
        )

    def _hash_args(self, args: dict[str, Any]) -> str | None:
        if self._hmac_key is None:
            return None
        return hash_payload(args, self._hmac_key)

    def _hash_payload_str(self, payload: str) -> str | None:
        if self._hmac_key is None:
            return None
        return hash_payload(payload, self._hmac_key)

    # ----------- Cache helpers -----------

    async def _maybe_cache_get(
        self,
        ctx: RequestContext,
        tool_name: str,
        args_hash: str | None,
    ) -> ToolResult[Any] | None:
        """Read the per-turn tool cache. None on miss, on missing config,
        or on a payload that no longer matches the typed Result class
        (e.g. schema drift between deploys — fail open and refetch).
        """
        if self._redis_storage is None or args_hash is None:
            return None
        cached = await self._redis_storage.get_cached_tool_result(
            user_id=ctx.user_id,
            patient_id=ctx.patient_id,
            tool_name=tool_name,
            args_hash=args_hash,
        )
        if cached is None:
            return None
        result_cls = _RESULT_CLASSES.get(tool_name)
        if result_cls is None:
            return None
        try:
            return result_cls.model_validate(cached)
        except Exception:
            # Payload shape no longer matches the typed Result. Treat as
            # a miss; the fetch will overwrite the stale entry.
            return None

    async def _maybe_cache_set(
        self,
        ctx: RequestContext,
        tool_name: str,
        args_hash: str | None,
        result: ToolResult[Any],
    ) -> None:
        if self._redis_storage is None or args_hash is None:
            return
        payload = result.model_dump(mode="json")
        await self._redis_storage.cache_tool_result(
            user_id=ctx.user_id,
            patient_id=ctx.patient_id,
            tool_name=tool_name,
            args_hash=args_hash,
            payload=payload,
        )


# Map tool name → typed Result class for cache rehydration. New tools
# register here when verifier+cache coverage catches up; an unmapped
# tool name silently falls through to a refetch (safe default).
_RESULT_CLASSES: Final[dict[str, type[ToolResult[Any]]]] = {
    "get_demographics": DemographicsResult,
    "get_active_problems": ProblemsResult,
    "get_active_medications": MedicationsResult,
    "get_active_allergies": AllergiesResult,
    "get_recent_labs": LabsResult,
    "get_vitals_trend": VitalsResult,
    "get_recent_notes": NotesResult,
    "search_notes": SearchNotesResult,
    "get_recent_encounters": EncountersResult,
    "get_immunizations": ImmunizationsResult,
    "get_procedures": ProceduresResult,
}


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)
