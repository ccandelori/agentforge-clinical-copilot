"""Redis-backed stores for dashboard browser sessions and pending auth.

Two stores share the same Redis Protocol:

* :class:`SessionStore` — long-lived browser sessions keyed by opaque
  cookie value. Holds the OAuth2 access/refresh tokens and the user
  identity claims the dashboard surfaces. TTL aligns with the working-day
  default (8h) per :class:`agentforge.config.Settings`.

* The same store also persists short-lived **pending auth state** —
  the ``state`` parameter, PKCE ``code_verifier``, and the post-login
  ``next`` target URL — between ``/auth/login`` and ``/auth/callback``.
  Pending state is consumed (deleted) on successful read so a stale
  state value can never satisfy a second callback.
"""

from __future__ import annotations

import json
import secrets
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, Field


class _RedisProto(Protocol):
    """Async Redis surface needed by the dashboard stores.

    Wider than ``agentforge.storage.redis_client._RedisProto`` because
    we need ``delete`` for explicit logout + pending-state consume.
    Tests pass an :class:`unittest.mock.AsyncMock` shaped to satisfy
    these signatures.
    """

    async def get(self, key: str) -> str | bytes | None: ...

    async def setex(self, key: str, time: int, value: str) -> object: ...

    async def delete(self, *keys: str) -> object: ...


class Session(BaseModel):
    """Browser session bound to an authenticated dashboard user."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    sub: str
    name: str | None = None
    fhir_user: str | None = None
    email: str | None = None
    access_token: str
    refresh_token: str | None = None
    expires_at: float = Field(
        ...,
        description="Unix timestamp when the access_token expires.",
    )


class PendingAuth(BaseModel):
    """Short-lived state held between ``/auth/login`` and ``/auth/callback``."""

    model_config = ConfigDict(extra="forbid")

    state: str
    code_verifier: str
    next_url: str


_SESSION_KEY_PREFIX = "dashboard:session:"
_PENDING_KEY_PREFIX = "dashboard:pending:"


def _new_token(byte_length: int = 32) -> str:
    """Generate a URL-safe random token of ``byte_length`` bytes of entropy."""
    return secrets.token_urlsafe(byte_length)


class SessionStore:
    """Redis-backed CRUD for browser sessions and pending OAuth state."""

    def __init__(
        self,
        *,
        redis_client: _RedisProto,
        session_ttl_seconds: int,
        pending_ttl_seconds: int,
    ) -> None:
        self._redis = redis_client
        self._session_ttl = session_ttl_seconds
        self._pending_ttl = pending_ttl_seconds

    # ---------- Browser session ----------

    async def create_session(
        self,
        *,
        sub: str,
        access_token: str,
        expires_at: float,
        refresh_token: str | None = None,
        name: str | None = None,
        fhir_user: str | None = None,
        email: str | None = None,
    ) -> Session:
        """Generate a new session_id and persist the session payload."""
        session = Session(
            session_id=_new_token(),
            sub=sub,
            name=name,
            fhir_user=fhir_user,
            email=email,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )
        await self._redis.setex(
            _SESSION_KEY_PREFIX + session.session_id,
            self._session_ttl,
            session.model_dump_json(),
        )
        return session

    async def get_session(self, session_id: str) -> Session | None:
        """Return the session for ``session_id`` or None if missing/invalid."""
        if not session_id:
            return None
        raw = await self._redis.get(_SESSION_KEY_PREFIX + session_id)
        if raw is None:
            return None
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        try:
            return Session.model_validate_json(text)
        except ValueError:
            return None

    async def delete_session(self, session_id: str) -> None:
        """Remove the session unconditionally."""
        if not session_id:
            return
        await self._redis.delete(_SESSION_KEY_PREFIX + session_id)

    # ---------- Pending OAuth state ----------

    async def create_pending_auth(self, *, next_url: str) -> PendingAuth:
        """Generate a fresh ``state`` + PKCE pair and persist for callback."""
        from agentforge.dashboard_auth.oauth import generate_pkce_pair

        state = _new_token()
        verifier, _challenge = generate_pkce_pair()
        pending = PendingAuth(state=state, code_verifier=verifier, next_url=next_url)
        await self._redis.setex(
            _PENDING_KEY_PREFIX + state,
            self._pending_ttl,
            pending.model_dump_json(),
        )
        return pending

    async def consume_pending_auth(self, state: str) -> PendingAuth | None:
        """Read-and-delete the pending auth keyed by ``state``.

        One-time-use: a successful read deletes the row. A second read
        with the same state returns None. Mitigates state-replay if a
        callback URL is ever leaked.
        """
        if not state:
            return None
        key = _PENDING_KEY_PREFIX + state
        raw = await self._redis.get(key)
        if raw is None:
            return None
        await self._redis.delete(key)
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        try:
            return PendingAuth.model_validate_json(text)
        except ValueError:
            return None


def cast_pending_auth(value: object) -> PendingAuth:
    """Narrow ``object`` to ``PendingAuth`` for callers that need the type."""
    return cast(PendingAuth, value)
