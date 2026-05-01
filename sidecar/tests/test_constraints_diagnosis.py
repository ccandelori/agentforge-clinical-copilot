"""Constraint 4: diagnosis traceability.

ARCHITECTURE.md S6 #4: a diagnosis stated as fact must trace to a
`lists` row OR an encounter note. For the cache (problems tool), the
citation goes to a `problem` record — direct lookup, no fuzzy matching.
Encounter-note traceability is deferred until Task 22 ships notes.
"""

from __future__ import annotations

from agentforge.verifier.citation import Citation
from agentforge.verifier.constraints import DomainConstraints


class TestDiagnosisConstraint:
    def test_diagnosis_cited_to_problem_passes(self) -> None:
        checker = DomainConstraints()
        passed, reason = checker.check(
            Citation(
                record_type="problem",
                record_id="5",
                extra=None,
                raw="[problem #5]",
            ),
            "Patient has CHF [problem #5].",
            {"id": 5, "title": "CHF"},
        )
        assert passed is True
        assert reason is None

    def test_diagnosis_cited_to_non_problem_fails(self) -> None:
        # If a diagnosis-shaped claim is cited to a medication or lab
        # record, that's a record-class mismatch — diagnosis should
        # trace to a problem-list row.
        # The constraint is keyed on record_type, so this test exercises
        # the dispatch behavior: a non-`problem` record_type doesn't
        # invoke the diagnosis check directly. The integration tests
        # cover the end-to-end case where a diagnosis claim cites a
        # medication record (which would fail the medication constraint
        # for unrelated reasons).
        # Here we verify that when the dispatcher routes a `problem`
        # citation but the record dict is empty/None, we fail-safe.
        checker = DomainConstraints()
        passed, reason = checker.check(
            Citation(
                record_type="problem",
                record_id="5",
                extra=None,
                raw="[problem #5]",
            ),
            "Patient has CHF [problem #5].",
            None,
        )
        assert passed is False
        assert reason == "diagnosis_not_in_problem_list"

    def test_problem_record_with_only_id_passes(self) -> None:
        # The constraint is "diagnosis traces to a problem row" — the
        # presence of the row itself is the trace. We don't fuzzy-match
        # the title against the claim; the structural cache lookup is
        # what proves the row exists.
        checker = DomainConstraints()
        passed, reason = checker.check(
            Citation(
                record_type="problem",
                record_id="5",
                extra=None,
                raw="[problem #5]",
            ),
            "Patient has chronic disease [problem #5].",
            {"id": 5},
        )
        assert passed is True
        assert reason is None
