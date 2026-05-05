"""Reranker Protocol + the no-op default implementation.

After RRF merges BM25 and dense retrieval into a unified candidate
list, an optional reranker reorders the top-N for higher precision
before the synthesizer reads them. The reranker shape is:

    async def rerank(query, candidates, top_k) -> list[RetrievalResult]

Currently shipped:

  * :class:`Reranker` Protocol — what the orchestrator depends on.
  * :class:`PassthroughReranker` — returns the input candidates'
    top-``top_k`` slice unchanged. Used in tests, in the no-API-key
    fallback path, and as the orchestrator's default for tasks where
    rerank quality doesn't justify the latency.

Heavy implementations (``CrossEncoderReranker`` over
``bge-reranker-base``, ``CohereReranker`` over the Cohere SDK) land
in a follow-up MR alongside the ``sentence-transformers`` /
``cohere`` extras that they depend on. Their addition won't change
the Protocol — that's the whole point of fixing this surface now.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from agentforge.rag.types import RetrievalResult


@runtime_checkable
class Reranker(Protocol):
    """Reorder retrieval candidates by relevance to a query.

    The contract:

    * Always returns at most ``top_k`` results.
    * Never returns a chunk that wasn't in ``candidates`` (no
      retrieval-side hallucination of new chunks).
    * Output ordering is descending by reranker-specific relevance.
    * Implementations may add structural metadata to scores but
      callers must not assume score domains across implementations.

    Async because production rerankers (Cohere, hosted cross-encoders)
    are remote calls. Local CPU-bound rerankers can ``await
    asyncio.to_thread(...)`` to satisfy the contract without blocking.
    """

    async def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        top_k: int,
    ) -> list[RetrievalResult]: ...


class PassthroughReranker:
    """No-op :class:`Reranker` — preserves the input order, truncates to ``top_k``.

    Used as the orchestrator's default when no reranker is configured,
    and in unit tests for retrieval components where the reranking
    layer isn't the system under test. Also the right choice on the
    no-API-key fallback path: a reranker that would have been
    Cohere-backed gracefully degrades to "use the RRF merge as-is"
    without adding latency to a request that has no rerank budget
    anyway.
    """

    async def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        top_k: int,
    ) -> list[RetrievalResult]:
        del query  # unused — passthrough is order-preserving
        if top_k <= 0:
            raise ValueError(f"top_k must be positive; got {top_k}")
        return list(candidates[:top_k])
