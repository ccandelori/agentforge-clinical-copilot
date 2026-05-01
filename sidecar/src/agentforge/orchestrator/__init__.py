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

import json
import time
from collections import Counter
from typing import Any, Final

from agentforge.gateway.auth_gateway import RequestContext
from agentforge.llm.client import LLMClient
from agentforge.llm.types import Message, ToolCall
from agentforge.observability.hmac_hash import hash_payload
from agentforge.observability.protocols import LangfuseClient, TraceHandle
from agentforge.orchestrator.memory import HARD_CAP, ConversationMemory
from agentforge.storage.redis_client import AgentRedisClient
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
from agentforge.tools.labs import LABS_TOOL_SPEC, LabsFetcher, LabsResult
from agentforge.tools.medications import (
    MEDICATIONS_TOOL_SPEC,
    MedicationsFetcher,
    MedicationsResult,
)
from agentforge.tools.problems import (
    PROBLEMS_TOOL_SPEC,
    ProblemsFetcher,
    ProblemsResult,
)
from agentforge.tools.vitals import VITALS_TOOL_SPEC, VitalsFetcher, VitalsResult
from agentforge.verifier import (
    DomainConstraintChecker,
    NullDomainConstraintChecker,
    StreamingVerifier,
    build_citation_index,
)

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

SYSTEM_PROMPT: Final[str] = """\
You are AgentForge, a clinical co-pilot embedded inside OpenEMR. A clinician \
is asking about a single patient whose chart is currently open in their \
browser. You answer questions grounded in that patient's record by calling \
tools — never from memory or speculation.

You have six tools:
  - get_demographics      : name, DOB, sex, preferred language
  - get_active_problems   : current diagnoses / problem list
  - get_active_medications: currently active medications (with begin/end dates)
  - get_active_allergies  : known allergies (allergen, reaction, severity)
  - get_recent_labs       : recent lab analytes (name, value, units, range, abnormal flag, date)
  - get_vitals_trend      : recent vital signs (BP, pulse, temp, SpO2, weight, BMI)

Citation rules:
- Every factual sentence about the patient MUST end with an inline citation \
of the form `[record_type #id]` (date optional), where the ID is one the \
tool result this turn actually returned. Examples: \
`[problem #5]`, `[medication #10, started 2024-08-15]`, `[demographic #7]`.
- Sentences without a recognised citation are dropped before the user sees \
them, so omitting a citation = the user gets nothing for that sentence.

Behavior rules:
1. When the user asks about ANY patient information, call the relevant tool \
first. Don't pre-narrate ("Let me look that up…"); just call.
2. If a question needs multiple tools, call them all — Claude's tool_use \
can emit several calls per turn.
3. After tool results return, briefly summarize and cite. Translate JSON \
into a clinical summary; don't quote raw fields.
4. Be concise, clinical, non-speculative. Use medical terminology where \
appropriate. Don't hedge with "as an AI…".
5. If a tool returns an error or empty result, say so plainly. Do not \
invent data to fill gaps. An empty problem list means "no active problems \
recorded," not "the patient is healthy."
6. If the user asks about something you don't have a tool for (encounter \
history, imaging, immunizations, etc.), name the gap explicitly: "I \
don't have access to encounter history in this version of the co-pilot \
— it's visible in the chart's encounters section."\
"""

MAX_TOOL_ITERATIONS: Final[int] = 4


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
        *,
        domain_constraints: DomainConstraintChecker | None = None,
        verifier_enabled: bool = False,
        langfuse: LangfuseClient | None = None,
        hmac_key: bytes | None = None,
        redis_storage: AgentRedisClient | None = None,
        memory: ConversationMemory | None = None,
    ) -> None:
        self._llm = llm
        self._demographics = demographics_fetcher
        self._medications = medications_fetcher
        self._problems = problems_fetcher
        self._allergies = allergies_fetcher
        self._labs = labs_fetcher
        self._vitals = vitals_fetcher
        self._domain_constraints = (
            domain_constraints or NullDomainConstraintChecker()
        )
        self._verifier_enabled = verifier_enabled
        self._langfuse = langfuse
        self._hmac_key = hmac_key
        self._redis_storage = redis_storage
        self._memory = memory

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
        # Reject up front when the session has already hit its hard cap;
        # we never call the model on a refused turn.
        if self._memory is not None and session_id is not None:
            existing = await self._memory.get_memory(session_id)
            if len(existing) // 2 >= HARD_CAP:
                return _SESSION_REFUSAL_TEXT

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
        ]
        # Per-turn tool-result accumulator. Keyed by tool name; later
        # iterations of the same tool overwrite — acceptable for MVP
        # because the catalogue is idempotent reads.
        tool_results: dict[str, ToolResult[Any]] = {}

        trace = self._open_trace(ctx)

        for _ in range(MAX_TOOL_ITERATIONS):
            llm_start = time.perf_counter()
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
                await self._maybe_persist_turn(
                    session_id, user_message, final_text
                )
                return final_text

            for call in response.tool_calls:
                content_json, result = await self._dispatch(ctx, call, trace)
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

    async def _dispatch(
        self,
        ctx: RequestContext,
        call: ToolCall,
        trace: TraceHandle | None,
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
                result = await self._demographics.fetch(
                    patient_id=ctx.patient_id, raw_token=ctx.raw_token
                )
            elif tool_name == "get_active_medications":
                result = await self._medications.fetch(
                    patient_id=ctx.patient_id, raw_token=ctx.raw_token
                )
            elif tool_name == "get_active_problems":
                result = await self._problems.fetch(
                    patient_id=ctx.patient_id, raw_token=ctx.raw_token
                )
            elif tool_name == "get_active_allergies":
                result = await self._allergies.fetch(
                    patient_id=ctx.patient_id, raw_token=ctx.raw_token
                )
            elif tool_name == "get_recent_labs":
                # since_days is optional; only forward when the model
                # actually picked one so the PHP default applies otherwise.
                raw_since = call.input.get("since_days")
                since_days = raw_since if isinstance(raw_since, int) else None
                result = await self._labs.fetch(
                    patient_id=ctx.patient_id,
                    raw_token=ctx.raw_token,
                    since_days=since_days,
                )
            elif tool_name == "get_vitals_trend":
                raw_since = call.input.get("since_days")
                since_days = raw_since if isinstance(raw_since, int) else None
                result = await self._vitals.fetch(
                    patient_id=ctx.patient_id,
                    raw_token=ctx.raw_token,
                    since_days=since_days,
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
        if self._langfuse is None or trace is None:
            return
        self._langfuse.record_llm_call(
            trace,
            model=_TRACE_MODEL,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
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
}


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)
