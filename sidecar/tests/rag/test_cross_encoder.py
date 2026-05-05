"""Tests for :class:`CrossEncoderReranker`.

Uses a deterministic stub cross-encoder so tests are fast and
offline. The real ``bge-reranker-base`` integration is exercised
end-to-end by the demo script (``retrieval_demo.py``), not here.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from agentforge.rag.cross_encoder import CrossEncoderReranker
from agentforge.rag.types import GuidelineChunk, RetrievalResult


def _chunk(chunk_id: str, text: str) -> GuidelineChunk:
    return GuidelineChunk.from_index_entry(
        doc_id="d", section="s", version="v",
        chunk_id=chunk_id, text=text, token_count=1, source_path="p",
    )


def _result(chunk_id: str, text: str, score: float = 0.0) -> RetrievalResult:
    return RetrievalResult(chunk=_chunk(chunk_id, text), score=score)


class _StubCrossEncoder:
    """Cross-encoder that returns predetermined scores per (q, t) pair.

    Construction takes a {(query, text) → score} dict; predict()
    returns the matching scores. This makes the rerank ordering
    deterministic across PyTorch / transformers versions.
    """

    def __init__(self, table: dict[tuple[str, str], float]) -> None:
        self._table = table
        self.calls: list[Sequence[tuple[str, str]]] = []

    def predict(self, pairs: Sequence[tuple[str, str]]) -> Sequence[float]:
        self.calls.append(list(pairs))
        scores: list[float] = []
        for query, text in pairs:
            if (query, text) not in self._table:
                raise KeyError(f"stub cross-encoder has no score for ({query!r}, {text!r})")
            scores.append(self._table[(query, text)])
        return scores


async def test_reranker_reorders_candidates_by_predicted_score() -> None:
    candidates = [
        _result("a", "alpha", score=0.1),
        _result("b", "beta",  score=0.5),
        _result("c", "gamma", score=0.9),
    ]
    stub = _StubCrossEncoder({
        ("query", "alpha"): 0.99,
        ("query", "beta"):  0.10,
        ("query", "gamma"): 0.50,
    })
    reranker = CrossEncoderReranker(stub)

    out = await reranker.rerank("query", candidates, top_k=3)
    assert [r.chunk.chunk_id for r in out] == ["a", "c", "b"]


async def test_reranker_truncates_to_top_k() -> None:
    candidates = [_result(f"c{i}", f"text{i}") for i in range(5)]
    stub = _StubCrossEncoder({
        ("q", f"text{i}"): float(5 - i)  # text0 highest, text4 lowest
        for i in range(5)
    })
    reranker = CrossEncoderReranker(stub)

    out = await reranker.rerank("q", candidates, top_k=2)
    assert [r.chunk.chunk_id for r in out] == ["c0", "c1"]


async def test_reranker_replaces_input_score_with_cross_encoder_score() -> None:
    """The output score is the cross-encoder logit, not the input
    BM25/RRF score. This locks the score-space invariant: the
    reranker is the boundary between merged scores and rerank
    relevance."""
    candidates = [_result("a", "alpha", score=99999.0)]
    stub = _StubCrossEncoder({("q", "alpha"): 0.42})
    reranker = CrossEncoderReranker(stub)

    out = await reranker.rerank("q", candidates, top_k=1)
    assert out[0].score == pytest.approx(0.42)


async def test_reranker_handles_empty_candidates() -> None:
    reranker = CrossEncoderReranker(_StubCrossEncoder({}))
    out = await reranker.rerank("q", [], top_k=5)
    assert out == []


async def test_reranker_rejects_non_positive_top_k() -> None:
    reranker = CrossEncoderReranker(_StubCrossEncoder({}))
    with pytest.raises(ValueError, match="top_k"):
        await reranker.rerank("q", [_result("a", "alpha")], top_k=0)


async def test_reranker_pairs_query_with_each_candidate_text() -> None:
    """The cross-encoder must see (query, text) for every candidate —
    not (query, chunk_id) or (text, query). This guards the join
    direction."""
    candidates = [
        _result("c1", "first text"),
        _result("c2", "second text"),
    ]
    stub = _StubCrossEncoder({
        ("my query", "first text"): 0.5,
        ("my query", "second text"): 0.7,
    })
    reranker = CrossEncoderReranker(stub)

    await reranker.rerank("my query", candidates, top_k=2)

    assert len(stub.calls) == 1
    call = stub.calls[0]
    assert list(call) == [("my query", "first text"), ("my query", "second text")]
