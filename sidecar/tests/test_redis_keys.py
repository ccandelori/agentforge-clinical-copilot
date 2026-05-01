"""Pure-function tests for the Redis key builders.

The key builders are tiny but load-bearing: they encode the cross-session
isolation discipline from ARCHITECTURE.md S7.1 (cache keyed by user +
patient + tool + args_hash; session memory keyed by session_id only).
Keeping them in a dedicated module with their own tests means future
code paths (e.g. the verifier's tool-result cache integration) reuse the
same canonical formats without accidentally diverging.
"""

from __future__ import annotations

from agentforge.storage.keys import session_key, tool_cache_key


def test_session_key_uses_session_prefix() -> None:
    assert session_key("abc-123") == "session:abc-123"


def test_session_key_round_trips_alphanumeric_session_ids() -> None:
    assert session_key("XYZ_456") == "session:XYZ_456"


def test_tool_cache_key_includes_all_dimensions_in_canonical_order() -> None:
    key = tool_cache_key(
        user_id=42,
        patient_id=123,
        tool_name="get_demographics",
        args_hash="deadbeef",
    )

    assert key == "tool:42:123:get_demographics:deadbeef"


def test_tool_cache_key_is_deterministic_for_same_inputs() -> None:
    a = tool_cache_key(user_id=1, patient_id=2, tool_name="t", args_hash="h")
    b = tool_cache_key(user_id=1, patient_id=2, tool_name="t", args_hash="h")

    assert a == b


def test_tool_cache_key_differs_when_user_id_differs() -> None:
    a = tool_cache_key(user_id=1, patient_id=2, tool_name="t", args_hash="h")
    b = tool_cache_key(user_id=99, patient_id=2, tool_name="t", args_hash="h")

    assert a != b


def test_tool_cache_key_differs_when_patient_id_differs() -> None:
    # Cross-patient leakage prevention: the same user looking at two
    # different charts must never collide on a cache key.
    a = tool_cache_key(user_id=1, patient_id=2, tool_name="t", args_hash="h")
    b = tool_cache_key(user_id=1, patient_id=99, tool_name="t", args_hash="h")

    assert a != b


def test_tool_cache_key_differs_when_tool_name_differs() -> None:
    a = tool_cache_key(user_id=1, patient_id=2, tool_name="t1", args_hash="h")
    b = tool_cache_key(user_id=1, patient_id=2, tool_name="t2", args_hash="h")

    assert a != b


def test_tool_cache_key_differs_when_args_hash_differs() -> None:
    a = tool_cache_key(user_id=1, patient_id=2, tool_name="t", args_hash="h1")
    b = tool_cache_key(user_id=1, patient_id=2, tool_name="t", args_hash="h2")

    assert a != b
