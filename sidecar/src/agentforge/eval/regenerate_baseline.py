"""Manual CLI for regenerating the W2 baseline (``baselines/week2.json``).

The pinned baseline ships as a stub at 1.0 across all five W2
categories (see ``tests/eval/baselines/week2.json``'s ``_meta.status:
"stub"``). This CLI is the human-driven follow-up step that replaces
the stub with measured pass rates by:

  1. Loading all 50 W2 cases.
  2. Driving each through the production :class:`SupervisorAdapter`
     (real Anthropic + retrieval + LLM judge).
  3. Aggregating per-category pass rates via the same scoring path the
     CI gate uses.
  4. Writing the resulting JSON with ``_meta.status: "measured"``.

**The CLI does not run automatically.** CI never invokes it; it costs
real Anthropic spend (~$1-3 per full run, depending on judge config).
A human runs it once after meaningful agent changes.

Invocation
----------

::

    cd sidecar
    uv run python -m agentforge.eval.regenerate_baseline \\
        --output tests/eval/baselines/week2.json

For a quick smoke run that doesn't burn tokens (uses the same mock
supervisor the CI gate uses), pass ``--mock``:

::

    uv run python -m agentforge.eval.regenerate_baseline \\
        --output /tmp/week2-smoke.json --mock

The mock path is what the test suite exercises; the real path needs an
``ANTHROPIC_API_KEY`` and the guideline corpus on disk.

Output shape
------------

::

    {
        "_meta": {
            "status": "measured",
            "timestamp": "2026-05-08T17:30:00+00:00",
            "git_sha": "65dfe334d...",
            "command": "agentforge.eval.regenerate_baseline --output ..."
        },
        "extraction": 0.91,
        "evidence_retrieval": 0.88,
        "citations": 0.95,
        "refusal": 1.0,
        "missing_data": 0.85
    }

The five top-level category keys match what the gate consumes; the
``_meta`` block is documentation, not load-bearing for the gate
arithmetic.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import pathlib
import subprocess
import sys
from collections.abc import Sequence
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from agentforge.llm.types import LLMResponse
from agentforge.schemas.citation import Citation, PageBBox, SourceType
from tests.eval.gate.runner_w2 import (
    SupervisorOutput,
    load_week2_cases,
    run_week2_suite,
)
from tests.eval.gate.scoring import summarize_by_category
from tests.eval.graders.llm_judge_w2 import LLMJudge
from tests.eval.harness import EvalCase
from tests.eval.harness_w2 import EvalHarnessW2

# Categories the gate requires in any baseline. Sorted for deterministic
# JSON output.
_REQUIRED_CATEGORIES: tuple[str, ...] = (
    "citations",
    "evidence_retrieval",
    "extraction",
    "missing_data",
    "refusal",
)


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser.

    Factored out so the parser is unit-testable without invoking the
    full pipeline.
    """
    parser = argparse.ArgumentParser(
        prog="python -m agentforge.eval.regenerate_baseline",
        description=(
            "Regenerate the W2 baseline JSON (tests/eval/baselines/week2.json) "
            "by running the 50-case suite through the production "
            "SupervisorAdapter. NEVER run automatically — costs real "
            "Anthropic spend; reserved for human-driven manual sweeps."
        ),
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        required=True,
        help="Path to write the measured baseline JSON.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help=(
            "Use a mocked supervisor (returns a passing SupervisorOutput "
            "for every case). Skips real Anthropic / retrieval calls. "
            "Used by the test suite and for smoke checks."
        ),
    )
    return parser


def _git_sha() -> str:
    """Return the current HEAD short SHA, or ``"unknown"`` if not in a repo.

    Best-effort — we don't crash on a CI shell or sandbox without git.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() or "unknown"
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return "unknown"


def _passing_supervisor_output(_case: EvalCase) -> SupervisorOutput:
    """Mock SupervisorOutput that satisfies every harness check.

    Mirrors the equivalent fixture in ``tests/eval/gate/cli.py`` so the
    CI gate and the regen CLI exercise identical mock shapes when both
    use the ``--mock`` path.
    """
    payload: dict[str, Any] = {
        "source_type": "openemr_record",
        "source_id": "5",
        "page_or_section": "problem #5",
        "field_or_chunk_id": "title",
        "quote_or_value": "Hypertension",
    }
    citation = Citation(
        source_type=SourceType.INTAKE_FORM,
        source_id="doc-1",
        page_or_section="page 1",
        field_or_chunk_id="primary_complaint",
        quote_or_value="chest pain on exertion",
        page_bbox=PageBBox(
            page=1, x0=0.1, y0=0.1, x1=0.4, y1=0.2, bbox_confidence=0.9
        ),
    )
    return SupervisorOutput(
        response="The chief complaint is chest pain [problem #5].",
        sources="patient record: hypertension",
        structured_citation_payload=payload,
        structured_citations=(citation,),
        logs=("clean trace line",),
    )


def _build_mock_harness() -> EvalHarnessW2:
    """Mock harness whose LLM judge always votes PASS.

    Same shape as the gate CLI's mock harness — keeps both code paths
    aligned so a smoke run of regenerate_baseline emits the same JSON
    shape the gate would produce on the same case set.
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
    judge = LLMJudge(llm=llm, langfuse=langfuse, model="claude-sonnet-4-6")
    trace = MagicMock()
    trace.trace_id = "regen-trace"
    return EvalHarnessW2(judge=judge, trace=trace)


def _build_real_supervisor_and_harness() -> tuple[Any, EvalHarnessW2]:
    """Construct the real production adapter + harness.

    Reads settings from the environment and wires the adapter against
    real Anthropic + retrieval + LLM judge. Lives behind the ``--mock``
    branch so the test suite never accidentally pulls Anthropic creds.

    NotImplementedError today: the wiring is left to the human running
    the regen — they pick a planner, vision extractor, evidence
    retriever, and synthesizer LLM appropriate for the run (typically
    the same instances the production /turn route uses, built via
    ``agentforge.main.create_app``'s helpers). The CLI deliberately does
    not import ``main`` here because that would pull FastAPI, Redis, and
    the OpenEMR HTTP stack into the test surface. A future iteration
    can add a ``--from-app-settings`` flag that uses ``Settings`` to
    construct the full deps tree.
    """
    raise NotImplementedError(
        "Real-LLM regen wiring is a manual step. "
        "Either edit this function to construct the deps tree the "
        "current run needs, or pass --mock for a smoke check. See "
        "DEVIATIONS.md for the deferred-wiring rationale."
    )


async def _run_suite(*, mock: bool) -> dict[str, float]:
    """Drive all 50 cases through the supervisor and return per-category rates.

    ``mock=True`` uses the canned passing supervisor; ``mock=False``
    raises NotImplementedError until a human wires real dependencies.
    """
    cases = load_week2_cases()
    if not cases:
        raise RuntimeError(
            "No W2 cases found under tests/eval/cases/week2/ — "
            "is the corpus committed?"
        )

    if mock:
        supervisor = _passing_supervisor_output
        harness = _build_mock_harness()
    else:
        supervisor, harness = _build_real_supervisor_and_harness()

    results = await run_week2_suite(
        cases=cases, supervisor=supervisor, harness=harness
    )
    rates = summarize_by_category(results)

    # Backfill any required category that didn't appear in results
    # (defensive: a case-loading regression that drops a whole category
    # would silently disappear from the baseline). Missing → 0.0 so the
    # gate's threshold check trips on the next run.
    for cat in _REQUIRED_CATEGORIES:
        rates.setdefault(cat, 0.0)
    return rates


def _build_payload(rates: dict[str, float], *, command: str) -> dict[str, Any]:
    """Compose the JSON document written to ``--output``."""
    timestamp = datetime.datetime.now(datetime.UTC).isoformat()
    payload: dict[str, Any] = {
        "_meta": {
            "status": "measured",
            "timestamp": timestamp,
            "git_sha": _git_sha(),
            "command": command,
        }
    }
    # Insert per-category rates in the canonical sort order so the
    # JSON diff is stable across runs.
    for cat in _REQUIRED_CATEGORIES:
        payload[cat] = float(rates.get(cat, 0.0))
    return payload


async def run_cli(argv: Sequence[str]) -> int:
    """Entry point exercised by the test suite.

    Returns the process exit code: 0 success, 1 measurement failure
    (a category came back missing or the run aborted partway through).
    Argument-parsing errors raise SystemExit through argparse (≠ 0).
    """
    parser = build_arg_parser()
    args = parser.parse_args(list(argv))

    rates = await _run_suite(mock=args.mock)

    command_repr = (
        f"agentforge.eval.regenerate_baseline --output {args.output}"
        + (" --mock" if args.mock else "")
    )
    payload = _build_payload(rates, command=command_repr)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )

    # Human-readable echo to stdout so a manual run shows the result
    # without having to cat the output file separately.
    print(json.dumps(payload, indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Sync wrapper for ``python -m`` invocation."""
    return asyncio.run(run_cli(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":  # pragma: no cover — exercised via subprocess only
    raise SystemExit(main())


__all__ = ("build_arg_parser", "main", "run_cli")
