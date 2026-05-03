"""SynthesisInputTruncator integration into the orchestrator (week1-gaps Task #6).

The truncator already exists (see :mod:`agentforge.orchestrator.truncation`)
and is well-tested in isolation. This file wires it into the
``Orchestrator`` constructor so a downstream subtask can consume
``self._truncator`` once the architectural prerequisites land.

**Architectural caveat — read before adding behavioral tests.**

The roadmap PRD for #6 said: "after all tool results are collected,
before final LLM call, truncate ``tool_results``". That phrasing
assumes a fetch-then-synthesize architecture: pre-fetch every tool
the planner asked for, then call the LLM ONCE with the synthesizer
prompt + results. Our orchestrator instead uses an iterative
tool-use loop where the LLM picks tools and we feed the results
back as additional ``tool`` messages. By the time the loop exits,
the LLM has already seen the unredacted tool payloads via the
``messages`` array — truncating ``tool_results`` *afterward* is
either a no-op (verifier_enabled=False; tool_results isn't read
again) or a regression (verifier_enabled=True; the verifier's
citation cache shrinks and valid claims start failing to ground).

So #6 wires the kwarg + stashes the truncator on the orchestrator
without invoking it. Behavioral integration moves to the streaming
refactor (#11/#13) where the synthesis call separates from the
tool loop and "before final LLM call" becomes a real seam. The
deviation is documented in DEVIATIONS.md.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from agentforge.config import get_settings
from agentforge.main import create_app
from agentforge.orchestrator import Orchestrator
from agentforge.orchestrator.truncation import SynthesisInputTruncator


def _build_orchestrator(
    *,
    truncator: SynthesisInputTruncator | None = None,
) -> Orchestrator:
    return Orchestrator(
        llm=AsyncMock(),
        demographics_fetcher=AsyncMock(),
        medications_fetcher=AsyncMock(),
        problems_fetcher=AsyncMock(),
        allergies_fetcher=AsyncMock(),
        labs_fetcher=AsyncMock(),
        vitals_fetcher=AsyncMock(),
        notes_fetcher=AsyncMock(),
        search_notes_fetcher=AsyncMock(),
        encounters_fetcher=AsyncMock(),
        immunizations_fetcher=AsyncMock(),
        procedures_fetcher=AsyncMock(),
        truncator=truncator,
    )


class TestTruncatorWiring:
    """Subtask 6.1 — constructor accepts a truncator and stashes it."""

    def test_orchestrator_accepts_truncator_kwarg(self) -> None:
        """The kwarg is optional, defaults to None, and the value
        stashes on ``self._truncator`` for the future behavioral
        integration. Pure wiring test — no behavior change.
        """
        truncator = SynthesisInputTruncator()

        orch = _build_orchestrator(truncator=truncator)

        assert orch._truncator is truncator

    def test_orchestrator_truncator_defaults_to_none(self) -> None:
        """Omitting the kwarg leaves ``_truncator`` as None so the
        legacy no-truncator path stays the default until behavioral
        integration lands (post-streaming refactor).
        """
        orch = _build_orchestrator()

        assert orch._truncator is None


def _build_redis_mock() -> AsyncMock:
    """Minimal redis surface for create_app() to boot."""
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)
    redis_mock.set = AsyncMock(return_value=True)
    redis_mock.delete = AsyncMock(return_value=0)
    redis_mock.keys = AsyncMock(return_value=[])
    redis_mock.smembers = AsyncMock(return_value=set())
    return redis_mock


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("HMAC_KEY", "test-hmac-key-32-bytes-aaaaaaaaaaaaa")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    get_settings.cache_clear()


class TestCreateAppTruncatorWiring:
    """Subtask 6.5 — create_app constructs and passes through a truncator."""

    def test_create_app_constructs_default_truncator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no truncator is injected, ``create_app()`` instantiates
        a real :class:`SynthesisInputTruncator` and passes it to the
        orchestrator. Default-on construction means the production
        path always has a truncator instance available — even though
        Orchestrator.turn doesn't call it yet (DEVIATIONS.md
        2026-05-02), holding the instance enables a behavior-only
        flip in the streaming refactor without a constructor change.
        """
        _set_required_env(monkeypatch)

        app = create_app(redis_client=_build_redis_mock())

        orchestrator = app.state.orchestrator
        assert isinstance(orchestrator, Orchestrator)
        assert isinstance(orchestrator._truncator, SynthesisInputTruncator)

    def test_create_app_accepts_injected_truncator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tests that need to override the encoding (or test the
        no-truncator path explicitly) can inject a stub. Mirrors how
        every other collaborator is injectable via create_app kwargs.
        """
        _set_required_env(monkeypatch)

        injected = SynthesisInputTruncator()
        app = create_app(
            redis_client=_build_redis_mock(),
            truncator=injected,
        )

        assert app.state.orchestrator._truncator is injected


# Marker — keeps the import set quiet against ruff's
# unused-import warning when test classes get reshuffled.
_ = (Any,)
