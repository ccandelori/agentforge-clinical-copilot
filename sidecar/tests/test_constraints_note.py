"""Constraint 3: note authorization echo.

ARCHITECTURE.md S6 #3 says the verifier double-checks that any cited
note was authorized for the requestor. **MVP simplification (per Task
29 spec):** the gateway already authorized everything in the per-turn
cache; the verifier's job here is only to confirm the citation points
at a record actually present. Re-authorization will be reintroduced
when notes ship as a tool (Task 22).
"""

from __future__ import annotations

from agentforge.verifier.citation import Citation
from agentforge.verifier.constraints import DomainConstraints


class TestNoteConstraint:
    def test_note_record_present_passes(self) -> None:
        # The structural verifier wouldn't have called us at all if the
        # citation didn't resolve in the cache; this test asserts that
        # the placeholder check is consistent with that contract.
        checker = DomainConstraints()
        passed, reason = checker.check(
            Citation(
                record_type="note",
                record_id="100",
                extra=None,
                raw="[note #100]",
            ),
            "Note documents stable findings [note #100].",
            {"id": 100, "body": "Patient is stable."},
        )
        assert passed is True
        assert reason is None

    def test_missing_record_dict_fails(self) -> None:
        # Defensive: if the structural layer somehow let us through with
        # a None record on a note citation, that's an integrity break —
        # fail closed.
        checker = DomainConstraints()
        passed, reason = checker.check(
            Citation(
                record_type="note",
                record_id="100",
                extra=None,
                raw="[note #100]",
            ),
            "Note says X [note #100].",
            None,
        )
        assert passed is False
        assert reason is not None
