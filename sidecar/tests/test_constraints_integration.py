"""Integration: DomainConstraints plugged into StreamingVerifier.

The Protocol seam locked in by Task 28 means a sync DomainConstraints
implementation drops in without any structural change to the verifier.
This test exercises the end-to-end path: tokens stream in, sentence-
buffered claims go through both the structural cache check and the
domain constraint check, and the right rejection_reason ends up on
each chunk.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from agentforge.verifier import (
    REJECTION_MARKER,
    StreamingVerifier,
    VerifiedChunk,
)
from agentforge.verifier.cache import CitationIndex
from agentforge.verifier.constraints import DomainConstraints


async def _stream(*tokens: str) -> AsyncIterator[str]:
    for t in tokens:
        yield t


async def _collect(verifier: StreamingVerifier, *tokens: str) -> list[VerifiedChunk]:
    return [chunk async for chunk in verifier.verify_stream(_stream(*tokens))]


class TestStreamingVerifierWithDomainConstraints:
    async def test_grounded_medication_claim_passes(self) -> None:
        index = CitationIndex(
            records={("medication", "10"): {"id": 10, "name": "lisinopril 20mg"}},
        )
        verifier = StreamingVerifier(
            citation_index=index,
            domain_checker=DomainConstraints(),
        )
        chunks = await _collect(
            verifier,
            "Patient on lisinopril 20mg [medication #10]. ",
        )
        assert len(chunks) == 1
        assert chunks[0].verified is True
        assert chunks[0].rejection_reason is None

    async def test_dose_mismatch_is_rejected_with_stable_reason(self) -> None:
        index = CitationIndex(
            records={("medication", "10"): {"id": 10, "name": "lisinopril 20mg"}},
        )
        verifier = StreamingVerifier(
            citation_index=index,
            domain_checker=DomainConstraints(),
        )
        chunks = await _collect(
            verifier,
            "Patient on lisinopril 10mg [medication #10]. ",
        )
        assert len(chunks) == 1
        assert chunks[0].verified is False
        assert chunks[0].text == REJECTION_MARKER
        assert chunks[0].rejection_reason == "medication_dose_mismatch"

    async def test_counterfactual_against_problem_is_rejected(self) -> None:
        index = CitationIndex(
            records={("problem", "5"): {"id": 5, "title": "CHF"}},
        )
        verifier = StreamingVerifier(
            citation_index=index,
            domain_checker=DomainConstraints(),
        )
        chunks = await _collect(
            verifier,
            "Patient denies smoking [problem #5]. ",
        )
        assert len(chunks) == 1
        assert chunks[0].verified is False
        assert chunks[0].rejection_reason == "counterfactual_without_supporting_note"

    async def test_lab_value_outside_tolerance_rejected(self) -> None:
        index = CitationIndex(
            records={("lab_result", "42"): {"id": 42, "value": "9.4"}},
        )
        verifier = StreamingVerifier(
            citation_index=index,
            domain_checker=DomainConstraints(),
        )
        chunks = await _collect(
            verifier,
            "A1C is 9.41 [lab_result #42]. ",
        )
        assert len(chunks) == 1
        assert chunks[0].verified is False
        assert chunks[0].rejection_reason == "lab_value_outside_tolerance"

    async def test_mixed_pass_fail_in_one_stream(self) -> None:
        # The verifier should emit two chunks: first verified, second
        # rejected with the constraint-specific reason.
        index = CitationIndex(
            records={
                ("medication", "10"): {"id": 10, "name": "lisinopril 20mg"},
                ("problem", "5"): {"id": 5, "title": "CHF"},
            },
        )
        verifier = StreamingVerifier(
            citation_index=index,
            domain_checker=DomainConstraints(),
        )
        chunks = await _collect(
            verifier,
            "Patient on lisinopril 20mg [medication #10]. ",
            "Patient denies smoking [problem #5]. ",
        )
        assert len(chunks) == 2
        assert chunks[0].verified is True
        assert chunks[1].verified is False
        assert chunks[1].rejection_reason == "counterfactual_without_supporting_note"

    async def test_diagnosis_to_problem_passes(self) -> None:
        index = CitationIndex(
            records={("problem", "5"): {"id": 5, "title": "CHF"}},
        )
        verifier = StreamingVerifier(
            citation_index=index,
            domain_checker=DomainConstraints(),
        )
        chunks = await _collect(
            verifier,
            "Patient has CHF [problem #5]. ",
        )
        assert len(chunks) == 1
        assert chunks[0].verified is True
