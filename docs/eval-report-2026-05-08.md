# AgentForge Eval Report — 2026-05-08 (W2)

## Executive summary

The W2 evaluation **infrastructure is complete and the gate is
proven correct, but no measured run against the real agent has
happened yet.** The headline:

- 50 golden cases live under
  [`sidecar/tests/eval/cases/week2/`](../sidecar/tests/eval/cases/week2/),
  distributed 12 / 10 / 10 / 8 / 10 across `extraction` /
  `evidence_retrieval` / `citations` / `refusal` / `missing_data`.
- Two-stage harness (`EvalHarnessW2`): three programmatic checks
  (`schema_valid`, `citation_present`, `no_phi_in_logs`) gate a
  binary LLM judge (`factually_consistent`, `safe_refusal`) at
  `temperature=0` with retry-and-tiebreaker on disagreement.
- The gate's threshold-and-regression arithmetic is empirically
  verified by the Task 19 self-test
  ([`test_gate_blocks_regression.py`](../sidecar/tests/eval/gate/test_gate_blocks_regression.py)):
  a deliberately-regressed adapter that strips citations off a
  fabricated `A1c = 15.5%` claim drops the `citations` pass rate
  from 1.0 → 0.6, and the gate returns `verdict.passed is False`
  with the expected violation kinds.
- The pinned baseline at
  [`sidecar/tests/eval/baselines/week2.json`](../sidecar/tests/eval/baselines/week2.json)
  is a **stub** with all five categories at 1.0 and
  `_meta.status: "stub"`. The production `Supervisor` adapter that
  bridges the LangGraph orchestrator to the runner's
  `Callable[[EvalCase], SupervisorOutput]` seam **does not exist
  yet** — that is why no real measured baseline exists. The CLI
  entry point at
  [`sidecar/tests/eval/gate/cli.py`](../sidecar/tests/eval/gate/cli.py)
  intentionally has no real-LLM mode.
- Per-category pass rates against the real agent are
  **deferred to the first measured run.** A projected cost for that
  run, derived from `cost.py`'s closed-form pricing, is **≈ $0.87**
  (≤ $2 worst case).

What ships today is a measurable floor that lets the gate's
threshold + regression arithmetic be exercised against an honest
reference. What does not ship is a graded report card on the agent
itself.

## Methodology

The W2 evaluation is a two-stage pipeline. Programmatic checks run
first; the LLM judge runs only on cases where the cheap layer has
already passed. Either layer failing fails the case overall. The
harness lives at
[`sidecar/tests/eval/harness_w2.py`](../sidecar/tests/eval/harness_w2.py)
(class `EvalHarnessW2`).

### Programmatic grounding layer

Three deterministic checks, no LLM calls. Implemented in
[`sidecar/tests/eval/graders/programmatic.py`](../sidecar/tests/eval/graders/programmatic.py)
and aggregated by `ProgrammaticChecks.run`:

- **`check_schema_valid`** — runs `BaseModel.model_validate` over the
  agent's structured-citation payload. Fails on any Pydantic
  ValidationError, including model-validators like the W2 bbox
  confidence floor.
- **`check_citation_present`** — accepts either an inline
  `[record_type #id]` token (W1 grammar from
  `agentforge.verifier.citation.find_citations`) or at least one
  attached structured `Citation`. Either path counts.
- **`check_no_phi_in_logs`** — regex sweep over trace-export log lines
  for SSN/MRN/phone-shaped digit runs that bypassed HMAC pseudonymization.

### LLM-as-judge layer

Binary PASS/FAIL judges at `temperature=0`, parsed from a structured
`VERDICT: PASS|FAIL` token. A free-form judge response falls back to
FAIL so a malformed judge cannot silently bless a bad agent response.
Implemented in
[`sidecar/tests/eval/graders/llm_judge_w2.py`](../sidecar/tests/eval/graders/llm_judge_w2.py)
(class `LLMJudge`):

- **`factually_consistent`** — does every claim in the response trace
  back to source documents the agent had? Prompt:
  [`prompts/v1/judge_factually_consistent.md`](../prompts/v1/judge_factually_consistent.md).
- **`safe_refusal`** — for refusal cases, did the agent decline an
  unsafe or out-of-scope request? Prompt:
  [`prompts/v1/judge_safe_refusal.md`](../prompts/v1/judge_safe_refusal.md).

`grade_with_retry` re-runs a disagreeing judge with a fresh seed and a
tiebreaker; both `attempts` and `tiebreaker_used` surface on the
outcome so reports can flag oscillating cases. Cost per judge call is
computed via `agentforge.observability.cost.calculate_cost` and
attached to the matching Langfuse span.

The pinned judge model and temperature are config (not code), in
[`sidecar/eval_config.yaml`](../sidecar/eval_config.yaml):
`llm_judge_model: claude-sonnet-4-6`, `llm_judge_temperature: 0`.

## Case set

50 golden cases live under
[`sidecar/tests/eval/cases/week2/`](../sidecar/tests/eval/cases/week2/),
loaded by `tests.eval.gate.runner_w2.load_week2_cases`:

| File | Category | Count |
| --- | --- | --- |
| `extraction.yaml` | extraction | 12 |
| `evidence_retrieval.yaml` | evidence_retrieval | 10 |
| `citations.yaml` | citations | 10 |
| `missing_data.yaml` | missing_data | 10 |
| `refusals.yaml` | refusal | 8 |

Total: 50 cases. The runner asserts `len(cases) == 50` on load (see
the gate self-test at line 222 of
[`tests/eval/gate/test_gate_blocks_regression.py`](../sidecar/tests/eval/gate/test_gate_blocks_regression.py)).

A 10-case representative subset (2 per category) is tagged
`tags: [eval_smoke]` and runs in the pre-commit hook against fully
mocked supervisor + judge in <30 s — see
[`tests/eval/gate/test_eval_smoke.py`](../sidecar/tests/eval/gate/test_eval_smoke.py)
and the runbook at
[`docs/agents/pre-commit-eval-smoke.md`](agents/pre-commit-eval-smoke.md).

The case YAMLs reference real fixtures under
[`week2/example-documents/`](../week2/example-documents/) (intake-forms +
lab-results, mapped 1:1 to dev-easy patient_ids 1–4).

## Baseline status

[`sidecar/tests/eval/baselines/week2.json`](../sidecar/tests/eval/baselines/week2.json)
**is a stub.** All five category pass rates are pinned at `1.0` and
the file's `_meta.status` field reads `"stub"`. The rationale field on
the file itself is the canonical explanation:

> Initial stub baseline pinned at 1.0 across all five W2 case
> categories. The runner's production-supervisor adapter is out of
> scope for Task 18 (the runner takes a callable; tests supply a
> mock). Generating a "real" baseline requires (a) wiring the
> LangGraph supervisor into a `SupervisorOutput`-shaped adapter and
> (b) burning real Anthropic + judge-LLM spend across all 50 cases.
> Both belong to a follow-up regen run by a human; the gate logic
> ships with a measurable floor in the meantime so threshold +
> regression arithmetic can be exercised against an honest reference.

In other words: **no measured production run has happened yet.** The
gate's threshold-and-regression arithmetic has been exercised against
a known reference (see "Gate correctness" below); the agent itself
has not been graded at scale. Per-category pass rates against the
real LangGraph supervisor are deferred to the first measured run.

### How the baseline gets regenerated

Once a production `Supervisor` adapter exists on top of
`agentforge.orchestrator.graph.build_graph()` that returns a
`SupervisorOutput` per case, run:

```bash
cd sidecar
./scripts/run_eval_gate.sh
```

The CLI entry point at
[`sidecar/tests/eval/gate/cli.py`](../sidecar/tests/eval/gate/cli.py)
intentionally **does not** wire a real-LLM mode today. Generating a
measured baseline is a follow-up that needs the production adapter
(out of scope for Task 18 by design). The shell wrapper at
[`sidecar/scripts/run_eval_gate.sh`](../sidecar/scripts/run_eval_gate.sh)
documents the env knobs (`EVAL_GATE_BASELINE`, `EVAL_GATE_CONFIG`,
`EVAL_GATE_RESULTS`, `EVAL_GATE_REPORT`) the regen run will set.

## Gate correctness

The empirical claim this report makes about the gate is **not** "the
agent passes" — it's "the gate fails on a deliberately-regressed
adapter." That claim is proven by the Task 19 self-test:

[`sidecar/tests/eval/gate/test_gate_blocks_regression.py`](../sidecar/tests/eval/gate/test_gate_blocks_regression.py)

Three end-to-end tests run under the `gate_validation` pytest marker
(deselected by default — `pyproject.toml`):

- **`test_clean_supervisor_passes_gate`** — pins the negative space.
  A clean adapter that returns a well-formed `SupervisorOutput`
  (citation attached, schema-valid payload) clears all five
  thresholds. Without this, a green failure test could mean "the
  gate always fails" rather than "the gate fires on real
  regressions."
- **`test_regressed_supervisor_fails_gate`** — the headline test.
  Regresses 4 of the 10 `citations` cases by emitting `A1c = 15.5%
  (no source attached).` (a clinical claim with no citation). The
  `citations` pass rate drops 1.0 → 0.6 — outside the 5%
  regression band and below the 0.9 absolute floor. The gate must
  return `verdict.passed is False` with at least one `citations`
  violation of kind `REGRESSION` or `BELOW_THRESHOLD`.
- **`test_regressed_run_cli_exits_non_zero`** — same regression
  routed through the CLI surface (`run_gate_cli`) that CI consumes.
  Confirms the failure surfaces all the way through to the process
  exit code. CI calls this same path via
  [`scripts/run_eval_gate.sh`](../sidecar/scripts/run_eval_gate.sh)
  → [`tests/eval/gate/cli.py`](../sidecar/tests/eval/gate/cli.py).

The thresholds the gate enforces come from
[`sidecar/eval_config.yaml`](../sidecar/eval_config.yaml):

| Knob | Value | Meaning |
| --- | --- | --- |
| `category_thresholds.*` | 0.9 | Absolute floor per category |
| `regression_threshold` | 0.05 | Max baseline-to-current drop allowed |
| `llm_judge_model` | `claude-sonnet-4-6` | Pinned judge model |
| `llm_judge_temperature` | 0 | Pinned for determinism |

### Caveat: the gate self-test is constrained by judge routing

The Task 19 brief framed the worst-case regression as a fabricated
`LabValue` (e.g. A1c=15.5% when the case expects 8.2%). The W2
harness routes only `HALLUCINATION` and `REFUSAL` cases to an LLM
judge (see `_JUDGE_BY_CATEGORY` at line 46 of
[`tests/eval/harness_w2.py`](../sidecar/tests/eval/harness_w2.py)),
and the 50-case W2 suite has zero `HALLUCINATION` cases — its
categories are `extraction` / `evidence_retrieval` / `citations` /
`refusal` / `missing_data`. So a pure value-fabrication that emits a
nicely-cited but factually wrong number could slip past the
programmatic layer. The self-test's regressed adapter compensates by
stripping the citation off the fabricated claim, which the
programmatic `check_citation_present` does catch. The gate's
threshold + regression arithmetic is exercised end-to-end. Routing
`extraction` and `missing_data` to a judge so a "well-cited but
factually wrong" payload also fails is a tracked follow-up
(see "Open follow-ups").

## Cost analysis (projection)

**This is a projection, not a measurement.** No measured 50-case run
has happened yet — the numbers below are derived from the closed-form
pricing functions in
[`sidecar/src/agentforge/observability/cost.py`](../sidecar/src/agentforge/observability/cost.py)
applied to plausible per-case token counts. They will be replaced
with real Langfuse-attached costs after the first end-to-end run.

### Inputs

The relevant pricing surface (see `PRICING` table at line 75 of
`cost.py`):

| Model | Input ($/M tok) | Output ($/M tok) |
| --- | ---: | ---: |
| `claude-sonnet-4-6` | 3.00 | 15.00 |
| `claude-haiku-4-5` | 0.80 | 4.00 |

Vision input cost uses
`agentforge.observability.cost.calculate_vision_cost`; image tokens
follow Anthropic's formula `tokens ≈ (width × height) / 750`
(constant `_IMAGE_TOKEN_DIVISOR` at line 50; helper
`estimate_image_tokens` at line 137). At 1024×1024 px per rendered
PDF page, that is ≈ 1,398 tokens per page.

### Per-call envelope

The 50-case suite runs three classes of LLM call:

1. **Agent turn** (Haiku 4.5 — orchestrator working model). Run
   once per case (50 calls). Envelope: ~6 k input tok + ~600 output
   tok. Per call: `0.80e-6 × 6000 + 4.0e-6 × 600 = $0.0048 + $0.0024 = $0.0072`.
2. **Vision extraction** (Sonnet 4.6) — runs on the 12 `extraction`
   cases plus a subset of `evidence_retrieval` / `citations` cases
   that attach a PDF. Conservative bound: 20 vision calls × 2 pages
   × ~1,400 tok-per-page + ~500 tok of text input + ~800 output tok.
   Per call: `3.0e-6 × (2800 + 500) + 15.0e-6 × 800 = $0.0099 + $0.012 = $0.0219`.
3. **LLM judge** (Sonnet 4.6) — only fires on cases routed by
   `_JUDGE_BY_CATEGORY`. With zero `HALLUCINATION` cases and 8
   `REFUSAL` cases in the W2 suite, that is **8 judge calls per
   run** (more if `grade_with_retry` triggers). Envelope: ~2 k input
   + ~150 output tok. Per call: `3.0e-6 × 2000 + 15.0e-6 × 150 ≈ $0.0083`.

### Per-run total (projection)

| Component | Calls | $/call | Subtotal |
| --- | ---: | ---: | ---: |
| Agent turns (Haiku) | 50 | $0.0072 | $0.36 |
| Vision extraction (Sonnet) | 20 | $0.0219 | $0.44 |
| Judge calls (Sonnet) | 8 | $0.0083 | $0.07 |
| **Total per 50-case run** | | | **≈ $0.87** |

Order-of-magnitude: under $1 per measured baseline regen. Worst case
with `grade_with_retry` going to a tiebreaker on every judge call
(3× the judge spend, ~$0.21 of judge cost) and a more vision-heavy
case mix puts the run in the $1.50–$2.00 range. Either way the
budget is small enough that running on every MR was rejected on
prudence grounds, not affordability — see
[`docs/agents/ci-eval.md`](agents/ci-eval.md) for the CI policy
discussion. The per-MR `agent-eval` job runs fully mocked; the gate
arithmetic is what's being checked, not real model behavior.

## Open follow-ups

The infrastructure is in place; these are the loose ends a human will
need to close before the report can carry measured numbers.

1. **Production `Supervisor` adapter.** Wire a callable on top of
   `agentforge.orchestrator.graph.build_graph()` that returns a
   `SupervisorOutput` per `EvalCase` (the dataclass at
   [`tests/eval/gate/runner_w2.py`](../sidecar/tests/eval/gate/runner_w2.py)).
   This is the missing seam between the LangGraph orchestrator and
   the W2 runner. Until it exists, every gate run is mocked.
2. **Real-baseline regeneration.** Once the adapter exists, run
   `./scripts/run_eval_gate.sh` end-to-end against a real Anthropic
   key and replace
   [`sidecar/tests/eval/baselines/week2.json`](../sidecar/tests/eval/baselines/week2.json)
   with the resulting per-category rates. Flip
   `_meta.status` from `"stub"` to `"measured"` and record the
   regen run's Langfuse trace id in `_meta.regenerated_by`.
3. **Judge routing for `extraction` and `missing_data`.** The W2
   harness's `_JUDGE_BY_CATEGORY` dict
   ([`tests/eval/harness_w2.py`](../sidecar/tests/eval/harness_w2.py)
   line 46) currently routes only `HALLUCINATION` and `REFUSAL`
   cases to a judge. With zero `HALLUCINATION` cases in the W2
   suite, only the 8 `refusal` cases ever invoke the judge. A
   "well-cited but factually wrong" extraction can therefore slip
   past the programmatic layer. Adding `EXTRACTION` and
   `MISSING_DATA` to the routing dict (with appropriate prompt
   components in `prompts/v1/`) closes that gap.
4. **`GLAB_TOKEN` masked CI variable.** The MR-comment poster in the
   `agent-eval` GitLab job (line 178 of
   [`.gitlab-ci.yml`](../.gitlab-ci.yml)) reads `GLAB_TOKEN` first
   and falls back to `CI_JOB_TOKEN`. `GLAB_TOKEN` is the
   recommended path because `CI_JOB_TOKEN` cannot post comments
   from forked-MR pipelines. The variable still needs to be created
   as a masked + protected project / group access token with `api`
   scope. Until then, MRs from forks get no diff-report comment
   (the job still runs and fails appropriately on regressions).

## Artifacts

Canonical paths for everything cited in this report.

### Configuration

- [`sidecar/eval_config.yaml`](../sidecar/eval_config.yaml) —
  thresholds, regression band, pinned judge model + temperature.

### Cases + baseline

- [`sidecar/tests/eval/cases/week2/`](../sidecar/tests/eval/cases/week2/)
  — 50 golden YAML cases across 5 files.
- [`sidecar/tests/eval/baselines/week2.json`](../sidecar/tests/eval/baselines/week2.json)
  — pinned stub baseline (`_meta.status: "stub"`).
- [`week2/example-documents/`](../week2/example-documents/) — intake
  forms + lab PDFs the case YAMLs reference.

### Runner + gate

- [`sidecar/tests/eval/gate/runner_w2.py`](../sidecar/tests/eval/gate/runner_w2.py)
  — `load_week2_cases`, `run_week2_suite`, `SupervisorOutput`.
- [`sidecar/tests/eval/gate/scoring.py`](../sidecar/tests/eval/gate/scoring.py)
  — `summarize_by_category`.
- [`sidecar/tests/eval/gate/gate.py`](../sidecar/tests/eval/gate/gate.py)
  — `evaluate_gate`, `GateVerdict`, `ViolationKind`.
- [`sidecar/tests/eval/gate/cli.py`](../sidecar/tests/eval/gate/cli.py)
  — `run_gate_cli` entry point used by CI.
- [`sidecar/tests/eval/gate/diff_report.py`](../sidecar/tests/eval/gate/diff_report.py)
  — markdown diff reporter for MR comments.
- [`sidecar/scripts/run_eval_gate.sh`](../sidecar/scripts/run_eval_gate.sh)
  — shell wrapper around the CLI.

### Harness + graders

- [`sidecar/tests/eval/harness_w2.py`](../sidecar/tests/eval/harness_w2.py)
  — `EvalHarnessW2`, `_JUDGE_BY_CATEGORY`.
- [`sidecar/tests/eval/graders/programmatic.py`](../sidecar/tests/eval/graders/programmatic.py)
  — `check_schema_valid`, `check_citation_present`,
  `check_no_phi_in_logs`, `ProgrammaticChecks`.
- [`sidecar/tests/eval/graders/llm_judge_w2.py`](../sidecar/tests/eval/graders/llm_judge_w2.py)
  — `LLMJudge`, `JudgeCategory`, `grade_with_retry`.
- [`prompts/v1/judge_factually_consistent.md`](../prompts/v1/judge_factually_consistent.md)
- [`prompts/v1/judge_safe_refusal.md`](../prompts/v1/judge_safe_refusal.md)

### Tests that prove the gate works

- [`sidecar/tests/eval/gate/test_gate_blocks_regression.py`](../sidecar/tests/eval/gate/test_gate_blocks_regression.py)
  — Task 19 end-to-end self-test (`gate_validation` marker).
- [`sidecar/tests/eval/gate/test_gate.py`](../sidecar/tests/eval/gate/test_gate.py)
  — gate-arithmetic unit tests.
- [`sidecar/tests/eval/gate/test_eval_smoke.py`](../sidecar/tests/eval/gate/test_eval_smoke.py)
  — pre-commit smoke (10 cases, mocked stack).

### Observability

- [`sidecar/src/agentforge/observability/cost.py`](../sidecar/src/agentforge/observability/cost.py)
  — `PRICING`, `calculate_cost`, `calculate_vision_cost`,
  `estimate_image_tokens`.

### CI + ops

- [`.gitlab-ci.yml`](../.gitlab-ci.yml) — `agent-eval` job (lines
  126+).
- [`docs/agents/ci-eval.md`](agents/ci-eval.md) — CI eval-gate
  runbook (`GLAB_TOKEN` setup, expected exit codes, artifacts).
- [`docs/agents/pre-commit-eval-smoke.md`](agents/pre-commit-eval-smoke.md)
  — pre-commit hook ops guide.
- [`.pre-commit-config.yaml`](../.pre-commit-config.yaml) —
  `agentforge-eval-smoke` hook entry.

### Companion docs

- [`docs/eval-report-2026-05-03.md`](eval-report-2026-05-03.md) —
  W1 framework smoke report (5 categories, 12 cases, all passing on
  the framework run).
- [`docs/eval-summary-2026-05-03.md`](eval-summary-2026-05-03.md) —
  W1 companion summary (live-stack baseline + framework + demo).
