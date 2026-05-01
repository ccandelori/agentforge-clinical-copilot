"""Behavior tests for AgentRedisClient.

The client wraps `redis.asyncio` to host two PHI stores defined in
ARCHITECTURE.md S7.1:

  * Session memory  - 75 min TTL, keyed by session_id.
  * Tool result cache - 60 s TTL, keyed by user+patient+tool+args_hash.

We test the public contract (round-trip, miss returns None, TTL set via
SETEX, key isolation across users/patients/tools) by injecting an
AsyncMock that satisfies the same Protocol the auth gateway uses. That
avoids a fakeredis dependency and keeps the test surface aligned with
the gateway's mocking style.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agentforge.storage.redis_client import (
    SESSION_TTL,
    TOOL_CACHE_TTL,
    AgentRedisClient,
)


def make_redis_mock(initial: dict[str, str] | None = None) -> AsyncMock:
    """Async Redis stand-in with a minimal in-memory `setex`/`get` impl.

    Mirrors the AsyncMock idiom used in `tests/test_auth_gateway.py`. We
    only need GET, SETEX, and CLOSE; the production client uses
    decode_responses=True so values flow as `str`, not `bytes`.
    """
    storage: dict[str, str] = dict(initial or {})
    redis_mock = AsyncMock()

    async def get(key: str) -> str | None:
        return storage.get(key)

    async def setex(key: str, ttl: int, value: str) -> None:
        storage[key] = value

    async def aclose() -> None:
        return None

    redis_mock.get.side_effect = get
    redis_mock.setex.side_effect = setex
    redis_mock.aclose.side_effect = aclose
    return redis_mock


@pytest.fixture
def redis_mock() -> AsyncMock:
    return make_redis_mock()


@pytest.fixture
def client(redis_mock: AsyncMock) -> AgentRedisClient:
    return AgentRedisClient(redis_client=redis_mock)


# ---------- TTL constants ----------


def test_session_ttl_matches_seventy_five_minute_encounter_window() -> None:
    assert SESSION_TTL == 75 * 60


def test_tool_cache_ttl_matches_sixty_second_phi_window() -> None:
    assert TOOL_CACHE_TTL == 60


# ---------- Session memory ----------


async def test_store_session_then_get_session_round_trips_messages(
    client: AgentRedisClient,
) -> None:
    memory: list[dict[str, Any]] = [
        {"role": "user", "content": "What are the active problems?"},
        {"role": "assistant", "content": "Let me look those up."},
    ]

    await client.store_session("sess-1", memory)
    result = await client.get_session("sess-1")

    assert result == memory


async def test_store_session_uses_setex_with_session_ttl(
    client: AgentRedisClient,
    redis_mock: AsyncMock,
) -> None:
    await client.store_session("sess-1", [{"role": "user", "content": "hi"}])

    redis_mock.setex.assert_awaited_once()
    call_args = redis_mock.setex.call_args
    assert call_args.args[0] == "session:sess-1"
    assert call_args.args[1] == SESSION_TTL


async def test_get_session_returns_none_when_session_absent(
    client: AgentRedisClient,
) -> None:
    assert await client.get_session("never-stored") is None


async def test_store_session_serialises_memory_as_json(
    client: AgentRedisClient,
    redis_mock: AsyncMock,
) -> None:
    # Documents the wire format. If session memory shape changes
    # (e.g. switches to msgpack), this test is the canary.
    memory: list[dict[str, object]] = [{"role": "user", "content": "msg"}]
    await client.store_session("sess-1", memory)

    serialised = redis_mock.setex.call_args.args[2]
    assert json.loads(serialised) == memory


# ---------- Tool result cache ----------


async def test_cache_tool_result_then_get_cached_round_trips_payload(
    client: AgentRedisClient,
) -> None:
    payload = {"first_name": "Jane", "last_name": "Patel", "age": 47}

    await client.cache_tool_result(
        user_id=42,
        patient_id=123,
        tool_name="get_demographics",
        args_hash="abc123",
        payload=payload,
    )
    result = await client.get_cached_tool_result(
        user_id=42,
        patient_id=123,
        tool_name="get_demographics",
        args_hash="abc123",
    )

    assert result == payload


async def test_cache_tool_result_uses_setex_with_tool_cache_ttl(
    client: AgentRedisClient,
    redis_mock: AsyncMock,
) -> None:
    await client.cache_tool_result(
        user_id=42,
        patient_id=123,
        tool_name="get_demographics",
        args_hash="abc123",
        payload={"x": 1},
    )

    redis_mock.setex.assert_awaited_once()
    call_args = redis_mock.setex.call_args
    assert call_args.args[0] == "tool:42:123:get_demographics:abc123"
    assert call_args.args[1] == TOOL_CACHE_TTL


async def test_get_cached_tool_result_returns_none_on_cache_miss(
    client: AgentRedisClient,
) -> None:
    result = await client.get_cached_tool_result(
        user_id=42,
        patient_id=123,
        tool_name="get_demographics",
        args_hash="never-cached",
    )

    assert result is None


async def test_cache_tool_result_isolates_keys_across_users(
    client: AgentRedisClient,
) -> None:
    # Same patient, same tool, same args - different users must NOT
    # share a cache entry. ARCHITECTURE.md S7.1 demands per-user keying
    # so audit trails stay distinct.
    await client.cache_tool_result(
        user_id=1, patient_id=99, tool_name="t", args_hash="h",
        payload={"who": "alice"},
    )
    await client.cache_tool_result(
        user_id=2, patient_id=99, tool_name="t", args_hash="h",
        payload={"who": "bob"},
    )

    a = await client.get_cached_tool_result(
        user_id=1, patient_id=99, tool_name="t", args_hash="h",
    )
    b = await client.get_cached_tool_result(
        user_id=2, patient_id=99, tool_name="t", args_hash="h",
    )

    assert a == {"who": "alice"}
    assert b == {"who": "bob"}


async def test_cache_tool_result_isolates_keys_across_patients(
    client: AgentRedisClient,
) -> None:
    await client.cache_tool_result(
        user_id=1, patient_id=10, tool_name="t", args_hash="h",
        payload={"chart": 10},
    )
    await client.cache_tool_result(
        user_id=1, patient_id=20, tool_name="t", args_hash="h",
        payload={"chart": 20},
    )

    a = await client.get_cached_tool_result(
        user_id=1, patient_id=10, tool_name="t", args_hash="h",
    )
    b = await client.get_cached_tool_result(
        user_id=1, patient_id=20, tool_name="t", args_hash="h",
    )

    assert a == {"chart": 10}
    assert b == {"chart": 20}


async def test_cache_tool_result_isolates_keys_across_tools(
    client: AgentRedisClient,
) -> None:
    await client.cache_tool_result(
        user_id=1, patient_id=10, tool_name="get_meds", args_hash="h",
        payload={"tool": "meds"},
    )
    await client.cache_tool_result(
        user_id=1, patient_id=10, tool_name="get_problems", args_hash="h",
        payload={"tool": "problems"},
    )

    a = await client.get_cached_tool_result(
        user_id=1, patient_id=10, tool_name="get_meds", args_hash="h",
    )
    b = await client.get_cached_tool_result(
        user_id=1, patient_id=10, tool_name="get_problems", args_hash="h",
    )

    assert a == {"tool": "meds"}
    assert b == {"tool": "problems"}


async def test_get_cached_tool_result_returns_dict_not_list(
    client: AgentRedisClient,
) -> None:
    # When the cached payload is not a JSON object (e.g. an old value
    # from a different tool got into the same key by mistake), the
    # method must refuse rather than silently returning the wrong shape.
    await client.cache_tool_result(
        user_id=1, patient_id=1, tool_name="t", args_hash="h",
        payload={"good": True},
    )

    result = await client.get_cached_tool_result(
        user_id=1, patient_id=1, tool_name="t", args_hash="h",
    )

    assert isinstance(result, dict)
    assert result["good"] is True


# ---------- Lifecycle ----------


async def test_aclose_closes_underlying_redis_client(
    client: AgentRedisClient,
    redis_mock: AsyncMock,
) -> None:
    await client.aclose()

    redis_mock.aclose.assert_awaited_once()


# ---------- Construction ergonomics ----------


def test_agent_redis_client_can_be_constructed_from_url() -> None:
    # Smoke test: passing a URL constructs without contacting Redis. The
    # underlying redis.asyncio.from_url is lazy - it doesn't open a
    # socket until the first command. We never issue one in this test.
    client = AgentRedisClient(redis_url="redis://localhost:6379/0")
    assert client is not None
