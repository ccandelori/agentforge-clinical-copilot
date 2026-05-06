"""Tests for the /turn route's W2 inputs (MR 7 slice D).

The /turn route gains two new optional fields:

* ``document_id`` — when present, the route fetches the document
  bytes via :class:`DocumentBytesFetcher`, renders them to per-page
  PNGs via :class:`PdfRenderer`, and passes the result to
  ``orchestrator.turn(...)`` as ``pdf_pages``.
* ``evidence_query`` — passed through to the orchestrator verbatim.
  The orchestrator's ``turn()`` decides whether to route through
  the W2 graph based on whether either field is set.

Test posture: stub the orchestrator so we observe what kwargs the
route forwards. Stub the fetcher + renderer via
``app.dependency_overrides`` so the test never makes a real HTTP
call to OpenEMR or parses a PDF. The chart-question (W1) path stays
under the existing tests; this file only covers the W2 surface.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from agentforge.config import get_settings
from agentforge.gateway.auth_gateway import RequestContext, get_request_context
from agentforge.main import (
    create_app,
    get_document_bytes_fetcher,
    get_orchestrator,
    get_pdf_renderer,
)
from agentforge.tools.attach_and_extract import RenderedPage
from agentforge.tools.document_bytes import DocumentBytes, DocumentBytesFetchError


def _ctx() -> RequestContext:
    return RequestContext(
        user_id=42,
        patient_id=7,
        username="test-user",
        role="clinician",
        breakglass_flag=False,
        breakglass_reason=None,
        sensitivity_clearances=frozenset(),
        raw_token="raw.jwt.token",
    )


def _redis_mock() -> AsyncMock:
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)
    redis_mock.set = AsyncMock(return_value=True)
    redis_mock.delete = AsyncMock(return_value=0)
    redis_mock.keys = AsyncMock(return_value=[])
    redis_mock.smembers = AsyncMock(return_value=set())
    return redis_mock


def _rendered_page(page_number: int = 1) -> RenderedPage:
    return RenderedPage(
        page_number=page_number,
        png_bytes=b"\x89PNG\r\n\x1a\n",
        pixel_width=100,
        pixel_height=100,
    )


class _StubOrchestrator:
    """Captures the kwargs the /turn route forwards.

    Matches the real :class:`Orchestrator.turn` signature so the
    route handler can call us with W1 or W2 kwargs without TypeError.
    """

    def __init__(self, reply: str = "stub-reply") -> None:
        self._reply = reply
        self.calls: list[dict[str, Any]] = []

    async def turn(
        self,
        ctx: RequestContext,
        user_message: str,
        *,
        session_id: str | None = None,
        pdf_pages: list[RenderedPage] | None = None,
        document_id: int | None = None,
        evidence_query: str = "",
    ) -> str:
        self.calls.append(
            {
                "user_message": user_message,
                "session_id": session_id,
                "pdf_pages": pdf_pages,
                "document_id": document_id,
                "evidence_query": evidence_query,
                "raw_token": ctx.raw_token,
            }
        )
        return self._reply


@pytest.fixture
def w2_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, _StubOrchestrator, AsyncMock, MagicMock]]:
    """Build a TestClient with stubs for orchestrator + fetcher + renderer.

    Streaming is disabled in this fixture because the W2 cutover
    returns JSON; mixing streaming on the same request would couple
    these tests to /turn's SSE wiring.
    """
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("HMAC_KEY", "test-hmac-key")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("STREAMING_ENABLED", "false")
    monkeypatch.setenv("EVIDENCE_RETRIEVER_ENABLED", "false")
    get_settings.cache_clear()

    orchestrator = _StubOrchestrator()
    fetcher = AsyncMock()
    # PdfRenderer.render_pages is synchronous — use MagicMock so the
    # return value is the configured list rather than a coroutine.
    renderer = MagicMock()

    app = create_app(redis_client=_redis_mock())
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    app.dependency_overrides[get_request_context] = _ctx
    app.dependency_overrides[get_document_bytes_fetcher] = lambda: fetcher
    app.dependency_overrides[get_pdf_renderer] = lambda: renderer

    with TestClient(app) as client:
        yield client, orchestrator, fetcher, renderer

    get_settings.cache_clear()


def test_turn_w1_path_unchanged_when_no_w2_inputs(
    w2_client: tuple[TestClient, _StubOrchestrator, AsyncMock, MagicMock],
) -> None:
    """A request with neither ``document_id`` nor ``evidence_query``
    must NOT touch the fetcher or the renderer, and must NOT pass
    the W2 kwargs to the orchestrator's ``turn()``."""
    client, orchestrator, fetcher, renderer = w2_client

    response = client.post("/turn", json={"message": "what's the plan?"})

    assert response.status_code == 200
    assert response.json() == {"reply": "stub-reply", "extraction": None}
    assert len(orchestrator.calls) == 1
    call = orchestrator.calls[0]
    # W1 contract preserved: pdf_pages omitted (default None),
    # evidence_query empty, document_id None.
    assert call["pdf_pages"] is None
    assert call["document_id"] is None
    assert call["evidence_query"] == ""
    fetcher.fetch.assert_not_called()
    renderer.render_pages.assert_not_called()


def test_turn_with_document_id_chains_fetcher_renderer_orchestrator(
    w2_client: tuple[TestClient, _StubOrchestrator, AsyncMock, MagicMock],
) -> None:
    """When ``document_id`` is supplied: the fetcher receives the id
    and the user-bound JWT, the renderer receives the bytes, and the
    orchestrator receives the resulting per-page list."""
    client, orchestrator, fetcher, renderer = w2_client
    fetcher.fetch.return_value = DocumentBytes(
        content=b"%PDF-1.4\n...", mimetype="application/pdf"
    )
    pages = [_rendered_page(1), _rendered_page(2)]
    renderer.render_pages.return_value = pages

    response = client.post(
        "/turn",
        json={"message": "extract this intake form", "document_id": 99},
    )

    assert response.status_code == 200
    fetcher.fetch.assert_awaited_once_with(
        document_id=99, raw_token="raw.jwt.token"
    )
    renderer.render_pages.assert_called_once_with(b"%PDF-1.4\n...")

    call = orchestrator.calls[0]
    assert call["document_id"] == 99
    assert call["pdf_pages"] == pages
    assert call["evidence_query"] == ""


def test_turn_with_evidence_query_only_skips_fetch_and_render(
    w2_client: tuple[TestClient, _StubOrchestrator, AsyncMock, MagicMock],
) -> None:
    """An evidence-only turn never touches the document pipeline; the
    orchestrator receives ``evidence_query`` and an empty ``pdf_pages``
    so the graph routes to EVIDENCE."""
    client, orchestrator, fetcher, renderer = w2_client

    response = client.post(
        "/turn",
        json={
            "message": "what's the A1C target for adults?",
            "evidence_query": "A1C target adult diabetes",
        },
    )

    assert response.status_code == 200
    fetcher.fetch.assert_not_called()
    renderer.render_pages.assert_not_called()

    call = orchestrator.calls[0]
    assert call["document_id"] is None
    assert call["evidence_query"] == "A1C target adult diabetes"
    # pdf_pages is None (W1-shape preservation) — empty list and None
    # are equivalent for routing, but None matches the schema default.
    assert call["pdf_pages"] is None


def test_turn_returns_502_when_fetcher_raises_upstream_error(
    w2_client: tuple[TestClient, _StubOrchestrator, AsyncMock, MagicMock],
) -> None:
    """A 4xx / 5xx from the document-bytes endpoint maps to 502 so
    the JS panel can distinguish 'OpenEMR rejected the fetch' from
    'sidecar can't reach OpenEMR'."""
    client, orchestrator, fetcher, _ = w2_client
    fetcher.fetch.side_effect = DocumentBytesFetchError(
        status_code=403, message="cross-patient"
    )

    response = client.post(
        "/turn",
        json={"message": "extract", "document_id": 99},
    )

    assert response.status_code == 502
    # Upstream status is surfaced for the panel to display.
    assert response.json()["detail"]["sidecar_upstream_status"] == 403
    # Orchestrator never invoked when the fetch fails.
    assert orchestrator.calls == []


def test_turn_returns_503_when_fetcher_transport_failure(
    w2_client: tuple[TestClient, _StubOrchestrator, AsyncMock, MagicMock],
) -> None:
    """A transport-level failure (status_code=0) maps to 503 since
    OpenEMR was unreachable, not just unhappy."""
    client, _, fetcher, _ = w2_client
    fetcher.fetch.side_effect = DocumentBytesFetchError(
        status_code=0, message="connect refused"
    )

    response = client.post(
        "/turn",
        json={"message": "extract", "document_id": 99},
    )

    assert response.status_code == 503


def test_turn_returns_422_when_document_is_not_a_pdf(
    w2_client: tuple[TestClient, _StubOrchestrator, AsyncMock, MagicMock],
) -> None:
    """Vision extraction is PDF-only. A document with the wrong
    mimetype is a client error: the user uploaded a non-PDF."""
    client, _, fetcher, _ = w2_client
    fetcher.fetch.return_value = DocumentBytes(
        content=b"\x89PNG", mimetype="image/png"
    )

    response = client.post(
        "/turn",
        json={"message": "extract", "document_id": 99},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "pdf" in detail.lower()


def test_turn_returns_422_when_renderer_rejects_bytes(
    w2_client: tuple[TestClient, _StubOrchestrator, AsyncMock, MagicMock],
) -> None:
    """A corrupt or empty PDF surfaces from PdfRenderer as ValueError;
    map it to 422 with a helpful message."""
    client, _, fetcher, renderer = w2_client
    fetcher.fetch.return_value = DocumentBytes(
        content=b"%PDF-1.4\nbroken", mimetype="application/pdf"
    )
    renderer.render_pages.side_effect = ValueError("failed to open PDF")

    response = client.post(
        "/turn",
        json={"message": "extract", "document_id": 99},
    )

    assert response.status_code == 422


def test_turn_response_carries_extraction_when_orchestrator_populates_contextvar(
    w2_client: tuple[TestClient, _StubOrchestrator, AsyncMock, MagicMock],
) -> None:
    """When the orchestrator's _run_graph_turn populates
    ``_TURN_EXTRACTION_VAR``, the /turn endpoint must surface it in
    ``TurnResponse.extraction`` so the browser can render the
    confirm-able panel below the chat bubble. The W1 path leaves
    the ContextVar at None, so chart-question turns still get
    ``extraction: null``."""
    from agentforge.orchestrator import _TURN_EXTRACTION_VAR

    client, orchestrator, fetcher, renderer = w2_client
    fetcher.fetch.return_value = DocumentBytes(
        content=b"%PDF-1.4\nstub", mimetype="application/pdf"
    )
    renderer.render_pages.return_value = [_rendered_page(1)]

    # Drive the stub orchestrator to populate the ContextVar from
    # within its ``turn`` call so the ``/turn`` handler reads the
    # populated value (matching ``_run_graph_turn``'s real behavior).
    expected_extraction = {
        "document_id": 99,
        "patient_id": 7,
        "chief_concern": "chest pain at rest",
        "demographics": [],
        "medications": [
            {"name": "lisinopril", "dose": "10 mg", "frequency": "daily"},
        ],
    }

    original_turn = orchestrator.turn

    async def _turn_with_extraction(*args: Any, **kwargs: Any) -> str:
        _TURN_EXTRACTION_VAR.set(expected_extraction)
        return await original_turn(*args, **kwargs)

    orchestrator.turn = _turn_with_extraction  # type: ignore[method-assign]

    response = client.post(
        "/turn",
        json={"message": "extract", "document_id": 99},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == "stub-reply"
    assert payload["extraction"] == expected_extraction


def test_turn_response_extraction_is_null_for_w1_chart_question(
    w2_client: tuple[TestClient, _StubOrchestrator, AsyncMock, MagicMock],
) -> None:
    """Chart-question turns never populate the extraction ContextVar,
    so the response must serialize ``extraction: null`` rather than
    leaking a stale value from a prior turn on the same task."""
    client, _, _, _ = w2_client

    response = client.post("/turn", json={"message": "what's the plan?"})

    assert response.status_code == 200
    assert response.json()["extraction"] is None
