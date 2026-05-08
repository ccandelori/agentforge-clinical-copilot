"""Tests for the W2 programmatic eval checks (Task 17.3).

Three deterministic checks that need no LLM:

  * ``schema_valid`` — Pydantic validation of structured output.
  * ``citation_present`` — every claim sentence carries a Citation.
  * ``no_phi_in_logs`` — regex sweep over Langfuse trace exports for
    SSN / MRN / phone-shaped PHI patterns.

These run before the LLM judge in the harness so the cheap signal lands
first; an LLM-judge call on a schema-broken response is wasted budget.
"""

from __future__ import annotations

from agentforge.schemas.citation import (
    Citation,
    PageBBox,
    SourceType,
)
from tests.eval.graders.programmatic import (
    ProgrammaticChecks,
    check_citation_present,
    check_no_phi_in_logs,
    check_schema_valid,
)


# ---------------------------------------------------------------------------
# schema_valid
# ---------------------------------------------------------------------------


class TestSchemaValid:
    def test_valid_citation_payload_passes(self) -> None:
        payload = {
            "source_type": "openemr_record",
            "source_id": "42",
            "page_or_section": "problem #42",
            "field_or_chunk_id": "title",
            "quote_or_value": "Type 2 diabetes",
        }
        result = check_schema_valid(Citation, payload)
        assert result.passed is True
        assert result.error is None

    def test_invalid_citation_payload_fails(self) -> None:
        # Missing required field source_type → pydantic ValidationError.
        payload = {"source_id": "42"}
        result = check_schema_valid(Citation, payload)
        assert result.passed is False
        assert result.error is not None
        assert "source_type" in result.error.lower()

    def test_scanned_source_without_bbox_fails(self) -> None:
        # The Citation model rejects LAB_PDF without page_bbox via
        # model_validator — a real schema-layer rejection, not a
        # missing required field.
        payload = {
            "source_type": "lab_pdf",
            "source_id": "doc-1",
            "page_or_section": "page 1",
            "field_or_chunk_id": "hba1c",
            "quote_or_value": "7.2",
        }
        result = check_schema_valid(Citation, payload)
        assert result.passed is False

    def test_scanned_source_with_low_confidence_bbox_fails(self) -> None:
        payload = {
            "source_type": "lab_pdf",
            "source_id": "doc-1",
            "page_or_section": "page 1",
            "field_or_chunk_id": "hba1c",
            "quote_or_value": "7.2",
            "page_bbox": {
                "page": 1,
                "x0": 0.1,
                "y0": 0.1,
                "x1": 0.5,
                "y1": 0.2,
                "bbox_confidence": 0.3,
            },
        }
        result = check_schema_valid(Citation, payload)
        assert result.passed is False

    def test_scanned_source_with_high_confidence_bbox_passes(self) -> None:
        payload = {
            "source_type": "lab_pdf",
            "source_id": "doc-1",
            "page_or_section": "page 1",
            "field_or_chunk_id": "hba1c",
            "quote_or_value": "7.2",
            "page_bbox": {
                "page": 1,
                "x0": 0.1,
                "y0": 0.1,
                "x1": 0.5,
                "y1": 0.2,
                "bbox_confidence": 0.9,
            },
        }
        result = check_schema_valid(Citation, payload)
        assert result.passed is True


# ---------------------------------------------------------------------------
# citation_present
# ---------------------------------------------------------------------------


def _intake_citation() -> Citation:
    return Citation(
        source_type=SourceType.INTAKE_FORM,
        source_id="doc-1",
        page_or_section="page 1",
        field_or_chunk_id="primary_complaint",
        quote_or_value="chest pain on exertion",
        page_bbox=PageBBox(
            page=1, x0=0.1, y0=0.1, x1=0.4, y1=0.2, bbox_confidence=0.9
        ),
    )


class TestCitationPresent:
    def test_response_with_inline_record_citation_passes(self) -> None:
        response = "Patient has type 2 diabetes [problem #11]."
        result = check_citation_present(response, structured_citations=())
        assert result.passed is True
        assert result.citation_count == 1

    def test_response_with_structured_citation_passes(self) -> None:
        response = "Primary complaint: chest pain on exertion."
        result = check_citation_present(
            response, structured_citations=(_intake_citation(),)
        )
        assert result.passed is True

    def test_response_with_no_citation_fails(self) -> None:
        response = "Patient has type 2 diabetes."
        result = check_citation_present(response, structured_citations=())
        assert result.passed is False
        assert result.citation_count == 0

    def test_empty_response_fails(self) -> None:
        result = check_citation_present("", structured_citations=())
        assert result.passed is False

    def test_multiple_citations_count_correctly(self) -> None:
        response = "[problem #11] and also [medication #21]."
        result = check_citation_present(response, structured_citations=())
        assert result.citation_count == 2
        assert result.passed is True


# ---------------------------------------------------------------------------
# no_phi_in_logs
# ---------------------------------------------------------------------------


class TestNoPhiInLogs:
    def test_clean_logs_pass(self) -> None:
        logs = [
            "trace_id=abc123 user_id_pseudonym=hash_xyz",
            "tool=get_active_problems status=ok latency_ms=42",
        ]
        result = check_no_phi_in_logs(logs)
        assert result.passed is True
        assert result.matches == ()

    def test_ssn_pattern_fails(self) -> None:
        logs = ["unexpected text 123-45-6789 leaked through hashing"]
        result = check_no_phi_in_logs(logs)
        assert result.passed is False
        # The matched substring is reported so the operator can find
        # the leak in the trace store.
        assert any("123-45-6789" in m for m in result.matches)

    def test_phone_pattern_fails(self) -> None:
        logs = ["call 555-867-5309 to confirm"]
        result = check_no_phi_in_logs(logs)
        assert result.passed is False

    def test_phone_dot_format_fails(self) -> None:
        logs = ["555.867.5309"]
        result = check_no_phi_in_logs(logs)
        assert result.passed is False

    def test_mrn_pattern_fails(self) -> None:
        # MRN is an 8-10 digit identifier; we conservatively flag any
        # unbroken 8-10 digit run as MRN-shaped.
        logs = ["MRN: 1234567890"]
        result = check_no_phi_in_logs(logs)
        assert result.passed is False

    def test_pseudonyms_do_not_match(self) -> None:
        # Hex pseudonyms are 12-char and don't match the all-digit
        # patterns; the check must not flag them.
        logs = ["user_id_pseudonym=ab12cd34ef56"]
        result = check_no_phi_in_logs(logs)
        assert result.passed is True

    def test_returns_match_text_for_each_offender(self) -> None:
        logs = ["123-45-6789", "555-555-5555", "ok ok ok"]
        result = check_no_phi_in_logs(logs)
        assert result.passed is False
        assert len(result.matches) >= 2


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


class TestProgrammaticChecksAggregate:
    def test_all_three_passing_yields_aggregate_pass(self) -> None:
        # Build a minimal compliant trio.
        response = "Patient has hypertension [problem #5]."
        payload = {
            "source_type": "openemr_record",
            "source_id": "5",
            "page_or_section": "problem #5",
            "field_or_chunk_id": "title",
            "quote_or_value": "Hypertension",
        }
        logs = ["trace ok"]

        agg = ProgrammaticChecks.run(
            response=response,
            structured_citation_payload=payload,
            structured_citations=(),
            logs=logs,
        )

        assert agg.schema_valid.passed is True
        assert agg.citation_present.passed is True
        assert agg.no_phi_in_logs.passed is True
        assert agg.passed is True

    def test_any_failure_fails_aggregate(self) -> None:
        # citation absent → aggregate fail even when the other two pass.
        agg = ProgrammaticChecks.run(
            response="No citation here.",
            structured_citation_payload={
                "source_type": "openemr_record",
                "source_id": "5",
                "page_or_section": "problem #5",
                "field_or_chunk_id": "title",
                "quote_or_value": "Hypertension",
            },
            structured_citations=(),
            logs=["clean"],
        )
        assert agg.passed is False
