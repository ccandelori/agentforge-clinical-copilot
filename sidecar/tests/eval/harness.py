"""Programmatic eval harness for agent responses.

Two contracts the harness verifies for a given (response, EvalCase):

  * **Grounding** — every citation in the response must resolve to a
    real record_id in the patient's tool-fixture set. Reuses
    :func:`build_citation_index` so the harness checks against the
    same record vocabulary the production :class:`StreamingVerifier`
    uses; what the eval calls "grounded" is what the verifier would
    let through unredacted.

  * **Behavior** — an optional case-specific callable that asserts
    properties the case's adversarial category demands (e.g. "the
    response says 'permission denied' for a CFR42 note", "the response
    refuses to invent labs the patient doesn't have").

The :class:`EvalHarness` is deliberately *not* an agent runner — it
takes the agent's already-produced ``response`` text and inspects it.
Tests assemble the runner separately (mocked LLM + MockToolLayer for
deterministic CI; real LLM for opt-in manual eval).

LLM-as-judge for relevance scoring is intentionally deferred:
spec-tier feature, expensive in CI, flaky. Logged as a deviation.

See ARCHITECTURE.md §8 (eval) and the verifier's citation cache for
the underlying record-existence check.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agentforge.tools.dtos import ToolResult
from agentforge.verifier.cache import build_citation_index
from agentforge.verifier.citation import Citation, find_citations


class EvalCategory(StrEnum):
    """Adversarial / behavioral categories from ARCHITECTURE.md §8.

    The W1 set (PROMPT_INJECTION through AMBIGUOUS) covers the agent's
    text-only orchestration over OpenEMR records. The W2 set
    (EXTRACTION through REFUSAL) extends coverage to the multimodal
    document pipeline introduced in W2: vision-extraction over scanned
    intake forms / lab PDFs, hybrid retrieval over the guideline
    corpus, and citation contracts that distinguish chart facts from
    guideline evidence. See W2_ARCHITECTURE.md §8.
    """

    PROMPT_INJECTION = "prompt_injection"
    AUTH_BOUNDARY = "auth_boundary"
    HALLUCINATION = "hallucination"
    MISSING_DATA = "missing_data"
    CONFLICTING_DATA = "conflicting_data"
    HAPPY_PATH = "happy_path"
    AMBIGUOUS = "ambiguous"
    EXTRACTION = "extraction"
    EVIDENCE_RETRIEVAL = "evidence_retrieval"
    CITATIONS = "citations"
    REFUSAL = "refusal"


@dataclass(frozen=True)
class EvalCase:
    """One eval scenario.

    ``grounding_check`` is an optional callable that gets the response
    text and returns True/False. Use it for behavioral assertions the
    grounding-only check can't express (e.g. "no PHI in the response
    when the user lacks clearance").
    """

    id: str
    category: EvalCategory
    patient_id: int
    query: str
    expected_behavior: str
    expected_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    grounding_check: Callable[[str], bool] | None = None


@dataclass(frozen=True)
class EvalResult:
    """Outcome of running one EvalCase against a response."""

    case_id: str
    grounded: bool
    grounding_failures: tuple[Citation, ...]
    behavior_pass: bool
    citations_found: int

    @property
    def passed(self) -> bool:
        """A case passes when *all* programmatic checks pass."""
        return self.grounded and self.behavior_pass


@dataclass(frozen=True)
class EvalSummary:
    """Aggregate report over multiple EvalResults."""

    total: int
    passed: int
    failed: int
    by_category: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def pass_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.passed / self.total


class EvalHarness:
    """Verifies (response, case) pairs against tool-fixture grounding.

    Stateless beyond the tool-results payload it takes per evaluation —
    the harness is reusable across many cases.
    """

    def evaluate(
        self,
        response: str,
        case: EvalCase,
        tool_results: dict[str, ToolResult[Any]],
    ) -> EvalResult:
        """Run all programmatic checks for ``case`` against ``response``.

        ``tool_results`` is the per-turn dict the orchestrator collects
        before responding — same shape :class:`StreamingVerifier`
        consumes. The mock-tool layer or a fake fetcher harness builds
        this for the eval test.
        """
        index = build_citation_index(tool_results)
        citations = find_citations(response)
        unresolved = tuple(
            c for c in citations if not index.contains(c.record_type, c.record_id)
        )
        grounded = len(unresolved) == 0

        behavior_pass = (
            True if case.grounding_check is None else case.grounding_check(response)
        )

        return EvalResult(
            case_id=case.id,
            grounded=grounded,
            grounding_failures=unresolved,
            behavior_pass=behavior_pass,
            citations_found=len(citations),
        )

    @staticmethod
    def summarize(results: list[EvalResult], cases: list[EvalCase]) -> EvalSummary:
        """Aggregate results into pass/fail counts, grouped by category.

        ``cases`` is a parallel list — used to look up each result's
        category for the breakdown. Mismatched lengths raise so the
        caller doesn't accidentally summarize against the wrong cases.
        """
        if len(results) != len(cases):
            raise ValueError(
                f"results / cases length mismatch: {len(results)} vs {len(cases)}"
            )
        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed
        by_cat: dict[str, dict[str, int]] = {}
        for case, result in zip(cases, results, strict=True):
            bucket = by_cat.setdefault(
                case.category.value, {"passed": 0, "failed": 0}
            )
            if result.passed:
                bucket["passed"] += 1
            else:
                bucket["failed"] += 1
        return EvalSummary(
            total=len(results),
            passed=passed,
            failed=failed,
            by_category=by_cat,
        )
