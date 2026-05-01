"""Streaming claim-by-claim grounding verifier.

The verifier is the trust boundary between LLM output and the user's
screen: tokens come in, sentence-buffered chunks come out, each one
either passed through verbatim or replaced with a redaction marker.

See ARCHITECTURE.md S6 for the design rationale and the five domain
constraints that ride on top of the structural check implemented here.
"""

from agentforge.verifier.cache import (
    CitationIndex,
    CitationKey,
    build_citation_index,
)
from agentforge.verifier.citation import (
    CITATION_PATTERN,
    Citation,
    find_citations,
    parse_citation,
)
from agentforge.verifier.constraints import DomainConstraints
from agentforge.verifier.protocols import (
    DomainConstraintChecker,
    NullDomainConstraintChecker,
)
from agentforge.verifier.streaming_verifier import (
    REJECTION_MARKER,
    StreamingVerifier,
    VerifiedChunk,
)

__all__ = [
    "CITATION_PATTERN",
    "REJECTION_MARKER",
    "Citation",
    "CitationIndex",
    "CitationKey",
    "DomainConstraintChecker",
    "DomainConstraints",
    "NullDomainConstraintChecker",
    "StreamingVerifier",
    "VerifiedChunk",
    "build_citation_index",
    "find_citations",
    "parse_citation",
]
