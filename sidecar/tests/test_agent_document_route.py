"""Integration tests for the BFF ``GET /api/agent/document/{id}`` route (T38.16).

Same auth pipeline as the ``/api/agent/turn`` BFF route — cookie →
session → resolved identity → minted internal JWT → AuthGateway-
validated RequestContext → JWT-authed PHP fetch via
:class:`DocumentBytesFetcher`. The PHP endpoint enforces patient
scoping on the JWT claim, so an injected fake fetcher is enough to
cover error mapping without standing up the upstream.

The route is the document-fetch half of the citation overlay: vue-ui's
``<DocumentViewer>`` calls this endpoint to load a stored PDF inline
when the bbox-overlay hits a ``[document #N]`` citation.
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
from agentforge.dashboard_auth.document_route import make_agent_document_router
from agentforge.dashboard_auth.internal_jwt import InternalJwtMinter
from agentforge.dashboard_auth.openemr_me import OpenEMRMeFetcher
from agentforge.dashboard_auth.openemr_patient_pid import OpenEMRPatientPidFetcher
from agentforge.gateway.auth_gateway import AuthGateway
from agentforge.tools.document_bytes import (
    DocumentBytes,
    DocumentBytesFetchError,
)

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


class _FakeDocumentBytesFetcher:
    """Stand-in for :class:`DocumentBytesFetcher` that records calls.

    The real fetcher hits a JWT-authed PHP endpoint. The route logic
    under test is "auth pipeline + error mapping"; the fetcher contract
    is covered in ``test_tools_document_bytes.py``. Either supply a
    successful :class:`DocumentBytes` response or an exception to raise.
    """

    def __init__(
        self,
        *,
        result: DocumentBytes | None = None,
        error: DocumentBytesFetchError | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def fetch(self, *, document_id: int, raw_token: str) -> DocumentBytes:
        self.calls.append({"document_id": document_id, "raw_token": raw_token})
        if self._error is not None:
            raise self._error
        assert self._result is not None  # test misconfiguration
        return self._result


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
    fetcher: _FakeDocumentBytesFetcher | None = None,
) -> tuple[FastAPI, _FakeDocumentBytesFetcher, SessionStore]:
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

    document_fetcher = fetcher or _FakeDocumentBytesFetcher(
        result=DocumentBytes(
            content=b"%PDF-1.4\nstub-bytes",
            mimetype="application/pdf",
        ),
    )

    router = make_agent_document_router(
        settings=settings,
        session_store=session_store,
        me_fetcher=me_fetcher,
        patient_pid_fetcher=pid_fetcher,
        jwt_minter=minter,
        auth_gateway=gateway,
        document_bytes_fetcher=document_fetcher,  # type: ignore[arg-type]
    )

    app = FastAPI()
    app.include_router(router)
    return app, document_fetcher, session_store


@pytest.mark.asyncio
async def test_unauthenticated_request_returns_401() -> None:
    app, _, _ = _build_app()
    with TestClient(app) as client:
        resp = client.get("/api/agent/document/42?patient_uuid=p-uuid")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_session_with_no_fhir_user_returns_401() -> None:
    app, _, store = _build_app()
    sid = await _seed_session(store, fhir_user="")
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.get("/api/agent/document/42?patient_uuid=p-uuid")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_missing_patient_uuid_returns_422() -> None:
    """Patient UUID is a required query param — FastAPI surfaces missing
    required params as 422."""
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
        resp = client.get("/api/agent/document/42")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_empty_patient_uuid_returns_422() -> None:
    """Empty patient_uuid fails the min_length=1 constraint."""
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
        resp = client.get("/api/agent/document/42?patient_uuid=")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_happy_path_returns_pdf_bytes_and_content_type() -> None:
    pdf_bytes = b"%PDF-1.4\n%stub-content\n%%EOF"
    fetcher = _FakeDocumentBytesFetcher(
        result=DocumentBytes(content=pdf_bytes, mimetype="application/pdf"),
    )
    app, captured, store = _build_app(
        me_response={
            "user_id": 17,
            "username": "admin",
            "role": "Administrators",
        },
        pid_response={"pid": 42},
        fetcher=fetcher,
    )
    sid = await _seed_session(
        store,
        fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/abc-uuid-123",
    )

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.get(
            "/api/agent/document/77?patient_uuid=patient-resource-uuid",
        )

    assert resp.status_code == 200
    assert resp.content == pdf_bytes
    # Content-Type may include extra parameters in some FastAPI versions;
    # the canonical media type is what matters.
    assert resp.headers["content-type"].startswith("application/pdf")
    # The fetcher saw the document_id from the path and a non-empty
    # raw_token (the JWT minted by the bridge).
    assert len(captured.calls) == 1
    assert captured.calls[0]["document_id"] == 77
    assert captured.calls[0]["raw_token"] != ""


@pytest.mark.asyncio
async def test_happy_path_propagates_fetched_mimetype() -> None:
    """Non-PDF mimetypes (e.g., scanned image) ride through unchanged.

    The route is mimetype-agnostic; vue-ui decides whether it can render
    the content based on the Content-Type. This keeps the route useful
    for non-PDF documents (PNG/JPEG scans) without a second endpoint.
    """
    fetcher = _FakeDocumentBytesFetcher(
        result=DocumentBytes(content=b"\x89PNG\r\n", mimetype="image/png"),
    )
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 1},
        fetcher=fetcher,
    )
    sid = await _seed_session(
        store,
        fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/u",
    )
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.get("/api/agent/document/9?patient_uuid=p-uuid")

    assert resp.status_code == 200
    assert resp.content == b"\x89PNG\r\n"
    assert resp.headers["content-type"].startswith("image/png")


@pytest.mark.asyncio
async def test_upstream_403_returns_403() -> None:
    """When the PHP endpoint refuses (JWT patient_id mismatch), surface
    the 403 to the dashboard so vue-ui can render an explicit
    "not authorized for this patient" message."""
    fetcher = _FakeDocumentBytesFetcher(
        error=DocumentBytesFetchError(
            status_code=403,
            message="forbidden",
        ),
    )
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 1},
        fetcher=fetcher,
    )
    sid = await _seed_session(
        store,
        fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/u",
    )
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.get("/api/agent/document/42?patient_uuid=p-uuid")

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_upstream_404_returns_404() -> None:
    """Missing document → 404 directly so vue-ui can render
    "document not found" rather than a generic upstream error."""
    fetcher = _FakeDocumentBytesFetcher(
        error=DocumentBytesFetchError(status_code=404, message="not found"),
    )
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 1},
        fetcher=fetcher,
    )
    sid = await _seed_session(
        store,
        fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/u",
    )
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.get("/api/agent/document/9999?patient_uuid=p-uuid")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_transport_failure_returns_502() -> None:
    """Transport-level failure (status_code=0) → 502, mirroring the
    /turn route's "OpenEMR is unreachable from the sidecar" mapping."""
    fetcher = _FakeDocumentBytesFetcher(
        error=DocumentBytesFetchError(
            status_code=0,
            message="transport failure",
        ),
    )
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 1},
        fetcher=fetcher,
    )
    sid = await _seed_session(
        store,
        fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/u",
    )
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.get("/api/agent/document/1?patient_uuid=p-uuid")

    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_upstream_5xx_returns_502() -> None:
    """OpenEMR-side 5xx → 502 (bad gateway)."""
    fetcher = _FakeDocumentBytesFetcher(
        error=DocumentBytesFetchError(
            status_code=503,
            message="upstream unavailable",
        ),
    )
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 1},
        fetcher=fetcher,
    )
    sid = await _seed_session(
        store,
        fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/u",
    )
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.get("/api/agent/document/1?patient_uuid=p-uuid")

    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_me_endpoint_404_returns_502() -> None:
    """Bridge /me failure → 502 (matches turn_route)."""
    app, _, store = _build_app(me_response=404, pid_response={"pid": 1})
    sid = await _seed_session(
        store,
        fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/missing",
    )
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.get("/api/agent/document/42?patient_uuid=p")
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_patient_pid_endpoint_404_returns_502() -> None:
    """Bridge /patient_pid failure → 502 (matches turn_route)."""
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
        resp = client.get("/api/agent/document/1?patient_uuid=missing-patient")
    assert resp.status_code == 502
