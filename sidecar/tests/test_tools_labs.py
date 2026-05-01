"""Behavior tests for the ``get_recent_labs`` tool layer.

Mirrors test_auth_gateway.py's style: end-to-end test of the fetcher
against an httpx MockTransport so we exercise the real URL/header/query
shaping without standing up the PHP endpoint. The fetcher is the trust
boundary into MariaDB for lab results, so the contract of "what URL,
what headers, what params" is the thing under test.
"""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from agentforge.tools.labs import (
    LABS_TOOL_SPEC,
    LabsFetcher,
    LabsPayload,
    LabsResult,
)

BASE_URL = "https://openemr.test"
LABS_PATH = (
    "/interface/modules/custom_modules/oe-module-agentforge"
    "/public/internal/labs.php"
)


def _multi_result_panel_body() -> dict[str, object]:
    """Realistic CMP-ish payload: one order, one report, several analytes."""
    return {
        "labs": [
            {
                "id": 101,
                "order_id": 11,
                "report_id": 22,
                "test_code": "2160-0",
                "test_name": "Creatinine",
                "value": "1.1",
                "units": "mg/dL",
                "reference_range": "0.6 - 1.2",
                "abnormal": "no",
                "date": "2026-04-15",
            },
            {
                "id": 102,
                "order_id": 11,
                "report_id": 22,
                "test_code": "2345-7",
                "test_name": "Glucose",
                "value": "112",
                "units": "mg/dL",
                "reference_range": "70 - 99",
                "abnormal": "high",
                "date": "2026-04-15",
            },
            {
                "id": 103,
                "order_id": 11,
                "report_id": 22,
                "test_code": "4548-4",
                "test_name": "Hemoglobin A1c",
                "value": None,
                "units": None,
                "reference_range": None,
                "abnormal": None,
                "date": None,
            },
        ]
    }


def _make_fetcher(handler: httpx.MockTransport) -> LabsFetcher:
    client = httpx.AsyncClient(transport=handler, base_url=BASE_URL)
    return LabsFetcher(base_url=BASE_URL, http_client=client)


async def test_fetch_parses_multi_analyte_panel_into_typed_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=_multi_result_panel_body())

    fetcher = _make_fetcher(httpx.MockTransport(handler))

    result = await fetcher.fetch(patient_id=42, raw_token="user-jwt-xyz")

    assert isinstance(result, LabsResult)
    assert isinstance(result.payload, LabsPayload)
    assert len(result.payload.labs) == 3
    creatinine = result.payload.labs[0]
    assert creatinine.test_name == "Creatinine"
    assert creatinine.value == "1.1"
    assert creatinine.abnormal == "no"
    assert creatinine.date == date(2026, 4, 15)
    a1c = result.payload.labs[2]
    assert a1c.value is None
    assert a1c.units is None
    assert a1c.date is None


async def test_fetch_calls_php_endpoint_with_pid_bearer_and_default_since_days() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"labs": []})

    fetcher = _make_fetcher(httpx.MockTransport(handler))

    await fetcher.fetch(patient_id=42, raw_token="user-jwt-xyz")

    assert captured["url"] == f"{BASE_URL}{LABS_PATH}?pid=42&since_days=90"
    assert captured["auth"] == "Bearer user-jwt-xyz"
    assert captured["params"] == {"pid": "42", "since_days": "90"}


async def test_fetch_passes_explicit_since_days_when_provided() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"labs": []})

    fetcher = _make_fetcher(httpx.MockTransport(handler))

    await fetcher.fetch(patient_id=42, raw_token="t", since_days=30)

    assert captured["params"] == {"pid": "42", "since_days": "30"}


async def test_fetch_returns_empty_payload_when_no_labs() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"labs": []})

    fetcher = _make_fetcher(httpx.MockTransport(handler))

    result = await fetcher.fetch(patient_id=42, raw_token="t")

    assert result.payload.labs == ()


async def test_fetch_raises_on_http_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    fetcher = _make_fetcher(httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError):
        await fetcher.fetch(patient_id=42, raw_token="t")


async def test_fetch_raises_on_unauthorized() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Invalid or expired token"})

    fetcher = _make_fetcher(httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError):
        await fetcher.fetch(patient_id=42, raw_token="t")


def test_tool_spec_declares_optional_since_days_parameter() -> None:
    # The orchestrator surface contract: name + description + JSON Schema
    # that includes since_days as an optional integer with a default.
    assert LABS_TOOL_SPEC.name == "get_recent_labs"
    assert "labs" in LABS_TOOL_SPEC.description.lower()

    schema = LABS_TOOL_SPEC.input_schema
    assert schema["type"] == "object"
    properties = schema["properties"]
    assert "since_days" in properties
    assert properties["since_days"]["type"] == "integer"
    # since_days is optional — the orchestrator falls back to the
    # fetcher's default when the model omits it.
    assert "since_days" not in schema.get("required", [])


def test_labs_payload_is_frozen_and_round_trips_json() -> None:
    body = _multi_result_panel_body()
    payload = LabsPayload.model_validate(body)

    rehydrated = LabsPayload.model_validate_json(payload.model_dump_json())
    assert rehydrated == payload

    # frozen=True: assignment to a field raises ValidationError under v2.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        payload.labs[0].value = "9.9"  # type: ignore[misc]


def test_labs_result_serializes_payload_envelope() -> None:
    # Sanity: orchestrator dumps the ToolResult to JSON and feeds it
    # back to Claude. The labs key has to round-trip.
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_multi_result_panel_body())

    fetcher = _make_fetcher(httpx.MockTransport(handler))

    async def run() -> str:
        result = await fetcher.fetch(patient_id=1, raw_token="t")
        return result.model_dump_json()

    import asyncio

    dumped = asyncio.run(run())
    decoded = json.loads(dumped)
    assert decoded["metadata"]["tool_name"] == "get_recent_labs"
    assert decoded["metadata"]["source"] == "openemr.labs"
    assert len(decoded["payload"]["labs"]) == 3
