"""Typed loader for ``sidecar/eval_config.yaml`` (Task 18.1).

The eval-gate config carries thresholds + LLM-judge settings used by
later subtasks (runner, gate logic, diff reporter).  Parsing is strict
— missing fields, out-of-range values, and unexpected top-level keys
raise :class:`ValueError` with the offending key in the message so a
CI failure points the human directly at the bad line.

Mirrors the existing pattern in :mod:`agentforge.config` (Pydantic
v2 ``BaseModel`` for typed access; Pydantic's :class:`ValidationError`
is reshaped into a flat ``ValueError`` so callers don't have to depend
on Pydantic's error structure).
"""

from __future__ import annotations

import pathlib
from typing import Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


_REQUIRED_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        "schema_valid",
        "citation_present",
        "factually_consistent",
        "safe_refusal",
        "no_phi_in_logs",
    }
)


# Default location: sidecar/eval_config.yaml at the sidecar root.  This
# file lives at sidecar/tests/eval/gate/config.py — three parents up.
_DEFAULT_CONFIG_PATH: Final[pathlib.Path] = (
    pathlib.Path(__file__).resolve().parents[3] / "eval_config.yaml"
)


class EvalGateConfig(BaseModel):
    """Validated eval-gate configuration.

    Pydantic ``model_config`` forbids extra keys so a typo in
    ``eval_config.yaml`` fails loud rather than silently dropping the
    misspelled field.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    category_thresholds: dict[str, float] = Field(...)
    regression_threshold: float = Field(..., ge=0.0, le=1.0)
    llm_judge_model: str = Field(..., min_length=1)
    llm_judge_temperature: float = Field(..., ge=0.0, le=2.0)


def load_eval_gate_config(
    path: pathlib.Path | str | None = None,
) -> EvalGateConfig:
    """Parse the eval-gate config YAML and return a typed object.

    Parameters
    ----------
    path:
        Optional path to the config YAML.  Defaults to
        ``sidecar/eval_config.yaml`` (resolved relative to this file's
        location, so it works from any working directory).

    Raises
    ------
    FileNotFoundError
        If ``path`` does not exist.
    ValueError
        If the YAML is missing required keys, has out-of-range values,
        or contains unexpected top-level keys.  The error message names
        the offending field.
    """
    target = pathlib.Path(path) if path is not None else _DEFAULT_CONFIG_PATH
    if not target.is_file():
        raise FileNotFoundError(f"eval-gate config not found: {target}")

    raw_text = target.read_text(encoding="utf-8")
    payload = yaml.safe_load(raw_text) or {}
    if not isinstance(payload, dict):
        raise ValueError(
            f"eval-gate config must be a YAML mapping; got {type(payload).__name__}"
        )

    # Validate the inner category_thresholds dict explicitly so a
    # missing required category surfaces with the offending key in the
    # error message.  Pydantic on its own would only complain that a
    # value was missing; we want the exact category name.
    thresholds = payload.get("category_thresholds")
    if isinstance(thresholds, dict):
        missing = sorted(_REQUIRED_CATEGORIES - thresholds.keys())
        if missing:
            raise ValueError(
                f"eval-gate config missing required category_thresholds: "
                f"{', '.join(missing)}"
            )
        for category, value in thresholds.items():
            if category not in _REQUIRED_CATEGORIES:
                raise ValueError(
                    f"eval-gate config has unknown category_thresholds key: "
                    f"{category!r}"
                )
            if not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
                raise ValueError(
                    f"eval-gate config category_thresholds[{category!r}] "
                    f"must be between 0.0 and 1.0; got {value!r}"
                )

    try:
        return EvalGateConfig.model_validate(payload)
    except ValidationError as exc:
        # Reshape Pydantic's structured error into a flat ValueError so
        # callers (and CI logs) get one human-readable line per problem.
        details: list[str] = []
        for err in exc.errors():
            loc = ".".join(str(part) for part in err.get("loc", ())) or "<root>"
            msg = err.get("msg", "validation error")
            details.append(f"{loc}: {msg}")
        raise ValueError(
            "eval-gate config validation failed: " + "; ".join(details)
        ) from exc
