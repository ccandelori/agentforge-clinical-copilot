"""Tests for the tool-layer DTO envelopes."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from agentforge.tools.demographics import DemographicsPayload, DemographicsResult
from agentforge.tools.dtos import ToolResultMetadata


def _sample_result() -> DemographicsResult:
    metadata = ToolResultMetadata(
        tool_name="get_demographics",
        fetched_at=datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC),
        data_freshness_seconds=60,
        source="openemr.demographics",
    )
    payload = DemographicsPayload(
        patient_id=42,
        given_name="Ada",
        family_name="Lovelace",
        date_of_birth=date(1815, 12, 10),
        sex="F",
        preferred_language="en-GB",
    )
    return DemographicsResult(metadata=metadata, payload=payload)


def test_demographics_result_is_frozen_and_round_trips() -> None:
    result = _sample_result()

    # Frozen: mutating either layer raises a ValidationError under Pydantic v2.
    with pytest.raises(ValidationError):
        result.payload.patient_id = 99
    with pytest.raises(ValidationError):
        result.metadata.tool_name = "other"

    # JSON round-trip preserves every field.
    rehydrated = DemographicsResult.model_validate_json(result.model_dump_json())
    assert rehydrated == result


def test_metadata_defaults() -> None:
    metadata = ToolResultMetadata(
        tool_name="get_demographics",
        fetched_at=datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC),
        data_freshness_seconds=60,
        source="openemr.demographics",
    )

    assert metadata.redaction_applied is False
    assert metadata.redacted_fields == ()


def test_serialization_uses_iso8601_for_datetimes() -> None:
    result = _sample_result()

    dumped = result.model_dump(mode="json")
    assert dumped["metadata"]["fetched_at"] == "2026-04-30T12:00:00Z"
    assert dumped["payload"]["date_of_birth"] == "1815-12-10"
