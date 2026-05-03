"""YAML case loader for the eval harness.

Reads YAML files from a ``cases/`` directory and deserializes each entry
into an :class:`EvalCase`.  The optional ``grounding_check`` callable
cannot round-trip through YAML and is intentionally omitted — set it
programmatically after loading if the test needs it.

YAML format (each document is a list of mappings)::

    - id: hp_1
      category: happy_path        # maps to EvalCategory value
      patient_id: 8
      query: "Give me a chart overview"
      expected_behavior: "Contains demographics, problems, medications with citations"
"""

from __future__ import annotations

import pathlib
from typing import Any

import yaml

from tests.eval.harness import EvalCase, EvalCategory

# Build a lookup from string value → EvalCategory member once at import
# time so the hot path in load_yaml_cases is O(1).
_CATEGORY_BY_VALUE: dict[str, EvalCategory] = {cat.value: cat for cat in EvalCategory}


def load_yaml_cases(path: str | pathlib.Path) -> list[EvalCase]:
    """Parse a YAML file and return a list of :class:`EvalCase` objects.

    Parameters
    ----------
    path:
        Absolute or relative path to the ``.yaml`` file.

    Returns
    -------
    list[EvalCase]
        One :class:`EvalCase` per YAML entry.  ``grounding_check`` is
        always ``None`` for YAML-loaded cases.

    Raises
    ------
    ValueError
        If any entry contains an unrecognised ``category`` value.  The
        error message includes the bad value so callers can surface it
        directly in test failure messages.
    """
    raw_text = pathlib.Path(path).read_text(encoding="utf-8")
    entries: list[dict[str, Any]] = yaml.safe_load(raw_text) or []

    cases: list[EvalCase] = []
    for entry in entries:
        category_str: str = entry["category"]
        if category_str not in _CATEGORY_BY_VALUE:
            raise ValueError(
                f"Unknown EvalCategory value {category_str!r}. "
                f"Valid values: {sorted(_CATEGORY_BY_VALUE)}"
            )
        cases.append(
            EvalCase(
                id=entry["id"],
                category=_CATEGORY_BY_VALUE[category_str],
                patient_id=int(entry["patient_id"]),
                query=entry["query"],
                expected_behavior=entry["expected_behavior"],
                grounding_check=None,
            )
        )
    return cases
