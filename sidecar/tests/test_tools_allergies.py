"""Behavior tests for the get_active_allergies tool.

Mirrors the medications/problems test surface: schema parsing, fetcher
URL/header construction, HTTP error propagation, empty payloads, and
null-coercion of empty reaction strings (the lists.reaction column is
NOT NULL DEFAULT '' in OpenEMR's schema, so an absent reaction comes
through as "" and the PHP repository normalises that to null).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from agentforge.tools.allergies import (
    ALLERGIES_TOOL_SPEC,
    AllergiesFetcher,
    AllergiesPayload,
    AllergyItem,
)


def make_transport(
    handler: callable,  # type: ignore[valid-type]
) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# ---------- Schema ----------


def test_allergy_item_parses_minimal_row() -> None:
    item = AllergyItem.model_validate(
        {
            "id": 1,
            "name": "Penicillin",
            "reaction": "Rash",
            "severity": "moderate",
            "begin_date": "2020-01-15",
            "end_date": None,
        }
    )

    assert item.id == 1
    assert item.name == "Penicillin"
    assert item.reaction == "Rash"
    assert item.severity == "moderate"


def test_allergy_item_accepts_null_reaction_and_severity() -> None:
    item = AllergyItem.model_validate(
        {
            "id": 1,
            "name": "Latex",
            "reaction": None,
            "severity": None,
            "begin_date": None,
            "end_date": None,
        }
    )

    assert item.reaction is None
    assert item.severity is None
    assert item.begin_date is None


def test_allergies_payload_handles_empty_list() -> None:
    payload = AllergiesPayload.model_validate({"allergies": []})
    assert payload.allergies == ()


def test_allergies_payload_preserves_order() -> None:
    payload = AllergiesPayload.model_validate(
        {
            "allergies": [
                {
                    "id": 7,
                    "name": "Penicillin",
                    "reaction": "Anaphylaxis",
                    "severity": "severe",
                    "begin_date": "2018-03-01",
                    "end_date": None,
                },
                {
                    "id": 8,
                    "name": "Sulfa drugs",
                    "reaction": None,
                    "severity": "mild",
                    "begin_date": None,
                    "end_date": None,
                },
            ]
        }
    )

    assert len(payload.allergies) == 2
    assert payload.allergies[0].name == "Penicillin"
    assert payload.allergies[1].name == "Sulfa drugs"


# ---------- Tool spec ----------


def test_tool_spec_takes_no_input_parameters() -> None:
    # patient_id is bound from RequestContext server-side, so the LLM
    # gets no input surface and can't widen scope.
    assert ALLERGIES_TOOL_SPEC.name == "get_active_allergies"
    assert ALLERGIES_TOOL_SPEC.input_schema["properties"] == {}
    assert ALLERGIES_TOOL_SPEC.input_schema["required"] == []


def test_tool_spec_description_mentions_allergies() -> None:
    description = ALLERGIES_TOOL_SPEC.description.lower()
    assert "allerg" in description


# ---------- Fetcher: URL + headers ----------


async def test_fetcher_calls_correct_url_with_pid_and_bearer_token() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"allergies": []})

    client = httpx.AsyncClient(transport=make_transport(handler))
    fetcher = AllergiesFetcher(base_url="https://emr.example.com", http_client=client)

    await fetcher.fetch(patient_id=42, raw_token="header.payload.signature")

    assert captured["url"] == (
        "https://emr.example.com"
        "/interface/modules/custom_modules/oe-module-agentforge"
        "/public/internal/allergies.php?pid=42"
    )
    assert captured["authorization"] == "Bearer header.payload.signature"


async def test_fetcher_strips_trailing_slash_from_base_url() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"allergies": []})

    client = httpx.AsyncClient(transport=make_transport(handler))
    fetcher = AllergiesFetcher(base_url="https://emr.example.com/", http_client=client)

    await fetcher.fetch(patient_id=1, raw_token="t")

    assert "//" not in captured["url"].split("://", 1)[1]


# ---------- Fetcher: response handling ----------


async def test_fetcher_returns_typed_result_for_populated_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "allergies": [
                    {
                        "id": 1,
                        "name": "Penicillin",
                        "reaction": "Rash",
                        "severity": "moderate",
                        "begin_date": "2020-01-15",
                        "end_date": None,
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=make_transport(handler))
    fetcher = AllergiesFetcher(base_url="http://x", http_client=client)

    result = await fetcher.fetch(patient_id=1, raw_token="t")

    assert result.metadata.tool_name == "get_active_allergies"
    assert result.metadata.source == "openemr.allergies"
    assert len(result.payload.allergies) == 1
    assert result.payload.allergies[0].name == "Penicillin"


async def test_fetcher_returns_empty_payload_when_endpoint_returns_no_allergies() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"allergies": []})

    client = httpx.AsyncClient(transport=make_transport(handler))
    fetcher = AllergiesFetcher(base_url="http://x", http_client=client)

    result = await fetcher.fetch(patient_id=1, raw_token="t")

    assert result.payload.allergies == ()


async def test_fetcher_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "Token patient_id mismatch"})

    client = httpx.AsyncClient(transport=make_transport(handler))
    fetcher = AllergiesFetcher(base_url="http://x", http_client=client)

    with pytest.raises(httpx.HTTPStatusError):
        await fetcher.fetch(patient_id=1, raw_token="t")


async def test_fetcher_raises_on_server_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal"})

    client = httpx.AsyncClient(transport=make_transport(handler))
    fetcher = AllergiesFetcher(base_url="http://x", http_client=client)

    with pytest.raises(httpx.HTTPStatusError):
        await fetcher.fetch(patient_id=1, raw_token="t")
