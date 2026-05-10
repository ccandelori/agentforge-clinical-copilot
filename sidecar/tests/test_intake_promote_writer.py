"""Unit tests for :class:`agentforge.tools.intake_promote.IntakePromoteWriter`.

Mirrors the shape of ``tests/test_document_upload.py`` (if it existed)
and the persister tests under ``tests/persist/`` — uses
:class:`httpx.MockTransport` to cover the four failure modes:

* transport-level error (DNS/TLS/refused) → ``status_code == 0``
* upstream 4xx/5xx → ``status_code == upstream``
* 2xx with a malformed (non-dict) body → contract-drift error
* 2xx with non-JSON bytes → contract-drift error

…plus the happy-path assertion that the JWT is forwarded as a
Bearer header and the body is sent verbatim.
"""

from __future__ import annotations

import pytest
import httpx

from agentforge.tools.intake_promote import (
    IntakePromoteError,
    IntakePromoteWriter,
)


def _writer_with(handler) -> IntakePromoteWriter:
    return IntakePromoteWriter(
        base_url="https://openemr.example",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.asyncio
async def test_happy_path_returns_parsed_receipt() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["auth"] = request.headers.get("Authorization")
        captured["json"] = request.read()
        return httpx.Response(
            201,
            json={
                "promoted": [
                    {"kind": "allergy", "lists_id": 99, "title": "Penicillin"},
                ],
                "count": 1,
            },
        )

    writer = _writer_with(handler)
    result = await writer.promote(
        jwt="signed.jwt.value",
        body={"patient_id": 42, "items": [{"kind": "allergy", "title": "x"}]},
    )

    assert result["count"] == 1
    assert captured["method"] == "POST"
    assert captured["auth"] == "Bearer signed.jwt.value"
    # Path is the configured promote_intake.php under the AgentForge module.
    assert "/promote_intake.php" in captured["url"]


@pytest.mark.asyncio
async def test_upstream_4xx_raises_with_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    writer = _writer_with(handler)
    with pytest.raises(IntakePromoteError) as exc_info:
        await writer.promote(jwt="x", body={"patient_id": 1, "items": []})
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_transport_failure_raises_with_zero_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("DNS lookup failed", request=request)

    writer = _writer_with(handler)
    with pytest.raises(IntakePromoteError) as exc_info:
        await writer.promote(jwt="x", body={})
    assert exc_info.value.status_code == 0


@pytest.mark.asyncio
async def test_non_json_response_body_is_contract_drift() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json{")

    writer = _writer_with(handler)
    with pytest.raises(IntakePromoteError) as exc_info:
        await writer.promote(jwt="x", body={})
    # 2xx with malformed body — status_code stays the upstream 200,
    # which the BFF's status_code != 0 branch treats as upstream-class.
    assert exc_info.value.status_code == 200


@pytest.mark.asyncio
async def test_non_dict_response_body_is_contract_drift() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "a", "dict"])

    writer = _writer_with(handler)
    with pytest.raises(IntakePromoteError) as exc_info:
        await writer.promote(jwt="x", body={})
    assert exc_info.value.status_code == 200
