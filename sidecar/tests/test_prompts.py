"""Loader contract + content regression for the prompt library (Task 43).

These tests pin two things:

1. The loader resolves ``prompts/<version>/<component>.md`` correctly,
   strips the YAML frontmatter, caches the result, and surfaces a
   typed error (not a generic ``FileNotFoundError``) on misuse.
2. The migrated v1 prompts still contain the canonical content from
   the Task 51.3 ruleset — every concrete tool name, every section
   header, every use case. If a future prompt edit drops one of these
   markers, the regression-locks would catch the symptom; this test
   catches the cause.
"""

from __future__ import annotations

import pytest

from agentforge.prompts import PromptNotFoundError, load_prompt

# All eleven tools the synthesizer prompt advertises. Sourced from the
# orchestrator catalog — if a tool is added to the catalog, the prompt
# must teach the model about it AND this list must grow.
_EXPECTED_TOOL_NAMES = (
    "get_demographics",
    "get_active_problems",
    "get_active_medications",
    "get_active_allergies",
    "get_recent_labs",
    "get_vitals_trend",
    "get_recent_notes",
    "search_notes",
    "get_recent_encounters",
    "get_immunizations",
    "get_procedures",
)


# Canonical section headers from Task 51.3. Pinning all of them keeps
# the prompt aligned with the regression-locks file
# (test_regression_lock pins these in the response shape).
_EXPECTED_SECTION_HEADERS = (
    "## Active Problems",
    "## Active Medications",
    "## Allergies",
    "## Recent Labs",
    "## Recent Vitals",
    "## Recent Encounters",
    "## Notes",
    "## Immunizations",
    "## Recent Procedures",
)


_EXPECTED_USE_CASES = (
    "admit_synthesis",
    "contraindication",
    "delta_computation",
    "followup",
)


@pytest.fixture(autouse=True)
def _clear_prompt_cache() -> None:
    """Reset the loader cache between tests so cache-aware assertions
    start from a clean slate. The loader uses functools.cache, which
    is process-wide; without this, the first test's miss becomes every
    subsequent test's hit and ``cache_info()`` checks become brittle.
    """
    load_prompt.cache_clear()


class TestSynthesizerPrompt:
    def test_returns_non_empty_string(self) -> None:
        body = load_prompt("synthesizer")
        assert isinstance(body, str)
        assert body  # non-empty

    def test_does_not_leak_frontmatter(self) -> None:
        body = load_prompt("synthesizer")
        # Frontmatter must be stripped before the prompt reaches the
        # LLM — leaking the YAML block would inject "version: ..." into
        # the system prompt and confuse the model.
        assert not body.startswith("---")
        assert "last_modified:" not in body

    @pytest.mark.parametrize("tool_name", _EXPECTED_TOOL_NAMES)
    def test_mentions_every_tool(self, tool_name: str) -> None:
        body = load_prompt("synthesizer")
        assert tool_name in body, f"synthesizer prompt missing tool: {tool_name}"

    @pytest.mark.parametrize("header", _EXPECTED_SECTION_HEADERS)
    def test_pins_canonical_section_headers(self, header: str) -> None:
        body = load_prompt("synthesizer")
        assert header in body, (
            f"synthesizer prompt no longer pins canonical header: {header!r}. "
            f"This was set in Task 51.3 — see regression_locks.py."
        )

    def test_pins_out_of_scope_guardrail(self) -> None:
        # Task 51.4 — refusing to hedge with "in this version of the
        # co-pilot". The prompt must explicitly forbid that phrasing.
        body = load_prompt("synthesizer")
        assert "in this version of the" in body, (
            "Task 51.4 out-of-scope guardrail dropped from synthesizer prompt"
        )


class TestPlannerPrompt:
    def test_returns_non_empty_string(self) -> None:
        body = load_prompt("planner")
        assert isinstance(body, str)
        assert body

    def test_does_not_leak_frontmatter(self) -> None:
        body = load_prompt("planner")
        assert not body.startswith("---")
        assert "purpose:" not in body

    @pytest.mark.parametrize("use_case", _EXPECTED_USE_CASES)
    def test_mentions_every_use_case(self, use_case: str) -> None:
        body = load_prompt("planner")
        assert use_case in body, f"planner prompt missing use case: {use_case}"

    def test_instructs_submit_plan_tool_call(self) -> None:
        body = load_prompt("planner")
        # The Planner class consumes a submit_plan tool_use block. The
        # prompt must instruct the model to use it.
        assert "submit_plan" in body


class TestErrorContract:
    def test_unknown_component_raises_prompt_not_found(self) -> None:
        with pytest.raises(PromptNotFoundError) as excinfo:
            load_prompt("nonexistent_component")
        # Error message should at least reference the component name so
        # the cause is obvious from the traceback.
        assert "nonexistent_component" in str(excinfo.value)

    def test_unknown_component_is_not_generic_file_not_found(self) -> None:
        # Per the brief: callers should be able to distinguish
        # prompt-config problems from generic I/O.
        with pytest.raises(PromptNotFoundError):
            load_prompt("another_missing_one")
        # FileNotFoundError is OSError; PromptNotFoundError is LookupError.
        # Catching FileNotFoundError must NOT catch us.
        with pytest.raises(LookupError):
            load_prompt("yet_another_missing_one")


class TestCaching:
    def test_repeated_calls_are_cache_hits(self) -> None:
        # First call is a miss, populates the cache.
        load_prompt("synthesizer")
        info_after_first = load_prompt.cache_info()
        assert info_after_first.misses == 1
        assert info_after_first.hits == 0

        # Second call with the same component must come from cache.
        load_prompt("synthesizer")
        info_after_second = load_prompt.cache_info()
        assert info_after_second.misses == 1
        assert info_after_second.hits == 1

    def test_returns_same_object_for_same_component(self) -> None:
        # functools.cache returns the same str instance, not just an
        # equal one — useful when callers bind the result to
        # module-level constants.
        first = load_prompt("planner")
        second = load_prompt("planner")
        assert first is second


class TestRoundTripWithExportedConstants:
    """The orchestrator binds ``SYSTEM_PROMPT`` and the planner binds
    ``PLANNER_SYSTEM_PROMPT`` from ``load_prompt``. Pin that the values
    on those module-level constants are exactly what the loader
    returns — anyone who imports the constants gets the file content.
    """

    def test_synthesizer_constant_matches_loader(self) -> None:
        from agentforge.orchestrator import SYSTEM_PROMPT

        assert load_prompt("synthesizer") == SYSTEM_PROMPT

    def test_planner_constant_matches_loader(self) -> None:
        from agentforge.orchestrator.planner import PLANNER_SYSTEM_PROMPT

        assert load_prompt("planner") == PLANNER_SYSTEM_PROMPT
