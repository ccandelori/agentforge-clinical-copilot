"""W2 programmatic eval checks (Task 17.3).

Three deterministic checks that run before the LLM judge in the eval
harness. They cost no LLM tokens, are run-to-run identical, and catch
the cheapest classes of regression — schema breaks, missing citations,
PHI leaking into trace exports — before the judge spend kicks in.

Composition:
  * :func:`check_schema_valid` — ``BaseModel.model_validate`` returns
    a Pydantic error iff the structured output doesn't satisfy its
    schema (including model-validators like the W2 bbox-confidence
    floor).
  * :func:`check_citation_present` — a response either carries an
    inline ``[record_type #id]`` citation (the W1 grammar from
    :mod:`agentforge.verifier.citation`) or has at least one
    structured :class:`Citation` attached. Either path is enough.
  * :func:`check_no_phi_in_logs` — regex sweep over a list of trace
    log lines. Flags raw SSN / MRN / phone-shaped digit runs that
    bypassed HMAC-pseudonymisation. False positives on synthetic
    pseudonyms are avoided by requiring digit-only signatures.

The :class:`ProgrammaticChecks` aggregator runs all three and exposes
a single ``.passed`` property the harness reads.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from agentforge.schemas.citation import Citation
from agentforge.verifier.citation import find_citations


@dataclass(frozen=True)
class CheckResult:
    """One programmatic check's outcome."""

    name: str
    passed: bool
    error: str | None = None


@dataclass(frozen=True)
class CitationCheckResult:
    """:func:`check_citation_present`'s outcome with citation count."""

    name: str
    passed: bool
    citation_count: int


@dataclass(frozen=True)
class PhiCheckResult:
    """:func:`check_no_phi_in_logs`'s outcome with the matched offenders."""

    name: str
    passed: bool
    matches: tuple[str, ...]


def check_schema_valid(model: type[BaseModel], payload: dict[str, Any]) -> CheckResult:
    """Run Pydantic validation on a structured-output payload.

    Returns a :class:`CheckResult` whose ``error`` field carries a
    short, human-readable summary of the first ValidationError when the
    payload fails. Validation success returns ``error=None``.

    Catches :class:`Exception` (not :class:`ValidationError` only) so
    a malformed payload that breaks pydantic before it reaches a
    validator (e.g. wrong type for a model field) still produces a
    structured failure rather than crashing the eval run.
    """
    try:
        model.model_validate(payload)
    except ValidationError as exc:
        # First error's loc + msg is enough to triage; the full error
        # body would dwarf the rest of the eval report.
        first = exc.errors()[0]
        loc = ".".join(str(part) for part in first.get("loc", ())) or "<root>"
        return CheckResult(
            name="schema_valid",
            passed=False,
            error=f"{loc}: {first.get('msg', 'validation error')}",
        )
    except Exception as exc:  # pragma: no cover - defensive
        return CheckResult(
            name="schema_valid",
            passed=False,
            error=f"unexpected error: {type(exc).__name__}",
        )
    return CheckResult(name="schema_valid", passed=True)


def check_citation_present(
    response: str,
    *,
    structured_citations: Sequence[Citation] = (),
) -> CitationCheckResult:
    """Verify the response has at least one citation attached.

    The W2 contract is "every claim carries a Citation"; the cheapest
    smoke test is "any citation at all is present". Two paths satisfy:
    either the response text carries an inline ``[record_type #id]``
    token (W1 grammar) or the caller supplies at least one structured
    :class:`Citation`.
    """
    inline_count = len(find_citations(response))
    total = inline_count + len(structured_citations)
    return CitationCheckResult(
        name="citation_present",
        passed=total > 0,
        citation_count=total,
    )


# Three patterns covering the easy-to-leak PHI shapes:
#   - SSN:     XXX-XX-XXXX
#   - Phone:   XXX-XXX-XXXX or XXX.XXX.XXXX (we don't try to be exhaustive
#              here — the goal is "did a raw phone number bypass hashing",
#              not full E.164 coverage)
#   - MRN:     bare 8-10 digit run anchored on word boundaries. The
#              digit floor matches OpenEMR's typical MRN width while
#              avoiding 4-digit years and 6-digit ZIP+4 noise.
_PHI_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"\b\d{3}[-.]\d{3}[-.]\d{4}\b"),
    re.compile(r"\b\d{8,10}\b"),
)


def check_no_phi_in_logs(logs: Iterable[str]) -> PhiCheckResult:
    """Sweep trace export lines for digit-shaped PHI patterns.

    Boundary discipline is enforced upstream by the Langfuse client
    (see :mod:`agentforge.observability.langfuse_client`) — IDs are
    HMAC-pseudonymised before they leave the sidecar. This check is
    a defense-in-depth contract: if a code change ever lets raw IDs
    or free-text PHI through to the trace store, the eval suite
    surfaces it before a clinician notices.

    Pseudonym hex strings (12 hex chars, mixed letters and digits)
    fail the all-digit guards by construction, so we don't need to
    allowlist them explicitly.
    """
    matches: list[str] = []
    for line in logs:
        for pattern in _PHI_PATTERNS:
            for match in pattern.finditer(line):
                matches.append(match.group(0))
    return PhiCheckResult(
        name="no_phi_in_logs",
        passed=len(matches) == 0,
        matches=tuple(matches),
    )


@dataclass(frozen=True)
class ProgrammaticChecks:
    """Aggregate result of all three W2 programmatic checks."""

    schema_valid: CheckResult
    citation_present: CitationCheckResult
    no_phi_in_logs: PhiCheckResult

    @property
    def passed(self) -> bool:
        return (
            self.schema_valid.passed
            and self.citation_present.passed
            and self.no_phi_in_logs.passed
        )

    @classmethod
    def run(
        cls,
        *,
        response: str,
        structured_citation_payload: dict[str, Any],
        structured_citations: Sequence[Citation] = (),
        logs: Iterable[str] = (),
    ) -> ProgrammaticChecks:
        """Run all three checks and return the aggregate."""
        schema = check_schema_valid(Citation, structured_citation_payload)
        citations = check_citation_present(
            response, structured_citations=structured_citations
        )
        phi = check_no_phi_in_logs(logs)
        return cls(
            schema_valid=schema,
            citation_present=citations,
            no_phi_in_logs=phi,
        )
