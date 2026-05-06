"""Shared pytest fixtures for the AgentForge sidecar."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from agentforge.config import get_settings
from agentforge.main import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A TestClient backed by a freshly-constructed app with test settings.

    Uses monkeypatch to provide the required JWT_SECRET and HMAC_KEY env
    vars so Settings can construct without a .env file. The lru_cache on
    get_settings is cleared so each test starts with a clean configuration.

    Passes an explicit no-op AsyncMock as ``redis_client`` so the factory
    doesn't auto-construct a real Redis connection when the test runs
    against a developer machine with redis up on :6379. Tests that need
    to assert against actual policy / record-visibility state should
    build their own fixture (see ``tests/test_health_policy_status.py``).
    """
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("HMAC_KEY", "test-hmac-key-32-bytes-aaaaaaaaaaaaa")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    # The default is already ``False``, but we re-set it here so a
    # developer with ``EVIDENCE_RETRIEVER_ENABLED=true`` exported in
    # their shell (typical when iterating on the W2 evidence path)
    # doesn't accidentally trigger a 190 MB ML-weight load on every
    # client-fixture test. Tests that DO want a retriever construct
    # their own ``create_app(evidence_retriever=...)`` call.
    monkeypatch.setenv("EVIDENCE_RETRIEVER_ENABLED", "false")
    get_settings.cache_clear()

    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)
    redis_mock.set = AsyncMock(return_value=True)
    redis_mock.delete = AsyncMock(return_value=0)
    redis_mock.keys = AsyncMock(return_value=[])
    redis_mock.smembers = AsyncMock(return_value=set())
    return TestClient(create_app(redis_client=redis_mock))
