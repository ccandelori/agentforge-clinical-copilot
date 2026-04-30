"""Shared pytest fixtures for the AgentForge sidecar."""

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
    """
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("HMAC_KEY", "test-hmac-key-32-bytes-aaaaaaaaaaaaa")
    get_settings.cache_clear()
    return TestClient(create_app())
