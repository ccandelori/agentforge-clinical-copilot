"""Replay-mode regression self-test (W2 HARD GATE end-to-end).

The mock-mode self-test (:mod:`tests.eval.gate.test_gate_blocks_regression`)
proves the gate's threshold + regression math fire when a fabricated
``SupervisorOutput`` arrives. It does NOT prove that a *code-level*
regression — a change to the synthesizer that drops citations from its
output — propagates through the production code path into a gate
failure. The mock supervisor never runs the synthesizer.

This test closes that gap. It runs the replay-mode gate
(:mod:`tests.eval.gate.replay_cli`) end-to-end with two configurations:

1. **Clean replay** — the recorded fixture's response goes through the
   real synthesizer + real harness untouched. The gate must pass.
2. **Citation-drop regression** — a ``response_transform`` strips
   inline ``[record_type #id]`` citation tokens from the synthesizer's
   output before the harness sees it. This is the production-equivalent
   of "someone edited the synthesizer to stop emitting citations".
   The gate must fail.

The two halves bracket the gate's contract: a passing run + a failing
run on the same fixture set proves the *code path* under test
distinguishes the two cases. If the regression test ever flips to
PASS, the gate has gone blind to the most-likely class of W2 grader
regression.

The test runs as a normal pytest entry (no special marker) so CI
exercises it on every push — it costs no LLM tokens (replay is fully
deterministic) and runs in well under a second.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentforge.eval.replay import (
    DEFAULT_FIXTURE_DIR,
    ReplayCaseContext,
    ReplaySupervisor,
    default_intake_citation,
)
from agentforge.llm.types import LLMResponse
from tests.eval.gate.gate import GateVerdict, ViolationKind, evaluate_gate
from tests.eval.gate.replay_cli import (
    _CANNED_SOURCES,
    _build_replay_baseline,
    _build_replay_config,
    _pick_canned_sources,
)
from tests.eval.gate.runner_w2 import load_week2_cases, run_week2_suite
from tests.eval.gate.scoring import summarize_by_category
from tests.eval.graders.llm_judge_w2 import LLMJudge
from tests.eval.harness_w2 import EvalHarnessW2


# Strips inline citation tokens like ``[problem #5]`` /
# ``[guideline #ada-a1c-001]``. Mirrors the W1 citation grammar in
# :mod:`agentforge.verifier.citation` — a synthesizer regression that
# stopped emitting citations would produce text matching this stripped
# shape, so we simulate that shape here directly.
_CITATION_TOKEN = re.compile(r"\s*\[[a-z_]+\s+#[A-Za-z0-9_-]+\]")


def _strip_citations(text: str) -> str:
    """Mutate the synthesizer's response to drop every citation tag."""
    return _CITATION_TOKEN.sub("", text)


def _drop_structured_citations(_existing: object) -> tuple:
    """Citation-collector regression: return an empty tuple.

    Models a bug in the supervisor adapter that stops harvesting
    Citations from the graph state — combined with ``_strip_citations``,
    this leaves the harness with neither inline tokens nor structured
    citations, which is what trips ``check_citation_present``.
    """
    return ()


def _build_mock_judge_harness() -> EvalHarnessW2:
    """Mirror replay_cli's mock judge — keeps the test in lockstep."""
    llm = AsyncMock()
    llm.complete.return_value = LLMResponse(
        text="VERDICT: PASS\nRATIONALE: replay-mock judge",
        tool_calls=[],
        stop_reason="end_turn",
        input_tokens=0,
        output_tokens=0,
    )
    langfuse = MagicMock()
    judge = LLMJudge(llm=llm, langfuse=langfuse, model="claude-sonnet-4-6")
    trace = MagicMock()
    trace.trace_id = "regression-test-trace"
    return EvalHarnessW2(judge=judge, trace=trace)


def _register_seed_contexts(supervisor: ReplaySupervisor) -> set[str]:
    """Wire the replay supervisor against the committed seed fixtures.

    Returns the set of registered case_ids. The fixture directory is
    the source of truth for "which cases are covered"; we walk every
    yaml case and register the ones that have a fixture file.
    """
    registered: set[str] = set()
    for case in load_week2_cases():
        fixture_path = DEFAULT_FIXTURE_DIR / f"{case.id}.jsonl"
        if not fixture_path.is_file():
            continue
        ctx = ReplayCaseContext(
            case_id=case.id,
            fixture_path=fixture_path,
            canned_sources=_pick_canned_sources(case.id),
            canned_citations=(default_intake_citation(),),
        )
        supervisor.register(ctx)
        registered.add(case.id)
    return registered


class TestReplayModeRegression:
    """End-to-end: replay gate must distinguish clean vs regressed code path.

    Both tests run sequentially through the real synthesizer + harness
    code paths against the same fixture set. The only difference is the
    ``response_transform`` injected on the supervisor — clean leaves
    output untouched, regressed strips citation tokens. The gate's
    verdict must flip between the two.
    """

    @pytest.fixture(autouse=True)
    def _ensure_fixtures_exist(self) -> None:
        """Skip with a useful message if the seed fixtures aren't on disk."""
        if not DEFAULT_FIXTURE_DIR.is_dir():
            pytest.skip(
                f"replay fixture dir missing: {DEFAULT_FIXTURE_DIR}\n"
                "Run: uv run python -m agentforge.eval.seed_fixtures "
                "--output tests/eval/fixtures/recorded"
            )

    async def test_clean_replay_passes_gate(self) -> None:
        """The committed seed fixtures must clear the gate untouched.

        Negative-space anchor: without this, a green
        ``test_citation_strip_regression_fails_gate`` could mean "the
        gate always fails" rather than "the gate fires on the actual
        regression". This pins both halves.
        """
        cases = load_week2_cases()
        supervisor = ReplaySupervisor()
        registered = _register_seed_contexts(supervisor)
        assert registered, "no fixture seed found — see _ensure_fixtures_exist"

        selected = [c for c in cases if c.id in registered]
        harness = _build_mock_judge_harness()

        results = await run_week2_suite(
            cases=selected, supervisor=supervisor, harness=harness
        )
        rates = summarize_by_category(results)
        verdict = evaluate_gate(
            current=rates,
            baseline=_build_replay_baseline(),
            config=_build_replay_config(),
        )
        assert isinstance(verdict, GateVerdict)
        assert verdict.passed, (
            f"clean replay must pass the gate; rates={rates}, "
            f"violations={verdict.violations}"
        )

    async def test_citation_strip_regression_fails_gate(self) -> None:
        """A code path that drops citations must trip the gate.

        Two paths satisfy ``check_citation_present`` — inline ``[record_type
        #id]`` tokens in the response text, OR a non-empty
        ``structured_citations`` tuple from the supervisor adapter. A
        true citation-dropping regression knocks out both. We model
        that here:

        * ``response_transform`` strips inline tokens — simulates a
          synthesizer-level bug that stops emitting them.
        * ``citations_transform`` empties the structured tuple —
          simulates a supervisor-adapter bug that stops harvesting
          Citations from graph state.

        Both together leave the harness with no evidence of any
        citation, so ``check_citation_present`` fails on every case.
        That's the W2 HARD GATE contract: "during grading, we will
        introduce a small regression and confirm your CI gate fails."
        """
        cases = load_week2_cases()
        supervisor = ReplaySupervisor(
            response_transform=_strip_citations,
            citations_transform=_drop_structured_citations,
        )
        registered = _register_seed_contexts(supervisor)
        assert registered, "no fixture seed found — see _ensure_fixtures_exist"

        selected = [c for c in cases if c.id in registered]
        harness = _build_mock_judge_harness()

        results = await run_week2_suite(
            cases=selected, supervisor=supervisor, harness=harness
        )
        rates = summarize_by_category(results)
        verdict = evaluate_gate(
            current=rates,
            baseline=_build_replay_baseline(),
            config=_build_replay_config(),
        )

        # The gate must report a failing verdict.
        assert verdict.passed is False, (
            "expected gate to BLOCK a citation-drop regression but it "
            f"passed; rates={rates} violations={verdict.violations}"
        )

        # The violation set must include at least one citations-scoped
        # entry (the regression hits citation_present hardest, but the
        # other categories with cited fixtures will also drop). Both
        # REGRESSION (drop > 0.0) and BELOW_THRESHOLD (current < 0.99)
        # are acceptable — either proves the gate caught the regression.
        assert len(verdict.violations) > 0, (
            f"expected at least one violation; got rates={rates}"
        )
        kinds = {v.kind for v in verdict.violations}
        assert (
            ViolationKind.REGRESSION in kinds
            or ViolationKind.BELOW_THRESHOLD in kinds
        ), f"expected REGRESSION or BELOW_THRESHOLD; got {kinds}"

    async def test_seed_covers_every_w2_category(self) -> None:
        """Defensive: the seed must touch every category the gate scores.

        If the seed only covers (say) citations, a regression in
        evidence_retrieval would slip past the replay gate even
        though the test above passes. The seed-coverage assertion
        catches a future fixture-pruning that leaves a category cold.
        """
        from tests.eval.harness import EvalCategory

        cases = load_week2_cases()
        registered_ids: set[str] = set()
        for case in cases:
            if (DEFAULT_FIXTURE_DIR / f"{case.id}.jsonl").is_file():
                registered_ids.add(case.id)

        covered_categories = {
            case.category
            for case in cases
            if case.id in registered_ids
        }
        # We only assert categories that appear in the YAML suite —
        # if a category gets renamed at the source, this test breaks
        # alongside the runtime, which is the right failure mode.
        expected = {
            EvalCategory.CITATIONS,
            EvalCategory.EVIDENCE_RETRIEVAL,
            EvalCategory.EXTRACTION,
            EvalCategory.MISSING_DATA,
            EvalCategory.REFUSAL,
        }
        assert covered_categories >= expected, (
            f"replay-fixture seed missing categories: {expected - covered_categories}; "
            "every gate-scored category needs at least one fixture."
        )


class TestReplayCliExitCode:
    """Process-boundary check: replay_cli's main() returns non-zero on regression.

    The above test exercises the in-process gate function. CI consumes
    the exit code of ``python -m tests.eval.gate.replay_cli``, so we
    confirm the regression also propagates all the way out as a
    non-zero process exit. Uses ``main()`` (not subprocess) to keep the
    test fast and deterministic.
    """

    async def test_clean_replay_main_returns_zero(self, tmp_path: Path) -> None:
        from tests.eval.gate.replay_cli import _run

        args = MagicMock()
        args.fixture_dir = DEFAULT_FIXTURE_DIR
        args.config = None
        args.results = tmp_path / "results.json"
        args.report = tmp_path / "report.md"
        if not DEFAULT_FIXTURE_DIR.is_dir():
            pytest.skip("seed fixtures not on disk — see seed_fixtures CLI")
        exit_code = await _run(args)
        assert exit_code == 0, (
            f"clean replay must return 0; got {exit_code}. "
            f"Verdict: {args.results.read_text() if args.results.is_file() else '<no file>'}"
        )

    async def test_regressed_replay_main_returns_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Patch ReplaySupervisor to inject the citation-strip transform.

        Has to monkeypatch because ``_run`` constructs the supervisor
        internally and doesn't expose a transform knob. The patch
        targets ``tests.eval.gate.replay_cli.ReplaySupervisor`` so
        only the call site under test sees the regressed instance.
        """
        from tests.eval.gate import replay_cli

        if not DEFAULT_FIXTURE_DIR.is_dir():
            pytest.skip("seed fixtures not on disk — see seed_fixtures CLI")

        original_cls = replay_cli.ReplaySupervisor

        def _patched_supervisor() -> ReplaySupervisor:
            return original_cls(
                response_transform=_strip_citations,
                citations_transform=_drop_structured_citations,
            )

        monkeypatch.setattr(replay_cli, "ReplaySupervisor", _patched_supervisor)

        args = MagicMock()
        args.fixture_dir = DEFAULT_FIXTURE_DIR
        args.config = None
        args.results = tmp_path / "results.json"
        args.report = tmp_path / "report.md"
        exit_code = await replay_cli._run(args)
        assert exit_code == 1, (
            f"regressed replay must return 1; got {exit_code}. "
            f"Verdict file: "
            f"{args.results.read_text() if args.results.is_file() else '<no file>'}"
        )
