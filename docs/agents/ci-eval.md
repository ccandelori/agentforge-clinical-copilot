# CI eval gate (`agent-eval`)

The eval gate runs on every change to the codebase, in two CI systems:

- **GitLab MRs** — `agent-eval` job in `.gitlab-ci.yml` (canonical; see
  Task 20).
- **GitHub PRs** — `agent-eval` workflow in
  `.github/workflows/agent-eval.yml` (mirror; see Task 22). Triggers on
  every `pull_request` event regardless of base branch, plus
  `workflow_dispatch` for manual reruns from the Actions UI.

A failing gate blocks merge in either system.

**Both gates produce identical verdicts on the same commit** because
they invoke the same `sidecar/scripts/run_eval_gate.sh` driven by the
same `sidecar/eval_config.yaml`. There is no per-CI configuration. If
the GitLab and GitHub verdicts ever diverge, the divergence is a bug
in the surrounding CI machinery (image, env, dependency pinning), not
in the gate logic.

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
| `GLAB_TOKEN`          | GitLab project / group access token with `api` scope; enables MR comment posting on GitLab |
| `CI_JOB_TOKEN`        | GitLab fallback for MR-note posting when the instance allows it |
| `GITHUB_TOKEN`        | Auto-injected by GitHub Actions; used by the GitHub mirror to post the PR comment |

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

## GitHub Actions mirror

Why two gates? The Gauntlet cohort hosts on GitLab, but this repo is
also published as a public mirror at
`github.com/ccandelori/agentforge-clinical-copilot`. The mirror's PR
flow needs the same regression gate so external collaborators see the
same verdict GitLab MRs do.

The mirror lives at `.github/workflows/agent-eval.yml`. It is
deliberately a near-line-for-line equivalent of the GitLab job:

| Concern              | GitLab `agent-eval`                                | GitHub `agent-eval`                                                          |
|----------------------|----------------------------------------------------|------------------------------------------------------------------------------|
| Trigger              | `merge_request_event` + default-branch push        | `pull_request` (any branch) + `workflow_dispatch`                            |
| Image                | `python:3.12-slim` (`.python_base`)                | `python:3.12-slim` (job `container.image`)                                   |
| Bootstrap            | `pip install uv && cd sidecar && uv sync --frozen` | Same, split across `Install uv` + `Sync sidecar dependencies` steps          |
| Gate command         | `./scripts/run_eval_gate.sh`                       | `./sidecar/scripts/run_eval_gate.sh` (same script — different cwd)           |
| Artifacts            | `sidecar/var/eval_gate_results.json` + report      | Same paths, uploaded via `actions/upload-artifact@v7` (`if: always()`)       |
| Comment poster       | `curl` → GitLab `notes` API (after_script)         | `actions/github-script@v8` → `issues.createComment` (uses `GITHUB_TOKEN`)    |
| Block-on-failure     | `allow_failure: false` (explicit)                  | Workflow exit status governs the required-status-check on the GH repo       |

The shared `sidecar/scripts/run_eval_gate.sh` is the only place the
gate logic lives — neither workflow inlines a copy.

### Manual triggers

- **Open a PR** in the GitHub mirror — the workflow runs automatically.
- **Workflow dispatch** — go to the repo's Actions tab → `agent-eval`
  workflow → "Run workflow" button. Useful for verifying a baseline
  regen on the default branch without opening a no-op PR.

### Required status check

To enforce block-on-merge on the GitHub mirror, configure the repo's
branch protection rule for `main` to require the `agent-eval / agent-eval`
status check before merging. The workflow's exit status is the contract;
GitHub's branch-protection setting is what enforces it.

### Token permissions

The workflow declares only the minimum scopes:

- `contents: read` — `actions/checkout`
- `pull-requests: write` — `actions/github-script` posting the report
  as a PR comment (uses the auto-injected `GITHUB_TOKEN`; no PAT
  required)
