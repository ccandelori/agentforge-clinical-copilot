"""Reranker selection for the W2 evidence-retrieval pipeline.

Three deployment shapes share a single retriever construction site:

  * Cohere-equipped (operator paid for ``COHERE_API_KEY``) — use the
    hosted rerank API for stronger technical-text relevance and ~50 ms
    latency.
  * Local default — use :class:`CrossEncoderReranker` over
    ``bge-reranker-base``. No per-request cost; ~100 ms CPU.
  * Ablation runs — explicit ``force_passthrough=True`` returns a
    :class:`PassthroughReranker`. This is what the eval suite exercises
    when measuring the rerank-stage contribution to grounding scores.

Selection lives in this dedicated module rather than ``main.py`` so
the same factory is callable from tests, scripts, and the eval
harness without dragging the FastAPI app's import graph along. The
factory takes the heavy SDK objects (cohere SDK class, cross-encoder
model wrapper) by injection — production passes the real classes,
tests pass minimal stubs.

Why the factory does *not* auto-fallback: a Cohere-misconfigured
deployment (key set, SDK missing) should fail loud at startup, not
silently degrade to cross-encoder. Operators set the key with intent;
honoring that intent matters more than convenience.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from agentforge.rag.cross_encoder import (
    CrossEncoder,
    CrossEncoderReranker,
)
from agentforge.rag.reranker import PassthroughReranker, Reranker


@runtime_checkable
class _CohereRerankerFactory(Protocol):
    """Subset of the :class:`CohereReranker` constructor consumed by the factory.

    Narrowed to ``__call__(api_key: str) -> Reranker`` so tests can pass
    a fake class without satisfying the full :class:`CohereReranker`
    surface (model kwargs, ``aclose``, etc.). The real
    :class:`CohereReranker` satisfies this structurally — its
    constructor's ``api_key`` is positional-or-keyword and ``model``
    has a default.
    """

    def __call__(self, api_key: str) -> Reranker: ...


def _default_cohere_factory(api_key: str) -> Reranker:
    """Lazy-import :class:`CohereReranker` so deployments that never
    set ``COHERE_API_KEY`` don't pay the import cost (and don't fail
    when the optional cohere SDK is uninstalled)."""
    from agentforge.rag.cohere_rerank import CohereReranker

    return CohereReranker(api_key=api_key)


def select_reranker(
    *,
    cohere_api_key: str | None,
    cohere_factory: _CohereRerankerFactory | None = None,
    cross_encoder: CrossEncoder | None = None,
    cross_encoder_factory: Callable[[], CrossEncoder] | None = None,
    force_passthrough: bool = False,
) -> Reranker:
    """Pick the reranker for an evidence-retrieval pipeline build.

    Selection order:

    1. ``force_passthrough`` short-circuits to :class:`PassthroughReranker`
       regardless of the other arguments. This is the ablation-only
       path; production should never set the flag.
    2. Non-empty ``cohere_api_key`` → :class:`CohereReranker` via
       ``cohere_factory`` (defaults to lazy-importing
       :class:`agentforge.rag.cohere_rerank.CohereReranker`).
    3. Otherwise → :class:`CrossEncoderReranker` over the supplied
       ``cross_encoder`` (or one built by ``cross_encoder_factory`` if
       no instance is given).

    A construction failure (e.g. cohere SDK missing under branch 2,
    or neither ``cross_encoder`` nor factory given under branch 3)
    surfaces as a :class:`ValueError` from this function or its
    delegates — the caller handles it. The factory does not silently
    fall back across branches.
    """
    if force_passthrough:
        return PassthroughReranker()

    if cohere_api_key:
        factory = cohere_factory or _default_cohere_factory
        return factory(cohere_api_key)

    if cross_encoder is not None:
        return CrossEncoderReranker(cross_encoder)
    if cross_encoder_factory is not None:
        return CrossEncoderReranker(cross_encoder_factory())
    raise ValueError(
        "select_reranker requires a cross_encoder or cross_encoder_factory "
        "when cohere_api_key is empty"
    )
