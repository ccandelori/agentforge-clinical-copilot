# Where we left off — 2026-05-03 (end of session, week1-gaps 16/22)

Read me first when picking the project back up. Update or delete me
when the state captured here goes stale.

## Headline

**Streaming stack is fully wired end-to-end, minus the safety gate.**
This session shipped the entire streaming refactor chain (#9 → #12) plus
two independent tasks (#16 eval runner, #11 PHP proxy). The only remaining
piece before `STREAMING_ENABLED=true` is safe to flip in production is #13
(verifier-before-emit gate).

**Current state: week1-gaps 16/22 done.**

Remaining 6 tasks:
- #13 Verifier-before-emit gate (complexity 8) — **THE headline next task**
- #17 Five failure-mode eval cases (complexity 3) — depends on #16 ✓
- #18 Deterministic + LLM-judge graders (complexity 5) — depends on #17
- #19 Eval report writer (complexity 3) — depends on #18
- #20 Enable verifier by default (complexity 2) — depends on #13 + #19
- #22 Observability acceptance tests (complexity 8) — depends on #4, #6, #8, #13

## What shipped this session

```
week1-gaps tasks closed (in merge order, all on main):
  11  PHP turn.php SSE proxy         — Cache-Control + X-Accel-Buffering
  12  agent_panel.js SSE reader      — ReadableStream, split-frame handling
  16  EvalRunner direct Orch.turn()  — CI eval without live sidecar
  (plus #7, #8, #9, #10 shipped earlier this same session)

Total this session: 8 tasks (#7-#12, #16 + hotfixes from previous)
```

PHP isolated suite: **2996/2996** (no regressions)
Sidecar default suite: **last confirmed green** after #10 merge
JS tests: **8/8** (new agent_panel.test.js, jsdom environment)

## The one thing that matters next: Task #13

**Task #13 — Verifier runs on finalized streamed text (complexity 8)**

This is the safety gate that allows `STREAMING_ENABLED=true` to be set in
production. Until it ships, streaming is wired but disabled (the flag
defaults to `false` in `config.py`).

### Clinical safety constraint (DO NOT violate)

NEVER emit unverified clinical text to the browser then rewrite it.
That briefly exposes potentially unsafe content — a clinical-safety
violation regardless of how fast the rewrite arrives. Only emit sentences
AFTER they pass the verifier.

### Architecture

The `StreamingVerifier` at `sidecar/src/agentforge/verifier/streaming_verifier.py`
is already built. It buffers tokens until a sentence boundary (`.!?` +
whitespace or `\n\n`), runs `find_citations()` → `CitationIndex.contains()` +
`DomainConstraintChecker.check()`, and yields `VerifiedChunk(text, verified)`.
On failure it replaces the sentence with `REJECTION_MARKER`.

The existing `Orchestrator.stream_turn()` in `orchestrator/__init__.py`
was built in task #10 — it currently yields `StreamTextDelta` events
directly from `self._llm.stream()` WITHOUT routing through the verifier.
Task #13 wires the verifier into that path.

### What needs to change

**`sidecar/src/agentforge/orchestrator/__init__.py`** — in `_stream_turn_inner()`:

After the tool loop collects `tool_results`, instead of streaming LLM
tokens directly:
1. Build `CitationIndex` from `tool_results` via `build_citation_index()`
2. Construct `StreamingVerifier(citation_index=index, domain_checker=self._domain_constraints)`
3. The LLM synthesis call currently yields `StreamTextDelta` events and
   collects `StreamFinal`. Extract the text delta stream and pipe it
   through `verifier.verify_stream(token_stream)`.
4. For each `VerifiedChunk` out of `verify_stream`: yield a `StreamTextDelta`
   with `chunk.text` (which is either the verified sentence or
   `REJECTION_MARKER`).
5. After `verify_stream` exhausts, collect the final `StreamFinal` (the
   LLM's assembled full response + tool_calls) and proceed as now.

Key import: `from agentforge.verifier.cache import build_citation_index`
Key import: `from agentforge.verifier.streaming_verifier import StreamingVerifier`
Key import: `from agentforge.verifier.protocols import DomainConstraintChecker`
(already imported for `turn()` path)

**`sidecar/src/agentforge/config.py`** — flip `streaming_enabled: bool = False`
to `True` ONLY after #13 tests pass. This is the production flip.

### StreamingVerifier API (already built, read-only)

```python
class StreamingVerifier:
    def __init__(
        self,
        citation_index: CitationIndex,
        domain_checker: DomainConstraintChecker | None = None,
    ) -> None: ...

    async def verify_stream(
        self,
        token_stream: AsyncIterator[str],  # yields raw text tokens
    ) -> AsyncIterator[VerifiedChunk]: ...

@dataclass(frozen=True, slots=True)
class VerifiedChunk:
    text: str           # verified sentence OR REJECTION_MARKER
    verified: bool
    rejection_reason: str | None = None
```

`REJECTION_MARKER = "[claim withheld — could not be grounded]"`

### Test plan for #13

New file: `sidecar/tests/test_orchestrator_streaming_verifier.py`

Key test cases:
1. Verified sentence passes through as `StreamTextDelta`
2. Ungrounded sentence is replaced with `REJECTION_MARKER` in the stream
3. Multiple sentences: first passes, second fails, third passes — order preserved
4. Tool-use turn (stop_reason="tool_use") — verifier sees no synthesis tokens,
   stream ends with `StreamFinal` carrying tool_calls
5. `STREAMING_ENABLED=true` smoke: end-to-end `/turn` with stub orchestrator
   (this is already covered by test_main_streaming.py — re-confirm it still passes)

Run existing suite before and after to catch regressions:
```
cd sidecar && uv run pytest  # ~620 tests, should stay green
```

### Latency tradeoff (documented, not a bug)

User-perceived latency is per-sentence, not per-token. First verified
sentence arrives ~1-2s after first model token. Total budget (7s p95)
is unchanged. Document in ARCHITECTURE.md §6 if not already there.

### After #13 lands

Flip `streaming_enabled: bool = True` in `config.py` and run:
```
cd sidecar && uv run pytest -m eval
```
ADV-CROSS-PATIENT should still XFAIL (IdentityGuard is wired, guard is on).
All other eval cases should PASS. That's the production-readiness signal.

## Remaining task dependency graph

```
#13 (verifier gate)
 └── #20 (enable verifier by default) — depends on #13 + #19
 └── #22 (observability acceptance)   — depends on #4,#6,#8,#13

#16 (done) ──► #17 (5 eval cases) ──► #18 (graders) ──► #19 (report)
                                                          └── #20
```

#17 + #18 + #19 are the eval quality ramp. They're independent of #13's
safety gate and can be done in parallel with or after #13.

## What's deployed and where

`https://143.244.157.90:9300/` — production demo. Still running pre-#7 code.
The droplet is now significantly behind main (~30+ commits). Recommended:
redeploy after #13 lands (that's the meaningful milestone — streaming live
with the safety gate).

## Quick-start checklist for next time

1. `git status` — confirm clean working tree
2. `task-master tags use week1-gaps && task-master list` — confirm 16/22
3. `cd sidecar && uv run pytest` — confirm default suite green
4. `cd sidecar && uv run pytest -m eval` — confirm 6 pass + 1 xfail
   (ADV-CROSS-PATIENT xfail = IdentityGuard is live, #13 not yet done)
5. `task-master next` should propose **#13**

## Key files for task #13

- `sidecar/src/agentforge/orchestrator/__init__.py` — `_stream_turn_inner()`
  is where the verifier wires in; `stream_turn()` is the public entry point
- `sidecar/src/agentforge/verifier/streaming_verifier.py` — already built,
  read-only for #13
- `sidecar/src/agentforge/verifier/cache.py` — `build_citation_index()`
- `sidecar/src/agentforge/config.py` — `streaming_enabled` flag (flip to
  True ONLY after #13 passes)
- `sidecar/tests/test_main_streaming.py` — existing SSE endpoint tests
  (should stay green after #13)
- `sidecar/tests/test_orchestrator_*.py` — existing orchestrator test files
  (use as patterns for the new #13 test file)

## Carryforwards (not in week1-gaps)

Same as previous session — none resolved this slice. Notable:
- Planner LLM cost not routed through `_record_llm_call` (undercounts cost)
- `per_attempt_timeout` not wired through httpx
- Droplet redeploy pending (waiting for #13 milestone)

## How this session ended

```
16 / 22 week1-gaps tasks closed
~12 commits this slice (tasks #11, #12, #16 + merges)
2996 PHP isolated tests green
8 new JS tests green (agent_panel SSE streaming)
Sidecar default suite last confirmed green after #10
```

Next session opens at **#13 (verifier-before-emit)** — the safety gate
that unlocks `STREAMING_ENABLED=true` in production and closes the
streaming refactor chain started at #9.
