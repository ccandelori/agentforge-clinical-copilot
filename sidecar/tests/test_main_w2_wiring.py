"""Tests for the W2 graph wiring in ``create_app`` (MR 7 slice C).

The /turn route handler in slice D needs three things on app.state to
translate a request body's ``document_id`` into ``pdf_pages`` for the
graph: a :class:`PdfRenderer`, a :class:`DocumentBytesFetcher`, and an
:class:`Orchestrator` whose ``_agent_graph`` is wired. Each is exposed
on app.state by ``create_app`` so the route handler doesn't have to
re-derive them per request.

Tests inject lightweight overrides where construction would otherwise
hit the network or the filesystem, and then assert on what
``create_app`` stashes. The conftest fixture sets
``EVIDENCE_RETRIEVER_ENABLED=false`` so the dense + cross-encoder
models stay off the test path; this file double-checks that flag also
keeps the graph wired (with a no-op evidence node) so the orchestrator
gains the W2 surface even when the corpus is unavailable.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from agentforge.config import get_settings
from agentforge.main import create_app
from agentforge.orchestrator import Orchestrator
from agentforge.tools.attach_and_extract import PdfRenderer
from agentforge.tools.document_bytes import DocumentBytesFetcher


def _redis_mock() -> AsyncMock:
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)
    redis_mock.set = AsyncMock(return_value=True)
    redis_mock.delete = AsyncMock(return_value=0)
    redis_mock.keys = AsyncMock(return_value=[])
    redis_mock.smembers = AsyncMock(return_value=set())
    return redis_mock


def test_create_app_exposes_pdf_renderer_on_state(
    monkeypatch: object,
) -> None:
    import os

    os.environ["JWT_SECRET"] = "test-jwt-secret"
    os.environ["HMAC_KEY"] = "test-hmac-key"
    os.environ["REDIS_URL"] = "redis://localhost:6379/0"
    os.environ["EVIDENCE_RETRIEVER_ENABLED"] = "false"
    get_settings.cache_clear()
    try:
        app = create_app(redis_client=_redis_mock())
        assert isinstance(app.state.pdf_renderer, PdfRenderer)
    finally:
        for key in (
            "JWT_SECRET",
            "HMAC_KEY",
            "REDIS_URL",
            "EVIDENCE_RETRIEVER_ENABLED",
        ):
            os.environ.pop(key, None)
        get_settings.cache_clear()


def test_create_app_exposes_document_bytes_fetcher_on_state() -> None:
    import os

    os.environ["JWT_SECRET"] = "test-jwt-secret"
    os.environ["HMAC_KEY"] = "test-hmac-key"
    os.environ["REDIS_URL"] = "redis://localhost:6379/0"
    os.environ["EVIDENCE_RETRIEVER_ENABLED"] = "false"
    get_settings.cache_clear()
    try:
        app = create_app(redis_client=_redis_mock())
        assert isinstance(app.state.document_bytes_fetcher, DocumentBytesFetcher)
    finally:
        for key in (
            "JWT_SECRET",
            "HMAC_KEY",
            "REDIS_URL",
            "EVIDENCE_RETRIEVER_ENABLED",
        ):
            os.environ.pop(key, None)
        get_settings.cache_clear()


def test_create_app_wires_agent_graph_into_orchestrator() -> None:
    import os

    os.environ["JWT_SECRET"] = "test-jwt-secret"
    os.environ["HMAC_KEY"] = "test-hmac-key"
    os.environ["REDIS_URL"] = "redis://localhost:6379/0"
    os.environ["EVIDENCE_RETRIEVER_ENABLED"] = "false"
    get_settings.cache_clear()
    try:
        app = create_app(redis_client=_redis_mock())
        orchestrator = app.state.orchestrator
        assert isinstance(orchestrator, Orchestrator)
        # The W2 cutover is "graph wired" when the orchestrator holds
        # an agent_graph reference. Tests without W2 inputs still go
        # through the W1 iterative path; the graph is dormant until a
        # /turn request supplies pdf_pages or evidence_query.
        assert orchestrator._agent_graph is not None
    finally:
        for key in (
            "JWT_SECRET",
            "HMAC_KEY",
            "REDIS_URL",
            "EVIDENCE_RETRIEVER_ENABLED",
        ):
            os.environ.pop(key, None)
        get_settings.cache_clear()


def test_health_endpoint_still_reachable_after_wiring() -> None:
    """Sanity: the wiring changes don't break the most basic route."""
    import os

    os.environ["JWT_SECRET"] = "test-jwt-secret"
    os.environ["HMAC_KEY"] = "test-hmac-key"
    os.environ["REDIS_URL"] = "redis://localhost:6379/0"
    os.environ["EVIDENCE_RETRIEVER_ENABLED"] = "false"
    get_settings.cache_clear()
    try:
        app = create_app(redis_client=_redis_mock())
        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json()["status"] == "healthy"
    finally:
        for key in (
            "JWT_SECRET",
            "HMAC_KEY",
            "REDIS_URL",
            "EVIDENCE_RETRIEVER_ENABLED",
        ):
            os.environ.pop(key, None)
        get_settings.cache_clear()
