"""Tests for :class:`DocumentBytesFetcher` (W2 MR 7).

The fetcher is the trust boundary between the sidecar and OpenEMR's
``InternalDocumentBytesController``: it forwards the user-bound JWT,
asks for one document by id, and returns the raw body. The PHP
endpoint enforces patient-scoping (the JWT's ``patient_id`` claim
must match the document's owning patient), so the contract under
test here is "what URL, what headers, what query params, and how do
non-2xx responses surface to the caller".
"""

from __future__ import annotations

import httpx
import pytest

from agentforge.tools.document_bytes import (
    DocumentBytes,
    DocumentBytesFetcher,
    DocumentBytesFetchError,
)

BASE_URL = "https://openemr.test"
PATH = (
    "/interface/modules/custom_modules/oe-module-agentforge"
    "/public/internal/get_document_bytes.php"
)


def _make_fetcher(handler: httpx.MockTransport) -> DocumentBytesFetcher:
    client = httpx.AsyncClient(transport=handler, base_url=BASE_URL)
    return DocumentBytesFetcher(base_url=BASE_URL, http_client=client)


async def test_fetch_returns_bytes_and_mimetype() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            content=b"%PDF-1.4\nstub-bytes",
            headers={"Content-Type": "application/pdf"},
        )

    fetcher = _make_fetcher(httpx.MockTransport(handler))

    result = await fetcher.fetch(document_id=42, raw_token="user-jwt-xyz")

    assert isinstance(result, DocumentBytes)
    assert result.content == b"%PDF-1.4\nstub-bytes"
    assert result.mimetype == "application/pdf"
    assert captured["url"] == f"{BASE_URL}{PATH}?document_id=42"
    assert captured["auth"] == "Bearer user-jwt-xyz"
    assert captured["params"] == {"document_id": "42"}


async def test_fetch_strips_charset_suffix_from_content_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"raw",
            headers={"Content-Type": "application/pdf; charset=binary"},
        )

    fetcher = _make_fetcher(httpx.MockTransport(handler))

    result = await fetcher.fetch(document_id=1, raw_token="jwt")

    assert result.mimetype == "application/pdf"


async def test_fetch_falls_back_to_octet_stream_when_content_type_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # httpx auto-injects content-type: text/plain when content is bytes
        # without a header — patch headers explicitly to simulate absence.
        response = httpx.Response(200, content=b"raw")
        response.headers.pop("content-type", None)
        return response

    fetcher = _make_fetcher(httpx.MockTransport(handler))

    result = await fetcher.fetch(document_id=1, raw_token="jwt")

    assert result.mimetype == "application/octet-stream"


@pytest.mark.parametrize("status", [401, 403, 404, 500])
async def test_fetch_raises_typed_error_on_non_2xx(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "nope"})

    fetcher = _make_fetcher(httpx.MockTransport(handler))

    with pytest.raises(DocumentBytesFetchError) as exc_info:
        await fetcher.fetch(document_id=42, raw_token="jwt")

    assert exc_info.value.status_code == status


async def test_fetch_wraps_transport_failures_with_status_zero() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    fetcher = _make_fetcher(httpx.MockTransport(handler))

    with pytest.raises(DocumentBytesFetchError) as exc_info:
        await fetcher.fetch(document_id=42, raw_token="jwt")

    # status_code == 0 signals "request never reached the server" so
    # the /turn route can map it to a 503 instead of a generic 502.
    assert exc_info.value.status_code == 0


@pytest.mark.parametrize("bad_id", [0, -1, -100])
async def test_fetch_rejects_non_positive_document_id(bad_id: int) -> None:
    fetcher = _make_fetcher(
        httpx.MockTransport(lambda r: httpx.Response(200, content=b""))
    )

    with pytest.raises(ValueError, match="document_id"):
        await fetcher.fetch(document_id=bad_id, raw_token="jwt")
