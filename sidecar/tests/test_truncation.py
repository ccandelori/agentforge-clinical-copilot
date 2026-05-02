"""SynthesisInputTruncator unit tests.

Task 45 — caps the cumulative tool-result content fed back to the LLM at
the synthesis step. Three layers, tested independently:

  45.1  Token counting (this file's first block).
  45.2  Priority-based whole-tool drop when total exceeds the cap.
  45.3  Within-tool oldest-first item drop instead of whole-tool drop.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

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
# Subtask 45.2 — priority-based whole-tool drop
# ---------------------------------------------------------------------------


class TestPriorityDropTruncate:
    def test_under_cap_returns_equivalent_dict(self) -> None:
        truncator = SynthesisInputTruncator()
        results: dict[str, ToolResult] = {
            "get_demographics": _demographics_result(),
            "get_active_problems": _problems_result(count=2),
        }
        out = truncator.truncate(results, max_tokens=10_000)
        assert set(out.keys()) == set(results.keys())
        # Same payload contents
        for k in results:
            assert out[k].payload.model_dump_json() == results[k].payload.model_dump_json()

    def test_returns_a_fresh_dict_does_not_mutate_input(self) -> None:
        truncator = SynthesisInputTruncator()
        results: dict[str, ToolResult] = {
            "get_demographics": _demographics_result(),
            "get_recent_notes": _notes_result(count=20),
        }
        before_len = len(results)
        before_keys = set(results.keys())
        truncator.truncate(results, max_tokens=50)
        assert len(results) == before_len  # input untouched
        assert set(results.keys()) == before_keys

    def test_drops_lowest_priority_first(self) -> None:
        """notes is lower priority than problems — evict notes first.

        With 45.3 in effect, "evict" can mean shrink rather than drop;
        we tighten the cap so notes have to shrink to zero (i.e. drop)
        to honor the assertion that demographics + problems both stay.
        """
        truncator = SynthesisInputTruncator()
        results: dict[str, ToolResult] = {
            "get_demographics": _demographics_result(),
            "get_active_problems": _problems_result(count=2),
            "get_recent_notes": _notes_result(count=30),  # bulky
        }
        keep_size = truncator.count_tokens(
            results["get_demographics"].payload.model_dump_json()
        ) + truncator.count_tokens(
            results["get_active_problems"].payload.model_dump_json()
        )
        # Exact fit for demographics + problems; notes must shrink to 0
        # and be dropped.
        out = truncator.truncate(results, max_tokens=keep_size)
        assert "get_recent_notes" not in out
        assert "get_demographics" in out
        assert "get_active_problems" in out

    def test_drops_in_strict_priority_order_search_before_notes(self) -> None:
        """search_notes is below notes; should drop first when both present."""
        truncator = SynthesisInputTruncator()
        from agentforge.tools.search_notes import (
            SearchHit,
            SearchNotesPayload,
        )

        search_results = ToolResult[SearchNotesPayload](
            metadata=_meta("search_notes"),
            payload=SearchNotesPayload(
                results=tuple(
                    SearchHit(
                        id=i,
                        source="pnote",
                        date=f"2026-01-{(i % 28) + 1:02d}",
                        title=f"Hit {i}",
                        snippet="This is a snippet " * 30,
                    )
                    for i in range(20)
                ),
            ),
        )
        notes = _notes_result(count=20)
        results: dict[str, ToolResult] = {
            "get_recent_notes": notes,
            "search_notes": search_results,
        }
        # Cap = notes intact + room for almost-no search. With 45.3,
        # even a single search hit costs ~50+ tokens so a 10-token
        # margin can't fit one — search must shrink to 0 then drop.
        notes_only = truncator.count_tokens(notes.payload.model_dump_json())
        out = truncator.truncate(results, max_tokens=notes_only + 10)
        assert "get_recent_notes" in out
        assert "search_notes" not in out

    def test_drops_demographics_last_even_under_extreme_pressure(self) -> None:
        """When the cap can only fit demographics, lower-priority tools all go."""
        truncator = SynthesisInputTruncator()
        demo = _demographics_result()
        results: dict[str, ToolResult] = {
            "get_demographics": demo,
            "get_recent_notes": _notes_result(count=50),
            "get_active_problems": _problems_result(count=20),
        }
        demo_size = truncator.count_tokens(demo.payload.model_dump_json())
        out = truncator.truncate(results, max_tokens=demo_size + 5)
        assert "get_demographics" in out
        # Best effort: lower-priority dropped
        assert "get_recent_notes" not in out

    def test_zero_cap_drops_everything(self) -> None:
        truncator = SynthesisInputTruncator()
        results: dict[str, ToolResult] = {
            "get_demographics": _demographics_result(),
            "get_recent_notes": _notes_result(count=5),
        }
        out = truncator.truncate(results, max_tokens=0)
        assert out == {}

    def test_unknown_tool_treated_as_lowest_priority(self) -> None:
        """Tools not in PRIORITY get dropped before known tools when over cap.

        Defensive default: a future tool wired in before PRIORITY is updated
        shouldn't crowd out demographics. Drop it first.
        """
        truncator = SynthesisInputTruncator()
        # Synthesize a 'mystery_tool' result by reusing problems shape but
        # under an unknown key.
        results: dict[str, ToolResult] = {
            "get_demographics": _demographics_result(),
            "mystery_tool": _problems_result(count=30),
        }
        demo_size = truncator.count_tokens(
            results["get_demographics"].payload.model_dump_json()
        )
        out = truncator.truncate(results, max_tokens=demo_size + 50)
        assert "get_demographics" in out
        assert "mystery_tool" not in out


# ---------------------------------------------------------------------------
# Subtask 45.3 — within-tool oldest-first shrink
# ---------------------------------------------------------------------------


class TestWithinToolShrink:
    def test_notes_shrunk_oldest_first_not_dropped(self) -> None:
        """When notes don't fit whole but partial would, shrink instead of drop."""
        truncator = SynthesisInputTruncator()
        notes = _notes_result(count=20)
        results: dict[str, ToolResult] = {
            "get_demographics": _demographics_result(),
            "get_recent_notes": notes,
        }
        # Cap that fits demographics + ~5 notes but not all 20
        demo_size = truncator.count_tokens(
            results["get_demographics"].payload.model_dump_json()
        )
        single_note_estimate = truncator.count_tokens(
            notes.payload.notes[0].model_dump_json()
        )
        # Allow demographics + ~5 notes worth of tokens
        budget = demo_size + 5 * (single_note_estimate + 5)
        out = truncator.truncate(results, max_tokens=budget)
        assert "get_recent_notes" in out, "should have shrunk, not dropped"
        kept = out["get_recent_notes"].payload.notes
        assert 0 < len(kept) < 20, f"expected partial shrink, got {len(kept)}"

    def test_shrink_keeps_newest_drops_oldest(self) -> None:
        """After shrinking, the surviving notes are the newest (lowest index)."""
        truncator = SynthesisInputTruncator()
        # _notes_result generates ids in DESCENDING order (newest first)
        notes = _notes_result(count=10)
        original_first_id = notes.payload.notes[0].id
        results: dict[str, ToolResult] = {"get_recent_notes": notes}
        # Force shrink — tiny budget
        single = truncator.count_tokens(notes.payload.notes[0].model_dump_json())
        out = truncator.truncate(results, max_tokens=2 * single)
        kept = out["get_recent_notes"].payload.notes
        assert len(kept) >= 1
        # First kept item must be the original first (newest)
        assert kept[0].id == original_first_id

    def test_demographics_dropped_wholly_when_over_cap_alone(self) -> None:
        """Demographics has no list field — can only be kept whole or dropped."""
        truncator = SynthesisInputTruncator()
        demo = _demographics_result()
        results: dict[str, ToolResult] = {"get_demographics": demo}
        out = truncator.truncate(results, max_tokens=2)
        assert "get_demographics" not in out

    def test_search_shrunk_before_recent_notes_shrunk(self) -> None:
        """search_notes is below recent_notes in PRIORITY — shrink it first."""
        truncator = SynthesisInputTruncator()
        from agentforge.tools.search_notes import (
            SearchHit,
            SearchNotesPayload,
        )

        notes = _notes_result(count=10)
        search = ToolResult[SearchNotesPayload](
            metadata=_meta("search_notes"),
            payload=SearchNotesPayload(
                results=tuple(
                    SearchHit(
                        id=i,
                        source="pnote",
                        date=f"2026-01-{(i % 28) + 1:02d}",
                        title=f"Hit {i}",
                        snippet="snip " * 25,
                    )
                    for i in range(15)
                ),
            ),
        )
        results: dict[str, ToolResult] = {
            "get_recent_notes": notes,
            "search_notes": search,
        }
        # Cap roomy enough for full notes + a couple of search hits
        notes_full = truncator.count_tokens(notes.payload.model_dump_json())
        out = truncator.truncate(results, max_tokens=notes_full + 100)
        # notes either intact or only minorly shrunk; search aggressively shrunk
        if "search_notes" in out:
            assert len(out["search_notes"].payload.results) <= 3
        if "get_recent_notes" in out:
            # at least 8 of the 10 notes should remain (only minor shrink)
            assert len(out["get_recent_notes"].payload.notes) >= 8

    def test_emptied_list_drops_the_tool_entirely(self) -> None:
        """When a tool has been shrunk to zero items, drop the whole entry."""
        truncator = SynthesisInputTruncator()
        results: dict[str, ToolResult] = {
            "get_demographics": _demographics_result(),
            "get_recent_notes": _notes_result(count=10),
        }
        demo_size = truncator.count_tokens(
            results["get_demographics"].payload.model_dump_json()
        )
        # Cap fits demographics only — notes can't keep even one item
        out = truncator.truncate(results, max_tokens=demo_size + 1)
        assert "get_recent_notes" not in out


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
