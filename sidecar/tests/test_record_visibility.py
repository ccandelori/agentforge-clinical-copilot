"""Behavior tests for AuthGateway.check_record_visibility (Task 10).

Record visibility is decided on metadata only — encounter category id,
note title prefix, structural attending-only flag. The body of the
record is never read. The check short-circuits on first deny, defaults
to allow when nothing matches, and runs in constant time per call.

ARCHITECTURE.md §2 + §6.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from agentforge.gateway.auth_gateway import AuthGateway, RecordMetadata, RequestContext
from agentforge.gateway.policy_loader import (
    POLICY_CLASS_PREFIX,
    POLICY_LOADED_KEY,
    load_sensitivity_policy,
)

JWT_SECRET = "test-secret-32-bytes-or-more-padding"

POLICY_PAYLOAD: dict[str, object] = {
    "version": 1,
    "record_classes": {
        "behavioral_health": {
            "required_clearances": ["mental_health_authorized"],
            "encounter_categories": [11, 12],
        },
        "substance_abuse_cfr42": {
            "required_clearances": ["cfr42_authorized"],
            "note_title_prefixes": ["SUD:", "Substance Abuse:"],
            "note_types": ["substance_abuse"],
        },
        "attending_only": {
            "required_clearances": ["attending_override"],
            "attending_only": True,
        },
    },
}


def make_redis_with_policy() -> AsyncMock:
    """Async-mock Redis preloaded with the policy fixture."""
    store: dict[str, bytes] = {
        POLICY_LOADED_KEY: b"1",
        f"{POLICY_CLASS_PREFIX}behavioral_health": json.dumps(
            {
                "name": "behavioral_health",
                "required_clearances": ["mental_health_authorized"],
                "encounter_categories": [11, 12],
                "note_title_prefixes": [],
                "note_types": [],
                "attending_only": False,
            }
        ).encode("utf-8"),
        f"{POLICY_CLASS_PREFIX}substance_abuse_cfr42": json.dumps(
            {
                "name": "substance_abuse_cfr42",
                "required_clearances": ["cfr42_authorized"],
                "encounter_categories": [],
                "note_title_prefixes": ["SUD:", "Substance Abuse:"],
                "note_types": ["substance_abuse"],
                "attending_only": False,
            }
        ).encode("utf-8"),
        f"{POLICY_CLASS_PREFIX}attending_only": json.dumps(
            {
                "name": "attending_only",
                "required_clearances": ["attending_override"],
                "encounter_categories": [],
                "note_title_prefixes": [],
                "note_types": [],
                "attending_only": True,
            }
        ).encode("utf-8"),
    }

    redis_mock = AsyncMock()

    async def get_(key: str) -> bytes | None:
        return store.get(key)

    async def keys_(pattern: str) -> list[bytes]:
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            return [k.encode("utf-8") for k in store if k.startswith(prefix)]
        return [k.encode("utf-8") for k in store if k == pattern]

    redis_mock.get.side_effect = get_
    redis_mock.keys.side_effect = keys_
    return redis_mock


def make_ctx(
    *,
    user_id: int = 42,
    clearances: frozenset[str] = frozenset(),
    breakglass_flag: bool = False,
    breakglass_reason: str | None = None,
) -> RequestContext:
    return RequestContext(
        user_id=user_id,
        patient_id=123,
        username="jpatel",
        role="Physicians",
        breakglass_flag=breakglass_flag,
        breakglass_reason=breakglass_reason,
        sensitivity_clearances=clearances,
    )


# ---------- behavioral_health ----------


async def test_behavioral_health_record_denied_when_user_has_no_mh_clearance() -> None:
    redis_mock = make_redis_with_policy()
    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=redis_mock)
    ctx = make_ctx(clearances=frozenset())
    metadata = RecordMetadata(encounter_category=11)

    assert await gateway.check_record_visibility(ctx, metadata) is False


async def test_behavioral_health_record_allowed_when_user_has_mh_clearance() -> None:
    redis_mock = make_redis_with_policy()
    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=redis_mock)
    ctx = make_ctx(clearances=frozenset({"mental_health_authorized"}))
    metadata = RecordMetadata(encounter_category=11)

    assert await gateway.check_record_visibility(ctx, metadata) is True


async def test_record_with_unmatched_encounter_category_is_allowed_by_default() -> None:
    redis_mock = make_redis_with_policy()
    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=redis_mock)
    ctx = make_ctx(clearances=frozenset())
    metadata = RecordMetadata(encounter_category=99)  # not in 11/12

    assert await gateway.check_record_visibility(ctx, metadata) is True


# ---------- substance_abuse_cfr42 ----------


async def test_cfr42_record_by_note_title_prefix_denied_without_clearance() -> None:
    redis_mock = make_redis_with_policy()
    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=redis_mock)
    ctx = make_ctx(clearances=frozenset({"mental_health_authorized"}))
    metadata = RecordMetadata(note_title="SUD: outpatient counseling")

    assert await gateway.check_record_visibility(ctx, metadata) is False


async def test_cfr42_record_by_note_title_prefix_allowed_with_clearance() -> None:
    redis_mock = make_redis_with_policy()
    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=redis_mock)
    ctx = make_ctx(clearances=frozenset({"cfr42_authorized"}))
    metadata = RecordMetadata(note_title="SUD: outpatient counseling")

    assert await gateway.check_record_visibility(ctx, metadata) is True


async def test_cfr42_record_by_note_type_denied_without_clearance() -> None:
    redis_mock = make_redis_with_policy()
    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=redis_mock)
    ctx = make_ctx(clearances=frozenset())
    metadata = RecordMetadata(note_type="substance_abuse")

    assert await gateway.check_record_visibility(ctx, metadata) is False


async def test_cfr42_note_title_match_is_prefix_only_not_substring() -> None:
    # Casual mention of "SUD:" mid-title should NOT be treated as a CFR42
    # record. The matcher is a literal prefix check.
    redis_mock = make_redis_with_policy()
    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=redis_mock)
    ctx = make_ctx(clearances=frozenset())
    metadata = RecordMetadata(note_title="History incl. SUD: prior")

    assert await gateway.check_record_visibility(ctx, metadata) is True


# ---------- attending_only ----------


async def test_attending_only_allows_the_attending_user() -> None:
    redis_mock = make_redis_with_policy()
    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=redis_mock)
    ctx = make_ctx(user_id=42)
    metadata = RecordMetadata(attending_only=True, attending_user_id=42)

    assert await gateway.check_record_visibility(ctx, metadata) is True


async def test_attending_only_denies_unrelated_user_without_override() -> None:
    redis_mock = make_redis_with_policy()
    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=redis_mock)
    ctx = make_ctx(user_id=99, clearances=frozenset())
    metadata = RecordMetadata(attending_only=True, attending_user_id=42)

    assert await gateway.check_record_visibility(ctx, metadata) is False


async def test_attending_only_allows_holder_of_attending_override() -> None:
    redis_mock = make_redis_with_policy()
    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=redis_mock)
    ctx = make_ctx(user_id=99, clearances=frozenset({"attending_override"}))
    metadata = RecordMetadata(attending_only=True, attending_user_id=42)

    assert await gateway.check_record_visibility(ctx, metadata) is True


async def test_attending_only_with_no_attending_user_id_fails_closed() -> None:
    # Missing metadata for a rule that fired = fail-closed. The record
    # claims to be attending-only but doesn't say whose; we cannot
    # safely surface it.
    redis_mock = make_redis_with_policy()
    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=redis_mock)
    ctx = make_ctx(user_id=42, clearances=frozenset())
    metadata = RecordMetadata(attending_only=True, attending_user_id=None)

    assert await gateway.check_record_visibility(ctx, metadata) is False


# ---------- defaults / no-match ----------


async def test_no_metadata_match_returns_true_default_allow() -> None:
    redis_mock = make_redis_with_policy()
    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=redis_mock)
    ctx = make_ctx(clearances=frozenset())
    metadata = RecordMetadata()  # all fields default — no rule fires

    assert await gateway.check_record_visibility(ctx, metadata) is True


async def test_first_deny_short_circuits_subsequent_rules() -> None:
    # A record with both behavioral_health (cat=11) AND a CFR42 note
    # title is denied as soon as the first rule rejects, regardless
    # of whether the user holds the second rule's clearance.
    redis_mock = make_redis_with_policy()
    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=redis_mock)
    ctx = make_ctx(clearances=frozenset({"cfr42_authorized"}))
    metadata = RecordMetadata(encounter_category=11, note_title="SUD: visit")

    assert await gateway.check_record_visibility(ctx, metadata) is False


# ---------- breakglass ----------


async def test_breakglass_reason_does_not_silently_bypass_visibility_check() -> None:
    # MVP discipline: breakglass is recorded for the audit layer (Task
    # 34, future) but does not flip a deny to an allow. The audit log
    # is the consumer of breakglass intent; record visibility stays
    # honest.
    redis_mock = make_redis_with_policy()
    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=redis_mock)
    ctx = make_ctx(
        clearances=frozenset(),
        breakglass_flag=True,
        breakglass_reason="emergency admit; PCP unreachable",
    )
    metadata = RecordMetadata(encounter_category=11)

    assert await gateway.check_record_visibility(ctx, metadata) is False


# ---------- fail-closed when policy not loaded ----------


async def test_check_record_visibility_fails_closed_when_policy_not_loaded() -> None:
    # Empty store — no policy loaded sentinel, no class keys.
    empty_redis = AsyncMock()

    async def get_(key: str) -> bytes | None:
        return None

    async def keys_(pattern: str) -> list[bytes]:
        return []

    empty_redis.get.side_effect = get_
    empty_redis.keys.side_effect = keys_

    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=empty_redis)
    ctx = make_ctx(clearances=frozenset({"mental_health_authorized"}))
    metadata = RecordMetadata(encounter_category=11)

    assert await gateway.check_record_visibility(ctx, metadata) is False


async def test_check_record_visibility_fails_closed_when_no_redis_configured() -> None:
    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=None)
    ctx = make_ctx(clearances=frozenset({"mental_health_authorized"}))
    metadata = RecordMetadata(encounter_category=11)

    assert await gateway.check_record_visibility(ctx, metadata) is False


# ---------- latency ----------


@pytest.mark.parametrize("iteration", range(20))
async def test_check_record_visibility_latency_under_5ms(iteration: int) -> None:
    redis_mock = make_redis_with_policy()
    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=redis_mock)
    ctx = make_ctx(clearances=frozenset({"mental_health_authorized"}))
    metadata = RecordMetadata(encounter_category=11)

    start = time.perf_counter()
    await gateway.check_record_visibility(ctx, metadata)
    elapsed_ms = (time.perf_counter() - start) * 1000

    # Even an in-memory async-mock-backed call should be well under 5ms.
    # If this fires, the policy fetch is doing something it shouldn't
    # (parsing a giant YAML on every call, blocking I/O, etc.)
    assert elapsed_ms < 5.0, f"check took {elapsed_ms:.2f}ms (iteration {iteration})"


# ---------- end-to-end policy file ----------


async def test_check_visibility_works_against_loaded_yaml_policy(tmp_path: Path) -> None:
    # Sanity: load the YAML through the production loader and run the
    # visibility check against the resulting Redis state — proves the
    # writer/reader pair round-trips correctly.
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(POLICY_PAYLOAD), encoding="utf-8")

    store: dict[str, bytes] = {}
    redis_mock = AsyncMock()

    async def set_(key: str, value: bytes | str) -> bool:
        store[key] = value.encode("utf-8") if isinstance(value, str) else value
        return True

    async def get_(key: str) -> bytes | None:
        return store.get(key)

    async def delete_(*keys: str) -> int:
        for k in keys:
            store.pop(k, None)
        return len(keys)

    async def keys_(pattern: str) -> list[bytes]:
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            return [k.encode("utf-8") for k in store if k.startswith(prefix)]
        return [k.encode("utf-8") for k in store if k == pattern]

    redis_mock.set.side_effect = set_
    redis_mock.get.side_effect = get_
    redis_mock.delete.side_effect = delete_
    redis_mock.keys.side_effect = keys_

    await load_sensitivity_policy(redis_mock, path)

    gateway = AuthGateway(jwt_secret=JWT_SECRET, redis_client=redis_mock)
    ctx_no_clearance = make_ctx(clearances=frozenset())
    ctx_with_mh = make_ctx(clearances=frozenset({"mental_health_authorized"}))

    bh = RecordMetadata(encounter_category=11)
    assert await gateway.check_record_visibility(ctx_no_clearance, bh) is False
    assert await gateway.check_record_visibility(ctx_with_mh, bh) is True
