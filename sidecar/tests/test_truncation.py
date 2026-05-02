"""SynthesisInputTruncator unit tests.

Task 45 — caps the cumulative tool-result content fed back to the LLM at
the synthesis step. Three layers, tested independently:

  45.1  Token counting (this file's first block).
  45.2  Priority-based whole-tool drop when total exceeds the cap.
  45.3  Within-tool oldest-first item drop instead of whole-tool drop.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from agentforge.orchestrator.truncation import SynthesisInputTruncator
from agentforge.tools.demographics import DemographicsPayload, DemographicsResult
from agentforge.tools.dtos import ToolResult, ToolResultMetadata
from agentforge.tools.notes import NoteItem, NotesPayload, NotesResult
from agentforge.tools.problems import ProblemItem, ProblemsPayload, ProblemsResult


# ---------------------------------------------------------------------------
# Subtask 45.1 — token counting
# ---------------------------------------------------------------------------


class TestCountTokens:
    def test_empty_string_is_zero_tokens(self) -> None:
        truncator = SynthesisInputTruncator()
        assert truncator.count_tokens("") == 0

    def test_simple_english_phrase_returns_positive_count(self) -> None:
        truncator = SynthesisInputTruncator()
        # "hello world" tokenizes to exactly 2 tokens under cl100k_base.
        assert truncator.count_tokens("hello world") == 2

    def test_token_count_grows_with_content(self) -> None:
        truncator = SynthesisInputTruncator()
        short = truncator.count_tokens("hi")
        longer = truncator.count_tokens(
            "this is a much longer sentence with substantially more content"
        )
        assert longer > short

    def test_count_tool_results_returns_positive_for_populated_dict(self) -> None:
        truncator = SynthesisInputTruncator()
        results: dict[str, ToolResult] = {
            "get_demographics": _demographics_result(),
            "get_active_problems": _problems_result(count=3),
        }
        n = truncator.count_tool_results(results)
        assert n > 0

    def test_count_tool_results_grows_with_more_results(self) -> None:
        truncator = SynthesisInputTruncator()
        small = {"get_active_problems": _problems_result(count=1)}
        large = {"get_active_problems": _problems_result(count=20)}
        assert truncator.count_tool_results(large) > truncator.count_tool_results(small)

    def test_count_tool_results_empty_dict_is_zero(self) -> None:
        truncator = SynthesisInputTruncator()
        assert truncator.count_tool_results({}) == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _meta(tool_name: str) -> ToolResultMetadata:
    return ToolResultMetadata(
        tool_name=tool_name,
        fetched_at=datetime.now(UTC),
        data_freshness_seconds=60,
        source=f"openemr.{tool_name}",
    )


def _demographics_result() -> DemographicsResult:
    return ToolResult[DemographicsPayload](
        metadata=_meta("get_demographics"),
        payload=DemographicsPayload(
            patient_id=8,
            given_name="Eula",
            family_name="Crist",
            date_of_birth=date(1972, 11, 30),
            sex="F",
            preferred_language="en-US",
        ),
    )


def _problems_result(count: int = 5) -> ProblemsResult:
    items = tuple(
        ProblemItem(
            id=i,
            title=f"Synthetic problem {i}",
            diagnosis=f"SNOMED-CT:1000{i}",
            begin_date=f"202{i % 5}-01-01",
        )
        for i in range(count)
    )
    return ToolResult[ProblemsPayload](
        metadata=_meta("get_active_problems"),
        payload=ProblemsPayload(problems=items),
    )


def _notes_result(count: int) -> NotesResult:
    """count notes, newest-first by ID (matches real-world ordering)."""
    items = tuple(
        NoteItem(
            id=count - i,
            source="pnote",
            date=f"2026-{(i % 12) + 1:02d}-15 10:00:00",
            author="dr-smith",
            title=f"Note {count - i}",
            body="Body text " * 30,  # ~60 tokens of filler so payloads have heft
            note_type=None,
            permission_denied=False,
        )
        for i in range(count)
    )
    return ToolResult[NotesPayload](
        metadata=_meta("get_recent_notes"),
        payload=NotesPayload(notes=items),
    )
