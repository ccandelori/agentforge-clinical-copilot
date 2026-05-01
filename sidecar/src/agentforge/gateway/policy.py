"""Pydantic schema for the sensitivity policy.

ARCHITECTURE.md §2 calls for a small policy table that maps record-level
metadata (encounter category, note title prefix, structural attending-only
flag) to the sensitivity clearance(s) a user must hold to surface the
record. The schema is deliberately narrow — the verifier and the
record-visibility check both consume it, so the contract is the same on
both ends.

Frozen models, tuples for collection fields. Tuples are hashable and
discourage accidental mutation; for a fail-closed authorization policy,
"who can edit this in flight" is a more important question than "who
can iterate it efficiently".
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RecordClassRule(BaseModel):
    """One row in the sensitivity policy: a metadata-only matcher plus
    the clearance(s) a user must hold.

    A rule "fires" when ANY of its matchers (encounter_categories,
    note_title_prefixes, note_types, attending_only) matches the
    record's metadata. When a rule fires, the user must hold ALL of
    its `required_clearances` for the record to be visible.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    name: str = Field(..., min_length=1)
    required_clearances: tuple[str, ...] = Field(..., min_length=1)
    encounter_categories: tuple[int, ...] = Field(default_factory=tuple)
    note_title_prefixes: tuple[str, ...] = Field(default_factory=tuple)
    note_types: tuple[str, ...] = Field(default_factory=tuple)
    attending_only: bool = False

    @field_validator(
        "required_clearances",
        "encounter_categories",
        "note_title_prefixes",
        "note_types",
        mode="before",
    )
    @classmethod
    def _coerce_lists_to_tuples(cls, value: object) -> object:
        # Pydantic accepts both lists and tuples for tuple-typed fields,
        # but YAML always loads sequences as lists. The before-validator
        # makes the coercion explicit; round-tripping through JSON works
        # the same way.
        if isinstance(value, list):
            return tuple(value)
        return value


class SensitivityPolicy(BaseModel):
    """The full policy: a version sentinel plus a dict of named record-
    class rules.

    `version` is a monotonically increasing integer. The loader writes
    it to Redis under `agentforge:policy:loaded` so the gateway can
    detect a missing or stale policy without re-reading the YAML.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    version: int = Field(..., ge=1)
    record_classes: dict[str, RecordClassRule]

    @field_validator("record_classes", mode="before")
    @classmethod
    def _attach_class_names(cls, value: object) -> object:
        # YAML stores rules as `{name: {required_clearances: ...}}`;
        # the rule's own `name` field mirrors the dict key. Stamp the
        # key onto each rule so callers can read `rule.name` without
        # having to know the dict context.
        if isinstance(value, dict):
            stamped: dict[str, object] = {}
            for class_name, rule in value.items():
                if isinstance(rule, dict) and "name" not in rule:
                    rule = {**rule, "name": class_name}
                stamped[class_name] = rule
            return stamped
        return value
