"""Citation token parser for the verifier.

The model is instructed to suffix every factual sentence with a citation
in one of two ID-anchored forms:

    [encounter #38241, 2026-04-12]
    [medication #7, started 2024-08-15]
    [problem #42]

These translate one-to-one into a Citation record the cache lookup and
domain checks can act on. Label-form citations (``[Rx: lisinopril]``)
are parsed as None for MVP — they have no ID, so the cache cannot
validate them. Future label resolution belongs with Task 29's domain
constraints, not the structural parser.

See ARCHITECTURE.md S6 (verification strategy).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ID-anchored citation grammar:
#
#   [<record_type> #<record_id>[, <extra>]]
#
# - record_type:  letter-leading identifier (so "[#42]" doesn't parse;
#                 the format must announce its record class)
# - record_id:    alphanumeric, underscore, or hyphen (handles UUID-shaped
#                 keys some sources use)
# - extra:        anything up to the closing bracket — usually a date or
#                 a "started YYYY-MM-DD" phrase. Optional.
#
# The capture is anchored on the brackets so the same regex works for
# scanning text via ``finditer`` and for parsing a single-citation string.
CITATION_PATTERN: re.Pattern[str] = re.compile(
    r"\[(?P<type>[A-Za-z][A-Za-z0-9_]*)\s+#(?P<id>[A-Za-z0-9_\-]+)"
    r"(?:,\s*(?P<extra>[^\]]+))?\]"
)

# Multi-id continuation: when the extra field is a comma-separated list
# of ``#<id>`` tokens (and ONLY id tokens — no interleaved text), we
# expand the parent citation into one Citation per id. Catches
# ``[problem #293, #294]`` where the model credits two related rows
# for one claim. Mixed extra (e.g. ``[problem #293, started 2024-01-01,
# #294]``) does NOT match this pattern; we fall back to single-citation
# parsing in that case to avoid false expansion.
_MULTI_ID_EXTRA_PATTERN: re.Pattern[str] = re.compile(
    r"^#(?P<first_id>[A-Za-z0-9_\-]+)"
    r"(?:\s*,\s*#(?P<rest>[A-Za-z0-9_\-]+))*\s*$"
)
# A separate compile to extract every id when the extra matches above
# — the named-group form above only captures the LAST repeated id.
_ID_TOKEN_PATTERN: re.Pattern[str] = re.compile(
    r"#(?P<id>[A-Za-z0-9_\-]+)"
)


@dataclass(frozen=True, slots=True)
class Citation:
    """One parsed citation token."""

    record_type: str
    record_id: str
    extra: str | None
    raw: str


def parse_citation(text: str) -> Citation | None:
    """Parse a single citation token. Returns None if ``text`` doesn't match.

    Useful when the caller already isolated a citation token and wants
    to validate its shape. For free-form text scanning, use
    ``find_citations``.
    """
    match = CITATION_PATTERN.fullmatch(text.strip())
    if match is None:
        return None
    return Citation(
        record_type=match.group("type"),
        record_id=match.group("id"),
        extra=match.group("extra"),
        raw=match.group(0),
    )


def find_citations(text: str) -> list[Citation]:
    """Return all ID-anchored citations found in ``text`` in order.

    Label-form citations (``[Rx: ...]``) are silently skipped — the
    grammar requires a leading identifier-then-``#id``, which they lack.

    Multi-id forms (``[problem #293, #294]``) are expanded: each id
    becomes its own :class:`Citation` with the same ``record_type``
    and ``raw`` token, so every id has to ground individually
    against the verifier's per-turn cache. Mixed extras (id +
    free text) fall back to single-citation parsing — see
    :data:`_MULTI_ID_EXTRA_PATTERN`.
    """
    citations: list[Citation] = []
    for match in CITATION_PATTERN.finditer(text):
        record_type = match.group("type")
        record_id = match.group("id")
        extra = match.group("extra")
        raw = match.group(0)

        # Pure-id-list extra? Expand. Otherwise (no extra, or mixed
        # extra with text), keep the single-citation form.
        if extra is not None and _MULTI_ID_EXTRA_PATTERN.match(extra):
            # First citation keeps the parent's id but with NO extra
            # (the extra slot was just a list of additional ids — it
            # carries no semantic info beyond the ids themselves).
            citations.append(
                Citation(
                    record_type=record_type,
                    record_id=record_id,
                    extra=None,
                    raw=raw,
                )
            )
            for id_match in _ID_TOKEN_PATTERN.finditer(extra):
                citations.append(
                    Citation(
                        record_type=record_type,
                        record_id=id_match.group("id"),
                        extra=None,
                        raw=raw,
                    )
                )
        else:
            citations.append(
                Citation(
                    record_type=record_type,
                    record_id=record_id,
                    extra=extra,
                    raw=raw,
                )
            )

    return citations
