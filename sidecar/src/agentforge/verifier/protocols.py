"""Protocol seam between the structural verifier and domain constraints.

Task 28 ships the structural half of the verifier: parse citations,
look them up against the per-turn cache, drop fabricated IDs. The
clinical-substance half — checking that the cited record actually
supports the claim's structured assertion — is Task 29's job. This
module pins the contract between them so Task 29 doesn't drift.

See ARCHITECTURE.md S6: the five domain constraints (medication name in
active prescriptions, lab values within tolerance, note authorization,
diagnosis traceability, no counterfactuals) all hang off the protocol
below.
"""

from __future__ import annotations

from typing import Any, Protocol

from agentforge.verifier.citation import Citation


class DomainConstraintChecker(Protocol):
    """Adjudicates whether a citation's substance grounds a claim.

    Implementations receive the parsed Citation, the claim text the
    citation was attached to, and the underlying record dict (or None
    if the citation pointed at a tool with no record-level entry). They
    return ``(verified, rejection_reason)`` — when ``verified`` is False,
    ``rejection_reason`` is a stable short tag the verifier surfaces in
    telemetry and the user-visible withhold marker.
    """

    def check(
        self,
        citation: Citation,
        claim_text: str,
        record: dict[str, Any] | None,
    ) -> tuple[bool, str | None]: ...


class NullDomainConstraintChecker:
    """Passes every claim. Used until Task 29 ships."""

    def check(
        self,
        citation: Citation,
        claim_text: str,
        record: dict[str, Any] | None,
    ) -> tuple[bool, str | None]:
        return True, None
