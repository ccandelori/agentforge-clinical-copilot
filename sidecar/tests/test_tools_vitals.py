"""Tests for the ``get_vitals_trend`` tool — schema + fetcher.

The fetcher is exercised against ``httpx.MockTransport`` so we can verify
the URL, query params, and bearer header without ever opening a socket.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from agentforge.tools.vitals import (
    VITALS_TOOL_SPEC,
    VitalsFetcher,
    VitalsItem,
    VitalsPayload,
    VitalsResult,
)


# ---------- Schema ----------


def test_vitals_item_parses_full_row() -> None:
    item = VitalsItem(
        id=42,
        date="2026-04-15 10:30:00",
        systolic=128,
        diastolic=82,
        pulse=72.0,
        respiration=16.0,
        temperature=98.6,
        temp_method="oral",
        oxygen_saturation=98.0,
        height=70.0,
        weight=180.5,
        bmi=25.9,
        bmi_status="overweight",
        note="Patient was post-prandial.",
    )

    assert item.id == 42
    assert item.systolic == 128
    assert item.bmi_status == "overweight"


def test_vitals_item_parses_all_null_row() -> None:
    # An entry with only an id + date should be valid — every clinical
    # field is nullable because the PHP repository surfaces zero/empty
    # source values as null.
    item = VitalsItem(id=1, date="2026-04-15 10:30:00")

    assert item.systolic is None
    assert item.diastolic is None
    assert item.pulse is None
    assert item.weight is None
    assert item.bmi is None


def test_vitals_payload_parses_empty_list() -> None:
    payload = VitalsPayload(vitals=())

    assert payload.vitals == ()


def test_vitals_payload_is_frozen() -> None:
    payload = VitalsPayload(vitals=())

    with pytest.raises(Exception):  # ValidationError on frozen models
        payload.vitals = (VitalsItem(id=1),)  # type: ignore[misc]


def test_tool_spec_exposes_since_days_parameter() -> None:
    properties = VITALS_TOOL_SPEC.input_schema["properties"]

    assert "since_days" in properties
    since_days = properties["since_days"]
    assert since_days["type"] == "integer"
    assert since_days["default"] == 90
    assert since_days["minimum"] == 1
    assert since_days["maximum"] == 730


# ---------- Fetcher ----------


def _ok_response(items: list[dict[str, Any]] | None = None) -> httpx.Response:
    body = {"vitals": items or []}
    return httpx.Response(200, json=body)


def _capture_transport(
    response: httpx.Response,
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """Return a transport that returns ``response`` and a list capturing each request."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return response

    return httpx.MockTransport(handler), captured


async def test_fetch_includes_pid_since_days_and_bearer_token() -> None:
    transport, captured = _capture_transport(_ok_response([{"id": 1}]))
    client = httpx.AsyncClient(transport=transport)
    fetcher = VitalsFetcher(base_url="http://openemr.local", http_client=client)

    result = await fetcher.fetch(
        patient_id=123,
        raw_token="jwt.token.here",
        since_days=30,
    )

    assert isinstance(result, VitalsResult)
    assert len(captured) == 1
    sent = captured[0]
    assert sent.url.path == (
        "/interface/modules/custom_modules/oe-module-agentforge"
        "/public/internal/vitals_trend.php"
    )
    assert sent.url.params["pid"] == "123"
    assert sent.url.params["since"] == "30"
    assert sent.headers["authorization"] == "Bearer jwt.token.here"


async def test_fetch_omits_since_param_when_caller_passes_none() -> None:
    # Letting the PHP controller's default win when the model didn't
    # specify a window keeps the "default 90 days" rule in one place.
    transport, captured = _capture_transport(_ok_response())
    client = httpx.AsyncClient(transport=transport)
    fetcher = VitalsFetcher(base_url="http://openemr.local", http_client=client)

    await fetcher.fetch(patient_id=123, raw_token="t")

    sent = captured[0]
    assert "since" not in sent.url.params
    assert sent.url.params["pid"] == "123"


async def test_fetch_returns_typed_payload_with_metadata() -> None:
    items = [
        {
            "id": 5,
            "date": "2026-04-20 09:00:00",
            "systolic": 130,
            "diastolic": 84,
            "pulse": 70.0,
            "weight": 80.0,
        }
    ]
    transport, _ = _capture_transport(_ok_response(items))
    client = httpx.AsyncClient(transport=transport)
    fetcher = VitalsFetcher(base_url="http://openemr.local", http_client=client)

    result = await fetcher.fetch(patient_id=1, raw_token="t", since_days=14)

    assert result.metadata.tool_name == "get_vitals_trend"
    assert result.metadata.source == "openemr.form_vitals"
    assert result.metadata.data_freshness_seconds == 60
    assert len(result.payload.vitals) == 1
    assert result.payload.vitals[0].systolic == 130


async def test_fetch_handles_empty_payload() -> None:
    transport, _ = _capture_transport(_ok_response([]))
    client = httpx.AsyncClient(transport=transport)
    fetcher = VitalsFetcher(base_url="http://openemr.local", http_client=client)

    result = await fetcher.fetch(patient_id=1, raw_token="t")

    assert result.payload.vitals == ()


async def test_fetch_raises_on_http_error_status() -> None:
    transport, _ = _capture_transport(httpx.Response(401, json={"error": "no good"}))
    client = httpx.AsyncClient(transport=transport)
    fetcher = VitalsFetcher(base_url="http://openemr.local", http_client=client)

    with pytest.raises(httpx.HTTPStatusError):
        await fetcher.fetch(patient_id=1, raw_token="t")


async def test_fetch_strips_trailing_slash_from_base_url() -> None:
    transport, captured = _capture_transport(_ok_response())
    client = httpx.AsyncClient(transport=transport)
    fetcher = VitalsFetcher(base_url="http://openemr.local/", http_client=client)

    await fetcher.fetch(patient_id=1, raw_token="t")

    sent = captured[0]
    assert str(sent.url).startswith("http://openemr.local/interface/")
    assert "//interface" not in str(sent.url)


async def test_fetch_propagates_nullable_fields_from_php_response() -> None:
    items = [
        {
            "id": 9,
            "date": "2026-04-20 09:00:00",
            "systolic": None,
            "diastolic": None,
            "pulse": None,
            "respiration": None,
            "temperature": None,
            "temp_method": None,
            "oxygen_saturation": None,
            "height": None,
            "weight": None,
            "bmi": None,
            "bmi_status": None,
            "note": None,
        }
    ]
    transport, _ = _capture_transport(_ok_response(items))
    client = httpx.AsyncClient(transport=transport)
    fetcher = VitalsFetcher(base_url="http://openemr.local", http_client=client)

    result = await fetcher.fetch(patient_id=1, raw_token="t")

    item = result.payload.vitals[0]
    assert item.id == 9
    assert item.systolic is None
    assert item.weight is None
    assert item.note is None
