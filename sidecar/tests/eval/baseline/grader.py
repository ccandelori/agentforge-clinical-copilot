"""Deterministic grader for baseline eval cases.

Given a :class:`BaselineCase` and the response text from a turn, the
grader emits a :class:`CaseResult` describing every rule that did or
didn't pass. Failures are itemized so the operator can see, on the
spot, what regressed — rather than a generic "case failed" with no
context.

The grader is intentionally LENIENT on the LLM's phrasing and STRICT
on the safety properties. ``expected_terms`` triggers on any one
match; ``forbidden_terms`` fails on any single hit. That asymmetry is
load-bearing: a hallucinated drug name appearing once is a clinical
failure even if the rest of the response reads fine.

All checks are pure-text + citation-parser. No LLM calls, no
retrieval, no scoring against a graded rubric — that lives in the
larger Task #16 expansion.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentforge.verifier.citation import find_citations
from tests.eval.baseline.cases import BaselineCase


@dataclass(frozen=True)
class CaseResult:
    """Outcome of grading one case against one response.

    ``failures`` is empty for a passing case and contains a one-line
    description per failed rule otherwise. ``warnings`` is for soft
    signals that don't fail the case but inform the operator (e.g.
    "0 citations on a UC-1 query — usually expect 3+, but case allowed").
    """

    case_id: str
    passed: bool
    status_code: int
    response_chars: int
    citation_count: int
    citation_types: frozenset[str]
    failures: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def summary_line(self) -> str:
        flag = "PASS" if self.passed else "FAIL"
        return (
            f"[{flag}] {self.case_id} "
            f"status={self.status_code} chars={self.response_chars} "
            f"citations={self.citation_count}"
        )

    def detail_lines(self) -> tuple[str, ...]:
        lines: list[str] = []
        if self.failures:
            lines.append("  failures:")
            for f in self.failures:
                lines.append(f"    - {f}")
        if self.warnings:
            lines.append("  warnings:")
            for w in self.warnings:
                lines.append(f"    - {w}")
        return tuple(lines)


@dataclass
class _Builder:
    """Mutable accumulator used while we collect rule failures."""

    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _check_status(status_code: int, builder: _Builder) -> None:
    """A 503 means the sidecar is unreachable; the runner converts
    that into a skip. Any other non-200 is a real failure."""
    if status_code != 200:
        builder.failures.append(
            f"non-200 status: {status_code} (sidecar reachable but "
            "returned an error)"
        )


def _check_response_size(
    case: BaselineCase, response_chars: int, builder: _Builder
) -> None:
    if response_chars == 0:
        builder.failures.append("response body is empty")
        return
    if case.max_response_chars is not None and response_chars > case.max_response_chars:
        builder.failures.append(
            f"response is {response_chars} chars; case caps at "
            f"{case.max_response_chars} (LLM probably ignored the "
            "narrowing qualifier in the query)"
        )


def _check_expected_terms(
    case: BaselineCase, response_lower: str, builder: _Builder
) -> None:
    if not case.expected_terms:
        return
    if any(term in response_lower for term in case.expected_terms):
        return
    builder.failures.append(
        f"none of expected_terms appeared: {list(case.expected_terms)}"
    )


def _check_forbidden_terms(
    case: BaselineCase, response_lower: str, builder: _Builder
) -> None:
    hits = [term for term in case.forbidden_terms if term in response_lower]
    if hits:
        builder.failures.append(
            f"forbidden_terms appeared in response: {hits}"
        )


def _check_citations(
    case: BaselineCase, citation_count: int, citation_types: frozenset[str], builder: _Builder
) -> None:
    if citation_count < case.min_citations:
        builder.failures.append(
            f"citation count {citation_count} below required minimum "
            f"{case.min_citations}"
        )
    missing_types = [
        t for t in case.required_record_types if t not in citation_types
    ]
    if missing_types:
        builder.failures.append(
            f"required citation types missing: {missing_types} "
            f"(found types: {sorted(citation_types)})"
        )
    # Soft warnings — useful operator signal without case failure.
    if not case.required_record_types and citation_count == 0 and case.min_citations == 0:
        builder.warnings.append(
            "0 citations and 0 required — confirm this is an "
            "intended refusal/sparse case"
        )


def grade(
    case: BaselineCase,
    response_body: str,
    status_code: int,
) -> CaseResult:
    """Apply every rule on the case and return a :class:`CaseResult`.

    The caller is responsible for handling 503 separately (skip the
    case entirely rather than fail it). Everything else, including
    4xx and 5xx other than 503, is graded normally so the suite
    surfaces controller-side regressions too.
    """
    response_lower = response_body.lower()
    citations = find_citations(response_body)
    citation_types = frozenset(c.record_type for c in citations)

    builder = _Builder()

    _check_status(status_code, builder)
    _check_response_size(case, len(response_body), builder)
    if status_code == 200:
        # Term and citation checks only meaningful when we have a
        # real response body to inspect; a 4xx body usually carries
        # an error envelope, not synthesized text.
        _check_expected_terms(case, response_lower, builder)
        _check_forbidden_terms(case, response_lower, builder)
        _check_citations(case, len(citations), citation_types, builder)

    return CaseResult(
        case_id=case.id,
        passed=not builder.failures,
        status_code=status_code,
        response_chars=len(response_body),
        citation_count=len(citations),
        citation_types=citation_types,
        failures=tuple(builder.failures),
        warnings=tuple(builder.warnings),
    )


__all__ = ["CaseResult", "grade"]
