"""Per-turn citation index built from tool results.

The verifier rejects any citation whose ``(record_type, record_id)`` is
not in this index. That's the prompt-injection mitigation: the model
cannot conjure citation IDs because every accepted citation has to
correspond to a record actually returned by a tool *this turn*.

The index is rebuilt for each turn from the orchestrator's tool result
cache. New tools register here when verifier coverage catches up — an
unknown tool name does not raise, it just contributes no entries (so
any citation against it is rejected, which is the safe default).

See ARCHITECTURE.md S6.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from agentforge.tools.dtos import ToolResult

CitationKey = tuple[str, str]  # (record_type, record_id)


# Per-tool descriptor: how to walk a payload's ``model_dump()`` and turn
# each row into a (record_type, record_id) plus the row dict itself. The
# verifier intentionally hard-codes this map rather than generalising
# over pydantic shapes — the trust boundary should know the exact set of
# record classes that can be cited, not infer them.
_ToolDescriptor = tuple[str, str, str]  # (record_type, list_field, id_field)

_KNOWN_TOOLS: dict[str, _ToolDescriptor] = {
    # demographics returns a single record, not a list — handled below.
    "get_active_problems": ("problem", "problems", "id"),
    "get_active_medications": ("medication", "medications", "id"),
    # The following land alongside Tasks 17/18/19; registering early
    # keeps verifier coverage in lockstep with the tools as they ship.
    "get_active_allergies": ("allergy", "allergies", "id"),
    "get_recent_labs": ("lab_result", "labs", "id"),
    "get_vitals_trend": ("vitals", "vitals", "id"),
}


@dataclass(frozen=True, slots=True)
class CitationIndex:
    """A read-only mapping of ``(record_type, record_id) -> record dict``.

    ``size`` is the number of indexed records. ``contains`` and ``get``
    are the only lookup primitives the verifier uses.
    """

    records: dict[CitationKey, dict[str, Any]] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.records)

    def contains(self, record_type: str, record_id: str) -> bool:
        return (record_type, record_id) in self.records

    def get(self, record_type: str, record_id: str) -> dict[str, Any] | None:
        return self.records.get((record_type, record_id))


def build_citation_index(
    tool_results: dict[str, ToolResult[Any]],
) -> CitationIndex:
    """Build a CitationIndex from this turn's tool results.

    Unknown tool names are silently ignored — they contribute no
    entries, so any citation against them is rejected by the verifier.
    """
    records: dict[CitationKey, dict[str, Any]] = {}

    for tool_name, result in tool_results.items():
        for record_type, record_id, record in _walk_payload(tool_name, result):
            records[(record_type, record_id)] = record

    return CitationIndex(records=records)


def _walk_payload(
    tool_name: str,
    result: ToolResult[Any],
) -> Iterable[tuple[str, str, dict[str, Any]]]:
    """Yield (record_type, record_id, record_dict) tuples for one tool.

    Demographics has a singleton shape; everything else is a list-payload
    keyed off ``_KNOWN_TOOLS``. Unknown tool names yield nothing.
    """
    payload = result.payload.model_dump()

    if tool_name == "get_demographics":
        patient_id = payload.get("patient_id")
        if patient_id is not None:
            yield ("demographic", str(patient_id), payload)
        return

    descriptor = _KNOWN_TOOLS.get(tool_name)
    if descriptor is None:
        return

    record_type, list_field, id_field = descriptor
    rows = payload.get(list_field) or []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_id = row.get(id_field)
        if row_id is None:
            continue
        yield (record_type, str(row_id), row)
