"""Unit tests for the recording / replay LLM-client wrappers."""

from __future__ import annotations

import pathlib
from unittest.mock import AsyncMock

import pytest

from agentforge.llm.recording import (
    RecordedCall,
    RecordingLLMClient,
    ReplayLLMClient,
    ReplayLookupError,
    hash_request,
)
from agentforge.llm.types import LLMResponse, Message, ToolCall, ToolSpec


def _stub_response(text: str = "ok", tool_calls: list[ToolCall] | None = None) -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_calls=tool_calls or [],
        stop_reason="end_turn",
        input_tokens=10,
        output_tokens=5,
    )


def _stub_inner(*responses: LLMResponse) -> AsyncMock:
    """Build an AsyncMock LLMClient that returns each response in order."""
    mock = AsyncMock()
    if len(responses) == 1:
        mock.complete.return_value = responses[0]
    else:
        mock.complete.side_effect = list(responses)
    return mock


class TestHashRequest:
    def test_hash_is_deterministic(self) -> None:
        req = {"system": "s", "messages": [], "tools": [], "max_tokens": 1024, "temperature": 1.0}
        assert hash_request(req) == hash_request(dict(req))

    def test_hash_changes_when_field_changes(self) -> None:
        req_a = {"system": "s", "messages": [], "tools": [], "max_tokens": 1024, "temperature": 1.0}
        req_b = {"system": "s2", "messages": [], "tools": [], "max_tokens": 1024, "temperature": 1.0}
        assert hash_request(req_a) != hash_request(req_b)

    def test_hash_is_order_independent(self) -> None:
        # JSON serialisation with sort_keys=True is what gives us the
        # invariant — proving the contract here so a future refactor
        # can't drop sort_keys without tripping a test.
        req_a = {"system": "s", "max_tokens": 1024, "messages": [], "temperature": 1.0, "tools": []}
        req_b = {"messages": [], "system": "s", "tools": [], "temperature": 1.0, "max_tokens": 1024}
        assert hash_request(req_a) == hash_request(req_b)


class TestRecordedCall:
    def test_round_trip_through_jsonl(self) -> None:
        original = RecordedCall(
            request_hash="abc123",
            request={"system": "s", "messages": [], "tools": [], "max_tokens": 100, "temperature": 0.0},
            response=_stub_response("hi"),
            label="case=foo,node=planner",
        )
        line = original.to_jsonl()
        # Must be exactly one line — no embedded newlines breaking JSONL.
        assert "\n" not in line
        restored = RecordedCall.from_jsonl(line)
        assert restored.request_hash == original.request_hash
        assert restored.label == original.label
        assert restored.response.text == "hi"
        assert restored.response.input_tokens == 10


class TestRecordingLLMClient:
    async def test_records_call_with_correct_hash(self, tmp_path: pathlib.Path) -> None:
        inner = _stub_inner(_stub_response("recorded text"))
        recorder = RecordingLLMClient(inner=inner, output_path=tmp_path / "fixture.jsonl")

        result = await recorder.complete(
            system="sys",
            messages=[Message(role="user", content="hi")],
            tools=None,
            max_tokens=512,
            temperature=0.7,
        )
        assert result.text == "recorded text"
        assert len(recorder.calls) == 1

        # Hash should match an independent computation off the same canonical form.
        call = recorder.calls[0]
        assert len(call.request_hash) == 64  # sha256 hex
        assert call.request["system"] == "sys"
        assert call.request["max_tokens"] == 512

    async def test_flush_writes_jsonl_and_round_trips(self, tmp_path: pathlib.Path) -> None:
        inner = _stub_inner(_stub_response("a"), _stub_response("b"))
        out = tmp_path / "out.jsonl"
        recorder = RecordingLLMClient(inner=inner, output_path=out)

        await recorder.complete(system="s1", messages=[Message(role="user", content="q1")])
        await recorder.complete(system="s2", messages=[Message(role="user", content="q2")])

        written = recorder.flush()
        assert written == 2
        # File contains exactly two non-empty lines.
        lines = [l for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 2
        # Each parses back into a RecordedCall.
        calls = [RecordedCall.from_jsonl(line) for line in lines]
        assert {c.response.text for c in calls} == {"a", "b"}

    async def test_label_provider_is_invoked(self, tmp_path: pathlib.Path) -> None:
        inner = _stub_inner(_stub_response())
        labels: list[str] = []

        def label_provider(req: dict) -> str:
            labels.append(req["system"])
            return f"system={req['system']}"

        recorder = RecordingLLMClient(
            inner=inner, output_path=tmp_path / "out.jsonl", label_provider=label_provider
        )
        await recorder.complete(system="planner-prompt", messages=[Message(role="user", content="x")])
        assert labels == ["planner-prompt"]
        assert recorder.calls[0].label == "system=planner-prompt"

    def test_stream_raises(self, tmp_path: pathlib.Path) -> None:
        inner = AsyncMock()
        recorder = RecordingLLMClient(inner=inner, output_path=tmp_path / "x.jsonl")
        with pytest.raises(NotImplementedError, match="streaming"):
            recorder.stream(system="s", messages=[])


class TestReplayLLMClient:
    async def test_serves_recorded_response_by_hash(self, tmp_path: pathlib.Path) -> None:
        # First record a call, then replay it.
        inner = _stub_inner(_stub_response("served from fixture"))
        out = tmp_path / "fix.jsonl"
        recorder = RecordingLLMClient(inner=inner, output_path=out)
        await recorder.complete(
            system="judge-prompt",
            messages=[Message(role="user", content="grade this")],
            temperature=0.0,
        )
        recorder.flush()

        replay = ReplayLLMClient(fixture_path=out)
        result = await replay.complete(
            system="judge-prompt",
            messages=[Message(role="user", content="grade this")],
            temperature=0.0,
        )
        assert result.text == "served from fixture"
        assert replay.calls_served == 1

    async def test_unmatched_request_raises_replay_lookup_error(
        self, tmp_path: pathlib.Path
    ) -> None:
        out = tmp_path / "fix.jsonl"
        # Empty fixture file.
        out.write_text("", encoding="utf-8")
        replay = ReplayLLMClient(fixture_path=out)
        with pytest.raises(ReplayLookupError, match="no recorded response"):
            await replay.complete(system="never recorded", messages=[])

    async def test_missing_fixture_raises_file_not_found(
        self, tmp_path: pathlib.Path
    ) -> None:
        replay = ReplayLLMClient(fixture_path=tmp_path / "does-not-exist.jsonl")
        with pytest.raises(FileNotFoundError, match="replay fixture not found"):
            await replay.complete(system="s", messages=[])

    async def test_duplicate_hash_served_fifo(self, tmp_path: pathlib.Path) -> None:
        # Record two identical requests with different responses — replay
        # must serve them in original order so a per-case observable trace
        # matches the recording.
        inner = _stub_inner(_stub_response("first"), _stub_response("second"))
        out = tmp_path / "fix.jsonl"
        recorder = RecordingLLMClient(inner=inner, output_path=out)
        msg = [Message(role="user", content="same prompt")]
        await recorder.complete(system="dup", messages=msg, temperature=0.0)
        await recorder.complete(system="dup", messages=msg, temperature=0.0)
        recorder.flush()

        replay = ReplayLLMClient(fixture_path=out)
        first = await replay.complete(system="dup", messages=msg, temperature=0.0)
        second = await replay.complete(system="dup", messages=msg, temperature=0.0)
        assert first.text == "first"
        assert second.text == "second"

    async def test_from_files_concatenates(self, tmp_path: pathlib.Path) -> None:
        inner_a = _stub_inner(_stub_response("from-a"))
        inner_b = _stub_inner(_stub_response("from-b"))
        path_a = tmp_path / "a.jsonl"
        path_b = tmp_path / "b.jsonl"
        rec_a = RecordingLLMClient(inner=inner_a, output_path=path_a)
        rec_b = RecordingLLMClient(inner=inner_b, output_path=path_b)
        await rec_a.complete(system="sys-a", messages=[Message(role="user", content="x")])
        await rec_b.complete(system="sys-b", messages=[Message(role="user", content="y")])
        rec_a.flush()
        rec_b.flush()

        replay = ReplayLLMClient.from_files(fixture_paths=[path_a, path_b])
        result_a = await replay.complete(system="sys-a", messages=[Message(role="user", content="x")])
        result_b = await replay.complete(system="sys-b", messages=[Message(role="user", content="y")])
        assert result_a.text == "from-a"
        assert result_b.text == "from-b"

    async def test_tool_calls_round_trip(self, tmp_path: pathlib.Path) -> None:
        # The planner uses tools — make sure tool_calls survive the
        # record/replay round-trip so the planner's tool-input parser
        # still works on the replay path.
        tool_call = ToolCall(id="tu_01", name="submit_plan", input={"use_case": "followup"})
        inner = _stub_inner(_stub_response(text="", tool_calls=[tool_call]))
        out = tmp_path / "tools.jsonl"
        recorder = RecordingLLMClient(inner=inner, output_path=out)
        await recorder.complete(
            system="planner",
            messages=[Message(role="user", content="what next")],
            tools=[
                ToolSpec(
                    name="submit_plan",
                    description="submit the plan",
                    input_schema={"type": "object"},
                ),
            ],
        )
        recorder.flush()

        replay = ReplayLLMClient(fixture_path=out)
        result = await replay.complete(
            system="planner",
            messages=[Message(role="user", content="what next")],
            tools=[
                ToolSpec(
                    name="submit_plan",
                    description="submit the plan",
                    input_schema={"type": "object"},
                ),
            ],
        )
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "submit_plan"
        assert result.tool_calls[0].input == {"use_case": "followup"}

    def test_stream_raises(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "fix.jsonl"
        out.write_text("", encoding="utf-8")
        replay = ReplayLLMClient(fixture_path=out)
        with pytest.raises(NotImplementedError, match="streaming"):
            replay.stream(system="s", messages=[])
