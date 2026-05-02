"""Synthesis input truncation (Task 45).

Caps the cumulative tool-result content fed to the LLM at the synthesis
step so we never exceed the configured ``synthesis_input_cap`` budget
defined on ``TimeoutPolicy`` (default 12,000 tokens). Three policies, in
order of severity:

1. **Pass-through.** When total tokens are under the cap, return the
   results dict unchanged.
2. **Whole-tool drop.** Walk the priority list in reverse (lowest
   priority first) and drop entire tool results until total fits. High-
   priority tools (demographics, problems, meds, allergies) are kept;
   low-priority ones (notes, search results) are dropped first.
3. **Within-tool oldest-first shrink.** Before dropping a tool entirely,
   shrink its list-shaped payload by removing items from the end of the
   list (which encodes "oldest" because the tools sort newest-first).
   Demographics is the exception — it's a single record, not a list, so
   it can only be kept whole or dropped.

Token counting uses tiktoken's ``cl100k_base`` encoding (GPT-4 family).
That's a documented approximation for Claude — within ~5-10% in
practice — and chosen for being local + deterministic. A network round-
trip to Anthropic's ``count_tokens`` endpoint is not appropriate for an
in-loop budget gate.

This module is wired-in by Task 27 (Planner restructure); for now it
ships as a standalone utility with full unit coverage so the wiring is
a one-line slot at the synthesis call site.
"""

from __future__ import annotations

from typing import Any

import tiktoken
from pydantic import BaseModel

from agentforge.tools.dtos import ToolResult


class SynthesisInputTruncator:
    """Cap synthesis-input tokens; drop or shrink lowest-priority first.

    Constructed once at startup; ``truncate`` is the only stateful call
    and it never mutates inputs (returns a fresh dict + fresh
    ``ToolResult`` instances for any tools whose payload changed).

    The priority tuple is ordered HIGHEST → LOWEST: demographics first,
    free-text search last. Truncation walks it in reverse.
    """

    PRIORITY: tuple[str, ...] = (
        "get_demographics",
        "get_active_problems",
        "get_active_medications",
        "get_active_allergies",
        "get_recent_labs",
        "get_recent_encounters",
        "get_vitals_trend",
        "get_recent_notes",
        "search_notes",
    )

    # Each tool's list-shaped payload field (the one that gets shrunk
    # oldest-first). Tools missing from this map can only be kept whole
    # or dropped — they have no shrinkable list.
    _LIST_FIELD_BY_TOOL: dict[str, str] = {
        "get_active_problems": "problems",
        "get_active_medications": "medications",
        "get_active_allergies": "allergies",
        "get_recent_labs": "labs",
        "get_recent_encounters": "encounters",
        "get_vitals_trend": "vitals",
        "get_recent_notes": "notes",
        "search_notes": "results",
    }

    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        self._encoder = tiktoken.get_encoding(encoding_name)

    # ------------------------------------------------------------------
    # 45.1 — token counting
    # ------------------------------------------------------------------

    def count_tokens(self, text: str) -> int:
        """Count tokens in a single string. Empty string -> 0."""
        if not text:
            return 0
        return len(self._encoder.encode(text))

    def count_tool_results(self, results: dict[str, ToolResult[Any]]) -> int:
        """Sum tokens across the JSON-serialized payload of every result.

        We count the payload's JSON surface (what the LLM actually
        sees), not the wrapper metadata. Metadata is small and constant
        per result; counting it adds noise without changing decisions.
        """
        total = 0
        for result in results.values():
            total += self.count_tokens(result.payload.model_dump_json())
        return total
