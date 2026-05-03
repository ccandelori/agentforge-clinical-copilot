"""No-PHI assertion test for Langfuse tracing.

Verifies that none of the raw patient identifiers, patient names, dates of
birth, raw tokens, or raw JSON payloads appear in any Langfuse SDK call
made during an orchestrator turn. The structural boundary is enforced by
the Protocol (callers must pass hashes, not bodies), but this integration
test confirms the full-stack wiring holds end-to-end without a live
Langfuse instance.

The test uses a real ``AgentLangfuse`` instance whose underlying Langfuse
SDK is replaced with a ``MagicMock`` so all SDK calls are recorded and
inspectable without network access.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from agentforge.gateway.auth_gateway import RequestContext
from agentforge.llm.types import LLMResponse
from agentforge.observability.langfuse_client import AgentLangfuse
from agentforge.orchestrator import Orchestrator
from agentforge.tools.demographics import DemographicsPayload, DemographicsResult
from agentforge.tools.dtos import ToolResultMetadata

# ---- Constants used in the test -----------------------------------------

HMAC_KEY = b"phi-test-hmac-key-32-bytes-xxxxx"
HOST = "http://localhost:3000"
PUBLIC_KEY = "pk-lf-test"
SECRET_KEY = "sk-lf-test"

# The raw PHI we plant in the mock demographics response. If any of these
# strings appear in a Langfuse SDK call argument, the boundary is broken.
RAW_PATIENT_NAME = "Jane PhiTestDoe"
RAW_PATIENT_DOB = "1972-06-15"  # ISO string form
RAW_BEARER_TOKEN = "super.secret.jwt.phi-token"
RAW_PATIENT_ID = 80808
RAW_USER_ID = 424242


def _meta(name: str) -> ToolResultMetadata:
    return ToolResultMetadata(
        tool_name=name,
        fetched_at=datetime.now(UTC),
        data_freshness_seconds=60,
        source=f"openemr.{name}",
    )


def _demographics_result() -> DemographicsResult:
    return DemographicsResult(
        metadata=_meta("get_demographics"),
        payload=DemographicsPayload(
            patient_id=RAW_PATIENT_ID,  # 80808 — unlikely to appear in token counts
            given_name="Jane",
            family_name="PhiTestDoe",
            date_of_birth=date(1972, 6, 15),
        ),
    )


def _ctx() -> RequestContext:
    return RequestContext(
        user_id=RAW_USER_ID,
        patient_id=RAW_PATIENT_ID,
        username="testuser",
        role="clinician",
        breakglass_flag=False,
        breakglass_reason=None,
        sensitivity_clearances=frozenset(),
        raw_token=RAW_BEARER_TOKEN,
    )


def _mock_span() -> MagicMock:
    span = MagicMock()
    span.trace_id = "trace-phi-test-001"
    span.start_observation.return_value = MagicMock()
    return span


def _build_langfuse_with_mock_sdk() -> tuple[AgentLangfuse, MagicMock]:
    """Return (AgentLangfuse, underlying SDK mock) with all network patched."""
    sdk_mock = MagicMock()
    sdk_mock.start_observation.return_value = _mock_span()
    with patch("langfuse.Langfuse", return_value=sdk_mock):
        client = AgentLangfuse(
            host=HOST,
            public_key=PUBLIC_KEY,
            secret_key=SECRET_KEY,
            hmac_key=HMAC_KEY,
        )
    return client, sdk_mock


def _collect_all_call_args(mock_obj: MagicMock) -> list[str]:
    """Recursively collect every argument string from all calls on a mock.

    Walks ``mock_calls`` including nested attribute calls so no SDK
    method is missed. Each argument is converted to its string
    representation so substring checks work uniformly.
    """
    args_strs: list[str] = []
    for c in mock_obj.mock_calls:
        # call objects expose args and kwargs
        for arg in c.args:
            args_strs.append(repr(arg))
        for v in c.kwargs.values():
            args_strs.append(repr(v))
    return args_strs


# ---- Test ----------------------------------------------------------------


class TestNoPHIInTraces:
    """End-to-end assertion that PHI never reaches the Langfuse SDK."""

    async def test_no_phi_in_langfuse_sdk_calls(self) -> None:
        """Run a full orchestrator turn and assert no raw PHI in SDK calls."""
        langfuse_client, sdk_mock = _build_langfuse_with_mock_sdk()

        llm_mock = AsyncMock()
        llm_mock.complete.return_value = LLMResponse(
            text="The patient information has been retrieved.",
            tool_calls=[],
            stop_reason="end_turn",
            input_tokens=10,
            output_tokens=8,
        )

        demographics_mock = AsyncMock()
        demographics_mock.fetch.return_value = _demographics_result()

        orch = Orchestrator(
            llm=llm_mock,
            demographics_fetcher=demographics_mock,
            medications_fetcher=AsyncMock(),
            problems_fetcher=AsyncMock(),
            allergies_fetcher=AsyncMock(),
            labs_fetcher=AsyncMock(),
            vitals_fetcher=AsyncMock(),
            notes_fetcher=AsyncMock(),
            search_notes_fetcher=AsyncMock(),
            encounters_fetcher=AsyncMock(),
            immunizations_fetcher=AsyncMock(),
            procedures_fetcher=AsyncMock(),
            langfuse=langfuse_client,
            hmac_key=HMAC_KEY,
        )

        ctx = _ctx()
        reply = await orch.turn(ctx, "What are this patient's details?")
        assert reply  # turn completed

        # Collect every string that reached the Langfuse SDK mock.
        all_call_args = _collect_all_call_args(sdk_mock)
        combined = " ".join(all_call_args)

        # --- PHI assertions ---

        # Patient name must not appear verbatim.
        assert RAW_PATIENT_NAME not in combined, (
            f"Raw patient name '{RAW_PATIENT_NAME}' found in Langfuse SDK calls"
        )
        # Given name alone (less specific, but still PHI).
        assert "PhiTestDoe" not in combined, (
            "Patient family name 'PhiTestDoe' found in Langfuse SDK calls"
        )

        # Date of birth must not appear in any recognisable form.
        assert RAW_PATIENT_DOB not in combined, (
            f"Raw date of birth '{RAW_PATIENT_DOB}' found in Langfuse SDK calls"
        )

        # Raw bearer token must never cross into the trace store.
        assert RAW_BEARER_TOKEN not in combined, (
            "Raw bearer token found in Langfuse SDK calls"
        )

        # Raw integer patient_id must not appear in any metadata dict value
        # passed to the SDK. We check dict values specifically (not arbitrary
        # substrings) to avoid false positives from coincident small integers
        # like token counts. Patient IDs are always passed as metadata values,
        # never as raw top-level kwargs by the Protocol contract.
        for c in sdk_mock.mock_calls:
            for v in c.kwargs.values():
                if isinstance(v, dict):
                    for field_name, field_val in v.items():
                        if field_val == RAW_PATIENT_ID or field_val == str(RAW_PATIENT_ID):
                            assert False, (
                                f"Raw patient_id '{RAW_PATIENT_ID}' found as "
                                f"metadata['{field_name}'] in Langfuse SDK call — "
                                "should be pseudonymised"
                            )
                        if field_val == RAW_USER_ID or field_val == str(RAW_USER_ID):
                            assert False, (
                                f"Raw user_id '{RAW_USER_ID}' found as "
                                f"metadata['{field_name}'] in Langfuse SDK call — "
                                "should be pseudonymised"
                            )

        # --- Hash format assertions ---
        # args_hash and result_hash must be hex strings (full SHA-256 width)
        # or None (no args / error case).
        _hex_re = re.compile(r"^[0-9a-f]{16,}$")
        for c in sdk_mock.mock_calls:
            # Look for metadata dicts passed to start_observation.
            for v in c.kwargs.values():
                if isinstance(v, dict):
                    for field in ("args_hash", "result_hash"):
                        val = v.get(field)
                        if val is not None:
                            assert _hex_re.match(val), (
                                f"Span field '{field}' = {val!r} is not a hex "
                                "string — raw payload may have leaked"
                            )

    async def test_pseudonyms_are_not_raw_ids(self) -> None:
        """The user_id_pseudonym and patient_id_pseudonym in the trace metadata
        must differ from the raw integers they were derived from."""
        langfuse_client, sdk_mock = _build_langfuse_with_mock_sdk()

        langfuse_client.trace_turn(
            user_id=RAW_USER_ID,
            patient_id=RAW_PATIENT_ID,
            breakglass_flag=False,
            role="clinician",
        )

        kwargs = sdk_mock.start_observation.call_args.kwargs
        metadata = kwargs["metadata"]

        pseudo_user = metadata["user_id_pseudonym"]
        pseudo_patient = metadata["patient_id_pseudonym"]

        # Pseudonyms must be non-empty strings.
        assert isinstance(pseudo_user, str) and pseudo_user
        assert isinstance(pseudo_patient, str) and pseudo_patient

        # They must NOT equal the raw integer values.
        assert pseudo_user != str(RAW_USER_ID)
        assert pseudo_patient != str(RAW_PATIENT_ID)

        # They must look like hex strings (HMAC output, 16 chars).
        _hex_re = re.compile(r"^[0-9a-f]{16}$")
        assert _hex_re.match(pseudo_user), (
            f"user pseudonym '{pseudo_user}' is not a 16-char hex string"
        )
        assert _hex_re.match(pseudo_patient), (
            f"patient pseudonym '{pseudo_patient}' is not a 16-char hex string"
        )
