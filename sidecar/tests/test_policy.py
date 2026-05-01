"""Behavior tests for the sensitivity policy schema (Task 9).

The policy is the data side of ARCHITECTURE.md §2's record-visibility
decisions. Each `RecordClassRule` describes a metadata-only matcher
(encounter category id, note title prefix, attending-only flag) and the
clearance(s) a user must hold for records that match. Pydantic models
are frozen so loaders cannot mutate parsed policy after the fact.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentforge.gateway.policy import (
    RecordClassRule,
    SensitivityPolicy,
)


def test_record_class_rule_is_immutable() -> None:
    rule = RecordClassRule(
        name="behavioral_health",
        required_clearances=("mental_health_authorized",),
        encounter_categories=(11, 12),
        note_title_prefixes=(),
        note_types=(),
        attending_only=False,
    )

    with pytest.raises(ValidationError):
        rule.required_clearances = ("other",)  # type: ignore[misc]


def test_sensitivity_policy_round_trips_minimal_yaml() -> None:
    raw = {
        "version": 1,
        "record_classes": {
            "behavioral_health": {
                "required_clearances": ["mental_health_authorized"],
                "encounter_categories": [11, 12],
            },
        },
    }

    policy = SensitivityPolicy.model_validate(raw)

    assert policy.version == 1
    assert "behavioral_health" in policy.record_classes
    assert policy.record_classes["behavioral_health"].required_clearances == (
        "mental_health_authorized",
    )
    assert policy.record_classes["behavioral_health"].encounter_categories == (11, 12)


def test_sensitivity_policy_rejects_unknown_record_class_keys() -> None:
    # Misnamed clearance fields silently doing nothing is the wrong default
    # for a fail-closed authorization policy. Pydantic's `extra=forbid`
    # turns typos into hard failures at load time.
    raw = {
        "version": 1,
        "record_classes": {
            "behavioral_health": {
                "required_clearances": ["mh"],
                "encounter_categores": [11],  # typo
            },
        },
    }

    with pytest.raises(ValidationError):
        SensitivityPolicy.model_validate(raw)


def test_sensitivity_policy_requires_version() -> None:
    raw = {
        "record_classes": {
            "behavioral_health": {"required_clearances": ["mh"]},
        },
    }

    with pytest.raises(ValidationError):
        SensitivityPolicy.model_validate(raw)


def test_sensitivity_policy_requires_at_least_one_clearance_per_class() -> None:
    raw = {
        "version": 1,
        "record_classes": {
            "behavioral_health": {
                "required_clearances": [],
                "encounter_categories": [11],
            },
        },
    }

    with pytest.raises(ValidationError):
        SensitivityPolicy.model_validate(raw)


def test_sensitivity_policy_loads_all_three_mvp_classes() -> None:
    raw = {
        "version": 1,
        "record_classes": {
            "behavioral_health": {
                "required_clearances": ["mental_health_authorized"],
                "encounter_categories": [11, 12],
            },
            "substance_abuse_cfr42": {
                "required_clearances": ["cfr42_authorized"],
                "note_title_prefixes": ["SUD:", "Substance Abuse:"],
                "note_types": ["substance_abuse"],
            },
            "attending_only": {
                "required_clearances": ["attending_override"],
                "attending_only": True,
            },
        },
    }

    policy = SensitivityPolicy.model_validate(raw)

    assert set(policy.record_classes.keys()) == {
        "behavioral_health",
        "substance_abuse_cfr42",
        "attending_only",
    }
    cfr42 = policy.record_classes["substance_abuse_cfr42"]
    assert cfr42.note_title_prefixes == ("SUD:", "Substance Abuse:")
    assert policy.record_classes["attending_only"].attending_only is True


def test_record_class_rule_defaults_match_no_metadata() -> None:
    # A rule with no matchers configured shouldn't accidentally trigger
    # against every record. The matcher tuples default to empty and the
    # attending_only flag defaults to False.
    rule = RecordClassRule(
        name="empty",
        required_clearances=("noop",),
    )

    assert rule.encounter_categories == ()
    assert rule.note_title_prefixes == ()
    assert rule.note_types == ()
    assert rule.attending_only is False
