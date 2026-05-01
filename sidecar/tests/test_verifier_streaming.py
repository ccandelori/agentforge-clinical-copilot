"""StreamingVerifier — sentence-buffered grounding check.

The verifier is the trust boundary between LLM output and the user's
screen. It buffers tokens until a claim boundary, then yields the claim
verbatim if its citation grounds in the per-turn cache, otherwise yields
a redaction marker. It never retries — see ARCHITECTURE.md S6.

The tests below exercise three layers:

  1. Boundary detection (the buffer flushes at sentence ends and
     paragraph breaks, not arbitrarily).
  2. Citation-cache grounding (a citation whose ID isn't in the per-turn
     index is rejected; one that is, passes through).
  3. Pluggable domain checks (Task 29 will plug a richer checker in via
     the DomainConstraintChecker protocol; we verify the protocol seam
     here so the contract is locked).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from agentforge.verifier import (
    Citation,
    DomainConstraintChecker,
    NullDomainConstraintChecker,
    StreamingVerifier,
    VerifiedChunk,
)
from agentforge.verifier.cache import CitationIndex

REJECTION_MARKER = "[claim withheld — could not be grounded]"


async def _stream(*tokens: str) -> AsyncIterator[str]:
    for t in tokens:
        yield t


async def _collect(verifier: StreamingVerifier, *tokens: str) -> list[VerifiedChunk]:
    return [chunk async for chunk in verifier.verify_stream(_stream(*tokens))]


def _index_with(*pairs: tuple[str, str]) -> CitationIndex:
    """Build a CitationIndex containing the given (record_type, id) keys.

    Bypasses the tool-result dispatch so tests can target the verifier
    in isolation; build_citation_index() is exercised separately in
    test_verifier_cache.py.
    """
    return CitationIndex(
        records={key: {"id": key[1]} for key in pairs},
    )


class TestSingleSentenceFlow:
    async def test_passes_through_grounded_sentence(self) -> None:
        index = _index_with(("encounter", "1"))
        verifier = StreamingVerifier(citation_index=index)
        chunks = await _collect(
            verifier,
            "Patient seen [encounter #1, 2026-04-12]. ",
        )
        assert len(chunks) == 1
        assert chunks[0].verified is True
        assert chunks[0].text == "Patient seen [encounter #1, 2026-04-12]. "
        assert chunks[0].rejection_reason is None

    async def test_rejects_sentence_with_unknown_citation(self) -> None:
        index = _index_with(("encounter", "1"))
        verifier = StreamingVerifier(citation_index=index)
        chunks = await _collect(
            verifier,
            "Patient seen [encounter #999, 2026-04-12]. ",
        )
        assert len(chunks) == 1
        assert chunks[0].verified is False
        assert chunks[0].text == REJECTION_MARKER
        assert chunks[0].rejection_reason == "citation_not_in_cache"

    async def test_rejects_sentence_with_no_citation(self) -> None:
        index = _index_with(("encounter", "1"))
        verifier = StreamingVerifier(citation_index=index)
        chunks = await _collect(
            verifier,
            "Patient is doing fine overall. ",
        )
        assert len(chunks) == 1
        assert chunks[0].verified is False
        assert chunks[0].rejection_reason == "no_citation"

    async def test_flushes_remainder_on_stream_end_without_terminator(self) -> None:
        # The model can drop the terminating period; we still flush at
        # stream end to avoid swallowing trailing claims.
        index = _index_with(("problem", "5"))
        verifier = StreamingVerifier(citation_index=index)
        chunks = await _collect(
            verifier,
            "Patient has chronic disease [problem #5]",
        )
        assert len(chunks) == 1
        assert chunks[0].verified is True


class TestTokenBufferingAcrossChunks:
    async def test_handles_token_split_across_citation(self) -> None:
        # The LLM stream chunks tokens however it wants; the verifier
        # must accumulate until a boundary, not flush per-token.
        index = _index_with(("medication", "10"))
        verifier = StreamingVerifier(citation_index=index)
        chunks = await _collect(
            verifier,
            "Patient on ", "lisino", "pril ",
            "[medication ", "#10, ", "started 2024]",
            ". ",
        )
        assert len(chunks) == 1
        assert chunks[0].verified is True
        assert "[medication #10, started 2024]" in chunks[0].text

    async def test_emits_two_chunks_for_two_sentences(self) -> None:
        index = _index_with(("encounter", "1"), ("problem", "5"))
        verifier = StreamingVerifier(citation_index=index)
        chunks = await _collect(
            verifier,
            "First [encounter #1, 2026-04-12]. ",
            "Second [problem #5]. ",
        )
        assert len(chunks) == 2
        assert all(c.verified for c in chunks)
        assert chunks[0].text.startswith("First")
        assert chunks[1].text.startswith("Second")

    async def test_mixes_verified_and_rejected_in_one_stream(self) -> None:
        index = _index_with(("encounter", "1"))
        verifier = StreamingVerifier(citation_index=index)
        chunks = await _collect(
            verifier,
            "Real claim [encounter #1, 2026-04-12]. ",
            "Fake claim [encounter #999, 2026-04-13]. ",
        )
        assert len(chunks) == 2
        assert chunks[0].verified is True
        assert chunks[1].verified is False
        assert chunks[1].text == REJECTION_MARKER


class TestParagraphBreaks:
    async def test_paragraph_break_flushes_buffer(self) -> None:
        # A paragraph break inside a stream is a hard boundary even if
        # no sentence-end punctuation was emitted.
        index = _index_with(("problem", "5"))
        verifier = StreamingVerifier(citation_index=index)
        chunks = await _collect(
            verifier,
            "Has chronic disease [problem #5]\n\n",
            "Next paragraph [problem #5]. ",
        )
        assert len(chunks) == 2
        assert all(c.verified for c in chunks)


class TestDomainCheckerSeam:
    async def test_passes_when_domain_checker_returns_true(self) -> None:
        index = _index_with(("medication", "10"))
        verifier = StreamingVerifier(
            citation_index=index,
            domain_checker=NullDomainConstraintChecker(),
        )
        chunks = await _collect(
            verifier, "Patient on lisinopril [medication #10]. "
        )
        assert chunks[0].verified is True

    async def test_rejects_when_domain_checker_returns_false(self) -> None:
        class _RejectAll:
            def check(
                self,
                citation: Citation,
                claim_text: str,
                record: dict | None,
            ) -> tuple[bool, str | None]:
                return False, "domain_constraint_violation"

        index = _index_with(("medication", "10"))
        verifier = StreamingVerifier(
            citation_index=index,
            domain_checker=_RejectAll(),
        )
        chunks = await _collect(
            verifier, "Patient on lisinopril [medication #10]. "
        )
        assert chunks[0].verified is False
        assert chunks[0].rejection_reason == "domain_constraint_violation"

    async def test_domain_checker_receives_record_from_index(self) -> None:
        seen: dict[str, object] = {}

        class _Capturing:
            def check(
                self,
                citation: Citation,
                claim_text: str,
                record: dict | None,
            ) -> tuple[bool, str | None]:
                seen["citation"] = citation
                seen["claim_text"] = claim_text
                seen["record"] = record
                return True, None

        index = CitationIndex(
            records={("medication", "10"): {"id": 10, "name": "lisinopril"}},
        )
        verifier = StreamingVerifier(
            citation_index=index, domain_checker=_Capturing()
        )
        await _collect(
            verifier, "Patient on lisinopril 20mg [medication #10]. "
        )
        assert isinstance(seen["citation"], Citation)
        assert seen["record"] == {"id": 10, "name": "lisinopril"}
        assert "lisinopril 20mg" in seen["claim_text"]  # type: ignore[operator]


class TestNoRetry:
    async def test_does_not_call_domain_checker_more_than_once_per_claim(
        self,
    ) -> None:
        # Locked policy: the verifier never retries. A failed check is
        # final. ARCHITECTURE.md S6: "retries are how a model fabricates
        # a justification."
        call_count = 0

        class _Counting:
            def check(
                self,
                citation: Citation,
                claim_text: str,
                record: dict | None,
            ) -> tuple[bool, str | None]:
                nonlocal call_count
                call_count += 1
                return False, "always_fails"

        index = _index_with(("medication", "10"))
        verifier = StreamingVerifier(
            citation_index=index, domain_checker=_Counting()
        )
        await _collect(
            verifier, "Patient on lisinopril [medication #10]. "
        )
        assert call_count == 1


class TestProtocolShape:
    def test_domain_constraint_checker_protocol_is_satisfied_by_null(self) -> None:
        # Structural typing: NullDomainConstraintChecker must satisfy the
        # DomainConstraintChecker protocol. If the protocol drifts, this
        # check fails fast at import time.
        checker: DomainConstraintChecker = NullDomainConstraintChecker()
        passed, reason = checker.check(
            Citation(record_type="x", record_id="1", extra=None, raw="[x #1]"),
            "claim",
            {"id": 1},
        )
        assert passed is True
        assert reason is None
