"""EvalRunner — invokes Orchestrator.turn() directly for CI eval.

Bridges the EvalHarness (response grader) and the Orchestrator (agent
runtime) without going through HTTP. Tests wire a fully-constructed
Orchestrator with mocked fetchers; this runner drives it the same way
the /turn endpoint does — one RequestContext + one user message — and
returns a graded EvalResult.

This makes the eval suite runnable in CI without a live sidecar or a
real LLM: supply an Orchestrator whose LLMClient is an AsyncMock and
whose fetchers are also mocked, and the grader gets a deterministic
response to inspect. Real-LLM evals remain in tests/eval/baseline/
and are gated behind @pytest.mark.eval.

See ARCHITECTURE.md §8 and harness.py for the EvalHarness contract.
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from typing import Any

from agentforge.gateway.auth_gateway import RequestContext
from agentforge.orchestrator import Orchestrator
from agentforge.tools.dtos import ToolResult

from tests.eval.harness import EvalCase, EvalHarness, EvalResult, EvalSummary


class EvalRunner:
    """Drives Orchestrator.turn() with a prebuilt EvalCase and grades the result.

    Stateless beyond the injected orchestrator — safe to reuse across
    many cases in a suite run.
    """

    def __init__(self, orchestrator: Orchestrator) -> None:
        self._orchestrator = orchestrator

    async def run_case(
        self,
        case: EvalCase,
        tool_results: dict[str, ToolResult[Any]],
    ) -> EvalResult:
        """Run one eval case and return a graded EvalResult.

        Builds a RequestContext with fixed harness credentials so tests
        only need to supply the patient_id from the case — not the full
        auth payload. Then calls Orchestrator.turn() and evaluates the
        response with EvalHarness.
        """
        ctx = RequestContext(
            user_id=1,
            patient_id=case.patient_id,
            username="eval-harness",
            role="clinician",
            breakglass_flag=False,
            breakglass_reason=None,
            sensitivity_clearances=frozenset(),
            raw_token="eval-token",
        )
        response = await self._orchestrator.turn(ctx, case.query)
        return EvalHarness().evaluate(response, case, tool_results)

    async def run_suite(
        self,
        cases: list[tuple[EvalCase, dict[str, ToolResult[Any]]]],
    ) -> EvalSummary:
        """Run all cases in sequence and return an aggregate EvalSummary.

        Cases run sequentially (not concurrently) so each turn sees a
        clean orchestrator state — no shared per-turn ContextVar
        cross-contamination from concurrent asyncio tasks.
        """
        results: list[EvalResult] = []
        for case, tool_results in cases:
            result = await self.run_case(case, tool_results)
            results.append(result)
        eval_cases = [c for c, _ in cases]
        return EvalHarness.summarize(results, eval_cases)


def generate_report(results: list[EvalResult], cases: list[EvalCase]) -> str:
    """Build a markdown eval report from graded results.

    Groups cases by category, marks each PASS/FAIL, and appends a
    failure reason line for any case that did not pass.
    """
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed

    lines = [
        f"# Eval Report {datetime.date.today()}",
        "",
        "## Summary",
        "",
        f"- Total: {len(results)}",
        f"- Passed: {passed}",
        f"- Failed: {failed}",
        "",
        "## Results",
        "",
    ]

    by_category: dict[str, list[tuple[EvalCase, EvalResult]]] = defaultdict(list)
    for case, result in zip(cases, results):
        by_category[case.category.value].append((case, result))

    for category, items in by_category.items():
        lines.append(f"### {category}")
        lines.append("")
        for case, result in items:
            marker = "PASS" if result.passed else "FAIL"
            query_snippet = case.query[:60]
            lines.append(f"- [{marker}] **{case.id}**: {query_snippet}")
            if not result.passed:
                reason = _failure_reason(result)
                lines.append(f"  - Reason: {reason}")
        lines.append("")

    return "\n".join(lines)


def _failure_reason(result: EvalResult) -> str:
    parts: list[str] = []
    if not result.grounded:
        ids = ", ".join(
            f"{c.record_type}#{c.record_id}" for c in result.grounding_failures
        )
        parts.append(f"ungrounded citations: {ids}" if ids else "ungrounded citations")
    if not result.behavior_pass:
        parts.append("behavior check failed")
    return "; ".join(parts) if parts else "unknown"
