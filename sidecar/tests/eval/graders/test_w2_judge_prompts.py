"""Tests that the W2 LLM-judge prompts are registered in the prompt library.

Task 17.1 wires two judge prompts into the same versioned prompt library
the orchestrator already uses (``prompts/v1/<component>.md`` pinned via
``prompts/version.json``). We round-trip through ``load_prompt`` rather
than asserting on raw file contents so the test catches both bad files
*and* a missing ``version.json`` registration.
"""

from __future__ import annotations

import pytest

from agentforge.prompts import load_prompt


@pytest.fixture(autouse=True)
def _clear_prompt_cache() -> None:
    load_prompt.cache_clear()
    yield
    load_prompt.cache_clear()


class TestW2JudgePromptsRegistered:
    def test_factually_consistent_prompt_loads(self) -> None:
        body = load_prompt("judge_factually_consistent")
        assert body, "factually_consistent prompt body must be non-empty"

    def test_safe_refusal_prompt_loads(self) -> None:
        body = load_prompt("judge_safe_refusal")
        assert body, "safe_refusal prompt body must be non-empty"

    def test_factually_consistent_prompt_demands_pass_fail_verdict(self) -> None:
        body = load_prompt("judge_factually_consistent")
        # Judges must emit a structured PASS / FAIL token so the parser
        # is deterministic; free-text "yes" / "no" creates ambiguity.
        assert "PASS" in body
        assert "FAIL" in body

    def test_safe_refusal_prompt_demands_pass_fail_verdict(self) -> None:
        body = load_prompt("judge_safe_refusal")
        assert "PASS" in body
        assert "FAIL" in body

    def test_factually_consistent_prompt_references_source_documents(self) -> None:
        body = load_prompt("judge_factually_consistent").lower()
        # The grounding contract: every claim must trace to a source doc.
        # Surface the word so a future edit that drops the contract trips
        # the test.
        assert "source" in body

    def test_safe_refusal_prompt_references_refusal(self) -> None:
        body = load_prompt("judge_safe_refusal").lower()
        assert "refus" in body
