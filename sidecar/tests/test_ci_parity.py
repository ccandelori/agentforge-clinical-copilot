"""CI parity invariant: both CI gates invoke the same shared script.

Task 22 added a GitHub Actions mirror (`agent-eval.yml`) of the GitLab
`agent-eval` job in `.gitlab-ci.yml`. Both must call the *same*
`sidecar/scripts/run_eval_gate.sh` so the two pipelines produce
identical verdicts on the same commit.

This test guards that invariant. If either CI file is edited to inline
its own eval-gate logic — duplicating the shared script's behaviour —
this test fails loudly. The fix is to keep the shared script as the
single entry point.

The test reads the YAML files directly (no GitHub / GitLab API calls)
and looks for the script name as a substring of any line. We don't
parse the YAML schemas because the two CI systems use very different
shapes (jobs/steps vs jobs/script); a substring match is the common
denominator.
"""

from __future__ import annotations

from pathlib import Path

# Repo root: sidecar/tests/test_ci_parity.py -> repo root is two parents up.
REPO_ROOT = Path(__file__).resolve().parents[2]

GITLAB_CI = REPO_ROOT / ".gitlab-ci.yml"
GITHUB_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "agent-eval.yml"
SHARED_SCRIPT_NAME = "run_eval_gate.sh"


def _ci_files_reference_script() -> list[Path]:
    """Return CI files (existence-checked) that should reference the script."""
    found: list[Path] = []
    for path in (GITLAB_CI, GITHUB_WORKFLOW):
        assert path.is_file(), f"Expected CI file missing: {path}"
        found.append(path)
    return found


def test_both_ci_systems_invoke_shared_eval_gate_script() -> None:
    """The GitLab job and the GitHub workflow both invoke the same script.

    If this test fails, one of the CI files has stopped delegating to
    `run_eval_gate.sh`. Fix the CI file rather than this test — the
    invariant is the whole point of the shared script.
    """
    for ci_file in _ci_files_reference_script():
        contents = ci_file.read_text(encoding="utf-8")
        assert SHARED_SCRIPT_NAME in contents, (
            f"{ci_file} no longer invokes {SHARED_SCRIPT_NAME}; both CI "
            f"systems must delegate to the shared eval-gate script so "
            f"their verdicts stay identical."
        )


def test_shared_script_exists_and_is_executable() -> None:
    """The shared script must be present and executable on disk."""
    script = REPO_ROOT / "sidecar" / "scripts" / SHARED_SCRIPT_NAME
    assert script.is_file(), f"Shared eval-gate script missing: {script}"
    # st_mode & 0o111 — any execute bit set on owner / group / other.
    assert script.stat().st_mode & 0o111, (
        f"{script} is not executable; CI invocations like "
        f"`./scripts/run_eval_gate.sh` will fail."
    )
