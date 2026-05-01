"""Behavior tests for the sensitivity policy loader (Task 9).

The loader reads `sensitivity_policy.yaml` from disk, validates it
against the Pydantic schema, and pushes per-record-class rules into
Redis under `agentforge:policy:class:<name>` JSON-encoded. A single
sentinel key (`agentforge:policy:loaded`) carries the policy version
once everything is in place — the gateway uses that sentinel as the
fail-closed gate for record-visibility checks.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from agentforge.gateway.policy import SensitivityPolicy
from agentforge.gateway.policy_loader import (
    POLICY_CLASS_PREFIX,
    POLICY_LOADED_KEY,
    load_sensitivity_policy,
)
from agentforge.gateway.policy_reader import fetch_sensitivity_rules

VALID_POLICY: dict[str, object] = {
    "version": 1,
    "record_classes": {
        "behavioral_health": {
            "required_clearances": ["mental_health_authorized"],
            "encounter_categories": [11, 12],
        },
        "substance_abuse_cfr42": {
            "required_clearances": ["cfr42_authorized"],
            "note_title_prefixes": ["SUD:"],
            "note_types": ["substance_abuse"],
        },
        "attending_only": {
            "required_clearances": ["attending_override"],
            "attending_only": True,
        },
    },
}


def write_policy(path: Path, payload: object) -> Path:
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def make_recording_redis() -> tuple[AsyncMock, dict[str, bytes]]:
    """Async-mock Redis whose `set` writes to a real dict so we can
    assert post-load state (and `get` reads back from it). Pure
    AsyncMock side-effect plumbing — no real Redis."""
    store: dict[str, bytes] = {}

    redis_mock = AsyncMock()

    async def set_(key: str, value: bytes | str) -> bool:
        store[key] = value.encode("utf-8") if isinstance(value, str) else value
        return True

    async def get_(key: str) -> bytes | None:
        return store.get(key)

    async def delete_(*keys: str) -> int:
        removed = 0
        for k in keys:
            if k in store:
                del store[k]
                removed += 1
        return removed

    async def keys_(pattern: str) -> list[bytes]:
        # Minimal glob: only the suffix-`*` pattern the loader uses for cleanup.
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            return [k.encode("utf-8") for k in store if k.startswith(prefix)]
        return [k.encode("utf-8") for k in store if k == pattern]

    redis_mock.set.side_effect = set_
    redis_mock.get.side_effect = get_
    redis_mock.delete.side_effect = delete_
    redis_mock.keys.side_effect = keys_
    return redis_mock, store


async def test_load_sensitivity_policy_returns_validated_policy(tmp_path: Path) -> None:
    path = write_policy(tmp_path / "policy.yaml", VALID_POLICY)
    redis_mock, _ = make_recording_redis()

    policy = await load_sensitivity_policy(redis_mock, path)

    assert isinstance(policy, SensitivityPolicy)
    assert policy.version == 1
    assert "behavioral_health" in policy.record_classes


async def test_load_sensitivity_policy_writes_one_redis_key_per_class(
    tmp_path: Path,
) -> None:
    path = write_policy(tmp_path / "policy.yaml", VALID_POLICY)
    redis_mock, store = make_recording_redis()

    await load_sensitivity_policy(redis_mock, path)

    # One key per record class, JSON-encoded, plus the loaded sentinel.
    bh_key = f"{POLICY_CLASS_PREFIX}behavioral_health"
    cfr_key = f"{POLICY_CLASS_PREFIX}substance_abuse_cfr42"
    att_key = f"{POLICY_CLASS_PREFIX}attending_only"
    assert bh_key in store
    assert cfr_key in store
    assert att_key in store

    bh = json.loads(store[bh_key].decode("utf-8"))
    assert bh["required_clearances"] == ["mental_health_authorized"]
    assert bh["encounter_categories"] == [11, 12]


async def test_load_sensitivity_policy_sets_loaded_sentinel(tmp_path: Path) -> None:
    path = write_policy(tmp_path / "policy.yaml", VALID_POLICY)
    redis_mock, store = make_recording_redis()

    await load_sensitivity_policy(redis_mock, path)

    assert store.get(POLICY_LOADED_KEY) == b"1"


async def test_load_sensitivity_policy_rejects_malformed_yaml(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(": : not valid yaml :::\n", encoding="utf-8")
    redis_mock, store = make_recording_redis()

    with pytest.raises(yaml.YAMLError):
        await load_sensitivity_policy(redis_mock, path)

    # Fail-closed: loaded sentinel never gets set on a bad load.
    assert POLICY_LOADED_KEY not in store


async def test_load_sensitivity_policy_rejects_schema_violations(tmp_path: Path) -> None:
    bad = {
        "version": 1,
        "record_classes": {
            "behavioral_health": {
                "required_clearances": [],  # empty — schema rejects
                "encounter_categories": [11],
            },
        },
    }
    path = write_policy(tmp_path / "policy.yaml", bad)
    redis_mock, store = make_recording_redis()

    with pytest.raises(Exception):  # noqa: B017 — pydantic.ValidationError or wrapper
        await load_sensitivity_policy(redis_mock, path)

    assert POLICY_LOADED_KEY not in store


async def test_load_sensitivity_policy_is_idempotent_on_reload(tmp_path: Path) -> None:
    path = write_policy(tmp_path / "policy.yaml", VALID_POLICY)
    redis_mock, store = make_recording_redis()

    await load_sensitivity_policy(redis_mock, path)
    snapshot = dict(store)

    await load_sensitivity_policy(redis_mock, path)

    # Same content; loading the same YAML twice should not produce
    # extra keys or different values.
    assert store == snapshot


async def test_load_sensitivity_policy_clears_stale_class_keys(tmp_path: Path) -> None:
    # If a previous policy version had a class that the new one drops,
    # the stale Redis key for that class must be removed — otherwise
    # the gateway would still enforce the dead rule.
    path = write_policy(tmp_path / "policy.yaml", VALID_POLICY)
    redis_mock, store = make_recording_redis()

    # Pre-seed a stale class from a prior load.
    store[f"{POLICY_CLASS_PREFIX}deprecated_class"] = b"{}"

    await load_sensitivity_policy(redis_mock, path)

    assert f"{POLICY_CLASS_PREFIX}deprecated_class" not in store


async def test_fetch_sensitivity_rules_round_trips_loaded_policy(tmp_path: Path) -> None:
    path = write_policy(tmp_path / "policy.yaml", VALID_POLICY)
    redis_mock, _ = make_recording_redis()

    await load_sensitivity_policy(redis_mock, path)
    policy = await fetch_sensitivity_rules(redis_mock)

    assert isinstance(policy, SensitivityPolicy)
    assert set(policy.record_classes.keys()) == {
        "behavioral_health",
        "substance_abuse_cfr42",
        "attending_only",
    }


async def test_fetch_sensitivity_rules_returns_none_when_not_loaded() -> None:
    redis_mock, _ = make_recording_redis()

    policy = await fetch_sensitivity_rules(redis_mock)

    assert policy is None
