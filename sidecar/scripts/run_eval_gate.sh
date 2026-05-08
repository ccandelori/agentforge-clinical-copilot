#!/usr/bin/env bash
# Eval-gate CI wrapper (Task 20.1).
#
# Thin shim around the W2 eval gate's Python entry point so the same
# shell command works from GitLab CI, GitHub Actions (Task 22), or a
# human terminal. Heavy lifting lives in tests/eval/gate/cli.py — keep
# this file boring so the GH-Actions mirror is a one-line copy.
#
# Usage (run from repo root or sidecar/):
#   ./scripts/run_eval_gate.sh                   # default paths
#   ./scripts/run_eval_gate.sh --inject-failure extraction   # drill
#
# Environment overrides (all optional):
#   EVAL_GATE_RESULTS    where to write the JSON verdict
#                        (default: $SIDECAR_DIR/var/eval_gate_results.json)
#   EVAL_GATE_REPORT     where to write the markdown diff report
#                        (default: $SIDECAR_DIR/var/eval_gate_report.md)
#   EVAL_GATE_BASELINE   baseline JSON path (default: pinned week2.json)
#   EVAL_GATE_CONFIG     eval_config.yaml path (default: pinned)
#
# Exit codes mirror the Python entry point:
#   0  gate passed
#   1  gate failed (any violation)
#   2  invocation error (missing config, no cases loaded, bad args)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIDECAR_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VAR_DIR="$SIDECAR_DIR/var"
mkdir -p "$VAR_DIR"

RESULTS="${EVAL_GATE_RESULTS:-$VAR_DIR/eval_gate_results.json}"
REPORT="${EVAL_GATE_REPORT:-$VAR_DIR/eval_gate_report.md}"

EXTRA_ARGS=()
if [[ -n "${EVAL_GATE_BASELINE:-}" ]]; then
    EXTRA_ARGS+=(--baseline "$EVAL_GATE_BASELINE")
fi
if [[ -n "${EVAL_GATE_CONFIG:-}" ]]; then
    EXTRA_ARGS+=(--config "$EVAL_GATE_CONFIG")
fi

cd "$SIDECAR_DIR"
# ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} expands to nothing when EXTRA_ARGS
# is empty — required because `set -u` treats `"${EMPTY[@]}"` on bash <
# 4.4 as referencing an unset variable.
exec uv run python -m tests.eval.gate.cli \
    --results "$RESULTS" \
    --report "$REPORT" \
    ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} \
    "$@"
