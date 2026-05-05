"""BM25 retrieval over a fixed guideline corpus.

Pulls the inline math from Task 10's ``retrieval_demo.py`` into a
proper class with a stable ``top_k(query, k)`` surface. The retriever
is **immutable after construction** — building the postings is the
expensive step, querying is cheap. Reuse one ``BM25Retriever`` across
all queries in a process.

Implementation follows Robertson/Sparck Jones with the canonical
``k1=1.5``, ``b=0.75`` defaults. The math is intentionally inline
(rather than reaching for ``rank_bm25``) because:

  * The corpus is small (~30 chunks at MVP scale); a 50-line BM25
    impl is faster to load than pulling a third-party package.
  * The IDF formula uses the +1-shifted variant to keep weights
    nonnegative even on very-frequent terms — that detail's
    documented in-repo and easier to assert against than a vendor
    library's choice.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

from agentforge.rag.types import GuidelineChunk, RetrievalResult

# Robertson/Sparck Jones canonical defaults; tunable if Task 17's eval
# suite shows the corpus rewards different values.
BM25_K1 = 1.5
BM25_B = 0.75


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]+")
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "have", "in", "is", "it", "its", "of", "on", "or", "that",
    "the", "this", "to", "was", "were", "will", "with",
})


def tokenize(text: str) -> list[str]:
    """Lowercase + word-token split, dropping a small stopword set.

    Crude on purpose — the W2 demo doesn't ride on tokenization
    sophistication. Production retrieval can swap in a stem-aware
    tokenizer; the retriever's API is stable across tokenization
    changes."""
    return [
        tok for tok in (m.group(0).lower() for m in _TOKEN_RE.finditer(text))
        if tok not in _STOPWORDS
    ]


class BM25Retriever:
    """BM25-scored retrieval over a fixed corpus of :class:`GuidelineChunk`.

    Construction is O(N * avg_doc_tokens); each call to ``top_k`` is
    O(N * len(query_tokens)). For the W2 corpus (~30 chunks, ~150
    tokens/chunk), both phases are sub-millisecond — no caching is
    needed.
    """

    def __init__(
        self,
        chunks: Sequence[GuidelineChunk],
        *,
        k1: float = BM25_K1,
        b: float = BM25_B,
    ) -> None:
        if k1 < 0 or b < 0 or b > 1:
            raise ValueError(
                f"BM25 hyperparameters out of range: k1={k1} (>=0), b={b} (0<=b<=1)"
            )

        self._chunks: tuple[GuidelineChunk, ...] = tuple(chunks)
        self._k1 = k1
        self._b = b

        self._tokenized: list[list[str]] = [tokenize(c.text) for c in self._chunks]
        self._doc_lengths: list[int] = [len(d) for d in self._tokenized]
        self._avg_doc_length: float = (
            sum(self._doc_lengths) / len(self._doc_lengths)
            if self._doc_lengths
            else 0.0
        )

        # Document frequency per term across the whole corpus.
        df: Counter[str] = Counter()
        for tokens in self._tokenized:
            df.update(set(tokens))

        # IDF using the BM25 numerator-shifted variant; +1 keeps it
        # nonnegative even when df > N/2 (avoids the rare case where
        # a near-universal term contributes negative weight).
        n = len(self._tokenized)
        self._idf: dict[str, float] = {
            term: math.log(((n - count + 0.5) / (count + 0.5)) + 1)
            for term, count in df.items()
        }

        # Per-doc term frequencies, precomputed for query speed.
        self._tf: list[Counter[str]] = [Counter(d) for d in self._tokenized]

    @property
    def corpus_size(self) -> int:
        return len(self._chunks)

    def score(self, query_tokens: list[str], doc_index: int) -> float:
        """Score a tokenized query against one document.

        Exposed for unit-testing the math; ``top_k`` is the caller's
        usual entry point.
        """
        if not query_tokens or not self._chunks:
            return 0.0
        tf = self._tf[doc_index]
        dl = self._doc_lengths[doc_index]
        avg_dl = self._avg_doc_length or 1.0
        score = 0.0
        for term in query_tokens:
            idf = self._idf.get(term)
            if idf is None:
                continue
            f = tf.get(term, 0)
            if f == 0:
                continue
            denom = f + self._k1 * (1 - self._b + self._b * (dl / avg_dl))
            score += idf * (f * (self._k1 + 1)) / denom
        return score

    def top_k(self, query: str, k: int) -> list[RetrievalResult]:
        """Return the top-``k`` non-zero hits, descending by BM25 score.

        ``k`` is a hard ceiling — we drop any hit that scores 0 even
        if the result list is shorter. Zero-score results contain no
        query terms; surfacing them as "least bad" hits would mislead
        the synthesizer.
        """
        if k <= 0:
            raise ValueError(f"k must be positive; got {k}")
        if not self._chunks:
            return []

        query_tokens = tokenize(query)
        scored = [
            RetrievalResult(chunk=chunk, score=self.score(query_tokens, i))
            for i, chunk in enumerate(self._chunks)
        ]
        scored.sort(key=lambda r: r.score, reverse=True)
        return [r for r in scored[:k] if r.score > 0]
