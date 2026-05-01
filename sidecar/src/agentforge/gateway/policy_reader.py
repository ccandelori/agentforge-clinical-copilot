"""Reader for the sensitivity policy stored in Redis (Task 9 / 10).

The gateway calls `fetch_sensitivity_rules` from `check_record_visibility`
to assemble a `SensitivityPolicy` snapshot from the keys the loader
populated. Returning `None` when the loaded sentinel is missing lets
the visibility check fail closed without raising — the caller treats
"no policy" the same way it treats "rule denies access".
"""

from __future__ import annotations

import json
from typing import Protocol

from agentforge.gateway.policy import RecordClassRule, SensitivityPolicy
from agentforge.gateway.policy_loader import POLICY_CLASS_PREFIX, POLICY_LOADED_KEY


class _RedisReaderProto(Protocol):
    """The Redis surface the reader needs."""

    async def get(self, key: str) -> bytes | None: ...

    async def keys(self, pattern: str) -> list[bytes] | list[str]: ...


async def fetch_sensitivity_rules(
    redis_client: _RedisReaderProto,
) -> SensitivityPolicy | None:
    """Reassemble the policy snapshot from Redis, or `None` if the
    loaded sentinel is absent."""
    sentinel = await redis_client.get(POLICY_LOADED_KEY)
    if sentinel is None:
        return None

    version_str = sentinel.decode("utf-8") if isinstance(sentinel, bytes) else sentinel
    try:
        version = int(version_str)
    except ValueError:
        return None

    class_keys_raw = await redis_client.keys(POLICY_CLASS_PREFIX + "*")
    record_classes: dict[str, RecordClassRule] = {}
    for raw_key in class_keys_raw:
        key = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else raw_key
        payload = await redis_client.get(key)
        if payload is None:
            continue
        rule_dict = json.loads(
            payload.decode("utf-8") if isinstance(payload, bytes) else payload
        )
        rule = RecordClassRule.model_validate(rule_dict)
        record_classes[rule.name] = rule

    return SensitivityPolicy(version=version, record_classes=record_classes)
