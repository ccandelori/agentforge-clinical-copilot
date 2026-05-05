"""Standalone evidence-retrieval demo over the AgentForge guideline
corpus (Task 10 deliverable).

Loads ``sidecar/data/guidelines/index.json``, scores chunks against a
free-text query using BM25, and prints the top-k results with their
metadata. The retriever is intentionally inline and dependency-free —
production will move to a proper retriever class wired into LangGraph
(Task 15), but for the W2 demo this proves the corpus is loadable,
indexable, and queryable end-to-end.

Run with:

    uv run python sidecar/scripts/retrieval_demo.py "A1C target for adult diabetes"

Or with --top-k:

    uv run python sidecar/scripts/retrieval_demo.py --top-k 5 "ASCVD risk statin"
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# BM25 hyperparameters (Robertson/Sparck Jones canonical defaults).
BM25_K1 = 1.5
BM25_B = 0.75


# ---------------------------------------------------------------------------
# Corpus loading + tokenization
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IndexedChunk:
    """In-memory shape of an index.json chunk for the retriever."""

    doc_id: str
    section: str
    version: str
    chunk_id: str
    text: str
    token_count: int
    source_path: str


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]+")
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "have", "in", "is", "it", "its", "of", "on", "or", "that",
    "the", "this", "to", "was", "were", "will", "with",
})


def tokenize(text: str) -> list[str]:
    """Lowercase + word-token split, dropping a small stopword set.

    Kept intentionally crude — the W2 demo doesn't ride on tokenization
    sophistication. Production retrieval (Task 15) can swap in
    something stem-aware."""
    return [
        tok for tok in (m.group(0).lower() for m in _TOKEN_RE.finditer(text))
        if tok not in _STOPWORDS
    ]


def load_index(path: Path) -> list[IndexedChunk]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    chunks: list[IndexedChunk] = []
    for c in raw.get("chunks", []):
        chunks.append(
            IndexedChunk(
                doc_id=c["doc_id"],
                section=c["section"],
                version=c["version"],
                chunk_id=c["chunk_id"],
                text=c["text"],
                token_count=int(c["token_count"]),
                source_path=c["source_path"],
            )
        )
    return chunks


# ---------------------------------------------------------------------------
# BM25 retriever (inline, no external dep)
# ---------------------------------------------------------------------------

class BM25Retriever:
    """BM25 over a fixed corpus of pre-tokenized chunks.

    Implementation follows Robertson/Sparck Jones with the canonical
    k1=1.5, b=0.75 defaults. A real production retriever (Task 15)
    would precompute postings lists and reuse the index across
    queries; this version is small and correct enough for a demo.
    """

    def __init__(self, chunks: list[IndexedChunk]) -> None:
        self._chunks = chunks
        self._tokenized: list[list[str]] = [tokenize(c.text) for c in chunks]
        self._doc_lengths = [len(d) for d in self._tokenized]
        self._avg_doc_length = (
            sum(self._doc_lengths) / len(self._doc_lengths)
            if self._doc_lengths
            else 0.0
        )
        # Document frequency per term across the whole corpus.
        df: Counter[str] = Counter()
        for tokens in self._tokenized:
            df.update(set(tokens))
        # IDF using the BM25 numerator-shifted variant; +1 keeps it
        # nonnegative even when df > N/2.
        n = len(self._tokenized)
        self._idf: dict[str, float] = {
            term: math.log(((n - count + 0.5) / (count + 0.5)) + 1)
            for term, count in df.items()
        }
        # Per-doc term frequencies, precomputed for query speed.
        self._tf: list[Counter[str]] = [Counter(d) for d in self._tokenized]

    def score(self, query_tokens: list[str], doc_index: int) -> float:
        if not query_tokens:
            return 0.0
        tf = self._tf[doc_index]
        dl = self._doc_lengths[doc_index]
        score = 0.0
        for term in query_tokens:
            if term not in self._idf:
                continue
            f = tf.get(term, 0)
            if f == 0:
                continue
            denom = f + BM25_K1 * (
                1 - BM25_B + BM25_B * (dl / (self._avg_doc_length or 1))
            )
            score += self._idf[term] * (f * (BM25_K1 + 1)) / denom
        return score

    def top_k(self, query: str, k: int) -> list[tuple[IndexedChunk, float]]:
        query_tokens = tokenize(query)
        scored = [
            (chunk, self.score(query_tokens, i))
            for i, chunk in enumerate(self._chunks)
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [pair for pair in scored[:k] if pair[1] > 0]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def format_result(rank: int, chunk: IndexedChunk, score: float) -> str:
    snippet = chunk.text.strip().replace("\n", " ")
    if len(snippet) > 240:
        snippet = snippet[:237] + "..."
    return (
        f"[{rank}] score={score:.3f}  doc={chunk.doc_id}  section=\"{chunk.section}\"\n"
        f"    chunk_id: {chunk.chunk_id}\n"
        f"    source:   {chunk.source_path} (version {chunk.version})\n"
        f"    excerpt:  {snippet}\n"
    )


def main(argv: list[str] | None = None) -> int:
    default_index = (
        Path(__file__).resolve().parents[1] / "data" / "guidelines" / "index.json"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Free-text query (quote multi-word inputs).")
    parser.add_argument(
        "--index",
        type=Path,
        default=default_index,
        help="Path to index.json (defaults to sidecar/data/guidelines/index.json).",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Number of top results to print (default 3).",
    )
    args = parser.parse_args(argv)

    if not args.index.exists():
        print(f"index not found: {args.index}", file=sys.stderr)
        print("Run: uv run python scripts/chunk_guidelines.py", file=sys.stderr)
        return 2

    chunks = load_index(args.index)
    retriever = BM25Retriever(chunks)
    results = retriever.top_k(args.query, args.top_k)

    print(f"Query: {args.query}")
    print(f"Corpus: {len(chunks)} chunks from {args.index}\n")

    if not results:
        print("(no chunks matched)")
        return 0

    for i, (chunk, score) in enumerate(results, start=1):
        print(format_result(i, chunk, score))

    return 0


if __name__ == "__main__":
    sys.exit(main())
