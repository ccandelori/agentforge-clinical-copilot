"""Behavior tests for the ``search_notes`` tool layer.

The MVP search tool walks each FULLTEXT hit through
``AuthGateway.check_record_visibility`` before emitting the typed
payload — same pattern as :mod:`agentforge.tools.notes`. The result
shape differs (snippets + scores, no body / author / note_type) so the
gating-on-deny clears `title` and `snippet` while keeping `id`,
`source`, `date`, and `score` for aggregate summarization.

Note: the PHP search endpoint does not surface ``note_type`` or
``attending_only`` per row, so the metadata passed to the gateway is
title-only. Title-prefix rules in the sensitivity policy still apply;
note-type / attending-only rules will be best-effort allow until the
PHP search response is extended (tracked separately).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from agentforge.gateway.auth_gateway import RecordMetadata, RequestContext
from agentforge.tools.search_notes import (
    SEARCH_NOTES_TOOL_SPEC,
    SearchHit,
    SearchNotesFetcher,
    SearchNotesPayload,
    SearchNotesResult,
)

BASE_URL = "https://openemr.test"
SEARCH_PATH = (
    "/interface/modules/custom_modules/oe-module-agentforge"
    "/public/internal/notes_search.php"
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
    gateway = AsyncMock()
    gateway.check_record_visibility = AsyncMock(return_value=True)
    return gateway


def _deny_all_gateway() -> AsyncMock:
    gateway = AsyncMock()
    gateway.check_record_visibility = AsyncMock(return_value=False)
    return gateway


def _ranked_body() -> dict[str, Any]:
    """Three search hits across both sources with descending scores."""
    return {
        "results": [
            {
                "id": 5,
                "source": "pnote",
                "date": "2026-04-20 14:30:00",
                "title": "Phone call",
                "snippet": "Patient reports new cough since last visit.",
                "score": 2.1,
            },
            {
                "id": 8,
                "source": "clinical_note",
                "date": "2026-04-15 00:00:00",
                "title": "Progress note",
                "snippet": "Cough resolving. Continued ACE inhibitor.",
                "score": 1.4,
            },
            {
                "id": 12,
                "source": "pnote",
                "date": "2026-04-10 09:15:00",
                "title": "Substance abuse counseling",
                "snippet": "Patient discussed cough syrup misuse triggers.",
                "score": 0.9,
            },
        ]
    }


def _make_fetcher(
    *,
    handler: httpx.MockTransport,
    gateway: AsyncMock | None = None,
) -> SearchNotesFetcher:
    client = httpx.AsyncClient(transport=handler, base_url=BASE_URL)
    return SearchNotesFetcher(
        base_url=BASE_URL,
        gateway=gateway or _allow_all_gateway(),
        http_client=client,
    )


# ---------- HTTP contract ----------


async def test_fetch_calls_php_endpoint_with_pid_q_and_default_limit_and_since() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"results": []})

    fetcher = _make_fetcher(handler=httpx.MockTransport(handler))

    await fetcher.fetch(ctx=_ctx(), query="cough")

    assert captured["url"] == (
        f"{BASE_URL}{SEARCH_PATH}?pid=42&q=cough&limit=5&since_days=365"
    )
    assert captured["auth"] == "Bearer user-jwt-xyz"
    assert captured["params"] == {
        "pid": "42",
        "q": "cough",
        "limit": "5",
        "since_days": "365",
    }


async def test_fetch_passes_explicit_limit_and_since() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"results": []})

    fetcher = _make_fetcher(handler=httpx.MockTransport(handler))

    await fetcher.fetch(ctx=_ctx(), query="cough", limit=10, since_days=30)

    assert captured["params"]["limit"] == "10"
    assert captured["params"]["since_days"] == "30"


async def test_fetch_returns_empty_payload_when_no_results() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    fetcher = _make_fetcher(handler=httpx.MockTransport(handler))

    result = await fetcher.fetch(ctx=_ctx(), query="anything")

    assert result.payload.results == ()
    assert result.metadata.redaction_applied is False


async def test_fetch_raises_on_http_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    fetcher = _make_fetcher(handler=httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError):
        await fetcher.fetch(ctx=_ctx(), query="x")


async def test_fetch_raises_on_unauthorized() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Invalid or expired token"})

    fetcher = _make_fetcher(handler=httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError):
        await fetcher.fetch(ctx=_ctx(), query="x")


# ---------- Empty / whitespace query ----------


async def test_fetch_short_circuits_on_empty_query_without_hitting_endpoint() -> None:
    # Defense-in-depth: PHP rejects empty `q` with 400, but the Python
    # layer should not waste a round trip on an obviously bad input.
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"results": []})

    fetcher = _make_fetcher(handler=httpx.MockTransport(handler))

    result = await fetcher.fetch(ctx=_ctx(), query="")

    assert called is False
    assert result.payload.results == ()


async def test_fetch_short_circuits_on_whitespace_only_query() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"results": []})

    fetcher = _make_fetcher(handler=httpx.MockTransport(handler))

    result = await fetcher.fetch(ctx=_ctx(), query="   \t  ")

    assert called is False
    assert result.payload.results == ()


# ---------- Payload typing ----------


async def test_fetch_parses_ranked_body_into_typed_payload_when_all_allowed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ranked_body())

    fetcher = _make_fetcher(handler=httpx.MockTransport(handler))

    result = await fetcher.fetch(ctx=_ctx(), query="cough")

    assert isinstance(result, SearchNotesResult)
    assert isinstance(result.payload, SearchNotesPayload)
    assert len(result.payload.results) == 3

    top, mid, low = result.payload.results
    assert top.id == 5
    assert top.source == "pnote"
    assert top.score == 2.1
    assert top.snippet is not None
    assert top.permission_denied is False
    assert mid.score == 1.4
    assert low.score == 0.9


def test_search_notes_payload_is_frozen_and_round_trips_json() -> None:
    body = _ranked_body()
    items = tuple(SearchHit.model_validate(r) for r in body["results"])
    payload = SearchNotesPayload(results=items)

    rehydrated = SearchNotesPayload.model_validate_json(payload.model_dump_json())
    assert rehydrated == payload

    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        payload.results[0].snippet = "edited"  # type: ignore[misc]


# ---------- Tool spec ----------


def test_tool_spec_declares_required_query_and_optional_limit() -> None:
    assert SEARCH_NOTES_TOOL_SPEC.name == "search_notes"
    assert "search" in SEARCH_NOTES_TOOL_SPEC.description.lower()

    schema = SEARCH_NOTES_TOOL_SPEC.input_schema
    assert schema["type"] == "object"
    properties = schema["properties"]
    assert "query" in properties
    assert properties["query"]["type"] == "string"
    # query is the only required parameter
    assert "query" in schema.get("required", [])
    assert "limit" in properties
    assert "since_days" in properties


# ---------- Sensitivity gating (snippet is the field we protect) ----------


async def test_all_allowed_emits_full_payload_with_no_redaction_flag() -> None:
    gateway = _allow_all_gateway()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ranked_body())

    fetcher = _make_fetcher(
        handler=httpx.MockTransport(handler), gateway=gateway,
    )

    result = await fetcher.fetch(ctx=_ctx(), query="cough")

    assert gateway.check_record_visibility.await_count == 3
    assert all(not h.permission_denied for h in result.payload.results)
    assert all(h.snippet for h in result.payload.results)
    assert result.metadata.redaction_applied is False
    assert result.metadata.redacted_fields == ()


async def test_all_denied_marks_every_hit_permission_denied_and_strips_snippet() -> None:
    gateway = _deny_all_gateway()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ranked_body())

    fetcher = _make_fetcher(
        handler=httpx.MockTransport(handler), gateway=gateway,
    )

    result = await fetcher.fetch(ctx=_ctx(), query="cough")

    assert len(result.payload.results) == 3
    assert all(h.permission_denied for h in result.payload.results)
    # Title and snippet leak content; both stripped.
    assert all(h.snippet is None for h in result.payload.results)
    assert all(h.title is None for h in result.payload.results)
    # id / source / date / score survive so the model can describe what
    # was withheld in aggregate ("3 restricted matches").
    assert all(h.id > 0 for h in result.payload.results)
    assert all(h.score is not None for h in result.payload.results)
    assert result.metadata.redaction_applied is True
    assert "snippet" in result.metadata.redacted_fields


async def test_mixed_visibility_redacts_only_denied_rows() -> None:
    decisions = iter([True, True, False])
    gateway = AsyncMock()

    async def _check(_ctx: RequestContext, _md: RecordMetadata) -> bool:
        return next(decisions)

    gateway.check_record_visibility = AsyncMock(side_effect=_check)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ranked_body())

    fetcher = _make_fetcher(
        handler=httpx.MockTransport(handler), gateway=gateway,
    )

    result = await fetcher.fetch(ctx=_ctx(), query="cough")

    assert len(result.payload.results) == 3
    assert result.payload.results[0].permission_denied is False
    assert result.payload.results[1].permission_denied is False
    assert result.payload.results[2].permission_denied is True
    assert result.payload.results[2].snippet is None
    assert result.payload.results[2].title is None
    # The denied row's score still surfaces (no body content).
    assert result.payload.results[2].score == 0.9
    assert result.metadata.redaction_applied is True


async def test_visibility_metadata_passes_note_title() -> None:
    captured: list[RecordMetadata] = []

    async def _check(_ctx: RequestContext, metadata: RecordMetadata) -> bool:
        captured.append(metadata)
        return True

    gateway = AsyncMock()
    gateway.check_record_visibility = AsyncMock(side_effect=_check)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ranked_body())

    fetcher = _make_fetcher(
        handler=httpx.MockTransport(handler), gateway=gateway,
    )

    await fetcher.fetch(ctx=_ctx(), query="cough")

    assert len(captured) == 3
    assert captured[2].note_title == "Substance abuse counseling"
    # Search payload doesn't surface note_type, so the fetcher passes
    # None — title-prefix rules in the policy still fire.
    assert captured[2].note_type is None
    # attending_only is also unknown at search time → default False.
    assert captured[2].attending_only is False


# ---------- Result envelope ----------


async def test_search_notes_result_serializes_payload_envelope() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ranked_body())

    fetcher = _make_fetcher(handler=httpx.MockTransport(handler))

    result = await fetcher.fetch(ctx=_ctx(), query="cough")
    decoded = json.loads(result.model_dump_json())

    assert decoded["metadata"]["tool_name"] == "search_notes"
    assert decoded["metadata"]["source"] == "openemr.notes_search"
    assert len(decoded["payload"]["results"]) == 3
