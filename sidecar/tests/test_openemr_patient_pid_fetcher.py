"""Tests for ``agentforge.dashboard_auth.openemr_patient_pid``.

The patient-pid fetcher is the second half of the auth-bridge
identity bootstrap (ADR-0001 §5): the dashboard's session knows the
patient by FHIR Patient resource UUID; the agent's internal JWT
needs the integer ``patient_data.pid``. This client mints a lookup
JWT and calls the OpenEMR module's ``/internal/patient_pid.php``.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx
import pytest

from agentforge.dashboard_auth.openemr_patient_pid import (
    OpenEMRPatientPidFetcher,
    OpenEMRPatientPidFetchError,
)


SECRET = "a-very-long-test-secret-that-is-at-least-32b"
BASE_URL = "https://openemr.example"


class _Clock:
    def now(self) -> dt.datetime:
        return dt.datetime.now(dt.UTC)


def _make_fetcher(handler: httpx.MockTransport) -> OpenEMRPatientPidFetcher:
    return OpenEMRPatientPidFetcher(
        http=httpx.AsyncClient(transport=handler, base_url=BASE_URL),
        base_url=BASE_URL,
        jwt_secret=SECRET,
        clock=_Clock(),
    )


@pytest.mark.asyncio
async def test_fetch_returns_int_pid_on_200() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"pid": 42})

    fetcher = _make_fetcher(httpx.MockTransport(handler))
    pid = await fetcher.fetch(patient_uuid="abc-uuid")

    assert pid == 42
    assert "/interface/modules/custom_modules/oe-module-agentforge/public/internal/patient_pid.php" in captured["url"]
    assert "patient_uuid=abc-uuid" in captured["url"]
    assert captured["auth"].startswith("Bearer ")


@pytest.mark.asyncio
async def test_fetch_raises_on_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "Not found"})

    fetcher = _make_fetcher(httpx.MockTransport(handler))

    with pytest.raises(OpenEMRPatientPidFetchError) as excinfo:
        await fetcher.fetch(patient_uuid="missing")
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_fetch_raises_on_500() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    fetcher = _make_fetcher(httpx.MockTransport(handler))

    with pytest.raises(OpenEMRPatientPidFetchError) as excinfo:
        await fetcher.fetch(patient_uuid="any")
    assert excinfo.value.status_code == 500


@pytest.mark.asyncio
async def test_fetch_raises_on_transport_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    fetcher = _make_fetcher(httpx.MockTransport(handler))

    with pytest.raises(OpenEMRPatientPidFetchError) as excinfo:
        await fetcher.fetch(patient_uuid="any")
    assert excinfo.value.status_code == 0


@pytest.mark.asyncio
async def test_fetch_raises_on_malformed_response_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"pid": "not-an-int"})

    fetcher = _make_fetcher(httpx.MockTransport(handler))

    with pytest.raises(OpenEMRPatientPidFetchError):
        await fetcher.fetch(patient_uuid="any")


@pytest.mark.asyncio
async def test_fetch_raises_on_negative_pid() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"pid": -1})

    fetcher = _make_fetcher(httpx.MockTransport(handler))

    with pytest.raises(OpenEMRPatientPidFetchError):
        await fetcher.fetch(patient_uuid="any")
