"""Integration tests for the dashboard BFF routers.

Builds a minimal FastAPI app with the dashboard routers mounted (no
``create_app`` dependency) so the tests stay fast and focused on the
auth + proxy surface. Network is mocked via :class:`httpx.MockTransport`;
Redis is the in-memory AsyncMock stand-in shared with
``test_dashboard_auth_sessions``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentforge.config import Settings
from agentforge.dashboard_auth import (
    OAuthClient,
    SessionStore,
    make_dashboard_routers,
)


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
    redis_mock._storage = storage
    return redis_mock


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        jwt_secret="test-jwt-secret",
        hmac_key="test-hmac-key-32-bytes-aaaaaaaaaaaaa",
        redis_url="redis://localhost:6379/0",
        dashboard_oauth_authority="https://openemr.test/oauth2/default",
        dashboard_oauth_client_id="dash-client",
        dashboard_oauth_client_secret="dash-secret",
        dashboard_oauth_redirect_uri="http://localhost:5173/auth/callback",
        dashboard_oauth_post_logout_redirect_uri="http://localhost:5173/",
        dashboard_oauth_scope="openid offline_access user/Patient.read",
        dashboard_oauth_audience="https://openemr.test/apis/default/fhir",
        dashboard_app_url="http://localhost:5173/",
        dashboard_fhir_base_url="https://openemr.test/apis/default/fhir",
        dashboard_session_cookie_name="agentforge_session",
        dashboard_session_ttl_seconds=8 * 3600,
        dashboard_pending_auth_ttl_seconds=10 * 60,
        dashboard_session_cookie_secure=False,
    )


def _build_app(
    *,
    settings: Settings,
    oauth_handler: httpx.MockTransport | None = None,
    fhir_handler: httpx.MockTransport | None = None,
) -> tuple[FastAPI, AsyncMock]:
    """Construct an app with mounted dashboard routers + injected mocks."""
    redis_mock = _make_redis_mock()
    session_store = SessionStore(
        redis_client=redis_mock,
        session_ttl_seconds=settings.dashboard_session_ttl_seconds,
        pending_ttl_seconds=settings.dashboard_pending_auth_ttl_seconds,
    )

    oauth_http = httpx.AsyncClient(transport=oauth_handler) if oauth_handler else None
    oauth_client = (
        OAuthClient(
            authority=settings.dashboard_oauth_authority,
            client_id=settings.dashboard_oauth_client_id,
            client_secret=settings.dashboard_oauth_client_secret,
            redirect_uri=settings.dashboard_oauth_redirect_uri,
            http=oauth_http,
        )
        if oauth_http is not None
        else None
    )

    fhir_http = httpx.AsyncClient(transport=fhir_handler) if fhir_handler else None

    auth_router, fhir_router = make_dashboard_routers(
        settings=settings,
        session_store=session_store,
        oauth_client=oauth_client,
        fhir_http=fhir_http,
    )
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(fhir_router)
    return app, redis_mock


# --------------------------------------------------------------------------
# /auth/login
# --------------------------------------------------------------------------


def test_login_redirects_to_authorize_with_pkce_and_state() -> None:
    app, redis_mock = _build_app(
        settings=_settings(),
        oauth_handler=httpx.MockTransport(lambda _r: httpx.Response(500)),
        fhir_handler=httpx.MockTransport(lambda _r: httpx.Response(500)),
    )
    with TestClient(app) as client:
        resp = client.get("/auth/login", follow_redirects=False)

    assert resp.status_code == 307
    location = resp.headers["location"]
    assert location.startswith("https://openemr.test/oauth2/default/authorize?")
    # The pending-auth row should exist in redis
    assert any(k.startswith("dashboard:pending:") for k in redis_mock._storage.keys())
    # Critical OAuth2 params present
    assert "response_type=code" in location
    assert "client_id=dash-client" in location
    assert "code_challenge_method=S256" in location
    assert "code_challenge=" in location
    assert "state=" in location
    assert "aud=https" in location  # audience encoded


def test_login_with_unsafe_next_falls_back_to_root() -> None:
    app, redis_mock = _build_app(
        settings=_settings(),
        oauth_handler=httpx.MockTransport(lambda _r: httpx.Response(500)),
        fhir_handler=httpx.MockTransport(lambda _r: httpx.Response(500)),
    )
    with TestClient(app) as client:
        resp = client.get("/auth/login?next=https://evil.example", follow_redirects=False)
    assert resp.status_code == 307
    # Pull the pending row out of redis to check next_url defaulted to "/"
    assert len(redis_mock._storage) == 1
    raw = next(iter(redis_mock._storage.values()))
    assert '"next_url":"/"' in raw


def test_login_returns_503_when_bff_unconfigured() -> None:
    bare = _settings().model_copy(
        update={"dashboard_oauth_client_id": "", "dashboard_oauth_client_secret": ""}
    )
    app, _ = _build_app(
        settings=bare,
        oauth_handler=httpx.MockTransport(lambda _r: httpx.Response(500)),
        fhir_handler=httpx.MockTransport(lambda _r: httpx.Response(500)),
    )
    with TestClient(app) as client:
        resp = client.get("/auth/login")
    assert resp.status_code == 503


# --------------------------------------------------------------------------
# /auth/callback
# --------------------------------------------------------------------------


def test_callback_unknown_state_returns_400() -> None:
    app, _ = _build_app(
        settings=_settings(),
        oauth_handler=httpx.MockTransport(lambda _r: httpx.Response(500)),
        fhir_handler=httpx.MockTransport(lambda _r: httpx.Response(500)),
    )
    with TestClient(app) as client:
        resp = client.get("/auth/callback?code=abc&state=never-seen")
    assert resp.status_code == 400


def test_callback_exchanges_code_creates_session_sets_cookie() -> None:
    settings = _settings()

    def oauth_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": "AT-final",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": "RT-final",
                },
            )
        if request.url.path.endswith("/userinfo"):
            return httpx.Response(
                200,
                json={
                    "sub": "user-99",
                    "name": "Dr. Houseman",
                    "fhirUser": "https://openemr.test/apis/default/fhir/Practitioner/99",
                },
            )
        return httpx.Response(500)

    app, redis_mock = _build_app(
        settings=settings,
        oauth_handler=httpx.MockTransport(oauth_handler),
        fhir_handler=httpx.MockTransport(lambda _r: httpx.Response(500)),
    )
    with TestClient(app) as client:
        # Seed pending state via /auth/login to get a valid state token
        login_resp = client.get("/auth/login?next=/patient/77", follow_redirects=False)
        from urllib.parse import parse_qs, urlparse

        location = login_resp.headers["location"]
        state = parse_qs(urlparse(location).query)["state"][0]

        # Now hit the callback with the same state and a fake code
        resp = client.get(
            f"/auth/callback?code=fake-code&state={state}",
            follow_redirects=False,
        )

    assert resp.status_code == 307
    # Landing URL is the dashboard origin + the pending next path
    assert resp.headers["location"].rstrip("/") == "http://localhost:5173/patient/77"
    # Session cookie was set
    cookie_header = resp.headers.get("set-cookie", "")
    assert "agentforge_session=" in cookie_header
    assert "HttpOnly" in cookie_header
    # A session row landed in redis
    sessions = [
        v for k, v in redis_mock._storage.items() if k.startswith("dashboard:session:")
    ]
    assert len(sessions) == 1
    assert "AT-final" in sessions[0]
    assert "user-99" in sessions[0]


def test_callback_prefers_id_token_claims_over_userinfo() -> None:
    """When the token response includes an id_token, claims come from
    the JWT directly — no /userinfo round trip needed (and the
    OpenEMR endpoint is broken anyway)."""
    import base64
    import json

    settings = _settings()
    payload = {
        "sub": "id-token-sub",
        "name": "Practitioner From IdToken",
        "fhirUser": "Practitioner/from-id-token",
        "email": "id@example.org",
    }
    encoded_payload = (
        base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    )
    id_token = f"header.{encoded_payload}.signature"

    userinfo_called = False

    def oauth_handler(request: httpx.Request) -> httpx.Response:
        nonlocal userinfo_called
        if request.url.path.endswith("/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": "AT",
                    "id_token": id_token,
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        if request.url.path.endswith("/userinfo"):
            userinfo_called = True
            return httpx.Response(404)
        return httpx.Response(500)

    app, redis_mock = _build_app(
        settings=settings,
        oauth_handler=httpx.MockTransport(oauth_handler),
        fhir_handler=httpx.MockTransport(lambda _r: httpx.Response(500)),
    )
    with TestClient(app) as client:
        from urllib.parse import parse_qs, urlparse

        location = client.get("/auth/login", follow_redirects=False).headers["location"]
        state = parse_qs(urlparse(location).query)["state"][0]
        client.get(f"/auth/callback?code=c&state={state}", follow_redirects=False)

        whoami = client.get("/auth/whoami").json()

    assert userinfo_called is False, (
        "userinfo should not be called when id_token has sub claim"
    )
    assert whoami["authenticated"] is True
    assert whoami["user"]["sub"] == "id-token-sub"
    assert whoami["user"]["name"] == "Practitioner From IdToken"
    assert whoami["user"]["fhir_user"] == "Practitioner/from-id-token"
    assert whoami["user"]["email"] == "id@example.org"


def test_callback_falls_back_to_userinfo_when_id_token_missing() -> None:
    """If the OAuth2 server doesn't return an id_token, fall back to
    userinfo — preserves compatibility with non-OIDC OAuth2 servers."""
    settings = _settings()
    userinfo_called = False

    def oauth_handler(request: httpx.Request) -> httpx.Response:
        nonlocal userinfo_called
        if request.url.path.endswith("/token"):
            return httpx.Response(
                200,
                json={"access_token": "AT", "expires_in": 3600, "token_type": "Bearer"},
            )
        if request.url.path.endswith("/userinfo"):
            userinfo_called = True
            return httpx.Response(200, json={"sub": "userinfo-sub", "name": "From Userinfo"})
        return httpx.Response(500)

    app, _ = _build_app(
        settings=settings,
        oauth_handler=httpx.MockTransport(oauth_handler),
        fhir_handler=httpx.MockTransport(lambda _r: httpx.Response(500)),
    )
    with TestClient(app) as client:
        from urllib.parse import parse_qs, urlparse

        location = client.get("/auth/login", follow_redirects=False).headers["location"]
        state = parse_qs(urlparse(location).query)["state"][0]
        client.get(f"/auth/callback?code=c&state={state}", follow_redirects=False)
        whoami = client.get("/auth/whoami").json()

    assert userinfo_called is True
    assert whoami["user"]["sub"] == "userinfo-sub"


def test_callback_with_oauth_error_redirects_to_login_with_error() -> None:
    app, _ = _build_app(
        settings=_settings(),
        oauth_handler=httpx.MockTransport(lambda _r: httpx.Response(500)),
        fhir_handler=httpx.MockTransport(lambda _r: httpx.Response(500)),
    )
    with TestClient(app) as client:
        resp = client.get(
            "/auth/callback?error=access_denied&error_description=user+cancelled",
            follow_redirects=False,
        )
    assert resp.status_code == 307
    assert "login?error=access_denied" in resp.headers["location"]


# --------------------------------------------------------------------------
# /auth/whoami + /auth/logout
# --------------------------------------------------------------------------


def test_whoami_without_cookie_returns_unauthenticated() -> None:
    app, _ = _build_app(
        settings=_settings(),
        oauth_handler=httpx.MockTransport(lambda _r: httpx.Response(500)),
        fhir_handler=httpx.MockTransport(lambda _r: httpx.Response(500)),
    )
    with TestClient(app) as client:
        resp = client.get("/auth/whoami")
    assert resp.status_code == 200
    assert resp.json() == {"authenticated": False}


def test_whoami_with_valid_session_returns_user() -> None:
    settings = _settings()

    def oauth_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(
                200, json={"access_token": "AT", "expires_in": 3600, "token_type": "Bearer"}
            )
        if request.url.path.endswith("/userinfo"):
            return httpx.Response(
                200,
                json={
                    "sub": "user-1",
                    "name": "Practitioner Test",
                    "fhirUser": "Practitioner/test",
                    "email": "p@example.org",
                },
            )
        return httpx.Response(500)

    app, _ = _build_app(
        settings=settings,
        oauth_handler=httpx.MockTransport(oauth_handler),
        fhir_handler=httpx.MockTransport(lambda _r: httpx.Response(500)),
    )
    with TestClient(app) as client:
        # Get a valid session via login → callback
        from urllib.parse import parse_qs, urlparse

        location = client.get("/auth/login", follow_redirects=False).headers["location"]
        state = parse_qs(urlparse(location).query)["state"][0]
        client.get(f"/auth/callback?code=c&state={state}", follow_redirects=False)
        # Cookie is now in the TestClient jar
        resp = client.get("/auth/whoami")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["authenticated"] is True
    assert payload["user"]["sub"] == "user-1"
    assert payload["user"]["fhir_user"] == "Practitioner/test"
    assert payload["user"]["email"] == "p@example.org"


def test_logout_clears_session_and_cookie() -> None:
    settings = _settings()

    def oauth_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(
                200, json={"access_token": "AT", "expires_in": 3600, "token_type": "Bearer"}
            )
        if request.url.path.endswith("/userinfo"):
            return httpx.Response(200, json={"sub": "u"})
        return httpx.Response(500)

    app, redis_mock = _build_app(
        settings=settings,
        oauth_handler=httpx.MockTransport(oauth_handler),
        fhir_handler=httpx.MockTransport(lambda _r: httpx.Response(500)),
    )
    with TestClient(app) as client:
        from urllib.parse import parse_qs, urlparse

        location = client.get("/auth/login", follow_redirects=False).headers["location"]
        state = parse_qs(urlparse(location).query)["state"][0]
        client.get(f"/auth/callback?code=c&state={state}", follow_redirects=False)

        # Pre-condition: session row exists
        assert any(k.startswith("dashboard:session:") for k in redis_mock._storage.keys())

        resp = client.post("/auth/logout")

    assert resp.status_code == 204
    # Session row is gone
    assert not any(k.startswith("dashboard:session:") for k in redis_mock._storage.keys())
    # Set-Cookie clears the cookie
    assert "agentforge_session=" in resp.headers.get("set-cookie", "")


# --------------------------------------------------------------------------
# /api/fhir proxy
# --------------------------------------------------------------------------


def test_fhir_proxy_without_session_returns_401() -> None:
    app, _ = _build_app(
        settings=_settings(),
        oauth_handler=httpx.MockTransport(lambda _r: httpx.Response(500)),
        fhir_handler=httpx.MockTransport(lambda _r: httpx.Response(500)),
    )
    with TestClient(app) as client:
        resp = client.get("/api/fhir/Patient")
    assert resp.status_code == 401


def test_fhir_proxy_with_session_forwards_bearer_and_returns_body() -> None:
    settings = _settings()
    captured: dict[str, Any] = {}

    def fhir_handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["auth"] = request.headers.get("authorization")
        captured["accept"] = request.headers.get("accept")
        return httpx.Response(
            200,
            json={"resourceType": "Bundle", "total": 0, "entry": []},
            headers={"content-type": "application/fhir+json"},
        )

    def oauth_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(
                200,
                json={"access_token": "the-token", "expires_in": 3600, "token_type": "Bearer"},
            )
        if request.url.path.endswith("/userinfo"):
            return httpx.Response(200, json={"sub": "u"})
        return httpx.Response(500)

    app, _ = _build_app(
        settings=settings,
        oauth_handler=httpx.MockTransport(oauth_handler),
        fhir_handler=httpx.MockTransport(fhir_handler),
    )
    with TestClient(app) as client:
        from urllib.parse import parse_qs, urlparse

        location = client.get("/auth/login", follow_redirects=False).headers["location"]
        state = parse_qs(urlparse(location).query)["state"][0]
        client.get(f"/auth/callback?code=c&state={state}", follow_redirects=False)

        resp = client.get(
            "/api/fhir/Patient?_count=10",
            headers={"Accept": "application/fhir+json"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"resourceType": "Bundle", "total": 0, "entry": []}
    assert captured["method"] == "GET"
    assert captured["url"].startswith("https://openemr.test/apis/default/fhir/Patient")
    assert "_count=10" in captured["url"]
    assert captured["auth"] == "Bearer the-token"
    assert captured["accept"] == "application/fhir+json"


def test_fhir_proxy_returns_502_on_upstream_connection_error() -> None:
    settings = _settings()

    def fhir_handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("upstream down")

    def oauth_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(
                200, json={"access_token": "AT", "expires_in": 3600, "token_type": "Bearer"}
            )
        if request.url.path.endswith("/userinfo"):
            return httpx.Response(200, json={"sub": "u"})
        return httpx.Response(500)

    app, _ = _build_app(
        settings=settings,
        oauth_handler=httpx.MockTransport(oauth_handler),
        fhir_handler=httpx.MockTransport(fhir_handler),
    )
    with TestClient(app) as client:
        from urllib.parse import parse_qs, urlparse

        location = client.get("/auth/login", follow_redirects=False).headers["location"]
        state = parse_qs(urlparse(location).query)["state"][0]
        client.get(f"/auth/callback?code=c&state={state}", follow_redirects=False)

        resp = client.get("/api/fhir/Patient")
    assert resp.status_code == 502
