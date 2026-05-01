"""Sensitivity policy loader — YAML on disk → Redis (Task 9).

The loader runs once at sidecar startup. It reads the YAML file at the
configured path, validates it through the Pydantic schema, and pushes
each `RecordClassRule` into Redis under
`agentforge:policy:class:<name>` (JSON-encoded). Once every rule is in
place, it sets `agentforge:policy:loaded` to the policy's version.

Fail-closed semantics: a malformed YAML file or a schema violation
raises before the loaded sentinel is set, so the gateway's record-
visibility check refuses every record until a successful reload.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

import yaml

from agentforge.gateway.policy import RecordClassRule, SensitivityPolicy

POLICY_LOADED_KEY = "agentforge:policy:loaded"
POLICY_CLASS_PREFIX = "agentforge:policy:class:"


class _RedisLoaderProto(Protocol):
    """The Redis surface the loader writes through. Kept Protocol-typed
    so the loader is testable against an `AsyncMock` without a running
    Redis."""

    async def set(self, key: str, value: bytes | str) -> object: ...

    async def delete(self, *keys: str) -> object: ...

    async def keys(self, pattern: str) -> list[bytes] | list[str]: ...


def _serialize_rule(rule: RecordClassRule) -> bytes:
    return json.dumps(rule.model_dump(mode="json")).encode("utf-8")


async def load_sensitivity_policy(
    redis_client: _RedisLoaderProto,
    yaml_path: Path,
) -> SensitivityPolicy:
    """Load and publish the sensitivity policy.

    Reads the YAML, validates via Pydantic, deletes any stale class
    keys from a previous policy version, writes one key per current
    class, and finally sets the loaded-version sentinel. The order
    matters — the sentinel is the *last* thing to land so a partial
    write never looks loaded to a concurrent reader.

    Raises:
        FileNotFoundError: yaml_path does not exist.
        yaml.YAMLError: malformed YAML.
        pydantic.ValidationError: schema rejected the parsed YAML.
    """
    raw_text = yaml_path.read_text(encoding="utf-8")
    raw = yaml.safe_load(raw_text)
    policy = SensitivityPolicy.model_validate(raw)

    await _purge_stale_class_keys(redis_client, policy)
    for class_name, rule in policy.record_classes.items():
        await redis_client.set(
            POLICY_CLASS_PREFIX + class_name,
            _serialize_rule(rule),
        )
    await redis_client.set(POLICY_LOADED_KEY, str(policy.version).encode("utf-8"))
    return policy


async def _purge_stale_class_keys(
    redis_client: _RedisLoaderProto,
    policy: SensitivityPolicy,
) -> None:
    existing_raw = await redis_client.keys(POLICY_CLASS_PREFIX + "*")
    existing = {_to_str(k) for k in existing_raw}
    current = {POLICY_CLASS_PREFIX + name for name in policy.record_classes}
    stale = existing - current
    if stale:
        await redis_client.delete(*sorted(stale))


def _to_str(key: bytes | str) -> str:
    return key.decode("utf-8") if isinstance(key, bytes) else key
