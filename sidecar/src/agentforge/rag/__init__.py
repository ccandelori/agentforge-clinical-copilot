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
  * :class:`DenseRetriever` — cosine-sim retrieval over a
    :class:`Encoder`-pre-embedded corpus (default
    ``all-MiniLM-L6-v2``).
  * :class:`RRFMerger` — reciprocal-rank fusion across two ranked
    lists, with deduplication by chunk_id.
  * :class:`Reranker` Protocol + three implementations:
    :class:`PassthroughReranker` (no-op default),
    :class:`CrossEncoderReranker` (over ``bge-reranker-base``),
    :class:`CohereReranker` (opt-in via ``COHERE_API_KEY``).
  * :class:`EvidenceRetriever` — composes the above into a single
    ``retrieve(query, top_k)`` pipeline that the orchestrator's
    evidence-retriever LangGraph node (Task 15) calls.
"""

from __future__ import annotations

from agentforge.rag.bm25 import BM25Retriever
from agentforge.rag.cohere_rerank import CohereReranker
from agentforge.rag.cross_encoder import (
    CrossEncoder,
    CrossEncoderReranker,
    SentenceTransformerCrossEncoder,
)
from agentforge.rag.dense import DenseRetriever, Encoder, SentenceTransformerEncoder
from agentforge.rag.evidence_retriever import EvidenceRetriever, RetrievalConfig
from agentforge.rag.loader import load_corpus
from agentforge.rag.reranker import PassthroughReranker, Reranker
from agentforge.rag.rrf import RRFMerger
from agentforge.rag.types import GuidelineChunk, RetrievalResult

__all__ = [
    "BM25Retriever",
    "CohereReranker",
    "CrossEncoder",
    "CrossEncoderReranker",
    "DenseRetriever",
    "Encoder",
    "EvidenceRetriever",
    "GuidelineChunk",
    "PassthroughReranker",
    "RRFMerger",
    "Reranker",
    "RetrievalConfig",
    "RetrievalResult",
    "SentenceTransformerCrossEncoder",
    "SentenceTransformerEncoder",
    "load_corpus",
]
