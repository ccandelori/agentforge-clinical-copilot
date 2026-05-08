"""CI entry point for the W2 eval gate (Task 20.1).

Wires the runner (Task 18.2), scoring (18.3), gate verdict (18.4), and
diff reporter (18.5) into a single ``python -m`` invocation. CI runs
this with mocks by default — no Anthropic spend on every MR.

Usage (CI default — fully mocked):
    uv run python -m tests.eval.gate.cli \\
        --results results.json --report report.md

Usage (override knobs):
    --baseline PATH        path to pinned baseline JSON
                           (default: tests/eval/baselines/week2.json)
    --config PATH          path to eval-gate config YAML
                           (default: sidecar/eval_config.yaml)
    --results PATH         where to write the JSON gate verdict
                           (required)
    --report PATH          where to write the markdown diff report
                           (required)
    --inject-failure CAT   make the mock supervisor return a
                           citation-empty response for cases in CAT
                           — used by the gate-validation tests to
                           force a regression deterministically.

Exit codes:
    0  gate passed
    1  gate failed (any violation)
    2  invocation error (bad args, missing config, etc.)

Real-LLM mode is intentionally **not** wired here — generating a
measured baseline is a follow-up that needs the production Supervisor
adapter (out of scope for Task 18). The mocks make the gate's exit
codes and report shape testable without burning tokens.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from collections.abc import Sequence
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from agentforge.llm.types import LLMResponse
from agentforge.schemas.citation import Citation, PageBBox, SourceType
from tests.eval.gate.config import load_eval_gate_config
from tests.eval.gate.diff_report import render_diff_report
from tests.eval.gate.gate import (
    BASELINE_PATH,
    evaluate_gate,
    load_baseline,
)
from tests.eval.gate.runner_w2 import (
    SupervisorOutput,
    load_week2_cases,
    run_week2_suite,
)
from tests.eval.gate.scoring import summarize_by_category
from tests.eval.graders.llm_judge_w2 import LLMJudge
from tests.eval.harness import EvalCase
from tests.eval.harness_w2 import EvalHarnessW2

_REQUIRED_CATEGORIES: tuple[str, ...] = (
    "extraction",
    "evidence_retrieval",
    "citations",
    "refusal",
    "missing_data",
)


def _passing_payload() -> dict[str, Any]:
    """Citation payload every programmatic check accepts."""
    return {
        "source_type": "openemr_record",
        "source_id": "5",
        "page_or_section": "problem #5",
        "field_or_chunk_id": "title",
        "quote_or_value": "Hypertension",
    }


def _passing_citation() -> Citation:
    return Citation(
        source_type=SourceType.INTAKE_FORM,
        source_id="doc-1",
        page_or_section="page 1",
        field_or_chunk_id="primary_complaint",
        quote_or_value="chest pain on exertion",
        page_bbox=PageBBox(
            page=1, x0=0.1, y0=0.1, x1=0.4, y1=0.2, bbox_confidence=0.9
        ),
    )


def _passing_supervisor_output() -> SupervisorOutput:
    return SupervisorOutput(
        response="The chief complaint is chest pain [problem #5].",
        sources="patient record: hypertension",
        structured_citation_payload=_passing_payload(),
        structured_citations=(_passing_citation(),),
        logs=("clean trace line",),
    )


def _failing_supervisor_output() -> SupervisorOutput:
    """Citation-empty response — programmatic citation_present trips."""
    return SupervisorOutput(
        response="No citation here.",
        sources="",
        structured_citation_payload=_passing_payload(),
        structured_citations=(),
        logs=(),
    )


def _build_mock_supervisor(
    inject_failure_for: frozenset[str],
):
    """Return a sync supervisor callable shaped like the runner expects.

    When ``inject_failure_for`` contains the case's category value, the
    supervisor returns a citation-empty response so the programmatic
    layer fails the case. Used by the gate-validation tests + by manual
    regression-drill runs.
    """
    def supervisor(case: EvalCase) -> SupervisorOutput:
        if case.category.value in inject_failure_for:
            return _failing_supervisor_output()
        return _passing_supervisor_output()

    return supervisor


def _build_mock_harness(model: str) -> EvalHarnessW2:
    """Build an :class:`EvalHarnessW2` with a stubbed LLM judge.

    The judge is the only LLM call inside the harness; mocking it makes
    the whole eval suite deterministic + free in CI. Trace handle is a
    :class:`MagicMock` since the gate never inspects it.
    """
    llm = AsyncMock()
    llm.complete.return_value = LLMResponse(
        text="VERDICT: PASS\nRATIONALE: stub",
        tool_calls=[],
        stop_reason="end_turn",
        input_tokens=0,
        output_tokens=0,
    )
    langfuse = MagicMock()
    judge = LLMJudge(llm=llm, langfuse=langfuse, model=model)
    trace = MagicMock()
    trace.trace_id = "ci-trace"
    return EvalHarnessW2(judge=judge, trace=trace)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m tests.eval.gate.cli",
        description=(
            "Run the W2 eval gate with a mocked supervisor + judge. "
            "Writes a JSON results file and a markdown diff report; "
            "exits non-zero on gate failure."
        ),
    )
    parser.add_argument(
        "--baseline",
        type=pathlib.Path,
        default=BASELINE_PATH,
        help="Pinned baseline JSON (default: tests/eval/baselines/week2.json)",
    )
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=None,
        help="Eval-gate config YAML (default: sidecar/eval_config.yaml)",
    )
    parser.add_argument(
        "--results",
        type=pathlib.Path,
        required=True,
        help="Where to write the JSON gate verdict",
    )
    parser.add_argument(
        "--report",
        type=pathlib.Path,
        required=True,
        help="Where to write the markdown diff report",
    )
    parser.add_argument(
        "--inject-failure",
        action="append",
        default=[],
        metavar="CATEGORY",
        help=(
            "Make the mock supervisor return a citation-empty response "
            "for cases in CATEGORY (test/regression-drill knob; can "
            "be passed multiple times)."
        ),
    )
    return parser.parse_args(list(argv))


async def _run(args: argparse.Namespace) -> int:
    config = load_eval_gate_config(args.config)
    cases = load_week2_cases()
    if not cases:
        print(
            "ERROR: no W2 eval cases found — check tests/eval/cases/week2/",
            file=sys.stderr,
        )
        return 2

    inject = frozenset(args.inject_failure)
    supervisor = _build_mock_supervisor(inject)
    harness = _build_mock_harness(model=config.llm_judge_model)

    results = await run_week2_suite(
        cases=cases, supervisor=supervisor, harness=harness
    )
    current = summarize_by_category(results)
    baseline = load_baseline(args.baseline)
    verdict = evaluate_gate(
        current=current,
        baseline=baseline,
        config=config,
        required_categories=_REQUIRED_CATEGORIES,
    )

    # Write the JSON verdict. Same shape as gate.run_gate_cli's output
    # so the diff reporter and any downstream parser see the same keys
    # whether the gate was driven from the CLI or from this entry point.
    payload = {
        "passed": verdict.passed,
        "violations": [v.to_dict() for v in verdict.violations],
        "current": dict(current),
        "baseline": baseline,
    }
    args.results.parent.mkdir(parents=True, exist_ok=True)
    args.results.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Render + write the markdown diff report.
    report_text = render_diff_report(
        verdict=verdict,
        current=current,
        baseline=baseline,
        config=config,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report_text, encoding="utf-8")

    # Mirror the report onto stdout so CI logs carry the table even
    # when the artifact upload fails.
    print(report_text)

    return 0 if verdict.passed else 1


def main(argv: Sequence[str] | None = None) -> int:
    parsed = _parse_args(sys.argv[1:] if argv is None else argv)
    return asyncio.run(_run(parsed))


if __name__ == "__main__":  # pragma: no cover — exercised via subprocess
    raise SystemExit(main())
