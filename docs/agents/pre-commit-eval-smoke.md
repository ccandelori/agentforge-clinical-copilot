# Pre-commit eval-smoke hook

The `agentforge-eval-smoke` pre-commit hook runs a 10-case subset of
the W2 eval suite on every commit that touches `sidecar/src/`,
`sidecar/tests/`, or `sidecar/pyproject.toml`. The hook spends no
Anthropic tokens (supervisor + LLM judge are mocked) and finishes in
well under a second on a warm cache; the wall-clock budget is 30
seconds.

Source of truth:

- Hook config: `.pre-commit-config.yaml` (id `agentforge-eval-smoke`)
- Test module: `sidecar/tests/eval/gate/test_eval_smoke.py`
- Tagged cases: `sidecar/tests/eval/cases/week2/*.yaml` (look for
  `tags: [eval_smoke]` — exactly two cases per category)
- Marker registration: `sidecar/pyproject.toml`
  (`[tool.pytest.ini_options].markers`)

## Manual invocation

```bash
cd sidecar
uv run pytest -m eval_smoke -q
```

Expected: `11 passed, ... deselected` in under a second. (10 per-case
parametrized tests plus 1 suite-level budget guard.)

## Running through the hook

If `prek` or `pre-commit` is installed locally:

```bash
prek run agentforge-eval-smoke --all-files
# or
pre-commit run agentforge-eval-smoke --all-files
```

## Skip paths

There are two ways to bypass the hook for a single commit. Both are
appropriate when the hook is misbehaving or unavailable (e.g. `uv` not
on PATH on a contributor's machine); neither is appropriate as a
default workflow.

### `SKIP=...` — pre-commit-aware

```bash
SKIP=agentforge-eval-smoke git commit -m "..."
```

This works with both `prek` and `pre-commit`. It runs every other hook
and only skips the named one, so PHP/PHPStan/codespell still gate the
commit.

### `--no-verify` — git-native

```bash
git commit --no-verify -m "..."
```

This bypasses *every* hook, not just the smoke suite. Use only when
multiple hooks are broken or you are intentionally landing a hotfix
that you have validated manually. Pair with a follow-up commit that
re-runs the full hook stack to catch what was skipped.

## When to update the smoke set

The 10 selected cases are deliberately chosen to span failure modes
across all five W2 categories (extraction, evidence_retrieval,
citations, refusal, missing_data). Re-balancing the set is reasonable
when:

- A new category is added to `EvalCategory`. Add two representative
  cases under that category to the smoke set so the hook covers it.
- A particular failure mode keeps slipping through into the full eval
  gate but isn't caught at smoke time. Swap an existing pick for one
  that tests the missed mode.
- A case becomes flaky under the mocked harness (the mock pipeline
  should be deterministic — flakiness is a real bug to fix, not a
  reason to drop the case).

To change the set: edit the `tags: [eval_smoke]` lines in the relevant
YAML files. The selector tests in
`sidecar/tests/eval/test_yaml_cases.py::TestTagsRoundTrip` enforce the
"exactly 10, two per category, all five categories" invariants — they
will fail loudly if a swap goes wrong.
