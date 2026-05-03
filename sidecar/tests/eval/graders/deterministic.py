"""Deterministic grader for eval cases (week1-gaps Task #18).

Three checks that require no LLM:
  * Citation grounding — every citation in the response resolves to a
    real record in the per-turn tool_results index.
  * Required terms present — all strings in ``EvalCase.expected_terms``
    appear in the response (case-insensitive).
  * Forbidden terms absent — no string in ``EvalCase.forbidden_terms``
    appears in the response (case-insensitive).

A case passes only when all three checks pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentforge.tools.dtos import ToolResult
from agentforge.verifier.cache import build_citation_index
from agentforge.verifier.citation import Citation, find_citations
from tests.eval.harness import EvalCase


@dataclass(frozen=True)
class GradeResult:
    """Outcome of one DeterministicGrader.grade() call."""

    grounded: bool
    required_terms_present: bool
    forbidden_terms_absent: bool
    ungrounded_citations: tuple[Citation, ...]
    missing_terms: tuple[str, ...]
    present_forbidden: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return (
            self.grounded
            and self.required_terms_present
            and self.forbidden_terms_absent
        )


class DeterministicGrader:
    """Grade a response against an EvalCase without calling an LLM."""

    def grade(
        self,
        response: str,
        case: EvalCase,
        tool_results: dict[str, ToolResult[Any]],
    ) -> GradeResult:
        index = build_citation_index(tool_results)
        citations = find_citations(response)
        ungrounded = tuple(
            c for c in citations
            if not index.contains(c.record_type, c.record_id)
        )

        response_lower = response.lower()
        missing = tuple(
            t for t in case.expected_terms
            if t.lower() not in response_lower
        )
        present_forbidden = tuple(
            t for t in case.forbidden_terms
            if t.lower() in response_lower
        )

        return GradeResult(
            grounded=len(ungrounded) == 0,
            required_terms_present=len(missing) == 0,
            forbidden_terms_absent=len(present_forbidden) == 0,
            ungrounded_citations=ungrounded,
            missing_terms=missing,
            present_forbidden=present_forbidden,
        )
