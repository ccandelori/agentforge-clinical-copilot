"""Health endpoint reports sensitivity-policy load status (Task 9).

The /health endpoint already returns liveness; this test asserts it
also surfaces a `policy_loaded` boolean derived from the
`agentforge:policy:loaded` Redis sentinel. A 200-OK response with
`policy_loaded=False` is meant to be a deploy-time alarm, not a
liveness failure.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml
from fastapi.testclient import TestClient

from agentforge.config import get_settings
from agentforge.main import create_app

POLICY_FIXTURE: dict[str, object] = {
    "version": 1,
    "record_classes": {
        "behavioral_health": {
            "required_clearances": ["mental_health_authorized"],
            "encounter_categories": [11],
        },
    },
}


def _make_redis() -> tuple[AsyncMock, dict[str, bytes]]:
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
    return redis_mock, store


@pytest.fixture
def policy_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(POLICY_FIXTURE), encoding="utf-8")
    return path


def test_health_reports_policy_loaded_true_after_startup(
    monkeypatch: pytest.MonkeyPatch, policy_yaml: Path
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("HMAC_KEY", "test-hmac-key-32-bytes-aaaaaaaaaaaaa")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("SENSITIVITY_POLICY_PATH", str(policy_yaml))
    get_settings.cache_clear()

    redis_mock, _ = _make_redis()
    app = create_app(redis_client=redis_mock)
    # TestClient as a context manager triggers the FastAPI lifespan,
    # which is where the policy load actually runs after the post-Task-30
    # refactor (load can't sit in the sync factory anymore — Redis is async).
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["policy_loaded"] is True


def test_create_app_raises_when_policy_load_fails_and_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("HMAC_KEY", "test-hmac-key-32-bytes-aaaaaaaaaaaaa")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("SENSITIVITY_POLICY_PATH", str(tmp_path / "missing.yaml"))
    monkeypatch.setenv("SENSITIVITY_POLICY_REQUIRED", "true")
    get_settings.cache_clear()

    redis_mock, _ = _make_redis()
    app = create_app(redis_client=redis_mock)
    # Lifespan startup is the new failure point — entering the TestClient
    # context manager fires it. Exiting unwinds without re-raising, so we
    # assert on the enter.
    with pytest.raises(Exception):  # noqa: B017 — FileNotFoundError or wrapped variant
        with TestClient(app):
            pass


def test_create_app_continues_when_policy_load_fails_and_not_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("HMAC_KEY", "test-hmac-key-32-bytes-aaaaaaaaaaaaa")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("SENSITIVITY_POLICY_PATH", str(tmp_path / "missing.yaml"))
    monkeypatch.setenv("SENSITIVITY_POLICY_REQUIRED", "false")
    get_settings.cache_clear()

    redis_mock, _ = _make_redis()
    app = create_app(redis_client=redis_mock)
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["policy_loaded"] is False


def test_create_app_auto_constructs_redis_client_from_settings(
    monkeypatch: pytest.MonkeyPatch, policy_yaml: Path
) -> None:
    """When ``create_app()`` is called without a ``redis_client``
    keyword (the production path through
    ``uvicorn agentforge.main:create_app --factory``), the factory
    must build one from ``settings.redis_url`` and route it into
    the policy loader and the auth gateway. This closes the
    Week 1 ``policy_loaded=false`` carryforward.

    The test patches ``redis.asyncio.from_url`` to hand back the
    same in-memory ``AsyncMock`` we use elsewhere, so the wiring
    contract is exercised without a running Redis.
    """
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("HMAC_KEY", "test-hmac-key-32-bytes-aaaaaaaaaaaaa")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("SENSITIVITY_POLICY_PATH", str(policy_yaml))
    get_settings.cache_clear()

    redis_mock, _ = _make_redis()

    # Patch BOTH places redis.asyncio.from_url is referenced in
    # production: AgentRedisClient (storage) and the new factory
    # path inside create_app(). Returning the same mock from each
    # call keeps the policy load + visibility check + storage
    # all pointed at one logical store, mirroring production.
    import redis.asyncio

    monkeypatch.setattr(
        redis.asyncio,
        "from_url",
        lambda *_args, **_kwargs: redis_mock,
    )

    app = create_app()  # NO redis_client kwarg — production shape
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["policy_loaded"] is True, (
        "Auto-constructed redis_client did not flow into the policy "
        "loader; policy_loaded stayed False. Production calls "
        "create_app() with no redis_client kwarg, so this is the "
        "real-world wiring contract."
    )
