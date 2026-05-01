"""Canonical Redis key construction for the AgentForge storage layer.

Pure functions kept in their own module so every consumer of these key
shapes (the AgentRedisClient today, the verifier's tool-result cache
integration tomorrow) reuses one definition. The shapes are documented
in ARCHITECTURE.md S7.1:

  * Session memory:  ``session:{session_id}``
  * Tool result cache: ``tool:{user_id}:{patient_id}:{tool_name}:{args_hash}``

The four-dimension cache key encodes the cross-session isolation
discipline: cached PHI never leaks between users, between patients, or
between tool invocations with different arguments.
"""

from __future__ import annotations

SESSION_PREFIX = "session:"
TOOL_PREFIX = "tool:"


def session_key(session_id: str) -> str:
    """Return the Redis key for a session memory blob."""
    return f"{SESSION_PREFIX}{session_id}"


def tool_cache_key(
    *,
    user_id: int,
    patient_id: int,
    tool_name: str,
    args_hash: str,
) -> str:
    """Return the Redis key for a cached tool result.

    Keyword-only arguments to make positional-confusion bugs impossible
    at the call site - swapping ``user_id`` and ``patient_id`` would be
    a cross-patient PHI leak.
    """
    return f"{TOOL_PREFIX}{user_id}:{patient_id}:{tool_name}:{args_hash}"
