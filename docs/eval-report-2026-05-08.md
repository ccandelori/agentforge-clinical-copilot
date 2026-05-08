# AgentForge Eval Report — 2026-05-08 (W2)

## Executive summary

_TODO (31.1 outline)._

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

_TODO (31.3)._

## Cost analysis (projection)

_TODO (31.3)._

## Open follow-ups

_TODO (31.4)._

## Artifacts

_TODO (31.4)._
