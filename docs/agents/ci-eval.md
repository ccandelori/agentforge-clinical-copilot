# CI eval gate (`agent-eval`)

The `agent-eval` job in `.gitlab-ci.yml` runs the W2 eval gate on every MR
and on every push to the default branch. A failing gate blocks merge.

## What it runs

A single shell command from inside the sidecar Python environment:

```bash
./scripts/run_eval_gate.sh
```

That wrapper invokes `python -m tests.eval.gate.cli`, which:

1. Loads the 50 W2 eval cases from `sidecar/tests/eval/cases/week2/`.
2. Runs each case through a **mocked** supervisor (a synthetic
   `SupervisorOutput` shaped to clear the programmatic checks).
3. Grades each result with `EvalHarnessW2`, where the LLM judge is also
   mocked (`AsyncMock` returning `VERDICT: PASS`).
4. Aggregates per-category pass rates with `summarize_by_category`.
5. Loads the pinned baseline from
   `sidecar/tests/eval/baselines/week2.json`.
6. Computes a `GateVerdict` against `sidecar/eval_config.yaml`.
7. Writes the JSON verdict + a markdown diff report into `sidecar/var/`.
8. Exits 0 on pass, 1 on any violation, 2 on invocation error.

The gate's `after_script` step then POSTs the markdown report to the
MR's notes endpoint (when `GLAB_TOKEN` or `CI_JOB_TOKEN` is set).

## Why it's all mocked

Every MR pipeline runs the gate, so a real-LLM run would burn Anthropic
+ judge tokens on every commit. The supervisor + judge are out of scope
for Task 20 (the production LangGraph adapter is a follow-up).

The mock path still validates everything that matters for a CI gate:

- The 50 cases load.
- The harness contract holds (programmatic checks run; PHI sweep runs;
  citation index resolves).
- The aggregator + verdict + reporter render correctly.
- The job's exit code blocks merge.

When the production adapter lands, the manual real-LLM job (TBD) will
swap in the real supervisor + real judge while keeping the same script.

## Single source of truth

The same `sidecar/eval_config.yaml` drives local runs and CI. There is
no CI-only config override — if the thresholds are wrong in CI, they
were wrong locally too.

## Manual invocation

The wrapper works from any machine with `uv` installed:

```bash
# Default — write artifacts to sidecar/var/
sidecar/scripts/run_eval_gate.sh

# Force a regression for drill / smoke purposes
sidecar/scripts/run_eval_gate.sh --inject-failure extraction

# Custom output paths via env vars
EVAL_GATE_RESULTS=/tmp/results.json \
  EVAL_GATE_REPORT=/tmp/report.md \
  sidecar/scripts/run_eval_gate.sh
```

Same exit codes as in CI.

## Override knobs

| Env var               | Purpose                                                      |
|-----------------------|--------------------------------------------------------------|
| `EVAL_GATE_RESULTS`   | Where to write the JSON verdict (default: `sidecar/var/eval_gate_results.json`) |
| `EVAL_GATE_REPORT`    | Where to write the markdown report (default: `sidecar/var/eval_gate_report.md`) |
| `EVAL_GATE_BASELINE`  | Override baseline JSON (default: pinned `tests/eval/baselines/week2.json`) |
| `EVAL_GATE_CONFIG`    | Override `eval_config.yaml` path (default: pinned)            |
| `GLAB_TOKEN`          | Project / group access token with `api` scope; enables MR comment posting in CI |
| `CI_JOB_TOKEN`        | Fallback for MR-note posting when the instance allows it      |

CLI flags on `run_eval_gate.sh` pass straight through to
`python -m tests.eval.gate.cli`:

| Flag                       | Purpose                                                |
|----------------------------|--------------------------------------------------------|
| `--baseline PATH`          | Same as `EVAL_GATE_BASELINE` env override              |
| `--config PATH`            | Same as `EVAL_GATE_CONFIG` env override                |
| `--results PATH`           | Same as `EVAL_GATE_RESULTS` env override               |
| `--report PATH`            | Same as `EVAL_GATE_REPORT` env override                |
| `--inject-failure CATEGORY`| Force a citation-empty response for cases in CATEGORY (drill knob; can be passed multiple times) |

## Block-on-failure / overrides

`agent-eval` is `allow_failure: false`. There is no flag to bypass the
gate from the MR side — a failing run must be fixed (or the baseline /
threshold deliberately updated) before the pipeline goes green. If you
need to merge anyway in an emergency, the human override is to push a
commit that adjusts `eval_config.yaml` or
`tests/eval/baselines/week2.json` with a documented rationale (and a
DEVIATIONS.md entry).

## Image rationale

The job uses `python:3.12-slim` (via `.python_base`) rather than the
pre-baked sidecar image from Task 21. The mocks bypass the supervisor
entirely, so the HF model weights baked into the production image
provide no value here. When the real-LLM manual job lands, it will use
the pre-baked image — registry path is a pending decision (see
`docs/DEVIATIONS.md`).

## Mirror at GitHub Actions

Task 22 will mirror this into `.github/workflows/`. The mirror is
intentionally trivial: same `./scripts/run_eval_gate.sh`, same exit
codes, same artifact paths. Only the comment-posting step differs (`gh
pr comment` instead of the GitLab notes API).
