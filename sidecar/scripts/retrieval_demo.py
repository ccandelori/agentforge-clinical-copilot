"""Standalone evidence-retrieval demo over the AgentForge guideline
corpus (W2 Task 9 / Task 10 deliverable).

Loads ``sidecar/data/guidelines/index.json``, scores chunks against
a free-text query, and prints the top-k results with their metadata.

Three modes:

  * ``--mode bm25`` (default) — BM25 only. Fast, no model download.
  * ``--mode dense`` — Sentence-transformer cosine sim only. First
    run downloads the all-MiniLM-L6-v2 model (~80 MB) into the
    Hugging Face cache; subsequent runs are instant.
  * ``--mode hybrid`` — full BM25 + dense → RRF → cross-encoder
    pipeline (the production shape). First run also downloads the
    ``bge-reranker-base`` model (~110 MB).

Run with:

    cd sidecar

    uv run python scripts/retrieval_demo.py "A1C target for adult diabetes"
    uv run python scripts/retrieval_demo.py --mode hybrid "ASCVD risk statin"
    uv run python scripts/retrieval_demo.py --mode dense --top-k 5 \\
        "CKD stage 3 management"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from agentforge.rag import (
    BM25Retriever,
    DenseRetriever,
    EvidenceRetriever,
    GuidelineChunk,
    RetrievalConfig,
    RetrievalResult,
    RRFMerger,
    load_corpus,
)


def format_result(rank: int, chunk: GuidelineChunk, score: float) -> str:
    snippet = chunk.text.strip().replace("\n", " ")
    if len(snippet) > 240:
        snippet = snippet[:237] + "..."
    return (
        f"[{rank}] score={score:.3f}  doc={chunk.doc_id}  section=\"{chunk.section}\"\n"
        f"    chunk_id: {chunk.chunk_id}\n"
        f"    source:   {chunk.source_path} (version {chunk.version})\n"
        f"    excerpt:  {snippet}\n"
    )


def run_bm25(chunks: list[GuidelineChunk], query: str, top_k: int) -> list[RetrievalResult]:
    return BM25Retriever(chunks).top_k(query, top_k)


def run_dense(chunks: list[GuidelineChunk], query: str, top_k: int) -> list[RetrievalResult]:
    from agentforge.rag import SentenceTransformerEncoder

    encoder = SentenceTransformerEncoder()
    return DenseRetriever(chunks, encoder=encoder).top_k(query, top_k)


async def run_hybrid(
    chunks: list[GuidelineChunk],
    query: str,
    top_k: int,
) -> list[RetrievalResult]:
    """Full pipeline: BM25 + Dense → RRF → cross-encoder rerank.

    First-time use downloads two models (all-MiniLM-L6-v2 + bge-
    reranker-base) into the Hugging Face cache. Subsequent calls
    reuse the cached weights.
    """
    from agentforge.rag import (
        CrossEncoderReranker,
        SentenceTransformerCrossEncoder,
        SentenceTransformerEncoder,
    )

    encoder = SentenceTransformerEncoder()
    cross_encoder = SentenceTransformerCrossEncoder()
    retriever = EvidenceRetriever(
        bm25=BM25Retriever(chunks),
        dense=DenseRetriever(chunks, encoder=encoder),
        merger=RRFMerger(),
        reranker=CrossEncoderReranker(cross_encoder),
        config=RetrievalConfig(rerank_multiplier=4),
    )
    return await retriever.retrieve(query, top_k=top_k)


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
        "--mode",
        choices=["bm25", "dense", "hybrid"],
        default="bm25",
        help="Retrieval pipeline (default: bm25). Dense + hybrid download "
        "ML models on first use.",
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

    chunks = load_corpus(args.index)

    if args.mode == "bm25":
        results = run_bm25(chunks, args.query, args.top_k)
    elif args.mode == "dense":
        results = run_dense(chunks, args.query, args.top_k)
    else:
        results = asyncio.run(run_hybrid(chunks, args.query, args.top_k))

    print(f"Query: {args.query}")
    print(f"Mode:  {args.mode}")
    print(f"Corpus: {len(chunks)} chunks from {args.index}\n")

    if not results:
        print("(no chunks matched)")
        return 0

    for i, result in enumerate(results, start=1):
        print(format_result(i, result.chunk, result.score))

    return 0


if __name__ == "__main__":
    sys.exit(main())
