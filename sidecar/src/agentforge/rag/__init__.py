"""Hybrid RAG pipeline (W2 Task 9).

Public surface for the retrieval-augmented-generation components used
by the evidence-retriever LangGraph node (Task 15). The pipeline
shape is BM25 + dense → RRF merge → rerank, exposed as a
:class:`Retriever` protocol so the orchestrator stays decoupled from
the underlying implementations.

Currently shipped:

  * :class:`GuidelineChunk`, :class:`RetrievalResult` — load-bearing
    data shapes; every clinical claim downstream cites a chunk via
    :class:`agentforge.schemas.citation.Citation`.
  * :class:`BM25Retriever` — Robertson/Sparck Jones BM25 over a fixed
    corpus, returning ranked chunks with citations attached.
  * :class:`RRFMerger` — reciprocal-rank fusion across two ranked
    lists, with deduplication by chunk_id.
  * :class:`Reranker` Protocol + :class:`PassthroughReranker` — the
    abstract reordering surface plus a no-op default. The real
    cross-encoder + Cohere implementations land alongside the
    sentence-transformers / Cohere SDK dependencies in a follow-up
    MR (their ML weights add ~500 MB to the sidecar image, so they
    sit behind an optional-extra install).
"""

from __future__ import annotations

from agentforge.rag.bm25 import BM25Retriever
from agentforge.rag.reranker import PassthroughReranker, Reranker
from agentforge.rag.rrf import RRFMerger
from agentforge.rag.types import GuidelineChunk, RetrievalResult

__all__ = [
    "BM25Retriever",
    "GuidelineChunk",
    "PassthroughReranker",
    "RRFMerger",
    "Reranker",
    "RetrievalResult",
]
