"""Dense retrieval using a sentence-transformers encoder.

The retriever pre-encodes the corpus at construction time and answers
queries via cosine similarity. Cosine sim is computed on
L2-normalized vectors (so the dot product equals cosine sim) and
top-``k`` is selected with ``np.argpartition`` — O(N) instead of
O(N log N) for the corpus sizes we expect (~30 chunks at MVP, ~hundreds
in production).

The encoder is injected via the :class:`Encoder` Protocol so tests
can substitute a deterministic stub. Production wires in a
:class:`SentenceTransformerEncoder` over the
``all-MiniLM-L6-v2`` model (384-dim vectors, fast on CPU).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray

from agentforge.rag.types import GuidelineChunk, RetrievalResult

# Default model published by sentence-transformers; 384-dim, ~80 MB,
# good baseline for short technical text. Embeddings are
# L2-normalized by the model itself when ``normalize_embeddings=True``
# is passed at encode time.
DEFAULT_DENSE_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


@runtime_checkable
class Encoder(Protocol):
    """Encode a batch of strings into an ``(N, dim)`` float matrix.

    The contract is **L2-normalized rows** so the retriever can use
    matrix-vector dot product as cosine similarity without an explicit
    normalize step. Implementations that produce un-normalized vectors
    should normalize before returning.
    """

    def encode(self, texts: Sequence[str]) -> NDArray[np.float32]: ...


class SentenceTransformerEncoder:
    """Concrete :class:`Encoder` wrapping
    ``sentence_transformers.SentenceTransformer``.

    The model is loaded eagerly at construction so the
    cold-start cost (model download + tokenizer warm-up) is paid once
    per process. Subsequent ``encode()`` calls are fast (~10ms for a
    single short query on CPU).

    Lazy-imports the SDK so a sidecar process that never builds a
    DenseRetriever (e.g. one configured with no RAG, or one running
    under tests with a stub encoder) doesn't pay the
    sentence-transformers import time at startup.
    """

    def __init__(self, model_name: str = DEFAULT_DENSE_MODEL) -> None:
        # Lazy import — see class docstring rationale.
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    def encode(self, texts: Sequence[str]) -> NDArray[np.float32]:
        if not texts:
            # SentenceTransformer.encode raises on empty input; we
            # handle the edge here so callers don't have to special-case.
            return np.zeros((0, self._model.get_sentence_embedding_dimension()), dtype=np.float32)
        # Materialize Sequence -> list because the SDK's type stubs
        # require a list; harmless on common inputs.
        out = self._model.encode(
            list(texts),
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        # ``astype`` returns the same array type but the SDK's stubs
        # widen to ``Any``; narrow back at the seam.
        narrowed: NDArray[np.float32] = out.astype(np.float32, copy=False)
        return narrowed


class DenseRetriever:
    """Cosine-similarity retrieval over an encoder-pre-embedded corpus.

    Construction is O(N) encoder calls (the heavy step). Each
    ``top_k`` is O(N + k log k) — argpartition + a small partial
    sort over the partitioned head.
    """

    def __init__(
        self,
        chunks: Sequence[GuidelineChunk],
        *,
        encoder: Encoder,
    ) -> None:
        self._chunks: tuple[GuidelineChunk, ...] = tuple(chunks)
        self._encoder = encoder

        if self._chunks:
            corpus_texts = [c.text for c in self._chunks]
            self._embeddings = self._encoder.encode(corpus_texts)
            if self._embeddings.shape[0] != len(self._chunks):
                raise RuntimeError(
                    f"Encoder returned {self._embeddings.shape[0]} vectors "
                    f"for {len(self._chunks)} chunks; encoder contract violated"
                )
        else:
            # Defer dim discovery until we have something to encode.
            self._embeddings = np.zeros((0, 0), dtype=np.float32)

    @property
    def corpus_size(self) -> int:
        return len(self._chunks)

    def top_k(self, query: str, k: int) -> list[RetrievalResult]:
        """Return the top-``k`` chunks by cosine similarity to ``query``.

        ``k`` is a hard ceiling. Unlike :class:`BM25Retriever`, dense
        retrieval doesn't have a "zero score = no overlap" floor —
        cosine sim against a random query produces non-zero values for
        every doc. The retriever still returns ``k`` hits even on a
        nonsensical query; callers wanting a relevance threshold should
        post-filter on the score.
        """
        if k <= 0:
            raise ValueError(f"k must be positive; got {k}")
        if not self._chunks:
            return []

        query_vec = self._encoder.encode([query])
        if query_vec.shape[0] != 1:
            raise RuntimeError(
                f"Encoder returned {query_vec.shape[0]} vectors for one query"
            )

        # Cosine similarity = dot product on L2-normalized vectors.
        # corpus is (N, dim), query is (1, dim) → scores is (N,).
        scores = self._embeddings @ query_vec[0]

        # argpartition is O(N); the resulting top-k slice is then
        # sorted descending in O(k log k).
        n = scores.shape[0]
        top_k_count = min(k, n)
        if top_k_count == n:
            ordered = np.argsort(-scores)
        else:
            partitioned = np.argpartition(-scores, top_k_count)[:top_k_count]
            ordered = partitioned[np.argsort(-scores[partitioned])]

        return [
            RetrievalResult(chunk=self._chunks[int(i)], score=float(scores[int(i)]))
            for i in ordered
        ]
