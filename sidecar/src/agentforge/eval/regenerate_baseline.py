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
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from agentforge.llm.types import LLMResponse, Message, ToolSpec
from agentforge.observability.cost import calculate_cost
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


@dataclass
class _CostAccumulator:
    """Mutable USD accumulator shared across cost-tracking facades.

    Lives outside the LLM client wrapper so multiple wrapped clients
    (synthesizer + planner + judge) can share one running total without
    coupling the wrapper class to global state. The ``vision_cost``
    field aggregates :class:`VisionExtractionResult.cost_usd` separately
    so we can surface a vision-vs-text breakdown if a future operator
    wants to split the bill — today the regen just sums them.
    """

    text_cost_usd: float = 0.0
    vision_cost_usd: float = 0.0
    text_calls: int = 0
    vision_calls: int = 0

    @property
    def total_usd(self) -> float:
        return self.text_cost_usd + self.vision_cost_usd

    def record_text_call(self, model: str, input_tokens: int, output_tokens: int) -> None:
        cost = calculate_cost(model, input_tokens, output_tokens)
        self.text_cost_usd += cost
        self.text_calls += 1

    def record_vision_cost(self, cost_usd: float | None) -> None:
        if cost_usd is not None and cost_usd > 0.0:
            self.vision_cost_usd += cost_usd
        self.vision_calls += 1


class _CostTrackingLLMClient:
    """LLMClient facade that tallies token cost on every ``complete``.

    Satisfies both :class:`agentforge.llm.client.LLMClient` and the
    narrower ``_SynthesisLLMLike`` Protocol the graph consumes — only
    the ``complete()`` method is exercised on the regen path (planner,
    synthesizer, judge). ``stream()`` is intentionally unimplemented
    because the regen never streams; if a future caller adds a stream
    path, this wrapper will need to extend to it.
    """

    def __init__(self, *, inner: Any, model: str, costs: _CostAccumulator) -> None:
        self._inner = inner
        self._model = model
        self._costs = costs

    async def complete(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
        temperature: float = 1.0,
    ) -> LLMResponse:
        response = await self._inner.complete(
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        self._costs.record_text_call(
            self._model,
            response.input_tokens,
            response.output_tokens,
        )
        return response


class _CostTrackingVisionExtractor:
    """Vision extractor facade that tallies cost off the result.

    Delegates to a real :class:`VisionExtractor` and reads
    ``cost_usd`` off the returned :class:`VisionExtractionResult` to
    accumulate spend without intercepting the SDK. Mirrors the shape of
    :class:`agentforge.orchestrator.graph._VisionExtractorLike` so the
    graph consumes it transparently.
    """

    def __init__(self, *, inner: Any, costs: _CostAccumulator) -> None:
        self._inner = inner
        self._costs = costs

    async def extract(
        self,
        *,
        pages: Any,
        document_id: int,
        patient_id: int,
        trace: Any = None,
    ) -> Any:
        result = await self._inner.extract(
            pages=pages,
            document_id=document_id,
            patient_id=patient_id,
            trace=trace,
        )
        self._costs.record_vision_cost(result.cost_usd)
        return result


# Module-level handle on the most recent real-run cost accumulator. The
# CLI reads this in ``run_cli`` to inject ``cost_usd`` into the output
# baseline's ``_meta`` block. Module-level (not threaded through every
# helper) because the regen CLI is single-threaded and runs exactly
# once per invocation; introducing a parameter chain through
# ``_run_suite`` would touch every test that exercises the mock path.
_LAST_REAL_RUN_COSTS: _CostAccumulator | None = None


def _build_real_supervisor_and_harness() -> tuple[Any, EvalHarnessW2]:
    """Construct the real production adapter + harness.

    Wires:
      * One shared :class:`anthropic.AsyncAnthropic` client (auth via
        ``ANTHROPIC_API_KEY``) for both vision extractors.
      * A primary :class:`ClaudeClient` for planner + synthesizer
        (model: ``Settings.claude_model`` or the SDK default).
      * A secondary :class:`ClaudeClient` for the LLM judge pinned at
        the ``llm_judge_model`` from ``eval_config.yaml`` — the gate
        config calibrates against this exact model so the judge must
        match it on a measured run.
      * The full hybrid-RAG :class:`EvidenceRetriever` (BM25 + dense +
        RRF + cross-encoder), forced on regardless of the env flag so
        evidence cases actually exercise retrieval.
      * :class:`FilenameDocumentResolver` against
        ``week2/example-documents/`` so extraction cases find their PDFs.

    All Anthropic-going clients are wrapped in cost-tracking facades
    so the CLI can surface the run's USD total in the baseline ``_meta``
    block. Vision extractors track cost off the
    :class:`VisionExtractionResult.cost_usd` field. Module-level
    :data:`_LAST_REAL_RUN_COSTS` lets ``run_cli`` reach the running
    total without re-plumbing the supervisor signature.

    Raises :class:`RuntimeError` when ``ANTHROPIC_API_KEY`` is unset —
    we'd rather fail at construction than burn half a run before
    Anthropic returns 401.
    """
    # Imports kept inside the function so the test suite's mock path
    # never pays the FastAPI / Anthropic SDK / RAG-model startup cost.
    # The module-level stays light per the docstring contract.
    from anthropic import AsyncAnthropic

    from agentforge.config import Settings
    from agentforge.eval.filename_resolver import FilenameDocumentResolver
    from agentforge.eval.supervisor_adapter import (
        SupervisorAdapter,
        SupervisorAdapterDeps,
    )
    from agentforge.llm.claude import ClaudeClient
    from agentforge.observability.null_client import NullLangfuseClient
    from agentforge.orchestrator.planner import Planner
    from agentforge.rag import (
        BM25Retriever,
        DenseRetriever,
        EvidenceRetriever,
        RRFMerger,
        SentenceTransformerCrossEncoder,
        SentenceTransformerEncoder,
        load_corpus,
        select_reranker,
    )
    from agentforge.tools.attach_and_extract import (
        INTAKE_CONTRACT,
        LAB_CONTRACT,
        PdfRenderer,
        VisionExtractor,
    )
    from tests.eval.gate.config import load_eval_gate_config

    settings = Settings()  # type: ignore[call-arg]  # populated from env / .env
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY must be set to run a measured baseline; "
            "either export it in the shell or write it into sidecar/.env."
        )

    # The judge model is pinned in eval_config.yaml — calibration was
    # done against that exact model. Loading the gate config here means
    # a future change to the pinned judge model only needs one edit.
    gate_config = load_eval_gate_config(None)
    judge_model = gate_config.llm_judge_model

    costs = _CostAccumulator()

    # ------------------------------------------------------------------
    # LLM clients — one shared AsyncAnthropic for vision (saves on
    # connection setup), separate ClaudeClient instances for the text
    # paths so each can pin its own model.
    # ------------------------------------------------------------------
    shared_anthropic = AsyncAnthropic(api_key=settings.anthropic_api_key)

    primary_model = settings.claude_model or "claude-sonnet-4-5"
    primary_inner = ClaudeClient(
        api_key=settings.anthropic_api_key,
        model=primary_model,
    )
    primary_llm = _CostTrackingLLMClient(
        inner=primary_inner, model=primary_model, costs=costs
    )

    judge_inner = ClaudeClient(
        api_key=settings.anthropic_api_key,
        model=judge_model,
    )
    judge_llm = _CostTrackingLLMClient(
        inner=judge_inner, model=judge_model, costs=costs
    )

    # ------------------------------------------------------------------
    # Workers — same construction shape main.create_app uses. Vision
    # extractors share the AsyncAnthropic client so the SDK's HTTP/2
    # connection pool can be reused across intake + lab calls.
    # ------------------------------------------------------------------
    planner = Planner(llm=primary_llm)

    intake_extractor_inner: VisionExtractor[Any] = VisionExtractor(
        contract=INTAKE_CONTRACT,
        client=shared_anthropic,
    )
    lab_extractor_inner: VisionExtractor[Any] = VisionExtractor(
        contract=LAB_CONTRACT,
        client=shared_anthropic,
    )
    intake_extractor = _CostTrackingVisionExtractor(
        inner=intake_extractor_inner, costs=costs
    )
    lab_extractor = _CostTrackingVisionExtractor(
        inner=lab_extractor_inner, costs=costs
    )

    # Force the retriever on for the regen — the corpus is committed
    # under sidecar/data/guidelines/ and evidence cases need it. If the
    # operator's .env has EVIDENCE_RETRIEVER_ENABLED=false (the
    # production-test default), respecting it would silently zero out
    # the evidence_retrieval pass rate. Construction does I/O (load_corpus)
    # but no model downloads — the encoder + reranker pull weights on
    # first encode, which happens naturally on the first evidence case.
    if not settings.guidelines_index_path.is_file():
        raise RuntimeError(
            f"Guideline corpus index missing at {settings.guidelines_index_path}; "
            "the W2 evidence_retrieval cases cannot run without it. Re-run "
            "scripts/chunk_guidelines.py or rebuild the sidecar package."
        )
    chunks = load_corpus(settings.guidelines_index_path)
    encoder = SentenceTransformerEncoder()
    reranker = select_reranker(
        cohere_api_key=settings.cohere_api_key or None,
        cross_encoder_factory=SentenceTransformerCrossEncoder,
    )
    evidence_retriever = EvidenceRetriever(
        bm25=BM25Retriever(chunks),
        dense=DenseRetriever(chunks, encoder=encoder),
        merger=RRFMerger(),
        reranker=reranker,
    )

    # ------------------------------------------------------------------
    # Document resolver — maps "(p01-chen-intake-typed.pdf)" in a case
    # query to rendered pages from the on-disk corpus. Resolves under
    # the worktree's ``week2/example-documents/`` (sidecar/.. /week2 in
    # repo layout); falls back to the working directory if the layout
    # ever moves so the regen doesn't break silently.
    # ------------------------------------------------------------------
    pdf_renderer = PdfRenderer()
    # __file__ is sidecar/src/agentforge/eval/regenerate_baseline.py;
    # parents[4] is the repo root where ``week2/`` lives. Don't use the
    # PathLib `[3]` index that other modules use to reach `sidecar/` —
    # the corpus is one level above sidecar.
    repo_root = pathlib.Path(__file__).resolve().parents[4]
    corpus_root = repo_root / "week2" / "example-documents"
    if not corpus_root.is_dir():
        raise RuntimeError(
            f"W2 corpus directory missing at {corpus_root}; extraction "
            "cases cannot resolve their referenced documents."
        )
    document_resolver = FilenameDocumentResolver(
        corpus_root=corpus_root,
        pdf_renderer=pdf_renderer,
    )

    deps = SupervisorAdapterDeps(
        planner=planner,
        vision_extractor=intake_extractor,
        evidence_retriever=evidence_retriever,
        synthesis_llm=primary_llm,
        document_resolver=document_resolver,
    )
    # SupervisorAdapter only currently wires the intake extractor (the
    # adapter hardcodes the intake contract; lab routing is a follow-up).
    # We construct ``lab_extractor`` so cost tracking sees the same wire
    # as production would, but the adapter path doesn't surface a kwarg
    # for it today. Quietly retain the reference to satisfy linters.
    _ = lab_extractor

    supervisor = SupervisorAdapter(deps=deps)

    # ------------------------------------------------------------------
    # Harness — real LLM judge against a NullLangfuseClient (the regen
    # doesn't ship traces; it just needs the no-op surface so the
    # judge's record_judge_grade call is a clean no-op).
    # ------------------------------------------------------------------
    null_langfuse = NullLangfuseClient()
    judge = LLMJudge(llm=judge_llm, langfuse=null_langfuse, model=judge_model)
    trace = null_langfuse.trace_turn(
        user_id="regen",
        patient_id="regen",
        breakglass_flag=False,
        role=None,
    )
    harness = EvalHarnessW2(judge=judge, trace=trace)

    # Stash for ``run_cli`` to surface in the baseline ``_meta`` block.
    global _LAST_REAL_RUN_COSTS
    _LAST_REAL_RUN_COSTS = costs

    return supervisor, harness


async def _run_suite(*, mock: bool) -> dict[str, float]:
    """Drive all 50 cases through the supervisor and return per-category rates.

    ``mock=True`` uses the canned passing supervisor (free, deterministic).
    ``mock=False`` constructs the real supervisor + LLM judge via
    :func:`_build_real_supervisor_and_harness` and runs each case
    sequentially. Real runs stream per-case progress to stderr so a
    human watching can spot a stalled call before burning a full
    budget; mock runs stay quiet to keep the test suite output clean.
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
        results = await run_week2_suite(
            cases=cases, supervisor=supervisor, harness=harness
        )
    else:
        real_supervisor, harness = _build_real_supervisor_and_harness()
        results = await _run_real_suite_with_progress(
            cases=cases, supervisor=real_supervisor, harness=harness
        )

    rates = summarize_by_category(results)

    # Backfill any required category that didn't appear in results
    # (defensive: a case-loading regression that drops a whole category
    # would silently disappear from the baseline). Missing → 0.0 so the
    # gate's threshold check trips on the next run.
    for cat in _REQUIRED_CATEGORIES:
        rates.setdefault(cat, 0.0)
    return rates


async def _run_real_suite_with_progress(
    *,
    cases: Sequence[EvalCase],
    supervisor: Any,
    harness: EvalHarnessW2,
) -> list[Any]:
    """Run the W2 suite sequentially with per-case progress reporting.

    Mirrors :func:`run_week2_suite`'s loop body but emits a stderr
    line per case so an operator burning real Anthropic spend can
    monitor progress. The split is deliberate: ``run_week2_suite`` is
    the test-pinned contract surface and stays silent; this wrapper
    is regen-specific and can be noisy.

    Per-case timing + running cost help spot a hung call early — the
    typical case is ~10-25s; >60s on a single case is a signal to kill
    the run before the rest of the budget evaporates.
    """
    from tests.eval.gate.runner_w2 import W2RunnerResult, _invoke_supervisor

    results: list[W2RunnerResult] = []
    suite_start = time.perf_counter()
    for idx, case in enumerate(cases, start=1):
        case_start = time.perf_counter()
        try:
            output = await _invoke_supervisor(supervisor, case)
            eval_result = await harness.evaluate(
                case=case,
                response=output.response,
                structured_citation_payload=output.structured_citation_payload,
                structured_citations=output.structured_citations,
                sources=output.sources,
                logs=output.logs,
            )
        except Exception as exc:  # noqa: BLE001 — regen visibility wins over re-raise
            elapsed = time.perf_counter() - case_start
            running_cost = (
                _LAST_REAL_RUN_COSTS.total_usd
                if _LAST_REAL_RUN_COSTS is not None
                else 0.0
            )
            print(
                f"[{idx}/{len(cases)}] {case.id} ({case.category.value}) "
                f"FAILED after {elapsed:.1f}s — cost so far ${running_cost:.4f}: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            raise

        elapsed = time.perf_counter() - case_start
        running_cost = (
            _LAST_REAL_RUN_COSTS.total_usd
            if _LAST_REAL_RUN_COSTS is not None
            else 0.0
        )
        verdict = "pass" if eval_result.passed else "FAIL"
        print(
            f"[{idx}/{len(cases)}] {case.id} ({case.category.value}) "
            f"{verdict} in {elapsed:.1f}s — running cost ${running_cost:.4f}",
            file=sys.stderr,
            flush=True,
        )
        results.append(W2RunnerResult(case=case, eval_result=eval_result))

    suite_elapsed = time.perf_counter() - suite_start
    final_cost = (
        _LAST_REAL_RUN_COSTS.total_usd
        if _LAST_REAL_RUN_COSTS is not None
        else 0.0
    )
    print(
        f"\nSuite complete: {len(results)} cases in {suite_elapsed:.1f}s, "
        f"total cost ${final_cost:.4f}",
        file=sys.stderr,
        flush=True,
    )
    return results


def _build_payload(
    rates: dict[str, float], *, command: str, mock: bool
) -> dict[str, Any]:
    """Compose the JSON document written to ``--output``.

    ``mock=True`` runs (test suite + smoke checks) emit the same shape
    so the test fixtures stay valid, but with ``cost_usd: 0.0`` and a
    note in ``_meta`` so a future reader can't mistake a smoke baseline
    for a measured one. ``mock=False`` runs surface the cost + timing
    captured by :data:`_LAST_REAL_RUN_COSTS`.
    """
    timestamp = datetime.datetime.now(datetime.UTC).isoformat()
    meta: dict[str, Any] = {
        "status": "measured",
        "timestamp": timestamp,
        "measured_at": timestamp,
        "git_sha": _git_sha(),
        "command": command,
    }
    if mock:
        meta["mock"] = True
        meta["cost_usd"] = 0.0
    elif _LAST_REAL_RUN_COSTS is not None:
        meta["cost_usd"] = round(_LAST_REAL_RUN_COSTS.total_usd, 6)
        meta["text_cost_usd"] = round(_LAST_REAL_RUN_COSTS.text_cost_usd, 6)
        meta["vision_cost_usd"] = round(_LAST_REAL_RUN_COSTS.vision_cost_usd, 6)
        meta["text_calls"] = _LAST_REAL_RUN_COSTS.text_calls
        meta["vision_calls"] = _LAST_REAL_RUN_COSTS.vision_calls

    payload: dict[str, Any] = {"_meta": meta}
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
    payload = _build_payload(rates, command=command_repr, mock=args.mock)

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
