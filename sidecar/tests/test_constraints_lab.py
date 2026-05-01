"""Constraint 2: lab value tolerance.

ARCHITECTURE.md S6 #2: numeric lab values match to 2 decimal places;
textual values match verbatim. A claim citing a lab record must include
the lab value (numeric or text) and that value must agree with the
cited row.
"""

from __future__ import annotations

from agentforge.verifier.citation import Citation
from agentforge.verifier.constraints import DomainConstraints


def _citation(record_id: str = "42") -> Citation:
    return Citation(
        record_type="lab_result",
        record_id=record_id,
        extra=None,
        raw=f"[lab_result #{record_id}]",
    )


class TestLabNumericTolerance:
    def test_numeric_match_at_two_decimals(self) -> None:
        checker = DomainConstraints()
        passed, reason = checker.check(
            _citation(),
            "Most recent A1C is 9.40 [lab_result #42].",
            {"id": 42, "value": "9.4"},
        )
        assert passed is True
        assert reason is None

    def test_numeric_within_tolerance(self) -> None:
        # 9.40 vs 9.4 — same to two decimal places; passes.
        checker = DomainConstraints()
        passed, reason = checker.check(
            _citation(),
            "A1C 9.40 [lab_result #42].",
            {"id": 42, "value": 9.4},
        )
        assert passed is True
        assert reason is None

    def test_numeric_outside_tolerance(self) -> None:
        # 9.41 vs 9.4 — not equal to two decimals; fails.
        checker = DomainConstraints()
        passed, reason = checker.check(
            _citation(),
            "A1C 9.41 [lab_result #42].",
            {"id": 42, "value": "9.4"},
        )
        assert passed is False
        assert reason == "lab_value_outside_tolerance"

    def test_numeric_with_units_in_claim_strips_units(self) -> None:
        # Claim "9.4%"; record "9.4". The percent sign and other unit
        # tokens are not numerically meaningful for the equality check.
        checker = DomainConstraints()
        passed, reason = checker.check(
            _citation(),
            "A1C 9.4% [lab_result #42].",
            {"id": 42, "value": "9.4"},
        )
        assert passed is True
        assert reason is None


class TestLabTextualMatch:
    def test_textual_exact_match_passes(self) -> None:
        checker = DomainConstraints()
        passed, reason = checker.check(
            _citation(),
            "Strep test negative [lab_result #42].",
            {"id": 42, "value": "negative"},
        )
        assert passed is True
        assert reason is None

    def test_textual_mismatch_fails(self) -> None:
        checker = DomainConstraints()
        passed, reason = checker.check(
            _citation(),
            "Strep test negative [lab_result #42].",
            {"id": 42, "value": "positive"},
        )
        assert passed is False
        assert reason == "lab_value_mismatch"

    def test_textual_match_is_case_insensitive(self) -> None:
        # "Negative" vs "negative" — same finding. Case folding is the
        # MVP rule; "verbatim" in the spec means token-equal, not
        # byte-equal.
        checker = DomainConstraints()
        passed, reason = checker.check(
            _citation(),
            "Strep test Negative [lab_result #42].",
            {"id": 42, "value": "negative"},
        )
        assert passed is True
        assert reason is None


class TestLabMixedTypes:
    def test_numeric_claim_against_textual_record_fails(self) -> None:
        # Claim "9.4" cited against a record carrying "negative" is a
        # type-mismatch — neither tolerance check applies, but the claim
        # is unsupported by the record.
        checker = DomainConstraints()
        passed, reason = checker.check(
            _citation(),
            "A1C 9.4 [lab_result #42].",
            {"id": 42, "value": "negative"},
        )
        assert passed is False
        assert reason == "lab_value_mismatch"

    def test_textual_claim_against_numeric_record_fails(self) -> None:
        checker = DomainConstraints()
        passed, reason = checker.check(
            _citation(),
            "Strep test negative [lab_result #42].",
            {"id": 42, "value": "9.4"},
        )
        assert passed is False
        assert reason == "lab_value_mismatch"


class TestLabClaimWithoutValue:
    def test_claim_with_no_value_passes(self) -> None:
        # If the claim merely names the lab without quoting a value
        # ("checked A1C [lab_result #42]"), there is nothing for this
        # constraint to falsify. The cache lookup is the floor.
        checker = DomainConstraints()
        passed, reason = checker.check(
            _citation(),
            "Recent A1C on file [lab_result #42].",
            {"id": 42, "value": "9.4"},
        )
        assert passed is True
        assert reason is None

    def test_missing_record_value_field_fails_safe(self) -> None:
        checker = DomainConstraints()
        passed, reason = checker.check(
            _citation(),
            "A1C 9.4 [lab_result #42].",
            {"id": 42},
        )
        assert passed is False
        assert reason == "lab_value_mismatch"
