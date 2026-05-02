"""Behavior tests for the ``get_recent_encounters`` tool layer.

Encounters carry the structural sensitivity signals OpenEMR already
keeps on the row: ``pc_catid`` (postcalendar category, mapped to
``RecordMetadata.encounter_category``) and the explicit
``sensitivity`` string. Both flow through ``check_record_visibility``
so policy rules can gate on either dimension. The gating contract
mirrors :mod:`agentforge.tools.notes`: denied rows survive in the
result with ``permission_denied=True`` and stripped content fields,
so the model can describe what was withheld in aggregate.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from agentforge.gateway.auth_gateway import RecordMetadata, RequestContext
from agentforge.tools.encounters import (
    ENCOUNTERS_TOOL_SPEC,
    EncounterItem,
    EncountersFetcher,
    EncountersPayload,
    EncountersResult,
)

BASE_URL = "https://openemr.test"
ENCOUNTERS_PATH = (
    "/interface/modules/custom_modules/oe-module-agentforge"
    "/public/internal/recent_encounters.php"
)


def _ctx() -> RequestContext:
    return RequestContext(
        user_id=1,
        patient_id=42,
        username="dr.smith",
        role="clinician",
        breakglass_flag=False,
        breakglass_reason=None,
        sensitivity_clearances=frozenset(),
        raw_token="user-jwt-xyz",
    )


def _allow_all_gateway() -> AsyncMock:
    gateway = AsyncMock()
    gateway.check_record_visibility = AsyncMock(return_value=True)
    return gateway


def _deny_all_gateway() -> AsyncMock:
    gateway = AsyncMock()
    gateway.check_record_visibility = AsyncMock(return_value=False)
    return gateway


def _mixed_body() -> dict[str, Any]:
    """Two routine encounters plus one psych — realistic shape from PHP."""
    return {
        "encounters": [
            {
                "id": 5,
                "date": "2026-04-20 14:30:00",
                "reason": "Follow-up: diabetes",
                "encounter_type": "Office Visit",
                "class_code": "AMB",
                "provider_id": 12,
                "provider_name": "dr.smith",
                "sensitivity": None,
                "encounter_category": 5,
            },
            {
                "id": 8,
                "date": "2026-03-15 09:00:00",
                "reason": "Annual physical",
                "encounter_type": "Wellness Visit",
                "class_code": "AMB",
                "provider_id": 12,
                "provider_name": "dr.smith",
                "sensitivity": None,
                "encounter_category": 5,
            },
            {
                "id": 12,
                "date": "2026-02-10 11:00:00",
                "reason": "Behavioral health intake",
                "encounter_type": "Psychiatric Evaluation",
                "class_code": "AMB",
                "provider_id": 17,
                "provider_name": "dr.jones",
                "sensitivity": "behavioral_health",
                "encounter_category": 11,
            },
        ]
    }


def _make_fetcher(
    *,
    handler: httpx.MockTransport,
    gateway: AsyncMock | None = None,
) -> EncountersFetcher:
    client = httpx.AsyncClient(transport=handler, base_url=BASE_URL)
    return EncountersFetcher(
        base_url=BASE_URL,
        gateway=gateway or _allow_all_gateway(),
        http_client=client,
    )


# ---------- HTTP contract ----------


async def test_fetch_calls_php_endpoint_with_pid_bearer_and_default_since() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"encounters": []})

    fetcher = _make_fetcher(handler=httpx.MockTransport(handler))

    await fetcher.fetch(ctx=_ctx())

    assert captured["url"] == f"{BASE_URL}{ENCOUNTERS_PATH}?pid=42&since_days=365"
    assert captured["auth"] == "Bearer user-jwt-xyz"
    assert captured["params"] == {"pid": "42", "since_days": "365"}


async def test_fetch_passes_explicit_since_days_when_provided() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"encounters": []})

    fetcher = _make_fetcher(handler=httpx.MockTransport(handler))

    await fetcher.fetch(ctx=_ctx(), since_days=90)

    assert captured["params"]["since_days"] == "90"


async def test_fetch_returns_empty_payload_when_no_encounters() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"encounters": []})

    fetcher = _make_fetcher(handler=httpx.MockTransport(handler))

    result = await fetcher.fetch(ctx=_ctx())

    assert result.payload.encounters == ()
    assert result.metadata.redaction_applied is False


async def test_fetch_raises_on_http_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    fetcher = _make_fetcher(handler=httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError):
        await fetcher.fetch(ctx=_ctx())


async def test_fetch_raises_on_unauthorized() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Invalid or expired token"})

    fetcher = _make_fetcher(handler=httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError):
        await fetcher.fetch(ctx=_ctx())


# ---------- Payload typing ----------


async def test_fetch_parses_mixed_body_into_typed_payload_when_all_allowed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_mixed_body())

    fetcher = _make_fetcher(handler=httpx.MockTransport(handler))

    result = await fetcher.fetch(ctx=_ctx())

    assert isinstance(result, EncountersResult)
    assert isinstance(result.payload, EncountersPayload)
    assert len(result.payload.encounters) == 3

    visit, physical, psych = result.payload.encounters
    assert visit.encounter_type == "Office Visit"
    assert visit.provider_name == "dr.smith"
    assert physical.reason == "Annual physical"
    assert psych.sensitivity == "behavioral_health"


def test_encounters_payload_is_frozen_and_round_trips_json() -> None:
    body = _mixed_body()
    items = tuple(EncounterItem.model_validate(e) for e in body["encounters"])
    payload = EncountersPayload(encounters=items)

    rehydrated = EncountersPayload.model_validate_json(payload.model_dump_json())
    assert rehydrated == payload

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        payload.encounters[0].reason = "edited"  # type: ignore[misc]


# ---------- Tool spec ----------


def test_tool_spec_declares_optional_since_days_parameter() -> None:
    assert ENCOUNTERS_TOOL_SPEC.name == "get_recent_encounters"
    assert "encounter" in ENCOUNTERS_TOOL_SPEC.description.lower()

    schema = ENCOUNTERS_TOOL_SPEC.input_schema
    assert schema["type"] == "object"
    properties = schema["properties"]
    assert "since_days" in properties
    assert properties["since_days"]["type"] == "integer"
    assert "since_days" not in schema.get("required", [])


# ---------- Sensitivity gating ----------


async def test_all_allowed_emits_full_payload_with_no_redaction_flag() -> None:
    gateway = _allow_all_gateway()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_mixed_body())

    fetcher = _make_fetcher(
        handler=httpx.MockTransport(handler), gateway=gateway,
    )

    result = await fetcher.fetch(ctx=_ctx())

    assert gateway.check_record_visibility.await_count == 3
    assert all(not e.permission_denied for e in result.payload.encounters)
    assert all(e.reason for e in result.payload.encounters)
    assert result.metadata.redaction_applied is False
    assert result.metadata.redacted_fields == ()


async def test_all_denied_marks_every_row_permission_denied_and_strips_content() -> None:
    gateway = _deny_all_gateway()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_mixed_body())

    fetcher = _make_fetcher(
        handler=httpx.MockTransport(handler), gateway=gateway,
    )

    result = await fetcher.fetch(ctx=_ctx())

    assert len(result.payload.encounters) == 3
    assert all(e.permission_denied for e in result.payload.encounters)
    # Reason and provider_name leak content/identity; strip both.
    assert all(e.reason is None for e in result.payload.encounters)
    assert all(e.provider_name is None for e in result.payload.encounters)
    # Operational metadata stays.
    assert all(e.id > 0 for e in result.payload.encounters)
    assert all(e.date is not None for e in result.payload.encounters)
    assert result.metadata.redaction_applied is True
    assert "reason" in result.metadata.redacted_fields


async def test_mixed_visibility_redacts_only_denied_rows() -> None:
    decisions = iter([True, True, False])
    gateway = AsyncMock()

    async def _check(_ctx: RequestContext, _md: RecordMetadata) -> bool:
        return next(decisions)

    gateway.check_record_visibility = AsyncMock(side_effect=_check)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_mixed_body())

    fetcher = _make_fetcher(
        handler=httpx.MockTransport(handler), gateway=gateway,
    )

    result = await fetcher.fetch(ctx=_ctx())

    assert len(result.payload.encounters) == 3
    assert result.payload.encounters[0].permission_denied is False
    assert result.payload.encounters[1].permission_denied is False
    assert result.payload.encounters[2].permission_denied is True
    assert result.payload.encounters[2].reason is None
    # The denied row's date + sensitivity classifier survive so the
    # model can summarize "1 behavioral_health encounter withheld."
    assert result.payload.encounters[2].sensitivity == "behavioral_health"
    assert result.metadata.redaction_applied is True


async def test_visibility_metadata_passes_encounter_category_and_sensitivity() -> None:
    captured: list[RecordMetadata] = []

    async def _check(_ctx: RequestContext, metadata: RecordMetadata) -> bool:
        captured.append(metadata)
        return True

    gateway = AsyncMock()
    gateway.check_record_visibility = AsyncMock(side_effect=_check)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_mixed_body())

    fetcher = _make_fetcher(
        handler=httpx.MockTransport(handler), gateway=gateway,
    )

    await fetcher.fetch(ctx=_ctx())

    assert len(captured) == 3
    # The psych row's pc_catid (encounter_category=11) and sensitivity
    # marker both flow into the gateway. encounter_category is the
    # int field on RecordMetadata; sensitivity rides note_type so
    # behavioral_health rules in the policy match either way.
    assert captured[2].encounter_category == 11
    assert captured[2].note_type == "behavioral_health"


async def test_null_sensitivity_does_not_raise() -> None:
    # Encounters with NULL sensitivity (the common case) must not blow
    # up the Pydantic validation pass through.
    body = {
        "encounters": [
            {
                "id": 1,
                "date": "2026-04-15 10:00:00",
                "reason": "follow-up",
                "encounter_type": "Office Visit",
                "class_code": "AMB",
                "provider_id": 0,
                "provider_name": None,
                "sensitivity": None,
                "encounter_category": 5,
            }
        ]
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    fetcher = _make_fetcher(handler=httpx.MockTransport(handler))

    result = await fetcher.fetch(ctx=_ctx())
    assert result.payload.encounters[0].sensitivity is None
    assert result.payload.encounters[0].provider_name is None


# ---------- Result envelope ----------


async def test_encounters_result_serializes_payload_envelope() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_mixed_body())

    fetcher = _make_fetcher(handler=httpx.MockTransport(handler))

    result = await fetcher.fetch(ctx=_ctx())
    decoded = json.loads(result.model_dump_json())

    assert decoded["metadata"]["tool_name"] == "get_recent_encounters"
    assert decoded["metadata"]["source"] == "openemr.encounters"
    assert len(decoded["payload"]["encounters"]) == 3
