"""Tests for :class:`DenseRetriever`.

The retriever's behavior is verified against a deterministic stub
encoder so tests stay fast (~0.01s) and offline (no model download).
A separate slow-test marker can run the SentenceTransformerEncoder
end-to-end if needed; this file targets the retriever logic, not
the embedding model.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pytest
from numpy.typing import NDArray

from agentforge.rag.dense import DenseRetriever
from agentforge.rag.types import GuidelineChunk


def _chunk(chunk_id: str, text: str = "body") -> GuidelineChunk:
    return GuidelineChunk.from_index_entry(
        doc_id="d", section="s", version="v",
        chunk_id=chunk_id, text=text, token_count=1, source_path="p",
    )


class _StubEncoder:
    """Encoder that maps each input string to a fixed unit vector.

    Construction takes a {text → vector} dict; encode() returns the
    matching rows. Used to make cosine-sim outcomes deterministic
    independent of the real model's tokenization quirks.
    """

    def __init__(self, table: dict[str, list[float]]) -> None:
        self._table = {k: np.array(v, dtype=np.float32) for k, v in table.items()}
        for v in self._table.values():
            norm = float(np.linalg.norm(v))
            if abs(norm - 1.0) > 1e-4:
                raise ValueError(
                    f"Stub encoder vectors must be L2-normalized; got norm={norm}"
                )

    def encode(self, texts: Sequence[str]) -> NDArray[np.float32]:
        if not texts:
            dim = next(iter(self._table.values())).shape[0]
            return np.zeros((0, dim), dtype=np.float32)
        rows = []
        for t in texts:
            if t not in self._table:
                raise KeyError(f"stub encoder has no vector for {t!r}")
            rows.append(self._table[t])
        return np.vstack(rows)


def test_retriever_handles_empty_corpus() -> None:
    encoder = _StubEncoder({"q": [1.0, 0.0]})
    retriever = DenseRetriever([], encoder=encoder)
    assert retriever.corpus_size == 0
    assert retriever.top_k("q", k=5) == []


def test_retriever_returns_highest_cosine_first() -> None:
    """Build a corpus where 'cardio' aligns with the query and the
    others are orthogonal. Top hit must be 'cardio'."""
    chunks = [
        _chunk("cardio", "ASCVD risk statin"),
        _chunk("ortho", "knee replacement"),
        _chunk("derm", "psoriasis treatment"),
    ]
    encoder = _StubEncoder({
        "ASCVD risk statin":     [1.0, 0.0, 0.0],
        "knee replacement":      [0.0, 1.0, 0.0],
        "psoriasis treatment":   [0.0, 0.0, 1.0],
        "ASCVD treatment":       [1.0, 0.0, 0.0],
    })
    retriever = DenseRetriever(chunks, encoder=encoder)
    results = retriever.top_k("ASCVD treatment", k=3)
    assert [r.chunk.chunk_id for r in results] == ["cardio", "ortho", "derm"]
    # First result is a perfect cosine match (= 1.0).
    assert results[0].score == pytest.approx(1.0)


def test_retriever_top_k_truncates_to_k() -> None:
    chunks = [_chunk(f"c{i}", f"text{i}") for i in range(5)]
    encoder = _StubEncoder({
        **{f"text{i}": [1.0, 0.0, 0.0] for i in range(5)},
        "query": [1.0, 0.0, 0.0],
    })
    retriever = DenseRetriever(chunks, encoder=encoder)
    results = retriever.top_k("query", k=2)
    assert len(results) == 2


def test_retriever_returns_all_when_k_exceeds_corpus_size() -> None:
    chunks = [_chunk("a", "aaa"), _chunk("b", "bbb")]
    encoder = _StubEncoder({
        "aaa": [1.0, 0.0],
        "bbb": [0.0, 1.0],
        "q":   [0.7071068, 0.7071068],
    })
    retriever = DenseRetriever(chunks, encoder=encoder)
    results = retriever.top_k("q", k=10)
    assert len(results) == 2


def test_retriever_does_not_filter_zero_score_hits() -> None:
    """Unlike BM25, dense retrieval has no "zero = no relevance"
    floor. Even an orthogonal candidate stays in the result list —
    callers wanting a relevance threshold should post-filter."""
    chunks = [
        _chunk("aligned", "aligned text"),
        _chunk("orthogonal", "orthogonal text"),
    ]
    encoder = _StubEncoder({
        "aligned text":     [1.0, 0.0],
        "orthogonal text":  [0.0, 1.0],
        "query":            [1.0, 0.0],
    })
    retriever = DenseRetriever(chunks, encoder=encoder)
    results = retriever.top_k("query", k=2)
    assert len(results) == 2
    assert results[0].chunk.chunk_id == "aligned"
    assert results[0].score == pytest.approx(1.0)
    assert results[1].chunk.chunk_id == "orthogonal"
    # Orthogonal cosine = 0; the result still surfaces.
    assert results[1].score == pytest.approx(0.0, abs=1e-6)


def test_retriever_rejects_non_positive_k() -> None:
    chunks = [_chunk("a", "aaa")]
    encoder = _StubEncoder({"aaa": [1.0, 0.0]})
    retriever = DenseRetriever(chunks, encoder=encoder)
    with pytest.raises(ValueError, match="positive"):
        retriever.top_k("anything", k=0)


def test_retriever_rejects_encoder_with_mismatched_output_count() -> None:
    """If the encoder violates the contract by returning fewer/more
    vectors than inputs, we surface a clear error at construction
    rather than silently mis-aligning chunks to embeddings."""

    class _BrokenEncoder:
        def encode(self, texts: Sequence[str]) -> NDArray[np.float32]:
            # Return one fewer vector than requested.
            return np.zeros((max(len(texts) - 1, 0), 2), dtype=np.float32)

    chunks = [_chunk("a", "aaa"), _chunk("b", "bbb")]
    with pytest.raises(RuntimeError, match="encoder contract"):
        DenseRetriever(chunks, encoder=_BrokenEncoder())


def test_results_carry_citations() -> None:
    chunks = [_chunk("c1", "ASCVD statin")]
    encoder = _StubEncoder({"ASCVD statin": [1.0, 0.0], "ASCVD": [1.0, 0.0]})
    retriever = DenseRetriever(chunks, encoder=encoder)
    results = retriever.top_k("ASCVD", k=1)
    assert results[0].chunk.citation.field_or_chunk_id == "c1"
