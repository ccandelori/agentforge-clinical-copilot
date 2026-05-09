"""Sidecar→OpenEMR persistence clients (P1.1, fix/p1-1-sidecar-persist-extractions).

The W2 graph's intake / lab extractors emit validated Pydantic
extractions but the per-turn snapshot ContextVar is the only thing
that surfaces them back to the dashboard. Without a persistence step
those extractions live exactly as long as the HTTP turn — the user
sees them once and the structured EHR side never hears about them.

This package owns the "after the graph extracts, POST it to OpenEMR"
half of that loop. The HTTP shape mirrors :mod:`tools.document_bytes`:
a long-lived :class:`httpx.AsyncClient`, JWT bearer auth, narrow typed
exception class, generous timeout. The orchestrator wires the call
into :meth:`Orchestrator._run_graph_turn` after the graph completes,
best-effort — a persist failure logs a warning and continues so the
synthesis turn still surfaces the model's reply to the user.
"""

from agentforge.persist.extraction_persister import (
    ExtractionPersister,
    ExtractionPersistError,
    PersistedHandle,
)

__all__ = [
    "ExtractionPersistError",
    "ExtractionPersister",
    "PersistedHandle",
]
