"""Dashboard BFF — confidential OAuth2 client for the Vue patient dashboard.

The Vue dashboard runs as a public SPA in the user's browser; it cannot
hold an OAuth2 client_secret without leaking it to anyone with DevTools.
This module is the server-side half: the sidecar holds the secret,
performs the OAuth2 token exchange, stores sessions in Redis keyed by
opaque cookies, and proxies FHIR reads to OpenEMR with the user/* scopes
public clients are forbidden from holding.

Public surface:

    make_dashboard_routers(...)
        Factory returning (auth_router, fhir_proxy_router) ready to mount
        on a FastAPI application. See ``main.create_app``.

    OAuthClient
        Async OAuth2 client (httpx-based). Exposes ``exchange_code``,
        ``refresh``, and ``userinfo``. Pure logic in
        :mod:`agentforge.dashboard_auth.oauth` covers the URL builder
        and PKCE pair generation.

    SessionStore
        Redis-backed store for browser sessions and the short-lived
        pending-auth state (CSRF state + PKCE verifier + post-login
        target URL). One-time-read semantics on pending state.
"""

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
from agentforge.dashboard_auth.routes import make_dashboard_routers
from agentforge.dashboard_auth.sessions import (
    PendingAuth,
    Session,
    SessionStore,
)

__all__ = [
    "OAuthClient",
    "OAuthError",
    "PendingAuth",
    "Session",
    "SessionStore",
    "TokenResponse",
    "UserInfo",
    "build_authorize_url",
    "decode_id_token_claims",
    "derive_code_challenge",
    "generate_pkce_pair",
    "make_dashboard_routers",
]
