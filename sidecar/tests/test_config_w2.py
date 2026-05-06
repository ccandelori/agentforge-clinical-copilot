"""Tests for the W2-specific Settings fields (MR 7).

Settings.guidelines_index_path and Settings.evidence_retriever_enabled
land in this file rather than a generic test_config.py because the
existing sidecar tests cover JWT / Redis / Langfuse settings via the
fixtures that construct ``Settings(...)`` directly. This file pins
the W2 defaults so a typo in the path (or a flipped boolean default)
fails the suite instead of silently degrading evidence retrieval.
"""

from __future__ import annotations

from pathlib import Path

from agentforge.config import Settings


def _settings(**overrides: object) -> Settings:
    """Construct a Settings instance with the secrets test fixtures
    expect. Any field declared here is overridable via kwargs."""
    base: dict[str, object] = {
        "jwt_secret": "test-secret",
        "redis_url": "redis://localhost:6379",
        "hmac_key": "test-hmac",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_guidelines_index_path_defaults_to_bundled_corpus() -> None:
    settings = _settings()

    # The path is repo-relative — `sidecar/data/guidelines/index.json`
    # — and that file is committed to the tree, so the default is a
    # working pointer in any clean checkout.
    assert isinstance(settings.guidelines_index_path, Path)
    assert settings.guidelines_index_path.name == "index.json"
    assert settings.guidelines_index_path.parent.name == "guidelines"
    assert settings.guidelines_index_path.is_file(), (
        "bundled guideline corpus must exist for default to be useful; "
        "run scripts/chunk_guidelines.py if missing"
    )


def test_evidence_retriever_enabled_defaults_to_false() -> None:
    # Default-off because the dense + cross-encoder load ~190 MB of
    # ML weights on construction (3-5 seconds) — paying that cost on
    # every unit-test fixture or dev-time ``create_app()`` import is
    # an unacceptable regression. Production deployments opt in via
    # ``.env`` (``EVIDENCE_RETRIEVER_ENABLED=true``).
    settings = _settings()

    assert settings.evidence_retriever_enabled is False


def test_evidence_retriever_enabled_can_be_enabled_via_env() -> None:
    # Pydantic-settings reads env vars case-insensitively; production
    # ``.env`` flips this on after confirming the bundled corpus is
    # in place and the Hugging Face cache is warm.
    import os

    os.environ["EVIDENCE_RETRIEVER_ENABLED"] = "true"
    try:
        settings = _settings()
        assert settings.evidence_retriever_enabled is True
    finally:
        del os.environ["EVIDENCE_RETRIEVER_ENABLED"]


def test_guidelines_index_path_can_be_overridden() -> None:
    custom = Path("/var/agentforge/custom-corpus.json")
    settings = _settings(guidelines_index_path=custom)

    assert settings.guidelines_index_path == custom
