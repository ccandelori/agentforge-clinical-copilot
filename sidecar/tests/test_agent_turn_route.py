"""Integration tests for the BFF ``POST /api/agent/turn`` route.

End-to-end wiring from cookie → session → resolved identity → minted
internal JWT → AuthGateway-validated RequestContext → orchestrator.

The Orchestrator is replaced by a stub that captures its inputs so we
can assert the bridge produces the right ``ctx`` for downstream tools.
The OpenEMR ``/me`` endpoint is faked via :class:`httpx.MockTransport`
on the OpenEMRMeFetcher.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentforge.config import Settings
from agentforge.dashboard_auth import SessionStore
from agentforge.dashboard_auth.internal_jwt import InternalJwtMinter
from agentforge.dashboard_auth.openemr_me import OpenEMRMeFetcher
from agentforge.dashboard_auth.openemr_patient_pid import OpenEMRPatientPidFetcher
from agentforge.dashboard_auth.turn_route import make_agent_turn_router
from agentforge.gateway.auth_gateway import AuthGateway, RequestContext

SECRET = "a-very-long-test-secret-that-is-at-least-32b"
SESSION_COOKIE = "agentforge_session"
OPENEMR_BASE = "https://openemr.example"


class _Clock:
    def now(self) -> dt.datetime:
        return dt.datetime.now(dt.UTC)


def _make_redis_mock() -> AsyncMock:
    storage: dict[str, str] = {}
    redis_mock = AsyncMock()

    async def get(key: str) -> str | None:
        return storage.get(key)

    async def setex(key: str, _ttl: int, value: str) -> None:
        storage[key] = value

    async def delete(*keys: str) -> int:
        n = 0
        for k in keys:
            if k in storage:
                del storage[k]
                n += 1
        return n

    redis_mock.get.side_effect = get
    redis_mock.setex.side_effect = setex
    redis_mock.delete.side_effect = delete
    return redis_mock


class _CapturingOrchestrator:
    """Minimal Orchestrator stand-in: captures ``turn`` inputs."""

    def __init__(self, reply: str = "ok") -> None:
        self.reply = reply
        self.captured: dict[str, Any] = {}

    async def turn(
        self,
        ctx: RequestContext,
        user_message: str,
        *,
        session_id: str | None = None,
        pdf_pages: Any = None,
        document_id: int | None = None,
        evidence_query: str = "",
        **_: Any,
    ) -> str:
        self.captured = {
            "ctx": ctx,
            "user_message": user_message,
            "session_id": session_id,
            "pdf_pages": pdf_pages,
            "document_id": document_id,
            "evidence_query": evidence_query,
        }
        return self.reply


def _settings() -> Settings:
    return Settings(
        anthropic_api_key="x",
        jwt_secret=SECRET,
        dashboard_session_cookie_name=SESSION_COOKIE,
        redis_url="redis://localhost:6379/0",
        hmac_key="dGVzdC1obWFjLWtleS0zMi1ieXRlcy1zZWNyZXQtdGVzdGluZw==",
    )


async def _seed_session(
    store: SessionStore,
    *,
    fhir_user: str,
) -> str:
    session = await store.create_session(
        sub="oauth-sub-uuid",
        access_token="access-tok",
        expires_at=9_999_999_999.0,
        fhir_user=fhir_user,
    )
    return session.session_id


def _build_app(
    *,
    me_response: dict[str, Any] | int = 200,
    pid_response: dict[str, Any] | int = 200,
    orchestrator: _CapturingOrchestrator | None = None,
    document_bytes_fetcher: Any = None,
    pdf_renderer: Any = None,
) -> tuple[FastAPI, _CapturingOrchestrator, SessionStore]:
    settings = _settings()
    redis = _make_redis_mock()
    session_store = SessionStore(
        redis_client=redis,
        session_ttl_seconds=settings.dashboard_session_ttl_seconds,
        pending_ttl_seconds=settings.dashboard_pending_auth_ttl_seconds,
    )

    def me_handler(request: httpx.Request) -> httpx.Response:
        if isinstance(me_response, int):
            return httpx.Response(me_response, json={"error": "synthetic"})
        return httpx.Response(200, json=me_response)

    def pid_handler(request: httpx.Request) -> httpx.Response:
        if isinstance(pid_response, int):
            return httpx.Response(pid_response, json={"error": "synthetic"})
        return httpx.Response(200, json=pid_response)

    me_fetcher = OpenEMRMeFetcher(
        http=httpx.AsyncClient(transport=httpx.MockTransport(me_handler)),
        base_url=OPENEMR_BASE,
        jwt_secret=SECRET,
        clock=_Clock(),
    )
    pid_fetcher = OpenEMRPatientPidFetcher(
        http=httpx.AsyncClient(transport=httpx.MockTransport(pid_handler)),
        base_url=OPENEMR_BASE,
        jwt_secret=SECRET,
        clock=_Clock(),
    )
    minter = InternalJwtMinter(jwt_secret=SECRET, clock=_Clock())
    gateway = AuthGateway(jwt_secret=SECRET, redis_client=None)

    orch = orchestrator or _CapturingOrchestrator()

    router = make_agent_turn_router(
        settings=settings,
        session_store=session_store,
        me_fetcher=me_fetcher,
        patient_pid_fetcher=pid_fetcher,
        jwt_minter=minter,
        auth_gateway=gateway,
        orchestrator=orch,  # type: ignore[arg-type]
        document_bytes_fetcher=document_bytes_fetcher,
        pdf_renderer=pdf_renderer,
    )

    app = FastAPI()
    app.include_router(router)
    return app, orch, session_store


@pytest.mark.asyncio
async def test_unauthenticated_request_returns_401() -> None:
    app, _, _ = _build_app()
    with TestClient(app) as client:
        resp = client.post(
            "/api/agent/turn",
            json={"message": "hi", "patient_uuid": "p-uuid"},
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_session_with_no_fhir_user_returns_401() -> None:
    app, _, store = _build_app()
    sid = await _seed_session(store, fhir_user="")
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={"message": "hi", "patient_uuid": "p-uuid"},
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_happy_path_round_trips_through_gateway_to_orchestrator() -> None:
    app, orch, store = _build_app(
        me_response={
            "user_id": 17,
            "username": "admin",
            "role": "Administrators",
        },
        pid_response={"pid": 42},
    )
    sid = await _seed_session(
        store,
        fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/abc-uuid-123",
    )

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={
                "message": "what's the patient's allergies?",
                "patient_uuid": "patient-resource-uuid",
                "session_id": "convo-1",
            },
        )

    assert resp.status_code == 200
    # AgentTurnResponse now includes a `citations` field (T38.11) that
    # defaults to [] when the orchestrator reply has no inline [type #id]
    # markers. The test reply is the bare string "ok" so no citations
    # are extracted; assert the exact shape including the empty list.
    # T38.12 adds the `extraction` field (None for chart-question turns
    # like this one — no INTAKE flow ran, so the orchestrator did not
    # stash an extraction snapshot). P1.1 adds ``persisted_resource_id``
    # (None when the orchestrator did not POST a successful persist).
    assert resp.json() == {
        "reply": "ok",
        "citations": [],
        "extraction": None,
        "persisted_resource_id": None,
    }

    # Orchestrator received the right ctx — user_id from /me, pid from
    # /patient_pid — both routed through the real AuthGateway.
    ctx: RequestContext = orch.captured["ctx"]
    assert ctx.user_id == 17
    assert ctx.patient_id == 42
    assert ctx.username == "admin"
    assert ctx.role == "Administrators"
    assert orch.captured["user_message"] == "what's the patient's allergies?"
    assert orch.captured["session_id"] == "convo-1"


@pytest.mark.asyncio
async def test_response_surfaces_per_turn_extraction_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T38.12: when the orchestrator stashed an extraction snapshot for
    the current turn (W2 INTAKE flow), the /turn route surfaces it on
    ``AgentTurnResponse.extraction`` so the dashboard drawer can render
    a confirm-able panel below the assistant bubble.

    The orchestrator-side ContextVar plumbing already exists; this test
    pins the wire shape: ``extraction`` is an opaque dict (or None)
    forwarded verbatim from ``get_last_turn_extraction``.
    """
    fake_extraction: dict[str, Any] = {
        "document_kind": "lab_report",
        "fields": {
            "patient_name": "Jane Doe",
            "collected_at": "2026-05-01",
            "tests": [{"name": "HbA1c", "value": "6.7", "units": "%"}],
        },
        "confidence": 0.92,
    }

    monkeypatch.setattr(
        "agentforge.dashboard_auth.turn_route.get_last_turn_extraction",
        lambda: fake_extraction,
    )

    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 7},
    )
    sid = await _seed_session(
        store,
        fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/u",
    )

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={"message": "intake this PDF", "patient_uuid": "p"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["extraction"] == fake_extraction


@pytest.mark.asyncio
async def test_response_extraction_is_none_when_orchestrator_did_not_stash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T38.12: chart-question turns (no INTAKE flow) leave the
    extraction ContextVar at its ``None`` default; the route must
    surface that as ``extraction: null`` rather than omitting the
    field — vue-ui consumers branch on its presence."""
    monkeypatch.setattr(
        "agentforge.dashboard_auth.turn_route.get_last_turn_extraction",
        lambda: None,
    )

    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 7},
    )
    sid = await _seed_session(
        store,
        fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/u",
    )

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={"message": "what's the dx?", "patient_uuid": "p"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "extraction" in body
    assert body["extraction"] is None


@pytest.mark.asyncio
async def test_response_surfaces_persisted_resource_id_when_orchestrator_stashed_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1.1: when the orchestrator's post-extract persist call succeeded,
    ``get_last_persisted_handle`` returns a :class:`PersistedHandle` and
    the BFF route surfaces its ``resource_id`` on
    ``AgentTurnResponse.persisted_resource_id`` so the dashboard's
    confirm-panel can route a follow-up "open this resource" action
    without a second round-trip.
    """
    from agentforge.persist import PersistedHandle

    fake_handle = PersistedHandle(
        resource_id="117",
        kind="questionnaire_response",
    )
    monkeypatch.setattr(
        "agentforge.dashboard_auth.turn_route.get_last_persisted_handle",
        lambda: fake_handle,
    )

    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 7},
    )
    sid = await _seed_session(
        store,
        fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/u",
    )

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={"message": "intake this PDF", "patient_uuid": "p"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["persisted_resource_id"] == "117"


@pytest.mark.asyncio
async def test_uuid_extraction_handles_bare_practitioner_resource() -> None:
    """``fhirUser`` can also arrive as ``Practitioner/<uuid>`` without a
    full URI prefix; the bridge must accept either shape."""
    app, orch, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 99},
    )
    sid = await _seed_session(store, fhir_user="Practitioner/just-a-uuid")

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={"message": "hi", "patient_uuid": "p"},
        )

    assert resp.status_code == 200
    assert orch.captured["ctx"].user_id == 1


@pytest.mark.asyncio
async def test_me_endpoint_404_returns_502() -> None:
    """If OpenEMR has no row for the session's UUID, the bridge surfaces
    a 502 (bad gateway)."""
    app, _, store = _build_app(me_response=404, pid_response={"pid": 1})
    sid = await _seed_session(
        store,
        fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/missing",
    )
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={"message": "hi", "patient_uuid": "p"},
        )
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_patient_pid_endpoint_404_returns_502() -> None:
    """Likewise for an unknown patient UUID — the BFF stays healthy
    but the request can't proceed without the resolved pid."""
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response=404,
    )
    sid = await _seed_session(
        store,
        fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/u",
    )
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={"message": "hi", "patient_uuid": "missing-patient"},
        )
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_request_with_empty_patient_uuid_returns_422() -> None:
    """Pydantic enforces ``min_length=1`` on patient_uuid."""
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 1},
    )
    sid = await _seed_session(
        store,
        fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x",
    )
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={"message": "hi", "patient_uuid": ""},
        )
    # FastAPI/Pydantic surfaces validation errors as 422.
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_identity_and_pid_lookups_are_cached() -> None:
    """Second turn on the same session + patient should not re-hit
    /me OR /patient_pid — caching keeps the round-trips off the hot
    path."""
    settings = _settings()
    redis = _make_redis_mock()
    session_store = SessionStore(
        redis_client=redis,
        session_ttl_seconds=settings.dashboard_session_ttl_seconds,
        pending_ttl_seconds=settings.dashboard_pending_auth_ttl_seconds,
    )

    me_invocations = {"count": 0}
    pid_invocations = {"count": 0}

    def me_handler(request: httpx.Request) -> httpx.Response:
        me_invocations["count"] += 1
        return httpx.Response(
            200,
            json={"user_id": 1, "username": "u", "role": None},
        )

    def pid_handler(request: httpx.Request) -> httpx.Response:
        pid_invocations["count"] += 1
        return httpx.Response(200, json={"pid": 99})

    me_fetcher = OpenEMRMeFetcher(
        http=httpx.AsyncClient(transport=httpx.MockTransport(me_handler)),
        base_url=OPENEMR_BASE,
        jwt_secret=SECRET,
        clock=_Clock(),
    )
    pid_fetcher = OpenEMRPatientPidFetcher(
        http=httpx.AsyncClient(transport=httpx.MockTransport(pid_handler)),
        base_url=OPENEMR_BASE,
        jwt_secret=SECRET,
        clock=_Clock(),
    )
    minter = InternalJwtMinter(jwt_secret=SECRET, clock=_Clock())
    gateway = AuthGateway(jwt_secret=SECRET, redis_client=None)
    orch = _CapturingOrchestrator()

    router = make_agent_turn_router(
        settings=settings,
        session_store=session_store,
        me_fetcher=me_fetcher,
        patient_pid_fetcher=pid_fetcher,
        jwt_minter=minter,
        auth_gateway=gateway,
        orchestrator=orch,  # type: ignore[arg-type]
    )

    app = FastAPI()
    app.include_router(router)

    sid = await _seed_session(
        session_store,
        fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/cached-uuid",
    )

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        for _ in range(3):
            resp = client.post(
                "/api/agent/turn",
                json={"message": "hi", "patient_uuid": "p-uuid"},
            )
            assert resp.status_code == 200

    # 3 turns → 1 /me call AND 1 /patient_pid call.
    assert me_invocations["count"] == 1
    assert pid_invocations["count"] == 1


# ---------------------------------------------------------------------
# T38.15 — document_id field + W2 graph hookup
# ---------------------------------------------------------------------


class _StubDocumentBytes:
    """Mirrors agentforge.tools.document_bytes.DocumentBytes shape."""

    def __init__(self, *, content: bytes, mimetype: str) -> None:
        self.content = content
        self.mimetype = mimetype


class _CapturingDocumentBytesFetcher:
    """Captures fetch() inputs and returns a fixed PDF body."""

    def __init__(
        self,
        *,
        content: bytes = b"%PDF-1.4\nstub",
        mimetype: str = "application/pdf",
    ) -> None:
        self._content = content
        self._mimetype = mimetype
        self.captured: dict[str, Any] = {}

    async def fetch(
        self, *, document_id: int, raw_token: str
    ) -> _StubDocumentBytes:
        self.captured = {
            "document_id": document_id,
            "raw_token": raw_token,
        }
        return _StubDocumentBytes(content=self._content, mimetype=self._mimetype)


class _CapturingPdfRenderer:
    """Captures render_pages() bytes and returns a fixed list of stubs."""

    def __init__(self, *, pages: list[Any] | None = None) -> None:
        self.pages = pages if pages is not None else ["page-1", "page-2"]
        self.captured: dict[str, Any] = {}

    def render_pages(self, pdf_bytes: bytes) -> list[Any]:
        self.captured = {"pdf_bytes": pdf_bytes}
        return self.pages


@pytest.mark.asyncio
async def test_request_with_document_id_is_accepted() -> None:
    """``document_id`` is no longer rejected by extra=forbid (T38.15)."""
    fetcher = _CapturingDocumentBytesFetcher()
    renderer = _CapturingPdfRenderer()
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 42},
        document_bytes_fetcher=fetcher,
        pdf_renderer=renderer,
    )
    sid = await _seed_session(
        store, fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x"
    )
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={
                "message": "extract this lab",
                "patient_uuid": "p",
                "document_id": "123",
            },
        )
    # Pre-T38.15 this would have been 422 (extra=forbid).
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_document_id_routes_through_fetcher_and_renderer_to_orchestrator() -> None:
    """When ``document_id`` is supplied, the route fetches bytes via the
    fetcher, renders them via the renderer, and forwards ``pdf_pages``
    + ``document_id`` to ``orchestrator.turn``."""
    fetcher = _CapturingDocumentBytesFetcher(content=b"%PDF-1.4\nlab-bytes")
    renderer = _CapturingPdfRenderer(pages=["page-A", "page-B", "page-C"])
    app, orch, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 42},
        document_bytes_fetcher=fetcher,
        pdf_renderer=renderer,
    )
    sid = await _seed_session(
        store, fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x"
    )

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={
                "message": "extract",
                "patient_uuid": "p",
                "document_id": "777",
            },
        )

    assert resp.status_code == 200

    # Fetcher saw the document_id (parsed to int) and a non-empty token.
    assert fetcher.captured["document_id"] == 777
    assert isinstance(fetcher.captured["raw_token"], str)
    assert len(fetcher.captured["raw_token"]) > 0

    # Renderer saw the bytes the fetcher returned.
    assert renderer.captured["pdf_bytes"] == b"%PDF-1.4\nlab-bytes"

    # Orchestrator received the rendered pages + document_id verbatim.
    assert orch.captured["pdf_pages"] == ["page-A", "page-B", "page-C"]
    assert orch.captured["document_id"] == 777


@pytest.mark.asyncio
async def test_no_document_id_does_not_invoke_fetcher_or_renderer() -> None:
    """Backwards compat: a request without document_id MUST NOT touch
    the fetcher / renderer — keeps chart-question turns unchanged."""
    fetcher = _CapturingDocumentBytesFetcher()
    renderer = _CapturingPdfRenderer()
    app, orch, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 42},
        document_bytes_fetcher=fetcher,
        pdf_renderer=renderer,
    )
    sid = await _seed_session(
        store, fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x"
    )

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={"message": "what's the patient's allergies?", "patient_uuid": "p"},
        )

    assert resp.status_code == 200

    # Neither helper saw any input.
    assert fetcher.captured == {}
    assert renderer.captured == {}

    # Orchestrator's W2 kwargs were NOT populated.
    assert orch.captured["pdf_pages"] is None
    assert orch.captured["document_id"] is None


@pytest.mark.asyncio
async def test_document_id_with_non_pdf_mimetype_returns_422() -> None:
    """If the document store records the file as image/jpeg, the renderer
    can't parse it as PDF — surface that to the panel as 422 (the same
    status the legacy /turn route uses)."""
    fetcher = _CapturingDocumentBytesFetcher(
        content=b"\xff\xd8\xff", mimetype="image/jpeg"
    )
    renderer = _CapturingPdfRenderer()
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 42},
        document_bytes_fetcher=fetcher,
        pdf_renderer=renderer,
    )
    sid = await _seed_session(
        store, fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x"
    )

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={
                "message": "extract",
                "patient_uuid": "p",
                "document_id": "5",
            },
        )

    assert resp.status_code == 422
    # Renderer must NOT have been called for a non-pdf mimetype.
    assert renderer.captured == {}


@pytest.mark.asyncio
async def test_document_bytes_transport_failure_maps_to_503() -> None:
    """Mirrors legacy main.py /turn behavior: status_code == 0 from the
    fetcher (transport failure) → 503."""
    from agentforge.tools.document_bytes import DocumentBytesFetchError

    class _FailingFetcher:
        async def fetch(self, *, document_id: int, raw_token: str) -> Any:
            raise DocumentBytesFetchError(
                status_code=0, message="transport failure"
            )

    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 42},
        document_bytes_fetcher=_FailingFetcher(),
        pdf_renderer=_CapturingPdfRenderer(),
    )
    sid = await _seed_session(
        store, fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x"
    )

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={
                "message": "extract",
                "patient_uuid": "p",
                "document_id": "5",
            },
        )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_document_bytes_upstream_error_maps_to_502() -> None:
    """Non-zero status_code from the fetcher (upstream error) → 502."""
    from agentforge.tools.document_bytes import DocumentBytesFetchError

    class _FailingFetcher:
        async def fetch(self, *, document_id: int, raw_token: str) -> Any:
            raise DocumentBytesFetchError(status_code=403, message="forbidden")

    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 42},
        document_bytes_fetcher=_FailingFetcher(),
        pdf_renderer=_CapturingPdfRenderer(),
    )
    sid = await _seed_session(
        store, fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x"
    )

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={
                "message": "extract",
                "patient_uuid": "p",
                "document_id": "5",
            },
        )
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_document_id_provided_without_fetcher_returns_500() -> None:
    """Defensive: if main.py mounts the router without a fetcher
    (misconfiguration), a request with document_id must not silently
    proceed without rendering — surface a 500 so the operator notices."""
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 42},
        # No fetcher / renderer wired.
    )
    sid = await _seed_session(
        store, fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x"
    )

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={
                "message": "extract",
                "patient_uuid": "p",
                "document_id": "5",
            },
        )
    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_document_id_must_be_positive() -> None:
    """Pydantic / route validation rejects 0 / negative document_id."""
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 42},
        document_bytes_fetcher=_CapturingDocumentBytesFetcher(),
        pdf_renderer=_CapturingPdfRenderer(),
    )
    sid = await _seed_session(
        store, fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x"
    )

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={
                "message": "extract",
                "patient_uuid": "p",
                "document_id": "0",
            },
        )
    assert resp.status_code == 422


# ---------------------------------------------------------------------
# P1.3 — evidence_query forwarding
#
# The dashboard turn route MUST accept ``evidence_query`` and forward it
# to ``orchestrator.turn``. Without this, the W2 graph's evidence
# retriever node never fires from dashboard turns (it only fires when
# ``state["query"]`` is non-empty), so guideline RAG can only be
# triggered from the legacy /turn route. See
# sidecar/src/agentforge/orchestrator/__init__.py:357 for the
# orchestrator-side kwarg that this route now plumbs through.
# ---------------------------------------------------------------------


def test_agent_turn_request_accepts_evidence_query() -> None:
    """The request schema must accept ``evidence_query`` as an optional
    string. Without this field, dashboard turns can never trigger the W2
    evidence retriever."""
    from agentforge.dashboard_auth.turn_route import AgentTurnRequest

    req = AgentTurnRequest(
        message="dapagliflozin in CKD?",
        patient_uuid="p-uuid",
        evidence_query="dapagliflozin in CKD",
    )
    assert req.evidence_query == "dapagliflozin in CKD"


def test_agent_turn_request_evidence_query_defaults_to_empty_string() -> None:
    """Back-compat: existing dashboard JS will not send the field yet, so
    omitting it must default to ``""`` (matches the legacy /turn route's
    contract — empty string means "no RAG" rather than "unset")."""
    from agentforge.dashboard_auth.turn_route import AgentTurnRequest

    req = AgentTurnRequest(message="hi", patient_uuid="p-uuid")
    assert req.evidence_query == ""


def test_agent_turn_request_extra_fields_still_forbidden() -> None:
    """Regression guard: adding ``evidence_query`` must not relax the
    ``extra="forbid"`` model_config — bogus fields should still 422 at
    parse time."""
    from pydantic import ValidationError

    from agentforge.dashboard_auth.turn_route import AgentTurnRequest

    with pytest.raises(ValidationError):
        AgentTurnRequest(  # type: ignore[call-arg]
            message="hi",
            patient_uuid="p-uuid",
            bogus_field="nope",
        )


@pytest.mark.asyncio
async def test_evidence_query_is_forwarded_to_orchestrator() -> None:
    """End-to-end: when the request carries ``evidence_query``, the route
    must pass it through to ``orchestrator.turn`` so the W2 graph's
    evidence retriever node fires."""
    app, orch, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 42},
    )
    sid = await _seed_session(
        store, fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x"
    )

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={
                "message": "dapagliflozin in CKD?",
                "patient_uuid": "p",
                "evidence_query": "dapagliflozin in CKD",
            },
        )

    assert resp.status_code == 200
    assert orch.captured["evidence_query"] == "dapagliflozin in CKD"


@pytest.mark.asyncio
async def test_evidence_query_defaults_to_empty_when_omitted() -> None:
    """Back-compat: when the dashboard does not send ``evidence_query``,
    the orchestrator must receive ``""`` so the W2 evidence node stays
    quiet (matches current chart-question behavior)."""
    app, orch, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 42},
    )
    sid = await _seed_session(
        store, fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x"
    )

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={"message": "what's the dx?", "patient_uuid": "p"},
        )

    assert resp.status_code == 200
    assert orch.captured["evidence_query"] == ""
