"""OAuth2 / OpenID Connect client primitives for the dashboard BFF.

Pure-logic helpers (PKCE pair generation, authorize URL construction)
plus an async :class:`OAuthClient` wrapper around ``httpx`` that performs
the authorization-code-for-token exchange, refresh, and userinfo lookups
against OpenEMR's OAuth2 endpoints.

The client never holds long-lived per-request state — all transient
state (the CSRF ``state`` token and the PKCE verifier) lives in the
:class:`agentforge.dashboard_auth.sessions.SessionStore`. The client is
therefore safe to share as a singleton across requests.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, ConfigDict, Field


def generate_pkce_pair() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` per :rfc:`7636` S256.

    The verifier is 64 bytes of randomness, base64url-encoded without
    padding (43–86 char range satisfies the spec's 43–128 cap). The
    challenge is the SHA-256 hash of the verifier, base64url-encoded
    without padding.
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    return verifier, derive_code_challenge(verifier)


def derive_code_challenge(verifier: str) -> str:
    """Compute the S256 challenge for an existing verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def decode_id_token_claims(id_token: str) -> dict[str, Any]:
    """Decode (without verification) the OIDC id_token's JSON claims.

    Returns an empty dict for malformed tokens. The id_token is delivered
    directly from OpenEMR's ``/token`` endpoint over TLS, so signature
    verification is currently skipped — fine for the dev/demo deployment.
    Production should verify against the JWKS endpoint
    (``{authority}/jwk``) before trusting these claims; tracked as a
    T38.14 follow-up.
    """
    import json

    parts = id_token.split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1]
    payload += "=" * ((4 - len(payload) % 4) % 4)
    try:
        decoded = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, OSError):
        return {}
    if not isinstance(decoded, dict):
        return {}
    return decoded


def build_authorize_url(
    *,
    authority: str,
    client_id: str,
    redirect_uri: str,
    scope: str,
    state: str,
    code_challenge: str,
    audience: str | None = None,
    extra: dict[str, str] | None = None,
) -> str:
    """Build the ``/authorize`` URL for an authorization-code-with-PKCE flow.

    ``authority`` is the OpenID Connect issuer URL (no trailing slash);
    the authorize endpoint is ``{authority}/authorize`` per OpenEMR's
    discovery document. The ``aud`` query parameter is OpenEMR-specific
    — the FHIR resource server URL it binds the issued token to — and
    is omitted when ``audience`` is empty/None.
    """
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if audience:
        params["aud"] = audience
    if extra:
        params.update(extra)
    return f"{authority.rstrip('/')}/authorize?{urlencode(params)}"


class TokenResponse(BaseModel):
    """The shape OpenEMR's ``/token`` endpoint returns on success."""

    model_config = ConfigDict(extra="ignore")

    access_token: str
    token_type: str = "Bearer"
    expires_in: int = 3600
    refresh_token: str | None = None
    id_token: str | None = None
    scope: str | None = None


class UserInfo(BaseModel):
    """Subset of ``/userinfo`` claims the dashboard surfaces.

    OpenEMR returns a richer payload (address, phone, etc.); we keep
    only the fields the dashboard actually renders so a future schema
    drift doesn't immediately break the BFF.
    """

    model_config = ConfigDict(extra="allow")

    sub: str
    name: str | None = None
    given_name: str | None = Field(default=None, alias="given_name")
    family_name: str | None = Field(default=None, alias="family_name")
    fhir_user: str | None = Field(default=None, alias="fhirUser")
    email: str | None = None


class OAuthError(RuntimeError):
    """Raised when OpenEMR's OAuth2 endpoints return an error response.

    ``status_code`` is the HTTP status from OpenEMR (or 0 when the
    request never reached the server — connection error). ``code`` is
    the OAuth2 ``error`` field from the JSON body when present.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 0,
        code: str | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.body = body


class OAuthClient:
    """Async OAuth2 client for the dashboard BFF.

    Wraps an ``httpx.AsyncClient`` so callers (the route handlers)
    don't manage the connection pool. Methods correspond to the three
    OAuth2/OIDC operations the BFF performs: token exchange after a
    successful authorize redirect, refresh against an existing
    refresh_token, and userinfo lookup with a freshly-minted
    access_token.
    """

    def __init__(
        self,
        *,
        authority: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        http: httpx.AsyncClient,
    ) -> None:
        self._authority = authority.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._http = http

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
    ) -> TokenResponse:
        """Trade an authorization code for tokens. PKCE verifier is required."""
        return await self._post_token(
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._redirect_uri,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code_verifier": code_verifier,
            },
        )

    async def refresh(self, *, refresh_token: str) -> TokenResponse:
        """Trade a refresh_token for a fresh access_token."""
        return await self._post_token(
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )

    async def userinfo(self, *, access_token: str) -> UserInfo:
        """GET the OIDC ``/userinfo`` claims for the bearer token."""
        url = f"{self._authority}/userinfo"
        try:
            response = await self._http.get(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.HTTPError as exc:
            raise OAuthError(f"userinfo request failed: {exc}") from exc

        body = self._parse_body(response)
        if not response.is_success:
            raise OAuthError(
                "userinfo returned non-success",
                status_code=response.status_code,
                body=body,
            )
        if not isinstance(body, dict):
            raise OAuthError(
                "userinfo returned non-object body",
                status_code=response.status_code,
                body=body,
            )
        return UserInfo.model_validate(body)

    async def _post_token(self, *, data: dict[str, str]) -> TokenResponse:
        url = f"{self._authority}/token"
        try:
            response = await self._http.post(
                url,
                data=data,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
        except httpx.HTTPError as exc:
            raise OAuthError(f"token request failed: {exc}") from exc

        body = self._parse_body(response)
        if not response.is_success:
            code: str | None = None
            if isinstance(body, dict):
                err = body.get("error")
                if isinstance(err, str):
                    code = err
            raise OAuthError(
                f"token endpoint returned {response.status_code}",
                status_code=response.status_code,
                code=code,
                body=body,
            )
        if not isinstance(body, dict):
            raise OAuthError(
                "token endpoint returned non-object body",
                status_code=response.status_code,
                body=body,
            )
        return TokenResponse.model_validate(body)

    @staticmethod
    def _parse_body(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return response.text
