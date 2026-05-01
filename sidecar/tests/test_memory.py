"""Multi-turn session memory for the orchestrator.

The encounter session is bounded: after SOFT_CAP turns the orchestrator
suggests a reset to the user, and after HARD_CAP turns it refuses
further turns until the session expires (Redis TTL clears it). The
caps protect the model's context window and keep cross-encounter
PHI separation intact — a stale session is preferable to one that
silently grew past the boundary the architecture promised.

See ARCHITECTURE.md S3 (orchestration) and S7.1 (session-memory data
class — 75 min TTL, PHI-bearing).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from agentforge.orchestrator.memory import (
    HARD_CAP,
    SOFT_CAP,
    ConversationMemory,
    generate_session_id,
)


def _redis_storage(*, initial: list[dict[str, object]] | None = None) -> AsyncMock:
    """Stand-in :class:`AgentRedisClient` whose session reads return ``initial``."""
    mock = AsyncMock()
    state: dict[str, list[dict[str, object]]] = {}
    if initial is not None:
        state["__test_session__"] = initial

    async def _get(session_id: str) -> list[dict[str, object]] | None:
        return state.get(session_id)

    async def _store(session_id: str, memory: list[dict[str, object]]) -> None:
        state[session_id] = list(memory)

    mock.get_session.side_effect = _get
    mock.store_session.side_effect = _store
    mock.__test_state__ = state  # type: ignore[attr-defined]
    return mock


class TestGenerateSessionId:
    def test_is_deterministic_for_same_inputs(self) -> None:
        a = generate_session_id(user_id=42, patient_id=7, start_time=1700000000.0)
        b = generate_session_id(user_id=42, patient_id=7, start_time=1700000000.0)
        assert a == b

    def test_differs_across_users(self) -> None:
        a = generate_session_id(user_id=1, patient_id=7, start_time=1700000000.0)
        b = generate_session_id(user_id=2, patient_id=7, start_time=1700000000.0)
        assert a != b

    def test_differs_across_patients(self) -> None:
        a = generate_session_id(user_id=1, patient_id=1, start_time=1700000000.0)
        b = generate_session_id(user_id=1, patient_id=2, start_time=1700000000.0)
        assert a != b

    def test_differs_across_start_times(self) -> None:
        a = generate_session_id(user_id=1, patient_id=1, start_time=1700000000.0)
        b = generate_session_id(user_id=1, patient_id=1, start_time=1700000001.0)
        assert a != b

    def test_returns_32_hex_chars(self) -> None:
        sid = generate_session_id(user_id=1, patient_id=1, start_time=1.0)
        assert len(sid) == 32
        assert all(c in "0123456789abcdef" for c in sid)

    def test_is_not_reversible_from_id_alone(self) -> None:
        # Hashed shape is intentional — session IDs travel in URLs and
        # logs; we don't want raw user_id/patient_id parseable from them.
        sid = generate_session_id(user_id=99, patient_id=99, start_time=1.0)
        assert "99" not in sid


class TestAddTurnEmpty:
    async def test_first_turn_appends_two_messages(self) -> None:
        redis = _redis_storage()
        memory = ConversationMemory(redis_storage=redis)

        result_memory, suggest_reset, refuse = await memory.add_turn(
            session_id="s1",
            user_message="hi",
            agent_response="hello",
        )

        assert len(result_memory) == 2
        assert result_memory[0] == {"role": "user", "content": "hi"}
        assert result_memory[1] == {"role": "assistant", "content": "hello"}
        assert suggest_reset is False
        assert refuse is False

    async def test_first_turn_persists_to_redis(self) -> None:
        redis = _redis_storage()
        memory = ConversationMemory(redis_storage=redis)
        await memory.add_turn(
            session_id="s1", user_message="hi", agent_response="hello"
        )
        redis.store_session.assert_awaited_once()
        kwargs = _kwargs(redis.store_session)
        assert kwargs["session_id"] == "s1"
        assert kwargs["memory"][1]["content"] == "hello"


class TestSoftCap:
    async def test_below_soft_cap_does_not_suggest_reset(self) -> None:
        redis = _redis_storage(initial=_history(3))
        memory = ConversationMemory(redis_storage=redis)
        _, suggest_reset, refuse = await memory.add_turn(
            session_id="__test_session__",
            user_message="q4",
            agent_response="r4",
        )
        assert suggest_reset is False
        assert refuse is False

    async def test_at_soft_cap_suggests_reset(self) -> None:
        # 5 turns already persisted; this call makes turn #6 = SOFT_CAP.
        redis = _redis_storage(initial=_history(SOFT_CAP - 1))
        memory = ConversationMemory(redis_storage=redis)
        _, suggest_reset, refuse = await memory.add_turn(
            session_id="__test_session__",
            user_message=f"q{SOFT_CAP}",
            agent_response=f"r{SOFT_CAP}",
        )
        assert suggest_reset is True
        assert refuse is False


class TestHardCap:
    async def test_at_hard_cap_refuses_and_skips_persist(self) -> None:
        redis = _redis_storage(initial=_history(HARD_CAP))
        memory = ConversationMemory(redis_storage=redis)
        _, _, refuse = await memory.add_turn(
            session_id="__test_session__",
            user_message="too-many",
            agent_response="ignored",
        )
        assert refuse is True
        # Hard-cap turns are NOT persisted — the cap is a contract, not
        # a soft warning. Persisting would let the next turn slip past
        # the boundary that this turn just hit.
        redis.store_session.assert_not_awaited()


class TestGetMemory:
    async def test_returns_empty_list_on_cache_miss(self) -> None:
        redis = _redis_storage()
        memory = ConversationMemory(redis_storage=redis)
        assert await memory.get_memory("missing") == []

    async def test_returns_persisted_history(self) -> None:
        redis = _redis_storage(initial=_history(2))
        memory = ConversationMemory(redis_storage=redis)
        history = await memory.get_memory("__test_session__")
        assert len(history) == 4  # 2 turns x (user+assistant)


class TestSessionIsolation:
    async def test_different_sessions_do_not_leak(self) -> None:
        redis = _redis_storage()
        memory = ConversationMemory(redis_storage=redis)

        await memory.add_turn(
            session_id="alice", user_message="ua", agent_response="ra"
        )
        await memory.add_turn(
            session_id="bob", user_message="ub", agent_response="rb"
        )

        alice_history = await memory.get_memory("alice")
        bob_history = await memory.get_memory("bob")
        assert alice_history == [
            {"role": "user", "content": "ua"},
            {"role": "assistant", "content": "ra"},
        ]
        assert bob_history == [
            {"role": "user", "content": "ub"},
            {"role": "assistant", "content": "rb"},
        ]


# -------- helpers --------


def _history(turns: int) -> list[dict[str, object]]:
    """Synthesize ``turns`` user/assistant pairs."""
    out: list[dict[str, object]] = []
    for i in range(turns):
        out.append({"role": "user", "content": f"q{i}"})
        out.append({"role": "assistant", "content": f"r{i}"})
    return out


def _kwargs(mock: Any) -> dict[str, Any]:
    """``call_args.kwargs`` in a way mypy doesn't trip over."""
    args, kwargs = mock.await_args
    if args:
        # Positional support — store_session(session_id, memory)
        return {"session_id": args[0], "memory": args[1]}
    return dict(kwargs)
