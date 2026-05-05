"""Cross-encoder reranker over ``bge-reranker-base``.

A cross-encoder scores ``(query, candidate_text)`` pairs jointly,
which is more accurate than the bi-encoder cosine score the
:class:`DenseRetriever` produces — at the cost of running the model
once per candidate. Use as the precision-boost layer after RRF has
narrowed the candidate set to ~top-20.

The model is injected via the :class:`CrossEncoder` Protocol so tests
can supply a deterministic stub. The protocol shape is intentionally
minimal: ``predict(pairs) -> list[float]`` mirrors
``sentence_transformers.CrossEncoder.predict``'s return type while
avoiding a hard dependency on the SDK type at the seam.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from agentforge.rag.types import RetrievalResult

# bge-reranker-base is a 110M-param model with strong scores on the
# BEIR benchmark — overkill for the W2 corpus but cheap enough to
# run on CPU and the de-facto choice for general-purpose reranking.
DEFAULT_CROSS_ENCODER_MODEL = "BAAI/bge-reranker-base"


@runtime_checkable
class CrossEncoder(Protocol):
    """Minimal cross-encoder surface for reranking.

    The contract: ``predict(pairs)`` accepts a sequence of
    (query, candidate_text) tuples and returns a parallel sequence
    of float relevance scores. Higher = more relevant. The score's
    domain is model-specific; treat it as ordinal, not absolute.
    """

    def predict(self, pairs: Sequence[tuple[str, str]]) -> Sequence[float]: ...


class SentenceTransformerCrossEncoder:
    """Concrete :class:`CrossEncoder` wrapping
    ``sentence_transformers.CrossEncoder``.

    Lazy-imports the SDK so a sidecar that uses only the
    :class:`PassthroughReranker` doesn't pay the
    sentence-transformers import time.
    """

    def __init__(self, model_name: str = DEFAULT_CROSS_ENCODER_MODEL) -> None:
        from sentence_transformers import CrossEncoder as _CrossEncoder

        self._model = _CrossEncoder(model_name)
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    def predict(self, pairs: Sequence[tuple[str, str]]) -> Sequence[float]:
        if not pairs:
            return []
        # The SDK accepts list[list[str]] or list[tuple[str, str]];
        # we materialize tuples → lists to avoid a type-stub mismatch.
        scores = self._model.predict([list(p) for p in pairs])
        return [float(s) for s in scores]


class CrossEncoderReranker:
    """Async reranker that scores (query, candidate.text) pairs with a
    cross-encoder.

    The cross-encoder model is CPU-bound; we run ``predict`` in a
    thread via :func:`asyncio.to_thread` so the event loop stays
    responsive to other concurrent retrievals (e.g. the BM25 +
    dense pre-filter that runs in parallel for the same query).

    Construction is cheap (just stores the model reference); the heavy
    lifting is in ``rerank``. Reuse a single instance across queries.
    """

    def __init__(self, cross_encoder: CrossEncoder) -> None:
        self._cross_encoder = cross_encoder

    async def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        top_k: int,
    ) -> list[RetrievalResult]:
        if top_k <= 0:
            raise ValueError(f"top_k must be positive; got {top_k}")
        if not candidates:
            return []

        pairs = [(query, c.chunk.text) for c in candidates]
        scores = await asyncio.to_thread(self._cross_encoder.predict, pairs)

        if len(scores) != len(candidates):
            raise RuntimeError(
                f"CrossEncoder returned {len(scores)} scores for "
                f"{len(candidates)} candidates; contract violated"
            )

        # Re-emit with the cross-encoder score replacing the input
        # (BM25/RRF) score. The score here is the rerank logit; the
        # synthesizer treats it as an ordinal relevance signal.
        rescored = [
            RetrievalResult(chunk=cand.chunk, score=float(score))
            for cand, score in zip(candidates, scores, strict=True)
        ]
        rescored.sort(key=lambda r: r.score, reverse=True)
        return rescored[:top_k]
