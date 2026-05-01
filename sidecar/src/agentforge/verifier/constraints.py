"""Domain constraint checks for the streaming verifier (Task 29).

Implements the five clinical-substance grounding checks called out in
ARCHITECTURE.md S6:

  1. Medication name (and dose) match the cited prescription record.
  2. Lab value tolerance — numeric to two decimals, textual verbatim.
  3. Note authorization echo (MVP placeholder; the gateway already
     authorized cache contents — this confirms the citation points at
     a present record).
  4. Diagnosis traceability — a diagnosis claim cited to a `problem`
     record passes by direct lookup; encounter-note traceability is
     deferred until Task 22 ships notes.
  5. No counterfactuals — claims like "patient denies X" or "no history
     of Y" must cite a `note` whose body contains the negation.

Stable ``rejection_reason`` tags (telemetry will key off these — do not
rename without coordinated downstream updates):

  * ``medication_dose_mismatch``
  * ``medication_name_mismatch``
  * ``lab_value_mismatch``
  * ``lab_value_outside_tolerance``
  * ``diagnosis_not_in_problem_list``
  * ``counterfactual_without_supporting_note``

The Protocol is sync (see docs/DEVIATIONS.md 2026-05-01: "Streaming
verifier DomainConstraintChecker is sync, not async"). None of these
constraints touch I/O — they're regex matches against the record dict
already in memory.
"""

from __future__ import annotations

import re
from typing import Any, Final

from agentforge.verifier.citation import Citation

# Stable rejection reasons. Telemetry tags off these — keep them stable.
REASON_MEDICATION_DOSE_MISMATCH: Final = "medication_dose_mismatch"
REASON_MEDICATION_NAME_MISMATCH: Final = "medication_name_mismatch"
REASON_LAB_VALUE_MISMATCH: Final = "lab_value_mismatch"
REASON_LAB_VALUE_OUTSIDE_TOLERANCE: Final = "lab_value_outside_tolerance"
REASON_DIAGNOSIS_NOT_IN_PROBLEM_LIST: Final = "diagnosis_not_in_problem_list"
REASON_COUNTERFACTUAL_WITHOUT_NOTE: Final = "counterfactual_without_supporting_note"

# Medication-with-dose pattern: a word followed by a number-then-unit
# token (mg, mcg, g, ml, units, iu). Matches "lisinopril 20mg",
# "metformin 500 mg", "warfarin 2.5mg", "insulin 10 units". The unit
# alternation is conservative — if the model emits a dose without a
# recognized unit we treat the claim as name-only and pass through to
# the cache check.
MEDICATION_DOSE_PATTERN: Final = re.compile(
    r"\b(?P<name>[A-Za-z][A-Za-z\-]+)\s+(?P<dose>\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|units?|iu))\b",
    re.IGNORECASE,
)

# Numeric value pattern: a decimal preceded by start-of-string or a
# non-alnum char (so "A1C" doesn't yield "1", and "lab_result #42"
# inside a citation doesn't yield "42" — but only if the citation token
# is stripped first, which the comparison routine does).
LAB_NUMERIC_PATTERN: Final = re.compile(r"(?:^|(?<=[^A-Za-z0-9.]))(-?\d+(?:\.\d+)?)")

# Citation-token stripper. The claim text passed to constraints includes
# the citation token (e.g. "[lab_result #42]"); when extracting a lab
# value we want only the prose part so a citation ID doesn't masquerade
# as a value. Reuses the same grammar as the citation parser.
_CITATION_STRIP: Final = re.compile(
    r"\[[A-Za-z][A-Za-z0-9_]*\s+#[A-Za-z0-9_\-]+(?:,\s*[^\]]+)?\]"
)

# Counterfactual / negation phrases. Any substring match fires the
# constraint. Conservative on purpose: false positives just force a
# cleaner re-citation, false negatives let fabricated denials through.
# Phrases like "without" are deliberately omitted — too noisy on their
# own ("admitted without complaint") and we don't want to over-flag.
COUNTERFACTUAL_PHRASES: Final = (
    "denies",
    "denied",
    "no history of",
    "not on",
    "not taking",
    "no evidence of",
    "negative for",
)


class DomainConstraints:
    """The five-constraint domain checker. Drop-in for the Protocol seam.

    Dispatches by ``citation.record_type`` to a per-type substance
    check, then unconditionally runs the counterfactual check (which
    fires regardless of record type when the claim contains a negation
    phrase).
    """

    def check(
        self,
        citation: Citation,
        claim_text: str,
        record: dict[str, Any] | None,
    ) -> tuple[bool, str | None]:
        # Counterfactual check fires across record types; do it first so
        # a denial cited to a non-note is rejected before we get into
        # type-specific logic.
        if _is_counterfactual(claim_text):
            return _check_counterfactual(citation, claim_text, record)

        record_type = citation.record_type.lower()

        if record_type == "medication":
            return _check_medication(citation, claim_text, record)
        if record_type == "lab_result":
            return _check_lab(citation, claim_text, record)
        if record_type == "problem":
            return _check_diagnosis(citation, claim_text, record)
        if record_type == "note":
            return _check_note(citation, claim_text, record)

        # Unknown record_type: pass through. The cache lookup proved
        # the record exists; future tools register their record_type
        # here when they need substance-level checks.
        return True, None


def _check_medication(
    _citation: Citation,
    claim_text: str,
    record: dict[str, Any] | None,
) -> tuple[bool, str | None]:
    record_name = (record or {}).get("name", "")
    if not isinstance(record_name, str) or not record_name:
        # Record missing or malformed — fail closed.
        return False, REASON_MEDICATION_NAME_MISMATCH

    record_lower = record_name.lower()

    match = MEDICATION_DOSE_PATTERN.search(claim_text)
    if match is None:
        # Claim has no dose token. Try the name-only path: extract the
        # likely drug name (the non-stopword tokens before the citation)
        # and verify it appears in the record name. If we can't isolate
        # a name token at all ("Patient takes their cardiac med"), pass
        # — the cache lookup is the floor.
        claim_name_only = _extract_name_only(claim_text)
        if claim_name_only is None:
            return True, None
        if claim_name_only in record_lower:
            return True, None
        return False, REASON_MEDICATION_NAME_MISMATCH

    claim_name = match.group("name").lower()
    claim_dose = _normalize_dose(match.group("dose"))

    # Name match: the claim's drug name must appear in the record name.
    # Generic / brand mismatches will surface as full-name mismatches;
    # for MVP we don't try to canonicalize across name spaces.
    if claim_name not in record_lower:
        return False, REASON_MEDICATION_NAME_MISMATCH

    # Dose match: the claim's dose token must appear (after whitespace
    # normalization and case-folding) in the record's name field. This
    # is the "exact-string for MVP" rule from ARCHITECTURE.md S6 #1.
    record_doses = {
        _normalize_dose(m.group("dose"))
        for m in MEDICATION_DOSE_PATTERN.finditer(record_lower)
    }
    # Also accept a literal substring match against the whitespace-
    # normalized record string, in case the record stores a dose with
    # spacing the pattern doesn't tokenize cleanly.
    record_norm = _normalize_dose(record_lower)
    if claim_dose in record_doses or claim_dose in record_norm:
        return True, None
    return False, REASON_MEDICATION_DOSE_MISMATCH


def _check_lab(
    _citation: Citation,
    claim_text: str,
    record: dict[str, Any] | None,
) -> tuple[bool, str | None]:
    # Strip the citation token from the claim so its numeric ID and
    # record_type words don't pollute value extraction.
    prose = _CITATION_STRIP.sub("", claim_text).strip()

    if record is None or "value" not in record:
        # Without a record value to compare to, any lab claim that quotes
        # a value in the prose is unsupported. A claim without a value
        # still passes (cache lookup is the floor).
        if _prose_has_lab_value(prose):
            return False, REASON_LAB_VALUE_MISMATCH
        return True, None

    record_value = record["value"]
    record_numeric = _coerce_to_numeric(record_value)
    claim_numeric = _extract_first_numeric(prose)
    claim_textual = _extract_textual_lab_value(prose)

    if claim_numeric is not None and record_numeric is not None:
        # Numeric-vs-numeric: tolerance is 2 decimal places.
        if round(abs(claim_numeric - record_numeric), 2) == 0:
            return True, None
        return False, REASON_LAB_VALUE_OUTSIDE_TOLERANCE

    if claim_numeric is not None and record_numeric is None:
        # Claim quotes a number but the record stores text — type
        # mismatch, not tolerance failure.
        return False, REASON_LAB_VALUE_MISMATCH

    if claim_textual is not None and record_numeric is not None:
        # Record is numeric but the claim is a textual value (e.g. the
        # claim says "negative" but the record stores "9.4").
        return False, REASON_LAB_VALUE_MISMATCH

    if claim_textual is not None:
        # Both textual: case-insensitive equality of the recognized token.
        record_str = str(record_value).strip().lower()
        if claim_textual == record_str:
            return True, None
        return False, REASON_LAB_VALUE_MISMATCH

    # Claim is value-less ("Recent A1C on file") — pass.
    return True, None


def _check_note(
    _citation: Citation,
    _claim_text: str,
    record: dict[str, Any] | None,
) -> tuple[bool, str | None]:
    # MVP placeholder. The gateway already authorized everything in the
    # per-turn cache; the verifier's role here is just to confirm a
    # record dict exists. Re-authorization comes back when notes ship
    # as a tool (Task 22) — at that point this method grows to inspect
    # source_attribution / authorized_user metadata.
    if record is None:
        return False, "note_record_missing"
    return True, None


def _check_diagnosis(
    _citation: Citation,
    _claim_text: str,
    record: dict[str, Any] | None,
) -> tuple[bool, str | None]:
    # Direct lookup: the citation pointed at a `problem` record_type;
    # the cache lookup proved the row exists. The constraint is just
    # "diagnosis traces to a problem row" — no fuzzy title matching.
    # Encounter-note traceability is deferred to Task 22.
    if record is None:
        return False, REASON_DIAGNOSIS_NOT_IN_PROBLEM_LIST
    return True, None


def _check_counterfactual(
    citation: Citation,
    claim_text: str,
    record: dict[str, Any] | None,
) -> tuple[bool, str | None]:
    # A counterfactual claim ("patient denies X") is only supported by a
    # note whose body literally documents the denial. Anything else —
    # citations to medications, problems, labs, even a note that doesn't
    # contain the negation — fails closed.
    if citation.record_type.lower() != "note" or record is None:
        return False, REASON_COUNTERFACTUAL_WITHOUT_NOTE

    body = record.get("body", "")
    if not isinstance(body, str):
        return False, REASON_COUNTERFACTUAL_WITHOUT_NOTE

    body_lower = body.lower()
    for phrase in COUNTERFACTUAL_PHRASES:
        if phrase in body_lower:
            return True, None

    return False, REASON_COUNTERFACTUAL_WITHOUT_NOTE


def _is_counterfactual(claim_text: str) -> bool:
    text_lower = claim_text.lower()
    return any(phrase in text_lower for phrase in COUNTERFACTUAL_PHRASES)


def _normalize_dose(dose: str) -> str:
    # Strip whitespace within the dose so "20 mg" and "20mg" compare
    # equal. Lowercase already happened at the call site.
    return re.sub(r"\s+", "", dose).lower()


# Common English filler tokens we don't want to mistake for a drug name
# during the name-only fallback path. Conservative: anything not in this
# list and longer than 3 chars is treated as a candidate drug name.
_NAME_ONLY_STOPWORDS: Final = frozenset(
    {
        "patient",
        "patients",
        "their",
        "takes",
        "taking",
        "took",
        "with",
        "from",
        "this",
        "that",
        "these",
        "those",
        "active",
        "started",
        "since",
        "currently",
        "current",
        "also",
        "still",
        "daily",
        "weekly",
        "monthly",
        "morning",
        "evening",
        "night",
        "every",
        "medication",
        "medications",
        "medicine",
        "drug",
        "prescribed",
        "prescription",
        "tablet",
        "tablets",
        "capsule",
        "capsules",
        "cardiac",
        "blood",
        "pressure",
    }
)

_NAME_TOKEN_PATTERN: Final = re.compile(r"[A-Za-z][A-Za-z\-]{3,}")


def _extract_name_only(claim_text: str) -> str | None:
    # Strip citation tokens; we don't want their record_type to count as
    # a drug name candidate.
    prose = _CITATION_STRIP.sub("", claim_text)
    for match in _NAME_TOKEN_PATTERN.finditer(prose):
        token_lower = match.group(0).lower()
        if token_lower in _NAME_ONLY_STOPWORDS:
            continue
        return token_lower
    return None


def _extract_first_numeric(text: str) -> float | None:
    match = LAB_NUMERIC_PATTERN.search(text)
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _coerce_to_numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        # bool is a subclass of int in Python; reject explicitly so a
        # True/False record value doesn't sneak through as 1.0/0.0.
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        # Allow record values with stray prefixes (e.g. "<5.0"). The
        # citation index normally stores raw strings.
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        if match is None:
            return None
        try:
            return float(match.group(0))
        except ValueError:
            return None
    return None


def _prose_has_lab_value(prose: str) -> bool:
    return (
        _extract_first_numeric(prose) is not None
        or _extract_textual_lab_value(prose) is not None
    )


# Conservative list of textual lab values the model is most likely to
# emit. Used only to distinguish "claim quotes a value" from "claim
# merely names the lab" when the record stores a numeric — false
# positives here just force re-citation.
_TEXTUAL_LAB_VALUES: Final = (
    "negative",
    "positive",
    "reactive",
    "non-reactive",
    "nonreactive",
    "normal",
    "abnormal",
    "detected",
    "not detected",
)


def _extract_textual_lab_value(claim_text: str) -> str | None:
    text_lower = claim_text.lower()
    for value in _TEXTUAL_LAB_VALUES:
        if value in text_lower:
            return value
    return None
