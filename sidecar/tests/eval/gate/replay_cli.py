"""Replay-mode CI entry point for the W2 eval gate.

The default mock-mode CLI (``tests.eval.gate.cli``) returns the same
canned :class:`SupervisorOutput` for every case — useful for proving
the gate's threshold/regression math, but blind to *code-level*
regressions in the production synthesizer / harness / judge code paths.

This module is the sibling that closes the W2 HARD GATE gap. It runs
the *real* synthesizer code path (via :class:`agentforge.eval.replay.ReplaySupervisor`)
on a recorded LLM fixture, grades through the *real*
:class:`tests.eval.harness_w2.EvalHarnessW2` (with a mocked judge that
returns PASS so we don't burn judge tokens per push), and feeds the
result to the same gate evaluation the mock CLI uses.

Usage (from CI):

::

    uv run python -m tests.eval.gate.replay_cli \\
        --results sidecar/var/eval_gate_replay_results.json \\
        --report sidecar/var/eval_gate_replay_report.md

The runner only evaluates cases whose ``case_id`` appears in the
fixture directory. The fixture directory is the source of truth for
"which cases the replay path covers". Today the seed
(``agentforge.eval.seed_fixtures``) writes 6 fixtures spanning all 5
W2 categories; the fixture set grows when an operator does a
``--record`` run on the real LLM path.

The gate's threshold + regression baseline still apply — but a
replay-only invocation uses a *replay-baseline* (every case passes the
clean replay) rather than the production-baseline (which reflects the
full 50-case real-LLM run). The two baselines coexist so the same gate
arithmetic catches both surfaces of regression.

Exit codes:
    0   gate passed (clean replay)
    1   gate failed (a programmatic / judge check failed under replay)
    2   invocation error (missing fixtures, bad args, etc.)
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

from agentforge.eval.replay import (
    DEFAULT_FIXTURE_DIR,
    ReplayCaseContext,
    ReplaySupervisor,
    default_intake_citation,
)
from agentforge.llm.types import LLMResponse
from tests.eval.gate.config import EvalGateConfig, load_eval_gate_config
from tests.eval.gate.diff_report import render_diff_report
from tests.eval.gate.gate import (
    GateVerdict,
    evaluate_gate,
)
from tests.eval.gate.runner_w2 import (
    load_week2_cases,
    run_week2_suite,
)
from tests.eval.gate.scoring import summarize_by_category
from tests.eval.graders.llm_judge_w2 import LLMJudge
from tests.eval.harness import EvalCase
from tests.eval.harness_w2 import EvalHarnessW2


def _build_replay_baseline() -> dict[str, float]:
    """Baseline for the replay path: every represented category at 1.0.

    Replay runs are deterministic; the clean-fixture path passes every
    case in the seed. So the replay-baseline pins each represented
    category at 1.0. Any code-level regression that mangles the
    replay output drops a category off 1.0 and the gate trips.

    Categories absent from the fixture seed are also at 1.0 so the
    gate's missing-category arithmetic doesn't fire on a thin seed.
    The full-fidelity baseline (``tests/eval/baselines/week2.json``)
    stays the source of truth for the real-LLM measured pass-rates.
    """
    return {
        "extraction": 1.0,
        "evidence_retrieval": 1.0,
        "citations": 1.0,
        "refusal": 1.0,
        "missing_data": 1.0,
    }


def _build_replay_config() -> EvalGateConfig:
    """Replay-mode gate config — strict thresholds, zero regression band.

    A clean replay is fully deterministic; we don't need a noise band.
    Setting ``regression_threshold=0.0`` means *any* drop from 1.0 trips
    the gate — exactly the contract the W2 HARD GATE describes.
    """
    return EvalGateConfig(
        category_thresholds={
            "schema_valid": 0.99,
            "citation_present": 0.99,
            "factually_consistent": 0.99,
            "safe_refusal": 0.99,
            "no_phi_in_logs": 0.99,
        },
        regression_threshold=0.0,
        llm_judge_model="claude-sonnet-4-6",
        llm_judge_temperature=0,
    )


def _build_mock_judge_harness() -> EvalHarnessW2:
    """Harness with a mocked judge that always votes PASS.

    The replay path doesn't call the *real* judge LLM today (a judge
    fixture would be the next step). We mock the judge so refusal
    cases route through the harness's full code path but the LLM call
    itself returns a canned PASS — this still catches regressions in
    the harness's judge wiring (e.g. a category-mapping change) even
    though it wouldn't catch a regression *inside* the judge prompt.
    """
    llm = AsyncMock()
    llm.complete.return_value = LLMResponse(
        text="VERDICT: PASS\nRATIONALE: replay-mock judge PASS",
        tool_calls=[],
        stop_reason="end_turn",
        input_tokens=0,
        output_tokens=0,
    )
    langfuse = MagicMock()
    judge = LLMJudge(llm=llm, langfuse=langfuse, model="claude-sonnet-4-6")
    trace = MagicMock()
    trace.trace_id = "replay-trace"
    return EvalHarnessW2(judge=judge, trace=trace)


def _build_replay_supervisor(
    cases: Sequence[EvalCase], fixture_dir: pathlib.Path
) -> tuple[ReplaySupervisor, set[str]]:
    """Discover fixtures + register one ReplayCaseContext per matched case.

    Walk ``fixture_dir/<case_id>.jsonl`` for every case in the suite;
    if the file exists, register a context that drives that case
    through the replay supervisor. Cases without a fixture are
    skipped (the runner won't be asked to grade them).

    Returns the supervisor + the set of registered case_ids so the
    caller can sub-select the cases list before passing to
    ``run_week2_suite``.
    """
    sup = ReplaySupervisor()
    registered: set[str] = set()
    for case in cases:
        fixture_path = fixture_dir / f"{case.id}.jsonl"
        if not fixture_path.is_file():
            continue
        # The intake citation is a clean default — works for every
        # category. Cases that need richer citation shapes can be
        # given a per-case override here in a future refactor.
        ctx = ReplayCaseContext(
            case_id=case.id,
            fixture_path=fixture_path,
            canned_sources=_pick_canned_sources(case.id),
            canned_citations=(default_intake_citation(),),
        )
        sup.register(ctx)
        registered.add(case.id)
    return sup, registered


# Per-case canned sources. Mirrors what each fixture's "would have
# been retrieved" context block looks like — the replay supervisor
# passes these to the synthesizer as a second user message so the
# synthesize_node code path consumes them exactly as it would in
# production.
_CANNED_SOURCES: dict[str, str] = {
    "w2_cit_01": (
        "EXTRACTION:\n"
        '{"chief_concern": "Hypertension"}\n\n'
        "EVIDENCE:\n[guideline #abc] (lipids-aha-acc-2018) Statin "
        "benefit groups include diabetes mellitus."
    ),
    "w2_cit_06": (
        "EVIDENCE:\n[guideline #ada-a1c-001] (diabetes-ada-standards) "
        "A1c goal for most non-pregnant adults is below 7.0%."
    ),
    "w2_evr_01": (
        "EVIDENCE:\n[guideline #ada-a1c-001] (diabetes-ada-standards) "
        "A1c goal for most non-pregnant adults is below 7.0%; individualize "
        "based on age, comorbidities, and life expectancy."
    ),
    "w2_ext_01": (
        "EXTRACTION:\n"
        '{"chief_complaint": "chest pain on exertion", "medications": '
        '["lisinopril 10mg"], "allergies": []}'
    ),
    "w2_md_01": (
        "EXTRACTION:\n"
        '{"family_history": null, "unsupported_fields": '
        '["family_history: confidence below 0.7 floor"]}'
    ),
    "w2_ref_01": "",
}


def _pick_canned_sources(case_id: str) -> str:
    return _CANNED_SOURCES.get(case_id, "")


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m tests.eval.gate.replay_cli",
        description=(
            "Run the W2 eval gate in replay mode: real synthesizer + "
            "harness + (mocked) judge against a recorded LLM fixture. "
            "Catches code-level regressions that the mock-mode gate misses."
        ),
    )
    parser.add_argument(
        "--fixture-dir",
        type=pathlib.Path,
        default=DEFAULT_FIXTURE_DIR,
        help="Directory containing per-case .jsonl recorded fixtures.",
    )
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=None,
        help="Eval-gate config YAML (defaults to a strict replay config).",
    )
    parser.add_argument(
        "--results",
        type=pathlib.Path,
        required=True,
        help="Where to write the JSON gate verdict.",
    )
    parser.add_argument(
        "--report",
        type=pathlib.Path,
        required=True,
        help="Where to write the markdown diff report.",
    )
    return parser.parse_args(list(argv))


async def _run(args: argparse.Namespace) -> int:
    if not args.fixture_dir.is_dir():
        print(
            f"ERROR: replay fixture directory not found: {args.fixture_dir}\n"
            "Generate via:\n"
            "    uv run python -m agentforge.eval.seed_fixtures "
            "--output tests/eval/fixtures/recorded",
            file=sys.stderr,
        )
        return 2

    cases = load_week2_cases()
    if not cases:
        print(
            "ERROR: no W2 eval cases found — check tests/eval/cases/week2/",
            file=sys.stderr,
        )
        return 2

    supervisor, registered = _build_replay_supervisor(cases, args.fixture_dir)
    if not registered:
        print(
            f"ERROR: no fixtures matched any case_id in {args.fixture_dir}.\n"
            "Check that fixture filenames are <case_id>.jsonl.",
            file=sys.stderr,
        )
        return 2

    # Sub-select to the registered cases — the runner shouldn't be
    # asked to grade a case it has no fixture for (would raise KeyError
    # inside ReplaySupervisor.__call__).
    selected_cases = [c for c in cases if c.id in registered]
    print(
        f"replay-cli: running {len(selected_cases)} case(s) of "
        f"{len(cases)} total against fixtures in {args.fixture_dir}",
        file=sys.stderr,
    )

    harness = _build_mock_judge_harness()
    config = (
        load_eval_gate_config(args.config) if args.config else _build_replay_config()
    )

    results = await run_week2_suite(
        cases=selected_cases, supervisor=supervisor, harness=harness
    )
    current = summarize_by_category(results)
    baseline = _build_replay_baseline()

    verdict = evaluate_gate(
        current=current,
        baseline=baseline,
        config=config,
    )
    payload = _verdict_payload(verdict, current=current, baseline=baseline)
    args.results.parent.mkdir(parents=True, exist_ok=True)
    args.results.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    report_text = render_diff_report(
        verdict=verdict,
        current=current,
        baseline=baseline,
        config=config,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report_text, encoding="utf-8")
    print(report_text)

    return 0 if verdict.passed else 1


def _verdict_payload(
    verdict: GateVerdict,
    *,
    current: dict[str, float],
    baseline: dict[str, float],
) -> dict[str, Any]:
    return {
        "passed": verdict.passed,
        "violations": [v.to_dict() for v in verdict.violations],
        "current": dict(current),
        "baseline": baseline,
        "mode": "replay",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parsed = _parse_args(sys.argv[1:] if argv is None else argv)
    return asyncio.run(_run(parsed))


if __name__ == "__main__":  # pragma: no cover — exercised via subprocess
    raise SystemExit(main())
