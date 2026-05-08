"""Production-side evaluation adapter for the W2 supervisor graph.

This package owns the seam between the eval surface (which expects
deterministic, well-shaped :class:`SupervisorOutput` per case) and the
LangGraph orchestrator (a streaming, LLM-driven graph). Tests under
``tests/eval/gate/`` pin the contract; this package supplies the
production callable that satisfies it.

See :mod:`agentforge.eval.supervisor_adapter` for the adapter itself
and :mod:`agentforge.eval.regenerate_baseline` for the manual CLI that
re-measures ``baselines/week2.json``.
"""

from agentforge.eval.filename_resolver import FilenameDocumentResolver
from agentforge.eval.supervisor_adapter import (
    DocumentFixtureResolver,
    SupervisorAdapter,
    SupervisorAdapterDeps,
)

__all__ = (
    "DocumentFixtureResolver",
    "FilenameDocumentResolver",
    "SupervisorAdapter",
    "SupervisorAdapterDeps",
)
