"""Behavior tests for the get_procedures tool.

Mirrors the labs test surface (since_days propagation, schema parsing,
fetcher URL/header construction, error handling). Procedures share
storage with labs in OpenEMR's procedure_order table; the PHP repository
is what discriminates (orders without procedure_result rows are true
procedures). This test layer just locks the wire contract.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest

from agentforge.tools.procedures import (
    PROCEDURES_TOOL_SPEC,
    ProcedureItem,
    ProceduresFetcher,
    ProceduresPayload,
)


def make_transport(
    handler: callable,  # type: ignore[valid-type]
) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# ---------- Schema ----------


def test_procedure_item_parses_typical_row() -> None:
    item = ProcedureItem.model_validate(
        {
            "id": 4321,
            "procedure_code": "SNOMED CT:171207006",
            "procedure_name": "Depression screening (procedure)",
            "date_ordered": "2026-03-06",
            "status": "completed",
            "encounter_id": 78,
        }
    )

    assert item.id == 4321
    assert item.procedure_code == "SNOMED CT:171207006"
    assert item.procedure_name == "Depression screening (procedure)"
    assert item.date_ordered == date(2026, 3, 6)
    assert item.status == "completed"
    assert item.encounter_id == 78


def test_procedure_item_accepts_all_nulls_except_id() -> None:
    item = ProcedureItem.model_validate(
        {
            "id": 1,
            "procedure_code": None,
            "procedure_name": None,
            "date_ordered": None,
            "status": None,
            "encounter_id": None,
        }
    )

    assert item.procedure_name is None
    assert item.date_ordered is None
    assert item.encounter_id is None


def test_procedures_payload_handles_empty_list() -> None:
    payload = ProceduresPayload.model_validate({"procedures": []})
    assert payload.procedures == ()


def test_procedures_payload_preserves_order() -> None:
    payload = ProceduresPayload.model_validate(
        {
            "procedures": [
                {
                    "id": 1,
                    "procedure_code": "SNOMED CT:171207006",
                    "procedure_name": "Depression screening (procedure)",
                    "date_ordered": "2026-03-06",
                    "status": "completed",
                    "encounter_id": 78,
                },
                {
                    "id": 2,
                    "procedure_code": "SNOMED CT:73761001",
                    "procedure_name": "Colonoscopy (procedure)",
                    "date_ordered": "2024-08-15",
                    "status": "completed",
                    "encounter_id": 45,
                },
            ]
        }
    )

    assert len(payload.procedures) == 2
    assert payload.procedures[0].procedure_name == "Depression screening (procedure)"
    assert payload.procedures[1].procedure_name == "Colonoscopy (procedure)"


# ---------- Tool spec ----------


def test_tool_spec_takes_optional_since_days_only() -> None:
    assert PROCEDURES_TOOL_SPEC.name == "get_procedures"
    # since_days is optional; patient_id is server-bound.
    assert PROCEDURES_TOOL_SPEC.input_schema["required"] == []
    assert "since_days" in PROCEDURES_TOOL_SPEC.input_schema["properties"]


def test_tool_spec_describes_procedures_distinct_from_labs() -> None:
    description = PROCEDURES_TOOL_SPEC.description.lower()
    assert "procedure" in description
    # The catalog has both get_procedures and get_recent_labs; the
    # description should hint at the distinction so the LLM doesn't
    # call the wrong one.
    assert "screen" in description or "surgical" in description or "intervention" in description


# ---------- Fetcher: URL + headers ----------


async def test_fetcher_calls_correct_url_with_pid_and_default_since_days() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"procedures": []})

    client = httpx.AsyncClient(transport=make_transport(handler))
    fetcher = ProceduresFetcher(
        base_url="https://emr.example.com", http_client=client
    )

    await fetcher.fetch(patient_id=8, raw_token="header.payload.signature")

    expected_path = (
        "/interface/modules/custom_modules/oe-module-agentforge"
        "/public/internal/procedures.php"
    )
    assert expected_path in captured["url"]
    assert "pid=8" in captured["url"]
    assert (
        f"since_days={ProceduresFetcher.DEFAULT_SINCE_DAYS}" in captured["url"]
    )
    assert captured["authorization"] == "Bearer header.payload.signature"


async def test_fetcher_propagates_custom_since_days() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"procedures": []})

    client = httpx.AsyncClient(transport=make_transport(handler))
    fetcher = ProceduresFetcher(base_url="http://x", http_client=client)

    await fetcher.fetch(patient_id=1, raw_token="t", since_days=30)

    assert "since_days=30" in captured["url"]


async def test_fetcher_strips_trailing_slash_from_base_url() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"procedures": []})

    client = httpx.AsyncClient(transport=make_transport(handler))
    fetcher = ProceduresFetcher(
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
                "procedures": [
                    {
                        "id": 4321,
                        "procedure_code": "SNOMED CT:171207006",
                        "procedure_name": "Depression screening (procedure)",
                        "date_ordered": "2026-03-06",
                        "status": "completed",
                        "encounter_id": 78,
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=make_transport(handler))
    fetcher = ProceduresFetcher(base_url="http://x", http_client=client)

    result = await fetcher.fetch(patient_id=1, raw_token="t")

    assert result.metadata.tool_name == "get_procedures"
    assert result.metadata.source == "openemr.procedures"
    assert len(result.payload.procedures) == 1
    assert result.payload.procedures[0].procedure_code == "SNOMED CT:171207006"


async def test_fetcher_returns_empty_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"procedures": []})

    client = httpx.AsyncClient(transport=make_transport(handler))
    fetcher = ProceduresFetcher(base_url="http://x", http_client=client)

    result = await fetcher.fetch(patient_id=1, raw_token="t")

    assert result.payload.procedures == ()


async def test_fetcher_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "Token patient_id mismatch"})

    client = httpx.AsyncClient(transport=make_transport(handler))
    fetcher = ProceduresFetcher(base_url="http://x", http_client=client)

    with pytest.raises(httpx.HTTPStatusError):
        await fetcher.fetch(patient_id=1, raw_token="t")


async def test_fetcher_raises_on_server_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal"})

    client = httpx.AsyncClient(transport=make_transport(handler))
    fetcher = ProceduresFetcher(base_url="http://x", http_client=client)

    with pytest.raises(httpx.HTTPStatusError):
        await fetcher.fetch(patient_id=1, raw_token="t")
