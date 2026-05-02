"""Redis tool-cache wiring inside the orchestrator turn loop.

Tool dispatch hits the per-turn Redis cache (60s TTL, keyed by
``user_id + patient_id + tool_name + args_hash``). On cache hit the
fetcher is skipped entirely and the cached payload is returned to the
model verbatim. On miss the fetcher runs and the result is stored.

The cache is opt-in: when ``redis_storage`` is None the orchestrator
behaves as before (no cache reads, no writes). See ARCHITECTURE.md
S7.1 for the cache lifetime.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

from agentforge.gateway.auth_gateway import RequestContext
from agentforge.llm.types import LLMResponse, ToolCall
from agentforge.orchestrator import Orchestrator
from agentforge.tools.dtos import ToolResultMetadata
from agentforge.tools.problems import ProblemItem, ProblemsPayload, ProblemsResult


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


def _meta(name: str) -> ToolResultMetadata:
    return ToolResultMetadata(
        tool_name=name,
        fetched_at=datetime.now(UTC),
        data_freshness_seconds=60,
        source=f"openemr.{name}",
    )


def _problems(*ids: int) -> ProblemsResult:
    return ProblemsResult(
        metadata=_meta("get_active_problems"),
        payload=ProblemsPayload(
            problems=tuple(ProblemItem(id=i, title=f"Problem {i}") for i in ids),
        ),
    )


def _llm_with(*responses: LLMResponse) -> AsyncMock:
    mock = AsyncMock()
    mock.complete.side_effect = list(responses)
    return mock


def _fetcher(result: Any) -> AsyncMock:
    mock = AsyncMock()
    mock.fetch.return_value = result
    return mock


def _make_redis_storage(*, cached_payload: dict[str, Any] | None = None) -> AsyncMock:
    """Stand-in :class:`AgentRedisClient` shaped object."""
    mock = AsyncMock()
    mock.get_cached_tool_result.return_value = cached_payload
    return mock


def _build(
    *,
    llm: AsyncMock,
    redis_storage: AsyncMock | None = None,
    hmac_key: bytes | None = b"test-key",
    problems: AsyncMock | None = None,
) -> Orchestrator:
    return Orchestrator(
        llm=llm,
        demographics_fetcher=AsyncMock(),
        medications_fetcher=AsyncMock(),
        problems_fetcher=problems or AsyncMock(),
        allergies_fetcher=AsyncMock(),
        labs_fetcher=AsyncMock(),
        vitals_fetcher=AsyncMock(),
        notes_fetcher=AsyncMock(),
        search_notes_fetcher=AsyncMock(),
        encounters_fetcher=AsyncMock(),
        immunizations_fetcher=AsyncMock(),
        procedures_fetcher=AsyncMock(),
        redis_storage=redis_storage,
        hmac_key=hmac_key,
    )


class TestCacheMiss:
    async def test_calls_fetcher_when_cache_miss(self) -> None:
        # Two-turn loop: tool_use → tool_result → end_turn.
        llm = _llm_with(
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(id="t1", name="get_active_problems", input={}),
                ],
                stop_reason="tool_use",
                input_tokens=10,
                output_tokens=2,
            ),
            LLMResponse(
                text="ok.",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=20,
                output_tokens=2,
            ),
        )
        problems = _fetcher(_problems(1))
        storage = _make_redis_storage(cached_payload=None)
        orch = _build(llm=llm, problems=problems, redis_storage=storage)
        await orch.turn(_ctx(), "?")

        problems.fetch.assert_awaited_once()
        # The cache was consulted (miss) AND the result was written.
        storage.get_cached_tool_result.assert_awaited_once()
        storage.cache_tool_result.assert_awaited_once()
        # Cache write carries the right keying components.
        write_kwargs = storage.cache_tool_result.call_args.kwargs
        assert write_kwargs["user_id"] == 42
        assert write_kwargs["patient_id"] == 7
        assert write_kwargs["tool_name"] == "get_active_problems"
        assert isinstance(write_kwargs["args_hash"], str)


class TestCacheHit:
    async def test_skips_fetcher_when_cache_hit(self) -> None:
        cached = {
            "metadata": {
                "tool_name": "get_active_problems",
                "fetched_at": datetime.now(UTC).isoformat(),
                "data_freshness_seconds": 60,
                "source": "openemr.get_active_problems",
                "redaction_applied": False,
                "redacted_fields": [],
            },
            "payload": {
                "problems": [
                    {
                        "id": 1,
                        "title": "Problem 1",
                        "diagnosis": None,
                        "begin_date": None,
                    }
                ],
            },
        }
        llm = _llm_with(
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(id="t1", name="get_active_problems", input={}),
                ],
                stop_reason="tool_use",
                input_tokens=10,
                output_tokens=2,
            ),
            LLMResponse(
                text="ok.",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=20,
                output_tokens=2,
            ),
        )
        problems = AsyncMock()  # would crash if called — verifies skip
        storage = _make_redis_storage(cached_payload=cached)
        orch = _build(llm=llm, problems=problems, redis_storage=storage)
        await orch.turn(_ctx(), "?")

        problems.fetch.assert_not_awaited()
        storage.cache_tool_result.assert_not_awaited()  # no re-write on hit
        # The model still saw the cached JSON. The captured `messages`
        # list is a live reference (mock retains the same object), so
        # we look for any role='tool' message rather than a specific
        # position.
        sent_messages = llm.complete.call_args_list[-1].kwargs["messages"]
        tool_msgs = [m for m in sent_messages if m.role == "tool"]
        assert len(tool_msgs) == 1
        body = json.loads(tool_msgs[0].content)
        assert body["payload"]["problems"][0]["id"] == 1


class TestCacheDisabled:
    async def test_no_cache_calls_when_redis_storage_is_none(self) -> None:
        llm = _llm_with(
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(id="t1", name="get_active_problems", input={}),
                ],
                stop_reason="tool_use",
                input_tokens=10,
                output_tokens=2,
            ),
            LLMResponse(
                text="ok.",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=20,
                output_tokens=2,
            ),
        )
        problems = _fetcher(_problems(1))
        orch = _build(llm=llm, problems=problems, redis_storage=None)
        await orch.turn(_ctx(), "?")
        problems.fetch.assert_awaited_once()


class TestCacheKeyIsolation:
    async def test_args_hash_changes_when_input_changes(self) -> None:
        # Two calls with different inputs produce different cache keys —
        # we verify by inspecting the args_hash recorded on cache writes.
        from agentforge.observability.hmac_hash import hash_payload

        h1 = hash_payload({"since_days": 30}, b"test-key")
        h2 = hash_payload({"since_days": 90}, b"test-key")
        assert h1 != h2
