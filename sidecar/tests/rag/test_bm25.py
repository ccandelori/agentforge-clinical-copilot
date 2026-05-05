"""Tests for :class:`BM25Retriever`.

Covers tokenization, the score math (relative ordering, not absolute
values — BM25 magnitudes depend on corpus statistics), and the
top-k truncation rules including the "drop zero-score hits" invariant.
"""

from __future__ import annotations

import pytest

from agentforge.rag.bm25 import BM25Retriever, tokenize
from agentforge.rag.types import GuidelineChunk


def _chunk(*, chunk_id: str, text: str, section: str = "test") -> GuidelineChunk:
    return GuidelineChunk.from_index_entry(
        doc_id="test_doc",
        section=section,
        version="2024-01",
        chunk_id=chunk_id,
        text=text,
        token_count=len(text.split()),
        source_path="test.md",
    )


# ---------------------------------------------------------------------------
# tokenize
# ---------------------------------------------------------------------------


def test_tokenize_splits_on_word_boundaries_and_lowercases() -> None:
    assert tokenize("Adults With Diabetes") == ["adults", "diabetes"]


def test_tokenize_drops_stopwords() -> None:
    assert tokenize("the patient is on metformin") == ["patient", "metformin"]


def test_tokenize_keeps_alphanumerics_after_alpha_start() -> None:
    """Words like A1c / SGLT2 are clinical terms — must survive."""
    tokens = tokenize("A1c <7.0% on SGLT2 inhibitor")
    assert "a1c" in tokens
    assert "sglt2" in tokens


def test_tokenize_returns_empty_for_blank_input() -> None:
    assert tokenize("") == []
    assert tokenize("   ") == []


# ---------------------------------------------------------------------------
# BM25Retriever construction
# ---------------------------------------------------------------------------


def test_retriever_rejects_invalid_hyperparameters() -> None:
    chunks = [_chunk(chunk_id="c1", text="foo bar")]
    with pytest.raises(ValueError, match="hyperparameters"):
        BM25Retriever(chunks, k1=-1.0)
    with pytest.raises(ValueError, match="hyperparameters"):
        BM25Retriever(chunks, b=1.5)


def test_retriever_handles_empty_corpus() -> None:
    retriever = BM25Retriever([])
    assert retriever.corpus_size == 0
    assert retriever.top_k("anything", k=5) == []


def test_corpus_size_reflects_input_count() -> None:
    chunks = [
        _chunk(chunk_id=f"c{i}", text=f"chunk {i} body")
        for i in range(7)
    ]
    assert BM25Retriever(chunks).corpus_size == 7


# ---------------------------------------------------------------------------
# Scoring + ranking
# ---------------------------------------------------------------------------


def test_top_k_returns_term_match_first() -> None:
    chunks = [
        _chunk(chunk_id="cardio", text="ASCVD risk requires statin therapy."),
        _chunk(chunk_id="diabetes", text="A1c targets vary by patient age."),
        _chunk(chunk_id="ckd", text="eGFR staging informs nephrology referral."),
    ]
    retriever = BM25Retriever(chunks)

    results = retriever.top_k("statin therapy", k=3)
    assert len(results) >= 1
    assert results[0].chunk.chunk_id == "cardio"


def test_top_k_orders_higher_term_overlap_first() -> None:
    chunks = [
        _chunk(chunk_id="strong", text="metformin metformin metformin glucose"),
        _chunk(chunk_id="weak", text="metformin renal dosing review"),
        _chunk(chunk_id="unrelated", text="influenza vaccination schedule adults"),
    ]
    retriever = BM25Retriever(chunks)

    results = retriever.top_k("metformin", k=3)
    assert [r.chunk.chunk_id for r in results[:2]] == ["strong", "weak"]


def test_top_k_respects_k_ceiling() -> None:
    chunks = [
        _chunk(chunk_id=f"c{i}", text="metformin glucose")
        for i in range(10)
    ]
    retriever = BM25Retriever(chunks)
    results = retriever.top_k("metformin", k=3)
    assert len(results) == 3


def test_top_k_drops_zero_score_hits() -> None:
    """Zero-score hits contain no query terms — surfacing them as
    'least bad' results would mislead the synthesizer. Result list
    can be shorter than k."""
    chunks = [
        _chunk(chunk_id="match", text="metformin renal dosing"),
        _chunk(chunk_id="dud", text="influenza vaccination schedule"),
    ]
    retriever = BM25Retriever(chunks)
    results = retriever.top_k("metformin", k=5)
    assert len(results) == 1
    assert results[0].chunk.chunk_id == "match"
    assert results[0].score > 0


def test_top_k_returns_empty_for_query_with_no_matches() -> None:
    chunks = [
        _chunk(chunk_id="c1", text="metformin renal dosing"),
    ]
    retriever = BM25Retriever(chunks)
    results = retriever.top_k("influenza vaccination", k=5)
    assert results == []


def test_top_k_returns_empty_for_unknown_query_terms() -> None:
    """Query terms not in the corpus contribute zero IDF — no hits."""
    chunks = [
        _chunk(chunk_id="c1", text="metformin renal dosing"),
    ]
    retriever = BM25Retriever(chunks)
    results = retriever.top_k("xyzzy plugh", k=5)
    assert results == []


def test_top_k_rejects_non_positive_k() -> None:
    chunks = [_chunk(chunk_id="c1", text="foo")]
    retriever = BM25Retriever(chunks)
    with pytest.raises(ValueError, match="positive"):
        retriever.top_k("foo", k=0)
    with pytest.raises(ValueError, match="positive"):
        retriever.top_k("foo", k=-1)


def test_score_returns_zero_for_empty_query_tokens() -> None:
    chunks = [_chunk(chunk_id="c1", text="metformin")]
    retriever = BM25Retriever(chunks)
    assert retriever.score([], 0) == 0.0


def test_results_carry_citations_attached() -> None:
    """Citations must round-trip through retrieval — that's the whole
    point of attaching them at chunk-construction time."""
    chunks = [
        _chunk(chunk_id="c1", text="ASCVD risk and statin therapy", section="cardio"),
    ]
    retriever = BM25Retriever(chunks)
    results = retriever.top_k("statin", k=1)
    assert len(results) == 1
    assert results[0].chunk.citation.source_id == "test_doc"
    assert results[0].chunk.citation.field_or_chunk_id == "c1"
