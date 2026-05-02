"""IdentityGuard — detects when a user's chat message references a patient
other than the one whose chart is currently open.

The agent's authorization is bound to the patient whose chart is open at
session start (see AuthGateway in `agentforge.gateway.auth_gateway`). If
the user types "what about MRN 12345" or "patient Robert Johnson" mid-
conversation, naively answering would be a quiet auth-boundary violation:
the model could infer "they want me to look up that other patient" and
start dispatching tools with a patient_id mismatch.

This guard inspects the raw message text BEFORE any tool fires and, on
any cross-patient reference, returns a refusal so the orchestrator skips
the turn.

Design decisions:

  * Conservative name matching. We use case-insensitive token overlap
    against `current_patient_name`. False negatives (missed references)
    are safer than false positives (refusing the legitimate chart owner)
    because the real auth boundary lives in the tool layer — this guard
    is a usability layer, not a security one.

  * Room/bed references always refuse. We have no way to map a room
    number to a patient identity at this layer, so any room/bed mention
    mid-session is treated as ambiguous.

  * Refusals never echo the disallowed reference back. We only mention
    the *current* patient in the refusal text, so the model can't
    accidentally chain on the disallowed identity in subsequent turns.

  * Frozen dataclasses everywhere — both the guard and the result are
    value objects with no mutable state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import final

# Module-level regex constants. Keeping these at module scope (rather than
# class-level) lets `re.compile` execute exactly once at import time.

# `patient` / `pt` / `pt.` (case-insensitive) followed by TWO Title-Cased
# name tokens. Requiring two tokens ("Susan Underwood", "Susan U") rules
# out idioms ("patient zero", "patient in room 401") without an explicit
# denylist. Title-Case on the name tokens is intentional: users writing
# "patient in room 401" or "patient zero" use lowercase common nouns,
# which the pattern won't match. Case-insensitive *comparison* against
# the current patient is handled in `_matches_current_name` via casefold.
#
# Token grammar:
#   * First token: capital letter then >=1 lowercase letters
#     (real names rather than initials).
#   * Second token: capital letter then >=0 lowercase letters, optional
#     trailing period — this matches surnames AND single-letter abbreviations
#     like "Susan U" or "Susan U.".
_PATIENT_NAME_PATTERN = re.compile(
    r"\b(?i:patient|pt\.?)\s+([A-Z][a-z][a-z\-']*(?:\s+[A-Z][a-z\-']*\.?)+)",
)

# MRN with optional `:` or `#` and optional whitespace. Allows alphanumeric
# MRNs (some sites use formats like "ABC-42").
_MRN_PATTERN = re.compile(
    r"\bMRN\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9\-]*)",
    re.IGNORECASE,
)

# Room/bed identifier — digits with an optional trailing letter (401, 12A, 5b).
_ROOM_PATTERN = re.compile(
    r"\b(?:room|bed)\s+(\d+[A-Za-z]?)\b",
    re.IGNORECASE,
)


@final
@dataclass(frozen=True, slots=True)
class IdentityCheckResult:
    """Outcome of a single message inspection.

    `matched_pattern` records which kind of reference fired the refusal
    ("patient_name", "mrn", or "room") so the orchestrator can emit
    structured telemetry without re-parsing the message.
    """

    is_valid: bool
    refusal_reason: str | None
    matched_pattern: str | None


@final
@dataclass(frozen=True, slots=True)
class IdentityGuard:
    """Inspects user messages for cross-patient references.

    Inject this once per session/turn with the chart-owner's identity.
    Both the guard and its results are immutable value objects.
    """

    current_patient_name: str
    current_mrn: str

    def check_message(self, message: str) -> IdentityCheckResult:
        """Return a verdict for `message`.

        First-failure-wins: scan name → MRN → room in that order, return
        the first refusal we find. Telemetry only needs to know that the
        turn was refused and which family of pattern fired; the
        orchestrator should not retry partial matches.
        """
        if not message:
            return _VALID

        name_result = self._check_name(message)
        if not name_result.is_valid:
            return name_result

        mrn_result = self._check_mrn(message)
        if not mrn_result.is_valid:
            return mrn_result

        room_result = self._check_room(message)
        if not room_result.is_valid:
            return room_result

        return _VALID

    # ---------- pattern checks ----------

    def _check_name(self, message: str) -> IdentityCheckResult:
        for match in _PATIENT_NAME_PATTERN.finditer(message):
            referenced = match.group(1).strip()
            if not self._matches_current_name(referenced):
                return IdentityCheckResult(
                    is_valid=False,
                    refusal_reason=self._refusal(),
                    matched_pattern="patient_name",
                )
        return _VALID

    def _check_mrn(self, message: str) -> IdentityCheckResult:
        for match in _MRN_PATTERN.finditer(message):
            referenced = match.group(1).strip()
            if referenced.casefold() != self.current_mrn.casefold():
                return IdentityCheckResult(
                    is_valid=False,
                    refusal_reason=self._refusal(),
                    matched_pattern="mrn",
                )
        return _VALID

    def _check_room(self, message: str) -> IdentityCheckResult:
        if _ROOM_PATTERN.search(message) is not None:
            return IdentityCheckResult(
                is_valid=False,
                refusal_reason=self._refusal(),
                matched_pattern="room",
            )
        return _VALID

    # ---------- helpers ----------

    def _matches_current_name(self, reference: str) -> bool:
        """Conservative case-insensitive match.

        We accept the reference as the current patient if any of:

          * the reference is a substring of the current name, or
          * the current name is a substring of the reference, or
          * any token of the reference appears as a token in the
            current name.

        The token-overlap branch handles cases like "patient Susan U"
        when the chart owner is "Susan Underwood": "Susan" is a shared
        token, so we let it through. False negatives are acceptable
        here; the tool layer is the authoritative auth boundary.
        """
        ref = reference.casefold().strip()
        current = self.current_patient_name.casefold().strip()
        if not ref or not current:
            return False
        if ref in current or current in ref:
            return True

        ref_tokens = set(ref.split())
        current_tokens = set(current.split())
        return bool(ref_tokens & current_tokens)

    def _refusal(self) -> str:
        # Mention only the current patient. Echoing the disallowed
        # reference back to the user (or to the model on the next turn)
        # could itself leak which alternate identity was guessed at.
        return (
            f"This conversation is scoped to {self.current_patient_name}. "
            "To ask about a different patient, please open that patient's chart."
        )


_VALID: IdentityCheckResult = IdentityCheckResult(
    is_valid=True, refusal_reason=None, matched_pattern=None
)
