"""Data shapes for the hybrid RAG pipeline.

A :class:`GuidelineChunk` is the persisted unit the retriever emits.
Every chunk carries a structured :class:`Citation` so downstream
clinical claims can be traced back to a specific guideline document
+ section without re-scanning the corpus.

A :class:`RetrievalResult` is what a retriever returns: a chunk plus
its score (BM25, cosine sim, or reranker logit, depending on which
component produced it). The score's domain is component-specific —
callers should treat it as ordinal, not a stable cross-component
signal. The merger (:class:`agentforge.rag.rrf.RRFMerger`) is the
single boundary that translates between component score-spaces via
reciprocal-rank fusion.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentforge.schemas.citation import Citation, SourceType


@dataclass(frozen=True)
class GuidelineChunk:
    """One chunk of a guideline document, ready for retrieval/citation.

    ``chunk_id`` is the stable handle the citation system uses to
    reference this chunk after retrieval. It must be unique within a
    given document (the chunker numbers from 0 within a doc).

    ``text`` is the body the retriever scores against. Keep it small
    enough that the LLM can read multiple chunks in a single context
    window — the corpus chunker (Task 10's ``chunk_guidelines.py``)
    targets ~500 tokens per chunk by default.

    ``citation`` is pre-built at chunking time so retrievers don't
    have to re-derive it. Source-type is always ``GUIDELINE`` and
    ``page_bbox`` is None — guideline citations identify the chunk by
    id, not a scanned-source bounding box.
    """

    doc_id: str
    section: str
    version: str
    chunk_id: str
    text: str
    token_count: int
    source_path: str
    citation: Citation

    @classmethod
    def from_index_entry(
        cls,
        *,
        doc_id: str,
        section: str,
        version: str,
        chunk_id: str,
        text: str,
        token_count: int,
        source_path: str,
    ) -> GuidelineChunk:
        """Build a chunk + its Citation from a flat index entry.

        Pulled out into a constructor so the chunker (Task 10) and the
        retrieval-time loader can both produce identical citation
        shapes — drift between the two would mean a click-to-source
        on a retrieved chunk lands on a different anchor than its
        index-time identity.
        """
        citation = Citation(
            source_type=SourceType.GUIDELINE,
            source_id=doc_id,
            page_or_section=section,
            field_or_chunk_id=chunk_id,
            # Keep the literal-quote slot as the chunk's full text;
            # rendering layers can shorten for display. Retaining the
            # full body here preserves the audit-trail invariant —
            # what the retriever surfaces is what the citation refers
            # to, byte-for-byte.
            quote_or_value=text,
            page_bbox=None,
        )
        return cls(
            doc_id=doc_id,
            section=section,
            version=version,
            chunk_id=chunk_id,
            text=text,
            token_count=token_count,
            source_path=source_path,
            citation=citation,
        )


@dataclass(frozen=True)
class RetrievalResult:
    """One ranked retrieval hit: the chunk plus its component-specific score.

    The score's units depend on the producer (BM25 saturation score,
    cosine similarity, reranker logit). Treat it as an ordinal
    quantity — ranks compare across components via RRF, raw scores do
    not.
    """

    chunk: GuidelineChunk
    score: float
