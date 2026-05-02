"""Session memory wired into Orchestrator.turn().

When the caller passes a ``session_id`` and a :class:`ConversationMemory`
is configured, prior turns are loaded as message history and the new
turn's user/assistant pair is persisted after the LLM finishes. The
``HARD_CAP`` from memory.py becomes a hard refusal — the model is not
called once the boundary has been crossed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from agentforge.gateway.auth_gateway import RequestContext
from agentforge.llm.types import LLMResponse
from agentforge.orchestrator import Orchestrator
from agentforge.orchestrator.memory import HARD_CAP, ConversationMemory


def _ctx() -> RequestContext:
    return RequestContext(
        user_id=42,
        patient_id=7,
        username="test-user",
        role="clinician",
        breakglass_flag=False,
        breakglass_reason=None,
        sensitivity_clearances=frozenset(),
        raw_token="raw.jwt.token",
    )


def _llm_with(*responses: LLMResponse) -> AsyncMock:
    mock = AsyncMock()
    mock.complete.side_effect = list(responses)
    return mock


def _redis_storage(*, initial: list[dict[str, object]] | None = None) -> AsyncMock:
    """Stand-in :class:`AgentRedisClient`."""
    mock = AsyncMock()
    state: dict[str, list[dict[str, object]]] = {}
    if initial is not None:
        state["sess-1"] = initial

    async def _get(session_id: str) -> list[dict[str, object]] | None:
        return state.get(session_id)

    async def _store(*, session_id: str, memory: list[dict[str, object]]) -> None:
        state[session_id] = list(memory)

    mock.get_session.side_effect = _get
    mock.store_session.side_effect = _store
    return mock


def _build(
    *,
    llm: AsyncMock,
    memory: ConversationMemory | None = None,
) -> Orchestrator:
    return Orchestrator(
        llm=llm,
        demographics_fetcher=AsyncMock(),
        medications_fetcher=AsyncMock(),
        problems_fetcher=AsyncMock(),
        allergies_fetcher=AsyncMock(),
        labs_fetcher=AsyncMock(),
        vitals_fetcher=AsyncMock(),
        notes_fetcher=AsyncMock(),
        memory=memory,
    )


def _final(text: str) -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_calls=[],
        stop_reason="end_turn",
        input_tokens=10,
        output_tokens=4,
    )


class TestSessionIdMissing:
    async def test_no_session_id_skips_memory_entirely(self) -> None:
        # The orchestrator's legacy contract: with no session_id, it
        # neither reads nor writes the memory store.
        redis = _redis_storage()
        memory = ConversationMemory(redis_storage=redis)
        llm = _llm_with(_final("ok"))
        orch = _build(llm=llm, memory=memory)
        reply = await orch.turn(_ctx(), "hi")
        assert reply == "ok"
        redis.get_session.assert_not_awaited()
        redis.store_session.assert_not_awaited()


class TestPriorHistoryIsLoaded:
    async def test_prepends_persisted_messages_to_llm_call(self) -> None:
        prior: list[dict[str, object]] = [
            {"role": "user", "content": "earlier question"},
            {"role": "assistant", "content": "earlier answer"},
        ]
        redis = _redis_storage(initial=prior)
        memory = ConversationMemory(redis_storage=redis)
        llm = _llm_with(_final("new answer"))
        orch = _build(llm=llm, memory=memory)
        await orch.turn(_ctx(), "follow-up", session_id="sess-1")

        sent_messages = llm.complete.call_args_list[0].kwargs["messages"]
        roles_and_contents = [(m.role, m.content) for m in sent_messages]
        # History first, then this turn's user message.
        assert roles_and_contents[0] == ("user", "earlier question")
        assert roles_and_contents[1] == ("assistant", "earlier answer")
        assert roles_and_contents[2] == ("user", "follow-up")


class TestPersistAfterTurn:
    async def test_user_and_final_assistant_text_are_persisted(self) -> None:
        redis = _redis_storage()
        memory = ConversationMemory(redis_storage=redis)
        llm = _llm_with(_final("first reply"))
        orch = _build(llm=llm, memory=memory)
        await orch.turn(_ctx(), "first question", session_id="sess-1")

        redis.store_session.assert_awaited_once()
        kwargs = _kwargs(redis.store_session)
        persisted = kwargs["memory"]
        assert persisted == [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first reply"},
        ]


class TestHardCapRefusal:
    async def test_refuses_without_calling_llm_at_hard_cap(self) -> None:
        # HARD_CAP turns already on file. The orchestrator must short-
        # circuit before invoking the model.
        prior = _history(HARD_CAP)
        redis = _redis_storage(initial=prior)
        memory = ConversationMemory(redis_storage=redis)
        llm = _llm_with(_final("never called"))
        orch = _build(llm=llm, memory=memory)
        reply = await orch.turn(_ctx(), "one more", session_id="sess-1")

        # No LLM call.
        llm.complete.assert_not_awaited()
        # No persist.
        redis.store_session.assert_not_awaited()
        # Polite refusal text.
        assert "session" in reply.lower()
        assert reply != "never called"


class TestSoftCapPassesThrough:
    async def test_at_soft_cap_turn_runs_normally(self) -> None:
        # SOFT_CAP turns already on file; the orchestrator runs as
        # usual. Surfacing the soft-cap hint to the UI is a UX
        # follow-up — for MVP the hint is implicit (the persisted
        # turn count keeps climbing until HARD_CAP).
        from agentforge.orchestrator.memory import SOFT_CAP

        redis = _redis_storage(initial=_history(SOFT_CAP - 1))
        memory = ConversationMemory(redis_storage=redis)
        llm = _llm_with(_final("answer"))
        orch = _build(llm=llm, memory=memory)
        reply = await orch.turn(_ctx(), "still going", session_id="sess-1")

        assert reply == "answer"
        llm.complete.assert_awaited_once()


# ---- helpers ----


def _history(turns: int) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for i in range(turns):
        out.append({"role": "user", "content": f"q{i}"})
        out.append({"role": "assistant", "content": f"r{i}"})
    return out


def _kwargs(mock: Any) -> dict[str, Any]:
    args, kwargs = mock.await_args
    if args and len(args) >= 2:
        return {"session_id": args[0], "memory": args[1]}
    return dict(kwargs)
