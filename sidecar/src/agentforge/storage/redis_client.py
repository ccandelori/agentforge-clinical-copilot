"""AgentRedisClient - the single point of truth for the sidecar's two
Redis-backed PHI stores (ARCHITECTURE.md S7.1).

Two stores live behind this class:

  * Session memory  - 75 min TTL, keyed by session_id only. Holds the
    multi-turn transcript for an in-progress encounter. Wire format:
    JSON-serialised ``list[dict]``.
  * Tool result cache - 60 s TTL, keyed by user_id + patient_id +
    tool_name + args_hash. Prevents cross-session leakage by including
    every authorization-relevant dimension in the key. Wire format:
    JSON-serialised ``dict``.

Both keys flow through ``agentforge.storage.keys`` so the verifier's
future cache integration reuses identical shapes. The class accepts
either a Redis URL (production path: builds ``redis.asyncio.Redis``
with ``decode_responses=True``) or a pre-built client matching the
``_RedisProto`` Protocol (test path: ``unittest.mock.AsyncMock``). The
gateway uses the same Protocol-shaped injection idiom; see
``agentforge.gateway.auth_gateway``.

Encryption at rest is the responsibility of the Redis deployment, not
this client. The BAA-covered managed instance handles that.
"""

from __future__ import annotations

import json
from typing import Protocol, cast

import redis.asyncio as redis_async

SESSION_TTL = 75 * 60
TOOL_CACHE_TTL = 60


class _RedisProto(Protocol):
    """Minimal async-Redis surface the storage layer needs.

    Mirrors the Protocol idiom in ``auth_gateway.py``: keeps tests free
    of fakeredis while staying compatible with the real
    ``redis.asyncio.Redis`` client.
    """

    async def get(self, key: str) -> str | None: ...

    async def setex(self, key: str, time: int, value: str) -> None: ...

    async def aclose(self) -> None: ...


class AgentRedisClient:
    """Wraps a redis.asyncio client with session + tool-cache helpers."""

    def __init__(
        self,
        *,
        redis_url: str | None = None,
        redis_client: _RedisProto | None = None,
    ) -> None:
        if redis_client is None and redis_url is None:
            raise ValueError("AgentRedisClient requires either redis_url or redis_client")
        if redis_client is not None and redis_url is not None:
            raise ValueError(
                "AgentRedisClient accepts redis_url XOR redis_client, not both",
            )

        if redis_client is not None:
            self._redis: _RedisProto = redis_client
        else:
            assert redis_url is not None  # narrowed by the guards above
            self._redis = cast(
                _RedisProto,
                redis_async.from_url(redis_url, decode_responses=True),
            )

    # ---------- Session memory ----------

    async def store_session(
        self,
        session_id: str,
        memory: list[dict[str, object]],
    ) -> None:
        """Persist the transcript for ``session_id`` with the encounter TTL."""
        from agentforge.storage.keys import session_key

        await self._redis.setex(
            session_key(session_id),
            SESSION_TTL,
            json.dumps(memory),
        )

    async def get_session(self, session_id: str) -> list[dict[str, object]] | None:
        """Return the stored transcript or ``None`` on miss / expiry."""
        from agentforge.storage.keys import session_key

        raw = await self._redis.get(session_key(session_id))
        if raw is None:
            return None
        decoded = json.loads(raw)
        if not isinstance(decoded, list):
            return None
        return cast(list[dict[str, object]], decoded)

    # ---------- Tool result cache ----------

    async def cache_tool_result(
        self,
        *,
        user_id: int,
        patient_id: int,
        tool_name: str,
        args_hash: str,
        payload: dict[str, object],
    ) -> None:
        """Store a tool result for the 60 s PHI cache window."""
        from agentforge.storage.keys import tool_cache_key

        key = tool_cache_key(
            user_id=user_id,
            patient_id=patient_id,
            tool_name=tool_name,
            args_hash=args_hash,
        )
        await self._redis.setex(key, TOOL_CACHE_TTL, json.dumps(payload))

    async def get_cached_tool_result(
        self,
        *,
        user_id: int,
        patient_id: int,
        tool_name: str,
        args_hash: str,
    ) -> dict[str, object] | None:
        """Return the cached payload or ``None`` on miss / expiry / shape mismatch."""
        from agentforge.storage.keys import tool_cache_key

        key = tool_cache_key(
            user_id=user_id,
            patient_id=patient_id,
            tool_name=tool_name,
            args_hash=args_hash,
        )
        raw = await self._redis.get(key)
        if raw is None:
            return None
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            return None
        return cast(dict[str, object], decoded)

    # ---------- Lifecycle ----------

    async def aclose(self) -> None:
        """Release the underlying connection pool. Call once at shutdown."""
        await self._redis.aclose()
