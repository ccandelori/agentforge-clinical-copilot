"""FastAPI routes for the dashboard BFF.

Two routers are produced by :func:`make_dashboard_routers`:

* ``auth_router`` mounted at ``/auth`` — implements the OAuth2
  authorization-code-with-PKCE flow on behalf of the SPA. The browser
  is redirected through OpenEMR's authorize endpoint, comes back to
  ``/auth/callback`` with a code, and the BFF exchanges that code for
  tokens server-side. The dashboard never sees the OAuth2 tokens; it
  receives only an opaque session cookie.

* ``fhir_router`` mounted at ``/api/fhir`` — proxies FHIR R4 reads to
  OpenEMR's resource server. The session cookie identifies the user,
  the BFF attaches their access_token, and the upstream response is
  forwarded verbatim. Self-signed-cert tolerance is left to the
  injected ``httpx.AsyncClient`` (the factory in ``main.py`` constructs
  it with ``verify=False`` against dev-easy).
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse

from agentforge.config import Settings
from agentforge.dashboard_auth.oauth import (
    OAuthClient,
    OAuthError,
    build_authorize_url,
    decode_id_token_claims,
    derive_code_challenge,
)
from agentforge.dashboard_auth.sessions import SessionStore

logger = logging.getLogger(__name__)


def _is_safe_next(value: str | None, *, default: str) -> str:
    """Reject open-redirect attempts; return ``default`` for unsafe inputs.

    A ``next`` parameter is considered safe when it's a same-origin
    relative path beginning with a single ``/`` (and not ``//``, which
    browsers parse as a protocol-relative URL pointing to a different
    host). Any absolute URL is rejected. Empty / missing falls back to
    ``default``.
    """
    if value is None or value == "":
        return default
    if not value.startswith("/"):
        return default
    if value.startswith("//"):
        return default
    return value


def make_dashboard_routers(
    *,
    settings: Settings,
    session_store: SessionStore,
    oauth_client: OAuthClient | None,
    fhir_http: httpx.AsyncClient | None,
) -> tuple[APIRouter, APIRouter]:
    """Construct the auth + FHIR-proxy routers.

    ``oauth_client`` and ``fhir_http`` may be ``None`` when the dashboard
    BFF is not configured (no client_id in env). In that case the routes
    are still mounted, but every request returns 503 — the dashboard
    surfaces this cleanly instead of producing opaque connection errors.
    """
    auth_router = APIRouter(prefix="/auth", tags=["dashboard-auth"])
    fhir_router = APIRouter(prefix="/api/fhir", tags=["dashboard-fhir"])

    def _bff_configured() -> bool:
        return bool(
            settings.dashboard_oauth_client_id
            and settings.dashboard_oauth_client_secret
            and settings.dashboard_oauth_authority
            and oauth_client is not None
            and fhir_http is not None
        )

    def _set_session_cookie(response: Response, session_id: str) -> None:
        response.set_cookie(
            key=settings.dashboard_session_cookie_name,
            value=session_id,
            max_age=settings.dashboard_session_ttl_seconds,
            httponly=True,
            samesite="lax",
            secure=settings.dashboard_session_cookie_secure,
            path="/",
        )

    def _clear_session_cookie(response: Response) -> None:
        response.delete_cookie(
            key=settings.dashboard_session_cookie_name,
            path="/",
        )

    @auth_router.get("/login")
    async def login(next: str | None = Query(default=None)) -> Response:  # noqa: A002 - FastAPI route param name
        if not _bff_configured():
            return JSONResponse(
                {"error": "BFF not configured"},
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        target = _is_safe_next(next, default="/")
        pending = await session_store.create_pending_auth(next_url=target)
        challenge = derive_code_challenge(pending.code_verifier)
        url = build_authorize_url(
            authority=settings.dashboard_oauth_authority,
            client_id=settings.dashboard_oauth_client_id,
            redirect_uri=settings.dashboard_oauth_redirect_uri,
            scope=settings.dashboard_oauth_scope,
            state=pending.state,
            code_challenge=challenge,
            audience=settings.dashboard_oauth_audience or None,
        )
        return RedirectResponse(url=url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    @auth_router.get("/callback")
    async def callback(
        code: str | None = Query(default=None),
        state: str | None = Query(default=None),
        error: str | None = Query(default=None),
        error_description: str | None = Query(default=None),
    ) -> Response:
        if not _bff_configured():
            return JSONResponse(
                {"error": "BFF not configured"},
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if error:
            # Bounce the user back to the dashboard's login URL with the
            # OAuth error surfaced in the query string. Empty / absent
            # error_description is fine — the LoginView handles either.
            params = {"error": error}
            if error_description:
                params["error_description"] = error_description
            return RedirectResponse(
                url=f"{settings.dashboard_app_url.rstrip('/')}/login?{urlencode(params)}",
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            )

        if not code or not state:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing code or state",
            )

        pending = await session_store.consume_pending_auth(state)
        if pending is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown or expired state",
            )

        assert oauth_client is not None  # _bff_configured() guards this
        try:
            tokens = await oauth_client.exchange_code(
                code=code,
                code_verifier=pending.code_verifier,
            )
        except OAuthError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"error": "token exchange failed", "code": exc.code},
            ) from exc

        # Identity claims come from the id_token (OIDC standard) when
        # available — the JWT carries ``sub`` plus any profile claims
        # the requested scopes resolved (``fhirUser``, ``name``,
        # ``email`` etc.). OpenEMR's /userinfo endpoint is advertised
        # in discovery but returns 404 in dev-easy 8.1.1, so we don't
        # rely on it; if it ever comes back working we'd use it as a
        # backfill, but for now id_token-only is sufficient.
        sub = ""
        name: str | None = None
        fhir_user: str | None = None
        email: str | None = None
        if tokens.id_token:
            claims = decode_id_token_claims(tokens.id_token)
            sub_raw = claims.get("sub")
            if isinstance(sub_raw, str):
                sub = sub_raw
            name_raw = claims.get("name")
            if isinstance(name_raw, str):
                name = name_raw
            fhir_user_raw = claims.get("fhirUser")
            if isinstance(fhir_user_raw, str):
                fhir_user = fhir_user_raw
            email_raw = claims.get("email")
            if isinstance(email_raw, str):
                email = email_raw

        if sub == "":
            try:
                user = await oauth_client.userinfo(access_token=tokens.access_token)
                sub = user.sub
                name = user.name or name
                fhir_user = user.fhir_user or fhir_user
                email = user.email or email
            except OAuthError as exc:
                logger.warning(
                    "userinfo fallback failed (status=%s); session created with empty sub",
                    exc.status_code,
                )

        session = await session_store.create_session(
            sub=sub,
            name=name,
            fhir_user=fhir_user,
            email=email,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_at=time.time() + tokens.expires_in,
        )

        target = pending.next_url
        # ``next_url`` is a path; the post-auth landing is on the
        # dashboard origin, not the sidecar's. Compose a same-origin
        # URL on the dashboard.
        landing = settings.dashboard_app_url.rstrip("/") + target
        response = RedirectResponse(
            url=landing,
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
        _set_session_cookie(response, session.session_id)
        return response

    @auth_router.get("/whoami")
    async def whoami(request: Request) -> Response:
        cookie = request.cookies.get(settings.dashboard_session_cookie_name)
        if not cookie:
            return JSONResponse({"authenticated": False}, status_code=status.HTTP_200_OK)
        session = await session_store.get_session(cookie)
        if session is None:
            response = JSONResponse({"authenticated": False}, status_code=status.HTTP_200_OK)
            _clear_session_cookie(response)
            return response
        return JSONResponse(
            {
                "authenticated": True,
                "user": {
                    "sub": session.sub,
                    "name": session.name,
                    "fhir_user": session.fhir_user,
                    "email": session.email,
                },
                "expires_at": session.expires_at,
            },
            status_code=status.HTTP_200_OK,
        )

    @auth_router.post("/logout")
    async def logout(request: Request) -> Response:
        cookie = request.cookies.get(settings.dashboard_session_cookie_name)
        if cookie:
            await session_store.delete_session(cookie)
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        _clear_session_cookie(response)
        return response

    # ------------------------------------------------------------------
    # FHIR proxy
    # ------------------------------------------------------------------

    @fhir_router.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def fhir_proxy(path: str, request: Request) -> Response:
        if not _bff_configured() or fhir_http is None:
            return JSONResponse(
                {"error": "BFF not configured"},
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        cookie = request.cookies.get(settings.dashboard_session_cookie_name)
        if not cookie:
            return JSONResponse(
                {"error": "Not authenticated"},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        session = await session_store.get_session(cookie)
        if session is None:
            return JSONResponse(
                {"error": "Session expired"},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        target_base = settings.dashboard_fhir_base_url.rstrip("/")
        target_url = f"{target_base}/{path}"
        # Forward query parameters verbatim — FHIR search relies on them.
        # httpx's ``params`` accepts the tuple form with str | bool | None
        # values; we only ever produce str pairs from query_params, but
        # the wider annotation matches httpx's typed surface.
        forwarded_params: list[tuple[str, str | int | float | bool | None]] = [
            (key, value) for key, value in request.query_params.multi_items()
        ]

        # Headers: forward Accept and Content-Type only; everything else
        # (Host, Cookie, Authorization, etc.) we either set ourselves or
        # don't want bridging into the upstream.
        forwarded_headers: dict[str, str] = {
            "Authorization": f"Bearer {session.access_token}",
        }
        accept = request.headers.get("accept")
        if accept:
            forwarded_headers["Accept"] = accept
        content_type = request.headers.get("content-type")
        if content_type:
            forwarded_headers["Content-Type"] = content_type

        body: bytes = b""
        if request.method not in ("GET", "DELETE"):
            body = await request.body()

        try:
            upstream = await fhir_http.request(
                method=request.method,
                url=target_url,
                params=forwarded_params,
                headers=forwarded_headers,
                content=body if body else None,
            )
        except httpx.HTTPError as exc:
            return JSONResponse(
                {"error": "FHIR upstream unreachable", "detail": str(exc)},
                status_code=status.HTTP_502_BAD_GATEWAY,
            )

        # Surface the upstream content-type but drop hop-by-hop and
        # framing headers FastAPI will recompute.
        passthrough_headers: dict[str, str] = {}
        for key in ("content-type", "etag", "last-modified", "cache-control"):
            value = upstream.headers.get(key)
            if value is not None:
                passthrough_headers[key] = value

        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=passthrough_headers,
            media_type=passthrough_headers.get("content-type"),
        )

    return auth_router, fhir_router
