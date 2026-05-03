"""Baseline eval suite for the AgentForge orchestrator (Week 1 Task #21).

These tests are the safety net for the integration pass and the
streaming refactor: they invoke the real ``/agentforge/turn`` endpoint
end-to-end against the running stack and grade the response with a
deterministic grader. Whenever Tasks #4-#13 land a behavioral change,
re-running this suite tells us whether the agent still answers the
load-bearing user-flow questions sensibly.

Scope is intentionally minimal — a stake-in-the-ground baseline:

  * 7 hand-authored cases covering UC-1..UC-4 plus 3 adversarial
    probes (cross-patient, hallucinated drug, missing-data patient).
  * Deterministic grader only: status code, citation well-formedness,
    expected-term presence, forbidden-term absence, citation type
    coverage. NO LLM-as-judge — that lives in the larger #16 task.
  * Pytest @pytest.mark.eval marker, deselected from default runs.

The suite reuses ``tests/integration/conftest.py`` fixtures so it
inherits the same authentication + patient-context setup the live UC
tests use. Stack-down skips cleanly via the existing
``_wait_for_openemr`` gate.

Running::

    cd sidecar
    uv run pytest -m eval                    # this suite
    uv run pytest -m eval --tb=short -v      # with per-case detail

Override per-case behavior via env (rarely needed):

  AGENTFORGE_INT_OPENEMR_URL    base URL of the OpenEMR stack
  AGENTFORGE_INT_DEMO_PIDS      override default demo cohort

Replaces the old fixture-only ``tests/eval/harness.py`` as the primary
eval. The harness remains in-repo because the regression-locks suite
still uses it for lower-level primitive tests; this baseline sits on
top, not in place of.
"""
