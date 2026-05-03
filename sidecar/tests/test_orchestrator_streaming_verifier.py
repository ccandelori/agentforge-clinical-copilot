"""Verifier-before-emit gate wired into stream_turn (week1-gaps Task #13).

``stream_turn`` must route synthesis tokens through ``StreamingVerifier``
when ``verifier_enabled=True``.  Only ``VerifiedChunk.text`` reaches the
consumer; ungrounded sentences are replaced with ``REJECTION_MARKER``.

The gate is transparent when ``verifier_enabled=False`` (legacy passthrough).

Test plan (from task spec):
  1. Verified sentence passes through as StreamTextDelta.
  2. Ungrounded sentence is replaced with REJECTION_MARKER in the stream.
  3. Multiple sentences: first passes, second fails, third passes — order
     preserved.
  4. Tool-use turn (stop_reason="tool_use") — verifier sees no synthesis
     tokens; stream ends with a StreamFinal carrying tool_calls, then a
     subsequent synthesis iteration is verified normally.
  5. verifier_enabled=False: direct passthrough (no verification).
  6. StreamFinal.response.text carries the verified text, not the raw LLM
     text.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentforge.gateway.auth_gateway import RequestContext
from agentforge.llm.types import (
    LLMResponse,
    StreamEvent,
    StreamFinal,
    StreamTextDelta,
    ToolCall,
)
from agentforge.orchestrator import Orchestrator
from agentforge.tools.dtos import ToolResultMetadata
from agentforge.tools.medications import (
    MedicationItem,
    MedicationsPayload,
    MedicationsResult,
)
from agentforge.tools.problems import ProblemItem, ProblemsPayload, ProblemsResult

REJECTION_MARKER = "[claim withheld — could not be grounded]"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _ctx(*, patient_id: int = 7, user_id: int = 42) -> RequestContext:
    return RequestContext(
        user_id=user_id,
        patient_id=patient_id,
        username="test-user",
        role="clinician",
        breakglass_flag=False,
        breakglass_reason=None,
        sensitivity_clearances=frozenset(),
        raw_token="raw.jwt.token",
    )


def _meta(name: str) -> ToolResultMetadata:
    return ToolResultMetadata(
        tool_name=name,
        fetched_at=datetime.now(UTC),
        data_freshness_seconds=60,
        source=f"openemr.{name}",
    )


def _problems(*ids: int) -> ProblemsResult:
    return ProblemsResult(
        metadata=_meta("get_active_problems"),
        payload=ProblemsPayload(
            problems=tuple(ProblemItem(id=i, title=f"Problem {i}") for i in ids),
        ),
    )


def _medications(*ids: int) -> MedicationsResult:
    return MedicationsResult(
        metadata=_meta("get_active_medications"),
        payload=MedicationsPayload(
            medications=tuple(
                MedicationItem(id=i, name=f"Med {i}") for i in ids
            ),
        ),
    )


def _fetcher_returning(result: Any) -> AsyncMock:
    mock = AsyncMock()
    mock.fetch.return_value = result
    return mock


def _make_streaming_llm(*event_sequences: list[StreamEvent]) -> Any:
    """Return a mock LLMClient whose stream() yields event sequences in order.

    Each positional arg is the list of ``StreamEvent`` objects yielded on
    successive ``stream()`` calls.  Wraps around if called more times than
    there are sequences.
    """
    sequences = list(event_sequences)
    call_count = [0]

    def _stream(*args: Any, **kwargs: Any) -> AsyncIterator[StreamEvent]:
        idx = call_count[0] % len(sequences)
        call_count[0] += 1
        events = sequences[idx]

        async def _gen() -> AsyncIterator[StreamEvent]:
            for e in events:
                yield e

        return _gen()

    llm = MagicMock()
    llm.stream = _stream
    # complete() is not called in the streaming path, but wire it to fail
    # loudly so any accidental call is visible in the test output.
    llm.complete = AsyncMock(
        side_effect=AssertionError("complete() must not be called in stream_turn tests")
    )
    return llm


def _synthesis_events(*tokens: str, stop_reason: str = "end_turn") -> list[StreamEvent]:
    """One StreamTextDelta per token followed by a terminal StreamFinal."""
    final_text = "".join(tokens)
    events: list[StreamEvent] = [StreamTextDelta(text=t) for t in tokens]
    events.append(
        StreamFinal(
            response=LLMResponse(
                text=final_text,
                tool_calls=[],
                stop_reason=stop_reason,
                input_tokens=10,
                output_tokens=max(1, len(tokens)),
            )
        )
    )
    return events


def _tool_use_events(tool_calls: list[ToolCall]) -> list[StreamEvent]:
    """Tool-use StreamEvent list: no text deltas, just a StreamFinal."""
    return [
        StreamFinal(
            response=LLMResponse(
                text="",
                tool_calls=tool_calls,
                stop_reason="tool_use",
                input_tokens=10,
                output_tokens=5,
            )
        )
    ]


def _build_orchestrator(
    *,
    llm: Any,
    problems: AsyncMock | None = None,
    medications: AsyncMock | None = None,
    verifier_enabled: bool = True,
) -> Orchestrator:
    return Orchestrator(
        llm=llm,
        demographics_fetcher=AsyncMock(),
        medications_fetcher=medications or AsyncMock(),
        problems_fetcher=problems or AsyncMock(),
        allergies_fetcher=AsyncMock(),
        labs_fetcher=AsyncMock(),
        vitals_fetcher=AsyncMock(),
        notes_fetcher=AsyncMock(),
        search_notes_fetcher=AsyncMock(),
        encounters_fetcher=AsyncMock(),
        immunizations_fetcher=AsyncMock(),
        procedures_fetcher=AsyncMock(),
        verifier_enabled=verifier_enabled,
    )


async def _collect_stream(
    orch: Orchestrator, message: str = "summarize"
) -> tuple[list[str], list[StreamEvent]]:
    """Drain stream_turn() and return (delta_texts, all_events)."""
    deltas: list[str] = []
    events: list[StreamEvent] = []
    async for event in orch.stream_turn(_ctx(), message):
        events.append(event)
        if isinstance(event, StreamTextDelta):
            deltas.append(event.text)
    return deltas, events


# ---------------------------------------------------------------------------
# Verifier wired — synthesis path
# ---------------------------------------------------------------------------


class TestStreamingVerifierGate:
    async def test_verified_sentence_passes_through_as_delta(self) -> None:
        # Tool call populates citation index; synthesis cites the result.
        llm = _make_streaming_llm(
            _tool_use_events([ToolCall(id="t1", name="get_active_problems", input={})]),
            _synthesis_events("Active diagnosis includes problem one [problem #1]. "),
        )
        problems = _fetcher_returning(_problems(1))
        orch = _build_orchestrator(llm=llm, problems=problems)

        deltas, _ = await _collect_stream(orch)

        text = "".join(deltas)
        assert "[problem #1]" in text
        assert REJECTION_MARKER not in text

    async def test_ungrounded_sentence_replaced_with_rejection_marker(self) -> None:
        # Synthesis text has no citations → entire sentence is redacted.
        llm = _make_streaming_llm(
            _tool_use_events([ToolCall(id="t1", name="get_active_problems", input={})]),
            _synthesis_events("Patient seems generally healthy. "),
        )
        problems = _fetcher_returning(_problems(1))
        orch = _build_orchestrator(llm=llm, problems=problems)

        deltas, _ = await _collect_stream(orch)

        assert REJECTION_MARKER in "".join(deltas)

    async def test_multiple_sentences_order_preserved(self) -> None:
        # Three sentences: first and third cite real records, second does not.
        llm = _make_streaming_llm(
            _tool_use_events([ToolCall(id="t1", name="get_active_problems", input={})]),
            _synthesis_events(
                "Known problem [problem #5]. ",
                "Something made up entirely. ",
                "Also on file [problem #5]. ",
            ),
        )
        problems = _fetcher_returning(_problems(5))
        orch = _build_orchestrator(llm=llm, problems=problems)

        deltas, _ = await _collect_stream(orch)
        text = "".join(deltas)

        assert "[problem #5]" in text
        assert REJECTION_MARKER in text
        # Verified sentence appears before the rejection marker (order preserved).
        assert text.index("[problem #5]") < text.index(REJECTION_MARKER)

    async def test_tool_use_iteration_then_verified_synthesis(self) -> None:
        # Multi-iteration turn: tool call first, then verified synthesis.
        # The consumer sees only deltas from the synthesis iteration.
        llm = _make_streaming_llm(
            _tool_use_events([ToolCall(id="t1", name="get_active_problems", input={})]),
            _synthesis_events("Confirmed finding [problem #3]. "),
        )
        problems = _fetcher_returning(_problems(3))
        orch = _build_orchestrator(llm=llm, problems=problems)

        deltas, events = await _collect_stream(orch)

        text = "".join(deltas)
        assert "[problem #3]" in text
        assert REJECTION_MARKER not in text

        # Exactly one StreamFinal from the synthesis iteration.
        finals = [e for e in events if isinstance(e, StreamFinal)]
        assert len(finals) == 1
        assert finals[0].response.stop_reason == "end_turn"

    async def test_stream_final_carries_verified_text(self) -> None:
        # The terminal StreamFinal.response.text must match what the consumer
        # received, not the raw unverified LLM output.
        llm = _make_streaming_llm(
            _tool_use_events([ToolCall(id="t1", name="get_active_problems", input={})]),
            _synthesis_events(
                "Verified claim [problem #2]. ",
                "Unverified claim with no citation. ",
            ),
        )
        problems = _fetcher_returning(_problems(2))
        orch = _build_orchestrator(llm=llm, problems=problems)

        deltas, events = await _collect_stream(orch)

        finals = [e for e in events if isinstance(e, StreamFinal)]
        assert len(finals) == 1
        final_text = finals[0].response.text

        # StreamFinal carries the verified text (contains REJECTION_MARKER for
        # the unverified sentence, not the raw fabricated text).
        assert REJECTION_MARKER in final_text
        assert "[problem #2]" in final_text
        # The consumer deltas and the StreamFinal text should be consistent.
        assert "".join(deltas) in final_text or final_text in "".join(deltas) or final_text == "".join(deltas)


# ---------------------------------------------------------------------------
# Verifier disabled — direct passthrough
# ---------------------------------------------------------------------------


class TestVerifierDisabledPassthrough:
    async def test_uncited_text_passes_through_when_verifier_disabled(self) -> None:
        # With verifier_enabled=False the legacy behavior is preserved:
        # uncited text reaches the consumer unchanged.
        llm = _make_streaming_llm(
            _synthesis_events("No citation here at all. "),
        )
        orch = _build_orchestrator(llm=llm, verifier_enabled=False)

        deltas, _ = await _collect_stream(orch)

        assert "".join(deltas) == "No citation here at all. "
        assert REJECTION_MARKER not in "".join(deltas)

    async def test_text_deltas_are_forwarded_as_is_when_disabled(self) -> None:
        # Each token must arrive individually (no buffering or reordering).
        llm = _make_streaming_llm(
            _synthesis_events("Hello, ", "Susan!"),
        )
        orch = _build_orchestrator(llm=llm, verifier_enabled=False)

        deltas, _ = await _collect_stream(orch)

        assert deltas == ["Hello, ", "Susan!"]
