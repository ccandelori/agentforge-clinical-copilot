"""Constraint 1: medication name (and dose) match the cited record.

ARCHITECTURE.md S6 #1: any medication name must match a row in the
cited prescription record. For MVP, dose match is exact-string —
"lisinopril 20mg" cited against a record carrying "lisinopril 20mg"
passes; "lisinopril 10mg" against the same record fails. A dose-less
claim against a dose-bearing record passes (the LLM is allowed to omit
the dose; what we forbid is *naming a wrong dose*).
"""

from __future__ import annotations

from agentforge.verifier.citation import Citation
from agentforge.verifier.constraints import DomainConstraints


def _citation(record_id: str = "10") -> Citation:
    return Citation(
        record_type="medication",
        record_id=record_id,
        extra=None,
        raw=f"[medication #{record_id}]",
    )


class TestMedicationConstraint:
    def test_name_and_dose_both_match(self) -> None:
        checker = DomainConstraints()
        passed, reason = checker.check(
            _citation(),
            "Patient on lisinopril 20mg [medication #10].",
            {"id": 10, "name": "lisinopril 20mg"},
        )
        assert passed is True
        assert reason is None

    def test_dose_mismatch_is_rejected(self) -> None:
        checker = DomainConstraints()
        passed, reason = checker.check(
            _citation(),
            "Patient on lisinopril 10mg [medication #10].",
            {"id": 10, "name": "lisinopril 20mg"},
        )
        assert passed is False
        assert reason == "medication_dose_mismatch"

    def test_name_only_claim_against_record_with_dose_passes(self) -> None:
        checker = DomainConstraints()
        passed, reason = checker.check(
            _citation(),
            "Patient on metoprolol [medication #10].",
            {"id": 10, "name": "metoprolol 25mg"},
        )
        assert passed is True
        assert reason is None

    def test_name_mismatch_is_rejected(self) -> None:
        checker = DomainConstraints()
        passed, reason = checker.check(
            _citation(),
            "Patient on lisinopril [medication #10].",
            {"id": 10, "name": "metoprolol"},
        )
        assert passed is False
        assert reason == "medication_name_mismatch"

    def test_no_dose_pattern_in_claim_passes_when_record_exists(self) -> None:
        # If the claim text has no recognizable medication-with-dose pattern
        # and the record exists, there's nothing for this constraint to
        # verify — pass through. The cache lookup already proved the record.
        checker = DomainConstraints()
        passed, reason = checker.check(
            _citation(),
            "Patient takes their cardiac med [medication #10].",
            {"id": 10, "name": "lisinopril 20mg"},
        )
        assert passed is True
        assert reason is None

    def test_missing_record_dict_fails_safe(self) -> None:
        # A None record means the cache had no payload for this key —
        # the structural check shouldn't have let us this far, but if it
        # does, this constraint must not crash.
        checker = DomainConstraints()
        passed, reason = checker.check(
            _citation(),
            "Patient on lisinopril 20mg [medication #10].",
            None,
        )
        assert passed is False
        assert reason == "medication_name_mismatch"

    def test_dose_match_is_case_insensitive(self) -> None:
        # The model may emit "Lisinopril 20MG"; the record carries
        # "lisinopril 20mg". Case folding is the conservative MVP rule.
        checker = DomainConstraints()
        passed, reason = checker.check(
            _citation(),
            "Patient on Lisinopril 20MG [medication #10].",
            {"id": 10, "name": "lisinopril 20mg"},
        )
        assert passed is True
        assert reason is None
