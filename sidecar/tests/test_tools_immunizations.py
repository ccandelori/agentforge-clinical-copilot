"""Behavior tests for the get_immunizations tool.

Mirrors the allergies/encounters test surface: schema parsing, fetcher
URL/header construction, HTTP error propagation, empty payloads, and
null-coercion of empty-string fields. The PHP repository normalises
empty strings to null at the JSON boundary so the pydantic model only
ever sees ``None`` or a populated string — but we still test the
populated path end-to-end here.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest

from agentforge.tools.immunizations import (
    IMMUNIZATIONS_TOOL_SPEC,
    ImmunizationItem,
    ImmunizationsFetcher,
    ImmunizationsPayload,
)


def make_transport(
    handler: callable,  # type: ignore[valid-type]
) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# ---------- Schema ----------


def test_immunization_item_parses_minimal_row() -> None:
    item = ImmunizationItem.model_validate(
        {
            "id": 100,
            "cvx_code": "140",
            "vaccine_name": "Influenza, seasonal, injectable, preservative free",
            "administered_date": "2025-07-11",
            "manufacturer": None,
            "lot_number": None,
            "note": None,
        }
    )

    assert item.id == 100
    assert item.cvx_code == "140"
    assert item.vaccine_name == (
        "Influenza, seasonal, injectable, preservative free"
    )
    assert item.administered_date == date(2025, 7, 11)
    assert item.manufacturer is None


def test_immunization_item_accepts_unresolved_vaccine_name() -> None:
    # When the codes table doesn't have a code_type=100 entry for the
    # CVX, vaccine_name comes through null. The agent still gets the
    # raw cvx_code.
    item = ImmunizationItem.model_validate(
        {
            "id": 7,
            "cvx_code": "999",
            "vaccine_name": None,
            "administered_date": None,
            "manufacturer": None,
            "lot_number": None,
            "note": None,
        }
    )

    assert item.vaccine_name is None
    assert item.administered_date is None
    assert item.cvx_code == "999"


def test_immunization_item_accepts_all_nulls() -> None:
    item = ImmunizationItem.model_validate(
        {
            "id": 1,
            "cvx_code": None,
            "vaccine_name": None,
            "administered_date": None,
            "manufacturer": None,
            "lot_number": None,
            "note": None,
        }
    )

    assert item.cvx_code is None


def test_immunizations_payload_handles_empty_list() -> None:
    payload = ImmunizationsPayload.model_validate({"immunizations": []})
    assert payload.immunizations == ()


def test_immunizations_payload_preserves_order() -> None:
    payload = ImmunizationsPayload.model_validate(
        {
            "immunizations": [
                {
                    "id": 100,
                    "cvx_code": "140",
                    "vaccine_name": "Influenza vaccine",
                    "administered_date": "2025-07-11",
                    "manufacturer": None,
                    "lot_number": None,
                    "note": None,
                },
                {
                    "id": 99,
                    "cvx_code": "113",
                    "vaccine_name": "Td (adult)",
                    "administered_date": "2024-08-30",
                    "manufacturer": None,
                    "lot_number": None,
                    "note": None,
                },
            ]
        }
    )

    assert len(payload.immunizations) == 2
    assert payload.immunizations[0].cvx_code == "140"
    assert payload.immunizations[1].cvx_code == "113"


# ---------- Tool spec ----------


def test_tool_spec_takes_no_input_parameters() -> None:
    # patient_id is bound from RequestContext server-side, so the LLM
    # gets no input surface and can't widen scope.
    assert IMMUNIZATIONS_TOOL_SPEC.name == "get_immunizations"
    assert IMMUNIZATIONS_TOOL_SPEC.input_schema["properties"] == {}
    assert IMMUNIZATIONS_TOOL_SPEC.input_schema["required"] == []


def test_tool_spec_description_mentions_immunizations() -> None:
    description = IMMUNIZATIONS_TOOL_SPEC.description.lower()
    assert "immuniz" in description or "vaccin" in description


# ---------- Fetcher: URL + headers ----------


async def test_fetcher_calls_correct_url_with_pid_and_bearer_token() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"immunizations": []})

    client = httpx.AsyncClient(transport=make_transport(handler))
    fetcher = ImmunizationsFetcher(
        base_url="https://emr.example.com", http_client=client
    )

    await fetcher.fetch(patient_id=8, raw_token="header.payload.signature")

    assert captured["url"] == (
        "https://emr.example.com"
        "/interface/modules/custom_modules/oe-module-agentforge"
        "/public/internal/immunizations.php?pid=8"
    )
    assert captured["authorization"] == "Bearer header.payload.signature"


async def test_fetcher_strips_trailing_slash_from_base_url() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"immunizations": []})

    client = httpx.AsyncClient(transport=make_transport(handler))
    fetcher = ImmunizationsFetcher(
        base_url="https://emr.example.com/", http_client=client
    )

    await fetcher.fetch(patient_id=1, raw_token="t")

    assert "//" not in captured["url"].split("://", 1)[1]


# ---------- Fetcher: response handling ----------


async def test_fetcher_returns_typed_result_for_populated_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "immunizations": [
                    {
                        "id": 100,
                        "cvx_code": "140",
                        "vaccine_name": (
                            "Influenza, seasonal, injectable, "
                            "preservative free"
                        ),
                        "administered_date": "2025-07-11",
                        "manufacturer": None,
                        "lot_number": None,
                        "note": None,
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=make_transport(handler))
    fetcher = ImmunizationsFetcher(base_url="http://x", http_client=client)

    result = await fetcher.fetch(patient_id=1, raw_token="t")

    assert result.metadata.tool_name == "get_immunizations"
    assert result.metadata.source == "openemr.immunizations"
    assert len(result.payload.immunizations) == 1
    assert result.payload.immunizations[0].cvx_code == "140"


async def test_fetcher_returns_empty_payload_when_endpoint_returns_none() -> (
    None
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"immunizations": []})

    client = httpx.AsyncClient(transport=make_transport(handler))
    fetcher = ImmunizationsFetcher(base_url="http://x", http_client=client)

    result = await fetcher.fetch(patient_id=1, raw_token="t")

    assert result.payload.immunizations == ()


async def test_fetcher_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "Token patient_id mismatch"})

    client = httpx.AsyncClient(transport=make_transport(handler))
    fetcher = ImmunizationsFetcher(base_url="http://x", http_client=client)

    with pytest.raises(httpx.HTTPStatusError):
        await fetcher.fetch(patient_id=1, raw_token="t")


async def test_fetcher_raises_on_server_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal"})

    client = httpx.AsyncClient(transport=make_transport(handler))
    fetcher = ImmunizationsFetcher(base_url="http://x", http_client=client)

    with pytest.raises(httpx.HTTPStatusError):
        await fetcher.fetch(patient_id=1, raw_token="t")
