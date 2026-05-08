"""Tests for the eval-gate CI entry point (Task 20.1).

The CLI wires together the runner, scoring, gate verdict, and diff
reporter into a single invocation that CI can call with one shell
command. Defaults are CI-safe:

  * ``--mock-supervisor`` (default true) — a synthetic supervisor that
    fabricates a passing :class:`SupervisorOutput` for each case. No
    Anthropic spend on every MR.
  * ``--mock-judge`` (default true) — an :class:`AsyncMock` LLMJudge
    that returns PASS verdicts. No judge spend either.

Real-LLM mode (both flags off) is reserved for the manual baseline
regen path and the optional gated CI job; this test module covers the
default mocked path.

Tests target three things:

  * Exit code 0 on a passing run with the stub baseline.
  * Exit code 1 when a forced regression (current < baseline by more
    than the regression threshold) trips the gate.
  * Side-effect files: the CLI writes both the JSON results file and
    the markdown report to the paths the caller supplied.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# This file lives at sidecar/tests/eval/gate/test_cli.py — three parents
# up is the sidecar root, which is the cwd python -m expects.
_SIDECAR_DIR = Path(__file__).resolve().parents[3]


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Invoke ``python -m tests.eval.gate.cli`` from the sidecar dir.

    Subprocess so we exercise the same module-entry path the CI shell
    wrapper uses, and so a ``sys.exit`` in the entry point is observable
    via the returncode rather than via :class:`SystemExit`.
    """
    return subprocess.run(
        [sys.executable, "-m", "tests.eval.gate.cli", *args],
        cwd=_SIDECAR_DIR,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.gate_validation
class TestCliExitCodes:
    def test_passing_run_exits_zero(self, tmp_path: Path) -> None:
        results = tmp_path / "results.json"
        report = tmp_path / "report.md"
        completed = _run_cli([
            "--results", str(results),
            "--report", str(report),
        ])
        assert completed.returncode == 0, (
            f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
        )
        assert results.is_file()
        assert report.is_file()

    def test_forced_regression_exits_one(self, tmp_path: Path) -> None:
        # Synthesise a baseline pinned higher than the mocked supervisor
        # can clear, so the regression check trips deterministically.
        baseline = tmp_path / "baseline.json"
        baseline.write_text(
            json.dumps(
                {
                    "extraction": 1.0,
                    "evidence_retrieval": 1.0,
                    "citations": 1.0,
                    "refusal": 1.0,
                    "missing_data": 1.0,
                    "_force_regression": True,
                }
            ),
            encoding="utf-8",
        )
        # Override one current rate well below baseline so the
        # regression threshold (0.05) is exceeded — we use the
        # ``--inject-failure`` knob so the test doesn't depend on
        # supervisor-output mutation tricks.
        results = tmp_path / "results.json"
        report = tmp_path / "report.md"
        completed = _run_cli([
            "--baseline", str(baseline),
            "--results", str(results),
            "--report", str(report),
            "--inject-failure", "extraction",
        ])
        assert completed.returncode == 1, (
            f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
        )
        assert results.is_file()
        payload = json.loads(results.read_text(encoding="utf-8"))
        assert payload["passed"] is False
        assert any(
            v["category"] == "extraction" for v in payload["violations"]
        )

    def test_report_renders_markdown_heading(self, tmp_path: Path) -> None:
        results = tmp_path / "results.json"
        report = tmp_path / "report.md"
        completed = _run_cli([
            "--results", str(results),
            "--report", str(report),
        ])
        assert completed.returncode == 0
        body = report.read_text(encoding="utf-8")
        # First line is the markdown H1 from diff_report.render_diff_report.
        assert body.startswith("# Eval Gate Report"), body[:80]
        assert "Per-category pass rates" in body
