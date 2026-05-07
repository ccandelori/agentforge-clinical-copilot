"""Tests for ``agentforge.dashboard_auth.openemr_me.OpenEMRMeFetcher``.

The fetcher is the sidecar half of the dashboard auth bridge described
in ``docs/adr/0001-dashboard-auth-bridging.md``. It mints a lookup-
purpose JWT, hits the OpenEMR module's ``/internal/me.php`` endpoint
with a UUID, and returns the resolved ``OpenEMRIdentity`` (integer
``user_id``, username, primary GACL group).

Network is faked with :class:`httpx.MockTransport` — the real
URL/header/query shaping is exercised, no OpenEMR instance required.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx
import jwt
import pytest

from agentforge.dashboard_auth.openemr_me import (
    OpenEMRIdentity,
    OpenEMRMeFetcher,
    OpenEMRMeFetchError,
)


SECRET = "a-very-long-test-secret-that-is-at-least-32b"
BASE_URL = "https://openemr.example"


class _FrozenClock:
    """PSR-20-ish ClockInterface stand-in returning a frozen UTC instant."""

    def __init__(self, now: dt.datetime) -> None:
        self._now = now

    def now(self) -> dt.datetime:
        return self._now


def _make_fetcher(
    handler: httpx.MockTransport,
    *,
    clock: _FrozenClock | None = None,
) -> OpenEMRMeFetcher:
    return OpenEMRMeFetcher(
        http=httpx.AsyncClient(transport=handler, base_url=BASE_URL),
        base_url=BASE_URL,
        jwt_secret=SECRET,
        clock=clock or _FrozenClock(dt.datetime(2026, 5, 6, 12, 0, tzinfo=dt.UTC)),
    )


@pytest.mark.asyncio
async def test_fetch_returns_resolved_identity_on_200() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={"user_id": 17, "username": "admin", "role": "Administrators"},
        )

    fetcher = _make_fetcher(httpx.MockTransport(handler))
    identity = await fetcher.fetch(user_uuid="abc-uuid")

    assert identity == OpenEMRIdentity(
        user_id=17,
        username="admin",
        role="Administrators",
    )
    # URL hits the legacy /internal/me.php path with the UUID in the
    # query string (the controller reads ``user_uuid`` from query).
    assert "/interface/modules/custom_modules/oe-module-agentforge/public/internal/me.php" in captured["url"]
    assert "user_uuid=abc-uuid" in captured["url"]
    assert captured["method"] == "GET"
    assert captured["auth"] is not None
    assert captured["auth"].startswith("Bearer ")


@pytest.mark.asyncio
async def test_fetch_passes_a_jwt_with_correct_issuer_and_signature() -> None:
    received_token: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        received_token["token"] = auth.removeprefix("Bearer ")
        return httpx.Response(200, json={"user_id": 1, "username": "u", "role": None})

    # PyJWT's decode() validates ``exp`` against real wall-clock time
    # — give the fetcher a current-time clock so the token isn't
    # expired against ``jwt.decode``'s own check.
    now_clock = _FrozenClock(dt.datetime.now(dt.UTC))
    fetcher = _make_fetcher(httpx.MockTransport(handler), clock=now_clock)
    await fetcher.fetch(user_uuid="anything")

    # The token should validate against the same secret + issuer the
    # OpenEMR validator expects. AgentJwtValidator::validateLookupBearer
    # only requires signature + issuer + non-expired; we mirror that
    # contract here.
    decoded = jwt.decode(
        received_token["token"],
        SECRET,
        algorithms=["HS256"],
        issuer="openemr-agentforge",
    )
    assert decoded["iss"] == "openemr-agentforge"
    assert "iat" in decoded
    assert "exp" in decoded


@pytest.mark.asyncio
async def test_fetch_accepts_role_null_for_users_without_gacl_group() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"user_id": 22, "username": "orphan", "role": None},
        )

    fetcher = _make_fetcher(httpx.MockTransport(handler))
    identity = await fetcher.fetch(user_uuid="orphan-uuid")

    assert identity == OpenEMRIdentity(user_id=22, username="orphan", role=None)


@pytest.mark.asyncio
async def test_fetch_raises_on_404_with_status_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "No OpenEMR user found"})

    fetcher = _make_fetcher(httpx.MockTransport(handler))

    with pytest.raises(OpenEMRMeFetchError) as excinfo:
        await fetcher.fetch(user_uuid="nonexistent")
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_fetch_raises_on_401() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Invalid or expired token"})

    fetcher = _make_fetcher(httpx.MockTransport(handler))

    with pytest.raises(OpenEMRMeFetchError) as excinfo:
        await fetcher.fetch(user_uuid="any")
    assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_fetch_raises_on_500() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    fetcher = _make_fetcher(httpx.MockTransport(handler))

    with pytest.raises(OpenEMRMeFetchError) as excinfo:
        await fetcher.fetch(user_uuid="any")
    assert excinfo.value.status_code == 500


@pytest.mark.asyncio
async def test_fetch_raises_on_transport_failure_with_status_zero() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    fetcher = _make_fetcher(httpx.MockTransport(handler))

    with pytest.raises(OpenEMRMeFetchError) as excinfo:
        await fetcher.fetch(user_uuid="any")
    # 0 is the established sentinel for "never reached upstream"
    # (see DocumentBytesFetchError for the same convention).
    assert excinfo.value.status_code == 0


@pytest.mark.asyncio
async def test_fetch_raises_on_malformed_response_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"user_id": "not-an-int", "username": "x"})

    fetcher = _make_fetcher(httpx.MockTransport(handler))

    with pytest.raises(OpenEMRMeFetchError):
        await fetcher.fetch(user_uuid="any")
