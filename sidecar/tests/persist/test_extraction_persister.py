"""Tests for :class:`ExtractionPersister` (P1.1).

The persister is the trust boundary between the sidecar's post-extract
hook and the OpenEMR persist controllers. It owns: URL construction
for both the intake and lab endpoints, JWT bearer header injection,
Pydantic→JSON body shaping, and typed error surface for upstream
failures (4xx, 5xx, transport).

What's pinned here:

- The POST URL each method targets — both endpoints live under
  ``/interface/modules/custom_modules/oe-module-agentforge/public/internal``
  matching the established sibling pattern (``DocumentBytesFetcher``).
- The body shape — ``model_dump(mode="json")`` of the extraction Pydantic
  shape. The PHP controllers consume the field names the Pydantic
  classes emit; if a field name diverges this test fails.
- The Authorization bearer header carries the supplied internal JWT
  verbatim. The PHP controller's :class:`AgentJwtValidator` parses the
  bearer prefix and the patient-scope claim off this token; the
  persister must not touch it.
- The returned :class:`PersistedHandle` carries the resource-id the
  controller emitted (``questionnaire_response_id`` or
  ``procedure_order_id``) so the orchestrator can stash it for the
  dashboard's confirm-panel.
- Failure shapes: 4xx, 5xx, and transport errors all raise
  :class:`ExtractionPersistError` with ``status_code`` set so the caller
  can tell "OpenEMR rejected our payload" (4xx — programmer bug) from
  "OpenEMR is down" (5xx / transport — operational issue) from "request
  was accepted but returned a malformed body" (decode failure).
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from agentforge.persist import (
    ExtractionPersister,
    ExtractionPersistError,
    PersistedHandle,
)
from agentforge.schemas.citation import Citation, PageBBox, SourceType
from agentforge.schemas.intake import (
    AllergyEntry,
    Demographic,
    FamilyHistoryEntry,
    IntakeFormExtraction,
    MedicationEntry,
)
from agentforge.schemas.lab import (
    AbnormalFlag,
    LabPdfExtraction,
    LabValue,
)

BASE_URL = "https://openemr.test"
INTAKE_PATH = (
    "/interface/modules/custom_modules/oe-module-agentforge"
    "/public/internal/persist_questionnaire_response.php"
)
LAB_PATH = (
    "/interface/modules/custom_modules/oe-module-agentforge"
    "/public/internal/persist_lab_result.php"
)


def _make_persister(handler: httpx.MockTransport) -> ExtractionPersister:
    client = httpx.AsyncClient(transport=handler, base_url=BASE_URL)
    return ExtractionPersister(base_url=BASE_URL, http_client=client)


def _citation(*, source_type: SourceType, source_id: str = "12") -> Citation:
    return Citation(
        source_type=source_type,
        source_id=source_id,
        page_or_section="page 1",
        field_or_chunk_id="dob",
        quote_or_value="1972-04-12",
        page_bbox=PageBBox(
            page=1, x0=0.1, y0=0.1, x1=0.5, y1=0.2, bbox_confidence=0.9
        ),
    )


def _intake_extraction(*, document_id: int = 12, patient_id: int = 8) -> IntakeFormExtraction:
    return IntakeFormExtraction(
        document_id=document_id,
        patient_id=patient_id,
        chief_concern="annual check up",
        chief_concern_citation=_citation(source_type=SourceType.INTAKE_FORM),
        demographics=[
            Demographic(
                field="date_of_birth",
                value="1972-04-12",
                citation=_citation(source_type=SourceType.INTAKE_FORM),
            ),
        ],
        medications=[
            MedicationEntry(
                name="metformin",
                dose="500mg",
                frequency="bid",
                citation=_citation(source_type=SourceType.INTAKE_FORM),
            ),
        ],
        allergies=[
            AllergyEntry(
                substance="penicillin",
                citation=_citation(source_type=SourceType.INTAKE_FORM),
            ),
        ],
        family_history=[
            FamilyHistoryEntry(
                relative="mother",
                condition="diabetes",
                citation=_citation(source_type=SourceType.INTAKE_FORM),
            ),
        ],
        extraction_confidence=0.91,
        unsupported_fields=[],
    )


def _lab_extraction(*, document_id: int = 12, patient_id: int = 8) -> LabPdfExtraction:
    return LabPdfExtraction(
        document_id=document_id,
        patient_id=patient_id,
        ordering_provider="Dr. Smith",
        accession_number="ACC-001",
        values=[
            LabValue(
                test_name="hemoglobin",
                loinc_code="718-7",
                value="13.2",
                unit="g/dL",
                reference_range="12.0 - 16.0",
                collection_date=date(2026, 5, 1),
                abnormal_flag=AbnormalFlag.NORMAL,
                citation=_citation(source_type=SourceType.LAB_PDF),
            ),
        ],
        extraction_confidence=0.93,
        unsupported_fields=[],
    )


# ---------------------------------------------------------------------------
# Intake — happy path
# ---------------------------------------------------------------------------


async def test_persist_intake_posts_extraction_body_and_returns_handle() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["auth"] = request.headers.get("Authorization")
        captured["content_type"] = request.headers.get("Content-Type")
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(
            201,
            json={
                "questionnaire_response_id": 117,
                "patient_id": 8,
                "extraction_status": "completed",
            },
        )

    persister = _make_persister(httpx.MockTransport(handler))
    extraction = _intake_extraction()

    handle = await persister.persist_intake(
        extraction,
        patient_id=8,
        document_id=12,
        internal_jwt="signed.jwt.value",
    )

    assert isinstance(handle, PersistedHandle)
    assert handle.resource_id == "117"
    assert handle.kind == "questionnaire_response"

    assert captured["method"] == "POST"
    assert captured["url"] == f"{BASE_URL}{INTAKE_PATH}"
    assert captured["auth"] == "Bearer signed.jwt.value"
    # JSON content type is what the controller's json_decode expects.
    assert isinstance(captured["content_type"], str)
    assert captured["content_type"].startswith("application/json")

    # The body is the Pydantic dump — we don't assert each leaf, but
    # we do pin the load-bearing fields the controller reads.
    import json as _json
    raw_body = captured["body"]
    assert isinstance(raw_body, str)
    body = _json.loads(raw_body)
    assert body["document_id"] == 12
    assert body["patient_id"] == 8
    assert body["chief_concern"] == "annual check up"
    assert isinstance(body["demographics"], list)
    assert body["demographics"][0]["field"] == "date_of_birth"
    assert isinstance(body["medications"], list)
    assert body["medications"][0]["name"] == "metformin"
    assert isinstance(body["allergies"], list)
    assert body["allergies"][0]["substance"] == "penicillin"
    assert isinstance(body["family_history"], list)
    assert body["family_history"][0]["relative"] == "mother"
    assert body["unsupported_fields"] == []


# ---------------------------------------------------------------------------
# Lab — happy path
# ---------------------------------------------------------------------------


async def test_persist_lab_posts_extraction_body_and_returns_handle() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(
            201,
            json={
                "procedure_order_id": 42,
                "procedure_report_id": 17,
                "procedure_result_ids": [55, 56],
                "patient_id": 8,
                "extraction_status": "completed",
            },
        )

    persister = _make_persister(httpx.MockTransport(handler))
    extraction = _lab_extraction()

    handle = await persister.persist_lab(
        extraction,
        patient_id=8,
        document_id=12,
        internal_jwt="signed.jwt.value",
    )

    assert handle.resource_id == "42"
    assert handle.kind == "procedure_order"

    assert captured["method"] == "POST"
    assert captured["url"] == f"{BASE_URL}{LAB_PATH}"
    assert captured["auth"] == "Bearer signed.jwt.value"

    import json as _json
    raw_body = captured["body"]
    assert isinstance(raw_body, str)
    body = _json.loads(raw_body)
    assert body["document_id"] == 12
    assert body["patient_id"] == 8
    assert body["ordering_provider"] == "Dr. Smith"
    assert body["accession_number"] == "ACC-001"
    assert isinstance(body["values"], list)
    row = body["values"][0]
    assert row["test_name"] == "hemoglobin"
    assert row["loinc_code"] == "718-7"
    assert row["value"] == "13.2"
    assert row["unit"] == "g/dL"
    assert row["reference_range"] == "12.0 - 16.0"
    # date → ISO string in JSON-mode dump
    assert row["collection_date"] == "2026-05-01"
    # StrEnum → its string value in JSON-mode dump
    assert row["abnormal_flag"] == "normal"


# ---------------------------------------------------------------------------
# Failure — 4xx
# ---------------------------------------------------------------------------


async def test_persist_intake_4xx_raises_typed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "Patient scope check failed"})

    persister = _make_persister(httpx.MockTransport(handler))
    extraction = _intake_extraction()

    with pytest.raises(ExtractionPersistError) as excinfo:
        await persister.persist_intake(
            extraction,
            patient_id=8,
            document_id=12,
            internal_jwt="signed.jwt.value",
        )

    assert excinfo.value.status_code == 403


async def test_persist_lab_5xx_raises_typed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    persister = _make_persister(httpx.MockTransport(handler))
    extraction = _lab_extraction()

    with pytest.raises(ExtractionPersistError) as excinfo:
        await persister.persist_lab(
            extraction,
            patient_id=8,
            document_id=12,
            internal_jwt="signed.jwt.value",
        )

    assert excinfo.value.status_code == 500


async def test_persist_transport_failure_raises_with_zero_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # Raising a transport error from the handler propagates it to the caller.
        raise httpx.ConnectError("OpenEMR unreachable")

    persister = _make_persister(httpx.MockTransport(handler))
    extraction = _intake_extraction()

    with pytest.raises(ExtractionPersistError) as excinfo:
        await persister.persist_intake(
            extraction,
            patient_id=8,
            document_id=12,
            internal_jwt="signed.jwt.value",
        )

    # 0 = mirror DocumentBytesFetcher's "transport-level failure" sentinel
    assert excinfo.value.status_code == 0


async def test_persist_intake_decode_failure_raises_typed_error() -> None:
    """A 2xx with a body that doesn't carry the expected resource id
    is a controller-contract violation; surface it as a typed error so
    the orchestrator's best-effort log records the actual failure instead
    of a TypeError.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"unrelated": True})

    persister = _make_persister(httpx.MockTransport(handler))
    extraction = _intake_extraction()

    with pytest.raises(ExtractionPersistError) as excinfo:
        await persister.persist_intake(
            extraction,
            patient_id=8,
            document_id=12,
            internal_jwt="signed.jwt.value",
        )

    # Distinguished from upstream failures: status is the actual response
    # status (the controller did succeed at the HTTP layer), and the
    # caller can read this to log the contract drift specifically.
    assert excinfo.value.status_code == 201


# ---------------------------------------------------------------------------
# aclose smoke
# ---------------------------------------------------------------------------


async def test_aclose_releases_underlying_client() -> None:
    transport = httpx.MockTransport(
        lambda r: httpx.Response(201, json={"questionnaire_response_id": 1})
    )
    persister = _make_persister(transport)
    await persister.aclose()
    # Subsequent calls fail because the client is closed; we just want the
    # method to exist and not raise on its own.
