"""Tests for ``agentforge.dashboard_auth.sessions``.

Behavior tests against an in-memory Redis stand-in (an :class:`AsyncMock`
shaped like the dashboard's ``_RedisProto``). Mirrors the style used in
``tests/test_redis_client.py`` so the auth surface stays consistent
with the rest of the sidecar's storage layer.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from agentforge.dashboard_auth.sessions import (
    PendingAuth,
    Session,
    SessionStore,
)


def make_redis_mock() -> AsyncMock:
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
    redis_mock._storage = storage  # exposed for assertions
    return redis_mock


def make_store() -> tuple[SessionStore, AsyncMock]:
    redis_mock = make_redis_mock()
    store = SessionStore(
        redis_client=redis_mock,
        session_ttl_seconds=8 * 3600,
        pending_ttl_seconds=10 * 60,
    )
    return store, redis_mock


# --------------------------------------------------------------------------
# Browser session
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_persists_and_round_trips() -> None:
    store, redis_mock = make_store()

    session = await store.create_session(
        sub="user-42",
        access_token="AT",
        refresh_token="RT",
        expires_at=1234567890.0,
        name="Dr. Test",
        fhir_user="Practitioner/abc",
        email="doc@example.org",
    )

    assert isinstance(session, Session)
    assert session.session_id  # non-empty
    assert session.access_token == "AT"
    # Redis got setex'd with the session_id-prefixed key
    storage: dict[str, Any] = redis_mock._storage
    assert any(key.startswith("dashboard:session:") for key in storage.keys())

    fetched = await store.get_session(session.session_id)
    assert fetched is not None
    assert fetched.sub == "user-42"
    assert fetched.fhir_user == "Practitioner/abc"
    assert fetched.access_token == "AT"


@pytest.mark.asyncio
async def test_get_session_returns_none_for_missing() -> None:
    store, _ = make_store()
    assert await store.get_session("does-not-exist") is None


@pytest.mark.asyncio
async def test_get_session_returns_none_for_empty_id() -> None:
    store, _ = make_store()
    assert await store.get_session("") is None


@pytest.mark.asyncio
async def test_delete_session_removes_the_row() -> None:
    store, _ = make_store()
    session = await store.create_session(
        sub="u",
        access_token="AT",
        expires_at=0.0,
    )
    assert await store.get_session(session.session_id) is not None

    await store.delete_session(session.session_id)
    assert await store.get_session(session.session_id) is None


@pytest.mark.asyncio
async def test_get_session_returns_none_for_corrupt_payload() -> None:
    store, redis_mock = make_store()
    redis_mock._storage["dashboard:session:bad"] = "not-json"
    assert await store.get_session("bad") is None


# --------------------------------------------------------------------------
# Pending OAuth state
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_pending_auth_persists_state_and_verifier() -> None:
    store, redis_mock = make_store()

    pending = await store.create_pending_auth(next_url="/patient/42")

    assert isinstance(pending, PendingAuth)
    assert pending.state
    assert pending.code_verifier
    assert pending.next_url == "/patient/42"
    assert any(
        key.startswith("dashboard:pending:") for key in redis_mock._storage.keys()
    )


@pytest.mark.asyncio
async def test_consume_pending_auth_round_trips_then_self_invalidates() -> None:
    store, _ = make_store()
    pending = await store.create_pending_auth(next_url="/")

    first = await store.consume_pending_auth(pending.state)
    assert first is not None
    assert first.state == pending.state
    assert first.code_verifier == pending.code_verifier
    assert first.next_url == "/"

    # Second consume returns None — pending state is one-time-use.
    second = await store.consume_pending_auth(pending.state)
    assert second is None


@pytest.mark.asyncio
async def test_consume_pending_auth_returns_none_for_unknown_state() -> None:
    store, _ = make_store()
    assert await store.consume_pending_auth("never-issued") is None


@pytest.mark.asyncio
async def test_consume_pending_auth_returns_none_for_empty_state() -> None:
    store, _ = make_store()
    assert await store.consume_pending_auth("") is None
