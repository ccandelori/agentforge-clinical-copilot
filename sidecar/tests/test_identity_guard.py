"""Behavior tests for IdentityGuard — detects when a user's chat message
references a different patient than the one whose chart is currently open.

The agent's auth is bound to the patient whose chart is open. If the user
types "what about MRN 12345" mid-conversation while a different chart is
open, naively answering would be a quiet auth-boundary violation. The
guard inspects the message text and produces a refusal so the orchestrator
doesn't fire any tools.

Task 42 (Implement Identity Ambiguity Detection).
"""

from __future__ import annotations

import pytest

from agentforge.orchestrator.identity_guard import IdentityCheckResult, IdentityGuard


@pytest.fixture
def guard() -> IdentityGuard:
    """Default guard scoped to 'Susan Underwood', MRN '900123'."""
    return IdentityGuard(current_patient_name="Susan Underwood", current_mrn="900123")


# ---------- positive: nothing to flag ----------


def test_message_with_no_patient_reference_passes(guard: IdentityGuard) -> None:
    result = guard.check_message("What was the last A1C value?")
    assert result == IdentityCheckResult(
        is_valid=True, refusal_reason=None, matched_pattern=None
    )


def test_empty_message_passes(guard: IdentityGuard) -> None:
    result = guard.check_message("")
    assert result.is_valid is True
    assert result.refusal_reason is None
    assert result.matched_pattern is None


# ---------- positive: references the *current* patient ----------


def test_message_naming_current_patient_passes(guard: IdentityGuard) -> None:
    result = guard.check_message("Has patient Susan Underwood ever been on metformin?")
    assert result.is_valid is True


def test_message_with_partial_first_name_passes(guard: IdentityGuard) -> None:
    # "Susan" should match "Susan Underwood" — token overlap is enough,
    # we'd rather false-negative than false-positive.
    result = guard.check_message("What about pt. Susan U's last visit?")
    assert result.is_valid is True


def test_case_insensitive_match_for_current_patient(guard: IdentityGuard) -> None:
    # The keyword is matched case-insensitively; realistic name input is
    # Title-Case. We only need the *comparison* against the current
    # patient (stored as "Susan Underwood") to be case-insensitive.
    guard_lower = IdentityGuard(
        current_patient_name="susan underwood", current_mrn="900123"
    )
    result = guard_lower.check_message("any update on Patient Susan Underwood?")
    assert result.is_valid is True


def test_current_patient_mrn_passes(guard: IdentityGuard) -> None:
    result = guard.check_message("Confirm MRN: 900123 — same chart, right?")
    assert result.is_valid is True


def test_current_patient_mrn_no_separator_passes(guard: IdentityGuard) -> None:
    result = guard.check_message("the chart for MRN 900123")
    assert result.is_valid is True


def test_current_patient_mrn_with_hash_passes(guard: IdentityGuard) -> None:
    result = guard.check_message("see mrn #900123")
    assert result.is_valid is True


# ---------- negative: a *different* patient ----------


def test_message_naming_different_patient_is_refused(guard: IdentityGuard) -> None:
    result = guard.check_message("What about patient Robert Johnson's labs?")
    assert result.is_valid is False
    assert result.matched_pattern == "patient_name"
    # refusal must mention current patient...
    assert result.refusal_reason is not None
    assert "Susan Underwood" in result.refusal_reason
    # ...and NOT echo the disallowed reference back.
    assert "Robert" not in result.refusal_reason
    assert "Johnson" not in result.refusal_reason


def test_message_with_different_mrn_is_refused(guard: IdentityGuard) -> None:
    result = guard.check_message("Pull up MRN 12345 quickly")
    assert result.is_valid is False
    assert result.matched_pattern == "mrn"
    assert result.refusal_reason is not None
    assert "Susan Underwood" in result.refusal_reason
    assert "12345" not in result.refusal_reason


def test_room_reference_is_always_refused(guard: IdentityGuard) -> None:
    # We can't tie a room number to a patient identity, so any room/bed
    # reference mid-session is treated as ambiguous and refused.
    result = guard.check_message("Is the patient in room 401 stable?")
    assert result.is_valid is False
    assert result.matched_pattern == "room"
    assert result.refusal_reason is not None


def test_bed_reference_is_always_refused(guard: IdentityGuard) -> None:
    result = guard.check_message("vitals for bed 12A please")
    assert result.is_valid is False
    assert result.matched_pattern == "room"


def test_room_reference_with_letter_suffix_refused(guard: IdentityGuard) -> None:
    result = guard.check_message("room 5b update")
    assert result.is_valid is False
    assert result.matched_pattern == "room"


# ---------- edge cases ----------


def test_patient_zero_does_not_trigger_name_pattern(guard: IdentityGuard) -> None:
    # "patient zero" is not a name reference; word-boundary regex must
    # avoid matching common idioms.
    result = guard.check_message("she's basically patient zero for this protocol")
    assert result.is_valid is True


def test_case_insensitive_match_in_both_directions() -> None:
    # Current name stored UPPERCASE; reference Title-Cased. Comparison
    # must casefold both sides.
    guard = IdentityGuard(current_patient_name="MARIA GARCIA", current_mrn="ABC-42")
    result = guard.check_message("what about Pt. Maria Garcia today?")
    assert result.is_valid is True


def test_alphanumeric_mrn_match() -> None:
    guard = IdentityGuard(current_patient_name="John Doe", current_mrn="ABC-42")
    same_chart = guard.check_message("ref MRN ABC-42")
    assert same_chart.is_valid is True

    other_chart = guard.check_message("ref MRN ABC-43")
    assert other_chart.is_valid is False
    assert other_chart.matched_pattern == "mrn"


def test_multiple_references_with_one_invalid_fails(guard: IdentityGuard) -> None:
    # Mention the current patient AND a different MRN — any invalid
    # reference must fail the whole turn.
    result = guard.check_message(
        "Susan Underwood looks good; also can you pull MRN 77777?"
    )
    assert result.is_valid is False
    assert result.matched_pattern == "mrn"


def test_result_is_frozen() -> None:
    result = IdentityCheckResult(
        is_valid=True, refusal_reason=None, matched_pattern=None
    )
    with pytest.raises((AttributeError, TypeError)):
        # frozen dataclass — assignment must fail
        result.is_valid = False  # type: ignore[misc]


def test_guard_is_frozen() -> None:
    guard = IdentityGuard(current_patient_name="A B", current_mrn="1")
    with pytest.raises((AttributeError, TypeError)):
        guard.current_mrn = "2"  # type: ignore[misc]
