"""Tests for ``agentforge.dashboard_auth.oauth``.

Pure-logic helpers (PKCE, authorize URL) plus the httpx-based
:class:`OAuthClient`. Network is mocked via :class:`httpx.MockTransport`
so we exercise the real URL/header/body shaping without standing up
OpenEMR.
"""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from agentforge.dashboard_auth.oauth import (
    OAuthClient,
    OAuthError,
    TokenResponse,
    UserInfo,
    build_authorize_url,
    decode_id_token_claims,
    derive_code_challenge,
    generate_pkce_pair,
)


def test_generate_pkce_pair_returns_distinct_verifier_and_challenge() -> None:
    verifier, challenge = generate_pkce_pair()
    assert verifier != challenge
    assert 43 <= len(verifier) <= 128
    assert "=" not in verifier
    assert "+" not in verifier
    assert "/" not in verifier


def test_derive_code_challenge_is_sha256_b64url_no_padding() -> None:
    verifier = "the-quick-brown-fox-jumps-over-the-lazy-dogs"
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert derive_code_challenge(verifier) == expected


def test_generate_pkce_pair_challenge_matches_derive() -> None:
    verifier, challenge = generate_pkce_pair()
    assert challenge == derive_code_challenge(verifier)


def test_generate_pkce_pair_is_unique() -> None:
    pairs = {generate_pkce_pair() for _ in range(20)}
    assert len(pairs) == 20


def test_build_authorize_url_has_required_oauth2_params() -> None:
    url = build_authorize_url(
        authority="https://localhost:9300/oauth2/default",
        client_id="my-client",
        redirect_uri="http://localhost:5173/auth/callback",
        scope="openid offline_access user/Patient.read",
        state="csrf-state-token",
        code_challenge="challenge-string",
        audience="https://localhost:9300/apis/default/fhir",
    )
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "localhost:9300"
    assert parsed.path == "/oauth2/default/authorize"
    qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert qs["response_type"] == "code"
    assert qs["client_id"] == "my-client"
    assert qs["redirect_uri"] == "http://localhost:5173/auth/callback"
    assert qs["scope"] == "openid offline_access user/Patient.read"
    assert qs["state"] == "csrf-state-token"
    assert qs["code_challenge"] == "challenge-string"
    assert qs["code_challenge_method"] == "S256"
    assert qs["aud"] == "https://localhost:9300/apis/default/fhir"


def test_build_authorize_url_omits_aud_when_empty() -> None:
    url = build_authorize_url(
        authority="https://localhost:9300/oauth2/default",
        client_id="c",
        redirect_uri="http://localhost:5173/auth/callback",
        scope="openid",
        state="s",
        code_challenge="ch",
    )
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert "aud" not in qs


def test_build_authorize_url_strips_trailing_slash_on_authority() -> None:
    url = build_authorize_url(
        authority="https://localhost:9300/oauth2/default/",
        client_id="c",
        redirect_uri="http://localhost:5173/auth/callback",
        scope="openid",
        state="s",
        code_challenge="ch",
    )
    assert "/oauth2/default/authorize?" in url
    assert "/oauth2/default//authorize" not in url


# --------------------------------------------------------------------------
# OAuthClient — token exchange / refresh / userinfo via httpx.MockTransport
# --------------------------------------------------------------------------

AUTHORITY = "https://openemr.test/oauth2/default"
CLIENT_ID = "test-client-id"
CLIENT_SECRET = "test-client-secret"
REDIRECT_URI = "http://localhost:5173/auth/callback"


def _mock_client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


@pytest.mark.asyncio
async def test_exchange_code_posts_form_with_pkce_verifier() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["content_type"] = request.headers.get("content-type")
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(
            200,
            json={
                "access_token": "AT",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "RT",
                "id_token": "IT",
                "scope": "openid",
            },
        )

    async with _mock_client(httpx.MockTransport(handler)) as http:
        client = OAuthClient(
            authority=AUTHORITY,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            http=http,
        )
        result = await client.exchange_code(code="auth-code", code_verifier="verifier")

    assert isinstance(result, TokenResponse)
    assert result.access_token == "AT"
    assert result.refresh_token == "RT"
    assert captured["url"] == f"{AUTHORITY}/token"
    assert captured["method"] == "POST"
    assert captured["content_type"] == "application/x-www-form-urlencoded"
    body = captured["body"]
    assert isinstance(body, str)
    assert "grant_type=authorization_code" in body
    assert "code=auth-code" in body
    assert "code_verifier=verifier" in body
    assert f"client_id={CLIENT_ID}" in body
    assert f"client_secret={CLIENT_SECRET}" in body


@pytest.mark.asyncio
async def test_exchange_code_raises_oauth_error_on_4xx() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant", "error_description": "bad code"})

    async with _mock_client(httpx.MockTransport(handler)) as http:
        client = OAuthClient(
            authority=AUTHORITY,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            http=http,
        )
        with pytest.raises(OAuthError) as exc_info:
            await client.exchange_code(code="bad", code_verifier="v")

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "invalid_grant"


@pytest.mark.asyncio
async def test_refresh_posts_refresh_token_grant() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(
            200,
            json={"access_token": "AT2", "token_type": "Bearer", "expires_in": 3600},
        )

    async with _mock_client(httpx.MockTransport(handler)) as http:
        client = OAuthClient(
            authority=AUTHORITY,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            http=http,
        )
        result = await client.refresh(refresh_token="rt")

    assert result.access_token == "AT2"
    body = captured["body"]
    assert isinstance(body, str)
    assert "grant_type=refresh_token" in body
    assert "refresh_token=rt" in body


@pytest.mark.asyncio
async def test_userinfo_sends_bearer_and_parses_claims() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "sub": "user-123",
                "name": "Dr. Test",
                "fhirUser": "https://openemr.test/apis/default/fhir/Practitioner/abc",
                "email": "doc@example.org",
            },
        )

    async with _mock_client(httpx.MockTransport(handler)) as http:
        client = OAuthClient(
            authority=AUTHORITY,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            http=http,
        )
        info = await client.userinfo(access_token="AT")

    assert isinstance(info, UserInfo)
    assert info.sub == "user-123"
    assert info.name == "Dr. Test"
    assert info.fhir_user == "https://openemr.test/apis/default/fhir/Practitioner/abc"
    assert captured["url"] == f"{AUTHORITY}/userinfo"
    assert captured["auth"] == "Bearer AT"


def test_decode_id_token_claims_round_trips_payload() -> None:
    # Hand-crafted JWT — header/signature segments aren't validated by
    # the decoder (see docstring), only the payload is parsed.
    import json

    header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=").decode()
    payload_obj = {
        "sub": "abc-123",
        "name": "Dr. Test",
        "fhirUser": "https://openemr.test/apis/default/fhir/Practitioner/abc",
        "email": "doc@example.org",
    }
    payload = (
        base64.urlsafe_b64encode(json.dumps(payload_obj).encode()).rstrip(b"=").decode()
    )
    sig = "fake-signature"
    token = f"{header}.{payload}.{sig}"

    claims = decode_id_token_claims(token)
    assert claims["sub"] == "abc-123"
    assert claims["name"] == "Dr. Test"
    assert claims["fhirUser"].endswith("/Practitioner/abc")
    assert claims["email"] == "doc@example.org"


def test_decode_id_token_claims_returns_empty_for_malformed() -> None:
    assert decode_id_token_claims("") == {}
    assert decode_id_token_claims("not.a.jwt") == {}
    assert decode_id_token_claims("only-one-segment") == {}
    assert decode_id_token_claims("a.b.c.d") == {}


@pytest.mark.asyncio
async def test_userinfo_raises_oauth_error_on_401() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_token"})

    async with _mock_client(httpx.MockTransport(handler)) as http:
        client = OAuthClient(
            authority=AUTHORITY,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            http=http,
        )
        with pytest.raises(OAuthError) as exc_info:
            await client.userinfo(access_token="bad")
    assert exc_info.value.status_code == 401
