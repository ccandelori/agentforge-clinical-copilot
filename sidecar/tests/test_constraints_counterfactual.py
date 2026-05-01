"""Constraint 5: no counterfactuals without supporting note.

ARCHITECTURE.md S6 #5: missing data is "not on file." The verifier
rejects "patient denies X" or "no history of Y" unless there is a
literal note documenting it. For MVP, that means: counterfactual
claims must cite a `note` record whose body contains the negation.
Until notes ship as a tool (Task 22), any counterfactual that cites
a non-note record fails closed.

False positives on the negation pattern are fine; false negatives are
not — better to over-flag and force re-citation than to let an
unsupported "patient denies smoking" through.
"""

from __future__ import annotations

from agentforge.verifier.citation import Citation
from agentforge.verifier.constraints import DomainConstraints


class TestCounterfactualConstraint:
    def test_denies_phrase_without_note_citation_fails(self) -> None:
        # "patient denies smoking" cited to a problem row is structurally
        # fine but substantively wrong — the problem row is a positive
        # finding ("CHF"), not a negation. A denial belongs in a note.
        checker = DomainConstraints()
        passed, reason = checker.check(
            Citation(
                record_type="problem",
                record_id="5",
                extra=None,
                raw="[problem #5]",
            ),
            "Patient denies smoking [problem #5].",
            {"id": 5, "title": "CHF"},
        )
        assert passed is False
        assert reason == "counterfactual_without_supporting_note"

    def test_no_history_phrase_without_note_citation_fails(self) -> None:
        checker = DomainConstraints()
        passed, reason = checker.check(
            Citation(
                record_type="problem",
                record_id="5",
                extra=None,
                raw="[problem #5]",
            ),
            "No history of CHF [problem #5].",
            {"id": 5, "title": "CHF"},
        )
        assert passed is False
        assert reason == "counterfactual_without_supporting_note"

    def test_affirmative_claim_passes_counterfactual_check(self) -> None:
        # No negation phrase in the claim → constraint doesn't fire.
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

    def test_counterfactual_cited_to_note_with_matching_body_passes(self) -> None:
        # The forward-compatible path: when notes do ship, a counterfactual
        # cited to a note whose body contains the negation passes.
        checker = DomainConstraints()
        passed, reason = checker.check(
            Citation(
                record_type="note",
                record_id="100",
                extra=None,
                raw="[note #100]",
            ),
            "Patient denies smoking [note #100].",
            {"id": 100, "body": "Patient denies smoking, alcohol, drug use."},
        )
        assert passed is True
        assert reason is None

    def test_counterfactual_cited_to_note_without_matching_body_fails(self) -> None:
        # Counterfactual cited to a note whose body does NOT mention the
        # denial → fail. The note has to actually carry the negation.
        checker = DomainConstraints()
        passed, reason = checker.check(
            Citation(
                record_type="note",
                record_id="100",
                extra=None,
                raw="[note #100]",
            ),
            "Patient denies smoking [note #100].",
            {"id": 100, "body": "Routine follow-up. Vitals stable."},
        )
        assert passed is False
        assert reason == "counterfactual_without_supporting_note"

    def test_counterfactual_negative_phrase_no_history_of_y(self) -> None:
        checker = DomainConstraints()
        passed, reason = checker.check(
            Citation(
                record_type="medication",
                record_id="10",
                extra=None,
                raw="[medication #10]",
            ),
            "No history of beta-blocker use [medication #10].",
            {"id": 10, "name": "lisinopril 20mg"},
        )
        assert passed is False
        assert reason == "counterfactual_without_supporting_note"

    def test_phrase_not_taking_is_counterfactual(self) -> None:
        # "Not taking" / "not on" are the most common negations in the
        # medication-discontinuation phrasing. Conservative pattern
        # match — better one false-positive than missing a fabrication.
        checker = DomainConstraints()
        passed, reason = checker.check(
            Citation(
                record_type="medication",
                record_id="10",
                extra=None,
                raw="[medication #10]",
            ),
            "Patient is not on lisinopril [medication #10].",
            {"id": 10, "name": "lisinopril 20mg"},
        )
        assert passed is False
        assert reason == "counterfactual_without_supporting_note"
