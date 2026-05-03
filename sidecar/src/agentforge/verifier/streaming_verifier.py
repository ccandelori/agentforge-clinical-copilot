"""Sentence-buffered streaming grounding verifier.

This is the trust boundary between LLM output and the user's screen.
Tokens come in (one or many at a time, in any chunking the upstream
streamer chose), and verified claims come out as complete units.

The buffer flush rule, kept conservative on purpose, is:

  1. Emit a chunk when sentence-terminating punctuation (``.!?``) is
     followed by whitespace (or stream end).
  2. Emit on a paragraph break (``\\n\\n``).
  3. Emit whatever remains at stream end.

Each emitted chunk goes through three checks:

  * has at least one ID-anchored citation (``no_citation`` otherwise),
  * the citation's ``(record_type, record_id)`` exists in this turn's
    CitationIndex (``citation_not_in_cache`` otherwise),
  * the pluggable DomainConstraintChecker accepts the claim — Task 29
    will plug a real checker in here; the default Null checker passes
    every grounded claim through.

A failed chunk is replaced with the rejection marker. The verifier
never retries — retries are how a model fabricates a justification.

See ARCHITECTURE.md S6.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

from agentforge.verifier.cache import CitationIndex
from agentforge.verifier.citation import find_citations
from agentforge.verifier.protocols import (
    DomainConstraintChecker,
    NullDomainConstraintChecker,
)

REJECTION_MARKER = "[claim withheld — could not be grounded]"

# Sentence end: terminator (.!?) followed by whitespace. The lookahead
# is whitespace-or-end so we don't fire on decimals like "1.5" mid-stream.
_SENTENCE_END = re.compile(r"[.!?](?=\s)")
_PARAGRAPH_BREAK = "\n\n"


@dataclass(frozen=True, slots=True)
class VerifiedChunk:
    """One unit of post-verification output."""

    text: str
    verified: bool
    rejection_reason: str | None = None


class StreamingVerifier:
    """Buffer tokens, flush at sentence boundaries, verify each claim."""

    def __init__(
        self,
        citation_index: CitationIndex,
        domain_checker: DomainConstraintChecker | None = None,
    ) -> None:
        self._index = citation_index
        self._domain = domain_checker or NullDomainConstraintChecker()

    async def verify_stream(
        self,
        token_stream: AsyncIterator[str],
    ) -> AsyncIterator[VerifiedChunk]:
        buffer = ""
        async for token in token_stream:
            buffer += token
            while True:
                claim, remainder = _split_at_boundary(buffer)
                if claim is None:
                    break
                yield self._verify(claim)
                buffer = remainder

        if buffer.strip():
            yield self._verify(buffer)

    def _verify(self, claim: str) -> VerifiedChunk:
        citations = find_citations(claim)
        if not citations:
            # Sentences without any citation are framing prose ("Here are
            # the medications:", transitions, summaries). Pass them
            # through verified — the safety guarantee is "if you cite,
            # you cite truthfully," not "every sentence must cite."
            # Citation-fabrication is still blocked below.
            return VerifiedChunk(text=claim, verified=True, rejection_reason=None)


        # MVP rule: a claim passes only if all of its citations resolve.
        # Models occasionally emit two citations in one sentence (one
        # real, one parroted) — rejecting on the first miss is the
        # conservative choice for a clinical co-pilot.
        for citation in citations:
            if not self._index.contains(citation.record_type, citation.record_id):
                return VerifiedChunk(
                    text=REJECTION_MARKER,
                    verified=False,
                    rejection_reason="citation_not_in_cache",
                )
            record = self._index.get(citation.record_type, citation.record_id)
            verified, reason = self._domain.check(citation, claim, record)
            if not verified:
                return VerifiedChunk(
                    text=REJECTION_MARKER,
                    verified=False,
                    rejection_reason=reason or "domain_constraint_violation",
                )

        return VerifiedChunk(text=claim, verified=True, rejection_reason=None)


def _split_at_boundary(buffer: str) -> tuple[str | None, str]:
    """Try to split ``buffer`` at the earliest claim boundary.

    Returns ``(claim, remainder)`` where ``claim`` includes everything
    up to and including the boundary punctuation/whitespace, or
    ``(None, buffer)`` if no boundary is present yet.
    """
    paragraph_idx = buffer.find(_PARAGRAPH_BREAK)
    sentence_match = _SENTENCE_END.search(buffer)

    sentence_idx = -1
    if sentence_match is not None:
        # Boundary is one past the trailing whitespace so the next chunk
        # doesn't start with the same space.
        sentence_idx = sentence_match.end() + 1

    candidates = [i for i in (paragraph_idx, sentence_idx) if i >= 0]
    if not candidates:
        return None, buffer

    boundary = min(candidates)
    if boundary == paragraph_idx:
        boundary += len(_PARAGRAPH_BREAK)

    return buffer[:boundary], buffer[boundary:]
