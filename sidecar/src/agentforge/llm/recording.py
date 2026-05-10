"""Recording / replay wrappers around the :class:`LLMClient` Protocol.

These wrappers exist to support the W2 eval gate's HARD GATE
contract: the gate must trip on a code-level regression (a change to
the synthesizer, planner, citation extractor, etc.) without burning
real Anthropic spend per push.

Two halves:

* :class:`RecordingLLMClient` — wraps a real LLM client, persists every
  ``complete()`` request + response to a JSONL fixture file. Used once
  from the manual baseline-regen path (paid run; ~$1.54).

* :class:`ReplayLLMClient` — reads a JSONL fixture and serves recorded
  responses keyed by request hash. Used by the CI eval-gate's replay
  job — every push runs the *real* planner / synthesizer / judge code
  paths, but the LLM round-trip is canned. A code-level regression
  (e.g. the synthesizer drops citations) processes the canned response
  differently and the gate trips.

Why a separate module (not a tweak to ``ClaudeClient``)
-------------------------------------------------------

The recording wrapper has to live above the provider boundary so the
fixture is provider-agnostic — replaying recorded Anthropic responses
into a future Vertex / OpenAI client is a pure translation problem
because the wire format is :class:`LLMResponse`, not Anthropic SDK
shapes. Keeping the wrapper in ``agentforge.llm`` (next to
``client.py`` / ``claude.py``) puts it in the same "provider seam"
namespace where the Protocol lives.

Streaming intentionally raises in the replay client. Today's eval
path only uses ``complete()`` (planner + synthesizer + judge are all
one-shot calls); when the verifier's streaming path enters the eval
loop, the replay wrapper will need a sibling fixture for stream
events. We trip loudly rather than silently swallow the stream call.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import pathlib
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from typing import Any

from agentforge.llm.client import LLMClient
from agentforge.llm.types import LLMResponse, Message, StreamEvent, ToolSpec


def _canonical_request(
    *,
    system: str,
    messages: list[Message],
    tools: list[ToolSpec] | None,
    max_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    """Return a JSON-serialisable, deterministic request representation.

    Sorted keys + frozen field order means hashing the same logical
    request twice yields the same digest regardless of dict insertion
    order. This is the key the replay client matches on, so any drift
    in field naming will manifest as a clean "no fixture matched"
    error rather than a silent wrong-response substitution.
    """
    return {
        "system": system,
        "messages": [m.model_dump(mode="json") for m in messages],
        "tools": [t.model_dump(mode="json") for t in (tools or [])],
        "max_tokens": int(max_tokens),
        # Round so 0.0 vs 0 doesn't change the hash; temperature is
        # one of the few floats the judge contract pins (0.0 for
        # determinism), so an exact-match comparison is fine.
        "temperature": float(temperature),
    }


def hash_request(request: dict[str, Any]) -> str:
    """Stable SHA256 digest of a canonical request dict.

    Public so callers can derive the same key the recording / replay
    pair uses (e.g. for fixture inspection or test assertions).
    """
    blob = json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


@dataclass(frozen=True)
class RecordedCall:
    """One LLM call captured from a recording session.

    The ``label`` is an optional human-readable tag the recorder writes
    so a fixture file is greppable by case_id / node_name when an
    operator inspects it. It is NOT load-bearing — replay matches on
    ``request_hash`` only.
    """

    request_hash: str
    request: dict[str, Any]
    response: LLMResponse
    label: str = ""

    def to_jsonl(self) -> str:
        return json.dumps(
            {
                "request_hash": self.request_hash,
                "request": self.request,
                "response": self.response.model_dump(mode="json"),
                "label": self.label,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_jsonl(cls, line: str) -> RecordedCall:
        data = json.loads(line)
        return cls(
            request_hash=data["request_hash"],
            request=data["request"],
            response=LLMResponse.model_validate(data["response"]),
            label=data.get("label", ""),
        )


class RecordingLLMClient:
    """LLMClient wrapper that captures each ``complete()`` call.

    The recorder is process-local — it accumulates calls in memory and
    flushes them to disk via :meth:`flush`. Callers (the regen CLI)
    invoke ``flush()`` once the suite finishes; intermediate flushes
    are safe and idempotent.

    The wrapper deliberately does not write per-call to keep the
    recording session free of partial files: if the suite aborts
    halfway through, no fixture file is half-written.

    The ``label_provider`` callable is optional — when set, it is
    invoked with the canonical request and is expected to return a
    short string identifying the call (e.g. "case_id=w2_cit_03,
    role=planner"). This is best-effort decoration, never used to key
    replay.
    """

    def __init__(
        self,
        *,
        inner: LLMClient,
        output_path: pathlib.Path,
        label_provider: Any = None,
    ) -> None:
        self._inner = inner
        self._output_path = output_path
        self._label_provider = label_provider
        self._calls: list[RecordedCall] = []
        # Avoid concurrent appends if a future caller fans out — the
        # eval gate runs sequentially today, but the lock makes the
        # invariant explicit rather than implicit.
        self._lock = asyncio.Lock()

    async def complete(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
        temperature: float = 1.0,
    ) -> LLMResponse:
        request = _canonical_request(
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        digest = hash_request(request)
        response = await self._inner.complete(
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        label = ""
        if self._label_provider is not None:
            with contextlib.suppress(Exception):
                label = str(self._label_provider(request))
        async with self._lock:
            self._calls.append(
                RecordedCall(
                    request_hash=digest,
                    request=request,
                    response=response,
                    label=label,
                )
            )
        return response

    def stream(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
    ) -> AsyncIterator[StreamEvent]:
        # Streaming isn't wired into the eval path yet — refuse loudly
        # rather than silently no-op so the next caller has to design
        # the recording shape deliberately.
        raise NotImplementedError(
            "RecordingLLMClient does not record streaming calls; "
            "the eval-replay path uses complete() exclusively. "
            "When the streaming verifier enters the eval loop, "
            "extend RecordedCall to carry a stream-event sequence."
        )

    @property
    def calls(self) -> tuple[RecordedCall, ...]:
        """Snapshot of recorded calls — immutable for read-only inspection."""
        return tuple(self._calls)

    def flush(self) -> int:
        """Write every recorded call to ``output_path`` as JSONL.

        Returns the number of calls written. Caller is responsible for
        invoking flush exactly once; subsequent invocations rewrite the
        file with the same content.
        """
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [call.to_jsonl() for call in self._calls]
        self._output_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return len(lines)


class ReplayLookupError(LookupError):
    """Raised when ``ReplayLLMClient`` cannot find a recorded response.

    Distinct from KeyError so callers can catch this specifically and
    surface a "your code path diverged from the recording" message
    rather than dumping a stack trace at the SDK boundary.
    """


class ReplayLLMClient:
    """LLMClient wrapper that serves recorded responses by request hash.

    Built once per process from a JSONL fixture file. Lookups are
    O(1) — duplicate request hashes are allowed (the recorder may emit
    them for cases that issue identical sub-prompts), and we serve them
    in FIFO order so the per-case observable trace matches the recording.

    A request that doesn't match any recorded hash raises
    :class:`ReplayLookupError` — silent fallback would defeat the whole
    point of the gate.
    """

    def __init__(self, *, fixture_path: pathlib.Path) -> None:
        self._fixture_path = fixture_path
        self._by_hash: dict[str, list[LLMResponse]] = {}
        self._loaded = False
        self._calls_served: int = 0

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if not self._fixture_path.is_file():
            raise FileNotFoundError(
                f"replay fixture not found at {self._fixture_path}; "
                "regenerate via "
                "`uv run python -m agentforge.eval.regenerate_baseline "
                "--record --record-dir tests/eval/fixtures/recorded`"
            )
        for raw_line in self._fixture_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            call = RecordedCall.from_jsonl(line)
            self._by_hash.setdefault(call.request_hash, []).append(call.response)
        self._loaded = True

    @classmethod
    def from_files(cls, *, fixture_paths: Iterable[pathlib.Path]) -> ReplayLLMClient:
        """Build a replay client by concatenating multiple fixture files.

        Useful when the recorder emits one fixture per case (better for
        diff hygiene) but the runtime expects a single client. Each
        file's calls are loaded in iteration order; per-hash FIFO
        ordering across files is preserved.
        """
        merged_lines: list[str] = []
        for path in fixture_paths:
            if not path.is_file():
                raise FileNotFoundError(f"replay fixture missing: {path}")
            merged_lines.extend(
                line for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        # Materialise into a temporary in-memory composite by overwriting
        # _by_hash directly so we don't have to round-trip through disk.
        instance = cls.__new__(cls)
        instance._fixture_path = pathlib.Path("(merged)")
        instance._by_hash = {}
        instance._loaded = True
        instance._calls_served = 0
        for raw_line in merged_lines:
            call = RecordedCall.from_jsonl(raw_line)
            instance._by_hash.setdefault(call.request_hash, []).append(call.response)
        return instance

    async def complete(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
        temperature: float = 1.0,
    ) -> LLMResponse:
        self._ensure_loaded()
        request = _canonical_request(
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        digest = hash_request(request)
        bucket = self._by_hash.get(digest)
        if not bucket:
            raise ReplayLookupError(
                f"no recorded response for request hash {digest[:12]}…; "
                f"either the calling code path diverged from the recording "
                f"or the fixture is stale. Re-record via the regen CLI."
            )
        # Pop the head — duplicate hashes are served FIFO so a planner
        # and a judge that issue the same prompt twice in a session get
        # their respective responses in order.
        response = bucket.pop(0)
        self._calls_served += 1
        return response

    def stream(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
    ) -> AsyncIterator[StreamEvent]:
        raise NotImplementedError(
            "ReplayLLMClient does not serve streaming calls; "
            "see RecordingLLMClient.stream for context."
        )

    @property
    def calls_served(self) -> int:
        """Total ``complete()`` calls served — for assertions in tests."""
        return self._calls_served

    @property
    def fixture_path(self) -> pathlib.Path:
        return self._fixture_path


@dataclass
class _RequestLabelContext:
    """Mutable per-call label provider for the recording client.

    The recorder tags each call with a free-form string ("case_id=...,
    node=planner"). The eval driver knows the case being processed but
    the LLM client doesn't — this context object is passed into the
    label_provider and updated by the driver as it walks each case.
    Module-level singleton because the eval path is single-threaded.
    """

    case_id: str = ""
    node_hint: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    def render(self, _request: dict[str, Any]) -> str:
        parts: list[str] = []
        if self.case_id:
            parts.append(f"case_id={self.case_id}")
        if self.node_hint:
            parts.append(f"node={self.node_hint}")
        for key, value in self.extra.items():
            parts.append(f"{key}={value}")
        return ",".join(parts)


__all__ = (
    "RecordedCall",
    "RecordingLLMClient",
    "ReplayLLMClient",
    "ReplayLookupError",
    "_RequestLabelContext",
    "hash_request",
)
