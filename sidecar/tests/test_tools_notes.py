"""Behavior tests for the ``get_recent_notes`` tool layer.

Notes are the first tool with per-record sensitivity gating, so the
fetcher's contract is broader than labs/vitals: in addition to URL +
header + query shaping, it also walks each row's metadata through
``AuthGateway.check_record_visibility`` and substitutes a redacted
placeholder for any row the user can't see. The body is the sensitive
field the policy is protecting; the model still gets to see *that* a
restricted note exists (id, date, source, note_type, count) so it can
tell the user truthfully how much was withheld.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from agentforge.gateway.auth_gateway import RecordMetadata, RequestContext
from agentforge.tools.notes import (
    NOTES_TOOL_SPEC,
    NoteItem,
    NotesFetcher,
    NotesPayload,
    NotesResult,
)

BASE_URL = "https://openemr.test"
NOTES_PATH = (
    "/interface/modules/custom_modules/oe-module-agentforge"
    "/public/internal/recent_notes.php"
)


def _ctx() -> RequestContext:
    return RequestContext(
        user_id=1,
        patient_id=42,
        username="test-user",
        role="clinician",
        breakglass_flag=False,
        breakglass_reason=None,
        sensitivity_clearances=frozenset(),
        raw_token="user-jwt-xyz",
    )


def _allow_all_gateway() -> AsyncMock:
    """Gateway stand-in whose visibility check always allows."""
    gateway = AsyncMock()
    gateway.check_record_visibility = AsyncMock(return_value=True)
    return gateway


def _deny_all_gateway() -> AsyncMock:
    gateway = AsyncMock()
    gateway.check_record_visibility = AsyncMock(return_value=False)
    return gateway


def _mixed_body() -> dict[str, Any]:
    """Two pnotes + one clinical note. Realistic shape from PHP endpoint."""
    return {
        "notes": [
            {
                "id": 5,
                "source": "pnote",
                "date": "2026-04-20 14:30:00",
                "author": "dr.smith",
                "title": "Phone call",
                "body": "Patient reports improvement.",
                "note_type": None,
            },
            {
                "id": 8,
                "source": "clinical_note",
                "date": "2026-04-15 00:00:00",
                "author": "dr.jones",
                "title": "Progress note",
                "body": "Discussed care plan.",
                "note_type": "progress",
            },
            {
                "id": 12,
                "source": "pnote",
                "date": "2026-04-10 09:15:00",
                "author": "dr.smith",
                "title": "Substance abuse counseling - week 3",
                "body": "Patient discussed triggers.",
                "note_type": "substance_abuse",
            },
        ]
    }


def _make_fetcher(
    *,
    handler: httpx.MockTransport,
    gateway: AsyncMock | None = None,
) -> NotesFetcher:
    client = httpx.AsyncClient(transport=handler, base_url=BASE_URL)
    return NotesFetcher(
        base_url=BASE_URL,
        gateway=gateway or _allow_all_gateway(),
        http_client=client,
    )


# ---------- HTTP contract ----------


async def test_fetch_calls_php_endpoint_with_pid_bearer_and_default_since_days() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"notes": []})

    fetcher = _make_fetcher(handler=httpx.MockTransport(handler))

    await fetcher.fetch(ctx=_ctx())

    assert captured["url"] == f"{BASE_URL}{NOTES_PATH}?pid=42&since_days=90"
    assert captured["auth"] == "Bearer user-jwt-xyz"
    assert captured["params"] == {"pid": "42", "since_days": "90"}


async def test_fetch_passes_explicit_since_days_when_provided() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"notes": []})

    fetcher = _make_fetcher(handler=httpx.MockTransport(handler))

    await fetcher.fetch(ctx=_ctx(), since_days=14)

    assert captured["params"] == {"pid": "42", "since_days": "14"}


async def test_fetch_returns_empty_payload_when_no_notes() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"notes": []})

    fetcher = _make_fetcher(handler=httpx.MockTransport(handler))

    result = await fetcher.fetch(ctx=_ctx())

    assert result.payload.notes == ()
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

    assert isinstance(result, NotesResult)
    assert isinstance(result.payload, NotesPayload)
    assert len(result.payload.notes) == 3

    pnote, clinical, sab = result.payload.notes
    assert pnote.source == "pnote"
    assert pnote.title == "Phone call"
    assert pnote.body == "Patient reports improvement."
    assert pnote.permission_denied is False
    assert clinical.source == "clinical_note"
    assert clinical.note_type == "progress"
    assert sab.note_type == "substance_abuse"


def test_notes_payload_is_frozen_and_round_trips_json() -> None:
    body = _mixed_body()
    items = tuple(NoteItem.model_validate(n) for n in body["notes"])
    payload = NotesPayload(notes=items)

    rehydrated = NotesPayload.model_validate_json(payload.model_dump_json())
    assert rehydrated == payload

    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        payload.notes[0].body = "edited"  # type: ignore[misc]


# ---------- Tool spec ----------


def test_tool_spec_declares_optional_since_days_parameter() -> None:
    assert NOTES_TOOL_SPEC.name == "get_recent_notes"
    assert "note" in NOTES_TOOL_SPEC.description.lower()

    schema = NOTES_TOOL_SPEC.input_schema
    assert schema["type"] == "object"
    properties = schema["properties"]
    assert "since_days" in properties
    assert properties["since_days"]["type"] == "integer"
    assert "since_days" not in schema.get("required", [])


# ---------- Sensitivity gating (the new behavior notes brings) ----------


async def test_all_allowed_emits_full_payload_with_no_redaction_flag() -> None:
    gateway = _allow_all_gateway()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_mixed_body())

    fetcher = _make_fetcher(
        handler=httpx.MockTransport(handler), gateway=gateway,
    )

    result = await fetcher.fetch(ctx=_ctx())

    # All three checked; all allowed; no redaction.
    assert gateway.check_record_visibility.await_count == 3
    assert all(not n.permission_denied for n in result.payload.notes)
    assert all(n.body for n in result.payload.notes)
    assert result.metadata.redaction_applied is False
    assert result.metadata.redacted_fields == ()


async def test_all_denied_marks_every_row_permission_denied_and_strips_body() -> None:
    gateway = _deny_all_gateway()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_mixed_body())

    fetcher = _make_fetcher(
        handler=httpx.MockTransport(handler), gateway=gateway,
    )

    result = await fetcher.fetch(ctx=_ctx())

    assert len(result.payload.notes) == 3
    assert all(n.permission_denied for n in result.payload.notes)
    # Body, title, and author all leak content — strip them on deny.
    assert all(n.body is None for n in result.payload.notes)
    assert all(n.title is None for n in result.payload.notes)
    assert all(n.author is None for n in result.payload.notes)
    # Operational metadata stays so the model can summarize what was withheld.
    assert all(n.id > 0 for n in result.payload.notes)
    assert all(n.date is not None for n in result.payload.notes)
    assert result.metadata.redaction_applied is True
    assert "body" in result.metadata.redacted_fields


async def test_mixed_visibility_redacts_only_denied_rows() -> None:
    # Deny only the substance-abuse row (the third in _mixed_body).
    decisions = iter([True, True, False])
    gateway = AsyncMock()

    async def _check(
        _ctx: RequestContext, metadata: RecordMetadata
    ) -> bool:
        return next(decisions)

    gateway.check_record_visibility = AsyncMock(side_effect=_check)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_mixed_body())

    fetcher = _make_fetcher(
        handler=httpx.MockTransport(handler), gateway=gateway,
    )

    result = await fetcher.fetch(ctx=_ctx())

    assert len(result.payload.notes) == 3
    assert result.payload.notes[0].permission_denied is False
    assert result.payload.notes[0].body == "Patient reports improvement."
    assert result.payload.notes[1].permission_denied is False
    assert result.payload.notes[2].permission_denied is True
    assert result.payload.notes[2].body is None
    # The denied row's note_type stays so the model can name what kind
    # of note was withheld in aggregate, e.g. "1 substance abuse note."
    assert result.payload.notes[2].note_type == "substance_abuse"
    assert result.metadata.redaction_applied is True


async def test_visibility_metadata_passes_note_type_and_title() -> None:
    captured_metadatas: list[RecordMetadata] = []

    async def _check(_ctx: RequestContext, metadata: RecordMetadata) -> bool:
        captured_metadatas.append(metadata)
        return True

    gateway = AsyncMock()
    gateway.check_record_visibility = AsyncMock(side_effect=_check)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_mixed_body())

    fetcher = _make_fetcher(
        handler=httpx.MockTransport(handler), gateway=gateway,
    )

    await fetcher.fetch(ctx=_ctx())

    assert len(captured_metadatas) == 3
    # The substance-abuse row must surface its note_type to the gateway —
    # that's the field the policy keys on for cfr42.
    sab_md = captured_metadatas[2]
    assert sab_md.note_type == "substance_abuse"
    assert sab_md.note_title == "Substance abuse counseling - week 3"


# ---------- Result envelope ----------


async def test_notes_result_serializes_payload_envelope() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_mixed_body())

    fetcher = _make_fetcher(handler=httpx.MockTransport(handler))

    result = await fetcher.fetch(ctx=_ctx())
    decoded = json.loads(result.model_dump_json())

    assert decoded["metadata"]["tool_name"] == "get_recent_notes"
    assert decoded["metadata"]["source"] == "openemr.notes"
    assert len(decoded["payload"]["notes"]) == 3
