"""Multi-turn session memory wrapping the Redis session store.

The orchestrator persists user messages and final assistant responses
turn-by-turn under a ``session_id`` keyed on (user, patient, start
time). Two caps protect the model's context window and signal the
user when an encounter has run on too long:

  * ``SOFT_CAP`` — at this turn count we *suggest* the user reset the
    conversation. The turn still runs and persists.
  * ``HARD_CAP`` — at this turn count we refuse the new turn outright.
    The persisted memory is left as-is; Redis TTL eventually clears
    it, freeing the slot for a fresh session.

A stale session is preferable to one that quietly grew past its
boundary. See ARCHITECTURE.md S3 (orchestration) and S7.1
(session-memory data class).
"""

from __future__ import annotations

from hashlib import sha256
from typing import Final

from agentforge.storage.redis_client import AgentRedisClient

SOFT_CAP: Final[int] = 6
HARD_CAP: Final[int] = 8


def generate_session_id(
    *,
    user_id: int,
    patient_id: int,
    start_time: float,
) -> str:
    """Deterministic 32-char hex session ID.

    Truncated SHA-256 over ``user_id:patient_id:int(start_time)``.
    Hashed shape is intentional: session IDs travel in URLs and logs;
    a clear-text join key would let anyone with log access reconstruct
    "user X looked at patient Y." The full 256-bit hash is overkill
    for the cardinality we need; 128 bits (32 hex chars) keeps the ID
    short while leaving collision odds astronomically below the
    encounter rate.
    """
    raw = f"{user_id}:{patient_id}:{int(start_time)}"
    return sha256(raw.encode("utf-8")).hexdigest()[:32]


class ConversationMemory:
    """Two-store wrapper: Redis-persisted transcripts + per-turn caps."""

    def __init__(self, redis_storage: AgentRedisClient) -> None:
        self._redis = redis_storage

    async def get_memory(self, session_id: str) -> list[dict[str, object]]:
        """Return the persisted transcript or ``[]`` on miss / expiry."""
        memory = await self._redis.get_session(session_id)
        return memory if memory is not None else []

    async def add_turn(
        self,
        *,
        session_id: str,
        user_message: str,
        agent_response: str,
    ) -> tuple[list[dict[str, object]], bool, bool]:
        """Append ``(user, assistant)`` to the session, return the caps.

        Returns ``(memory, suggest_reset, refuse_turn)``:
          * ``memory`` — the full transcript including the just-added
            turn (or unchanged when ``refuse_turn`` is True).
          * ``suggest_reset`` — True at or past ``SOFT_CAP``. The
            orchestrator surfaces this to the UI as a hint, not a hard
            stop.
          * ``refuse_turn`` — True at or past ``HARD_CAP``. The
            orchestrator returns a polite refusal to the user and
            does NOT persist this turn (the caller's pre-call
            ``get_memory`` already saw the over-cap state, so the
            new pair is dropped on the floor).

        The ordering matters: we read the existing memory, decide on
        the cap, and only persist when the new turn is allowed. This
        keeps the cap a contract, not a soft warning.
        """
        existing = await self._redis.get_session(session_id) or []
        memory = list(existing)
        memory.append({"role": "user", "content": user_message})
        memory.append({"role": "assistant", "content": agent_response})

        # Each turn = one user message + one assistant message.
        turn_count = len(memory) // 2
        suggest_reset = turn_count >= SOFT_CAP
        refuse_turn = turn_count > HARD_CAP

        if not refuse_turn:
            await self._redis.store_session(
                session_id=session_id, memory=memory
            )
            return memory, suggest_reset, refuse_turn

        # Refuse path: don't persist, return the existing memory
        # unchanged so the caller's UI can still show what's on file.
        return existing, suggest_reset, refuse_turn
