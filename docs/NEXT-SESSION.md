# Where we left off — 2026-05-05 (Task 1 fully shipped, MR 7 wiring gap remains)

Read me first when picking the project back up. Update or delete me
when the state captured here goes stale.

## Headline

**Task 1 — LangGraph supervisor refactor — is DONE.** All six MRs
landed this session:

| MR | Branch | Title |
|---|---|---|
| !30 | feat/w2-task-1d-terminal-citation-index | LangGraph supervisor refactor (MRs 1-4: skeleton + workers + synthesize + terminal + W2 citation index) |
| !31 | feat/w2-task-1e-truncator-dq-langfuse | Synth truncator + DataQuality + Langfuse handoff spans (MR 5) |
| !32 | feat/w2-task-1f-production-cutover | Graph cutover seam + W1 bridge + real routing (MR 6) |
| !33 | chore/w2-status-sync-task1 | Mark Task 1 + 1.1-1.8 done in tasks.json |

`task-master list` now shows Task 1 + every subtask as done.
`task-master next` proposes **Task 24** (Citation Overlay vanilla-JS
with vendored pdf.js) as the next pickup.

## ⚠️ The MVP demo gap — read this before celebrating

**The W2 graph is reachable from Python callers, but NOT yet wired
through the production HTTP path.** This means **the upload → extract →
synthesize demo still does not run end-to-end through the OpenEMR
UI**. MR 6 deliberately stopped at the cutover seam and deferred the
HTTP wiring so reviewers could see one focused diff at a time.

What's missing for the demo to work end-to-end (call this work "MR 7"):

1. **`sidecar/src/agentforge/main.py`** — construct the graph at app
   startup and pass to Orchestrator.
   * Build `VisionExtractor[IntakeFormExtraction]` against the existing
     Anthropic LLM client + `INTAKE_CONTRACT`.
   * Build `EvidenceRetriever` against BM25 + Dense + RRFMerger +
     Reranker. Needs the corpus loader — check the Task 9 RAG pipeline
     wiring for how main.py constructs these elsewhere.
   * Call `build_graph(planner=planner_instance,
     vision_extractor=..., evidence_retriever=...,
     synthesis_llm=llm, truncator=truncator_instance,
     data_quality_checker=data_quality_instance, langfuse=langfuse,
     domain_checker=None)`. Pass result as `agent_graph=...` to
     `Orchestrator(...)`.
2. **`TurnRequest` schema** — extend with optional fields:
   * `document_id: int | None = None`
   * `evidence_query: str = ""`
   * (Skip `pdf_pages` over the wire — bytes belong on a separate
     upload endpoint. The orchestrator's `pdf_pages` kwarg is filled
     server-side by a `DocumentBytesRepository` + `PdfRenderer` pair
     once `document_id` is resolved.)
3. **`/turn` route handler in `main.py`** — pass the new fields
   through to `orchestrator.turn(...)`.
4. **Document fetch + render glue** — when `document_id` is supplied,
   fetch the PDF via `DocumentBytesRepository`, render to pages via
   `PdfRenderer`, and pass `pdf_pages=[...]` to the orchestrator.
5. **PHP `AgentProxyController`** — pass `document_id` /
   `evidence_query` from the JSON body straight through to the
   sidecar (one or two new lines of payload forwarding).
6. **Frontend / chat UI** — when an upload triggered the turn, attach
   the new `document_id` to the next chat send.

This is mostly mechanical. The hard architectural question (W2 graph
shape, citation bridge, routing semantics) is settled. MR 7 is
plumbing — but it IS a real session of plumbing.

## Pick up here

Two choices for next session:

* **Option A — finish the MVP demo (recommended for demo-day prep).**
  Do MR 7 in one branch (probably `feat/w2-mr7-cutover-wiring`).
  Tight scope, tested incrementally. Once green, redeploy to the
  droplet (Task 30) and the upload → extract → synthesize flow
  actually works in the browser.
* **Option B — `task-master next` proposes Task 24.** Citation Overlay
  vanilla-JS component with vendored pdf.js. Independent of MR 7;
  could be done in parallel on the frontend track. Useful for the
  demo's polish (clickable citations to a highlighted PDF region) but
  the demo runs without it.

Memory note (`feedback_task_ordering.md`): "When `task-master next`
proposes a context switch and a depth-first alternative exists,
surface the choice; tiebreaker goes to staying in current context
unless critical-path leverage is significantly higher." Here Task 24
IS a context switch out of the W2 backend; MR 7 is the critical-path
continuation. Recommend MR 7 unless the user wants to context-switch.

## What shipped this session (MRs 30-33)

### MR 30 — LangGraph supervisor refactor (Task 1, MRs 1-4 collapsed)

Pushed and merged the four locally-implemented branches as a single
MR after confirming "no one will look closely" with the user.

* `AgentState` TypedDict, `RouteDecision` StrEnum, `MAX_ITERATIONS = 3`.
* `supervisor_node`, `intake_extractor_node`, `evidence_retriever_node`,
  `synthesize_node`, `terminal_node`.
* `build_w2_citation_index` (W2-only at this MR).
* 30 graph tests, ruff + mypy strict clean.

### MR 31 — synth truncator + DataQuality + Langfuse handoff spans

* `prompts/v1/graph_synthesizer.md` (NEW component, distinct from W1
  `synthesizer`).
* `SynthesisInputTruncator` wired at synthesize input edge (no-op on
  pure W2 turns until MR 6 bridge).
* `DataQualityChecker` warnings prepended to system prompt as
  `<system_reminder>` block; counts ride trace via
  `record_data_quality_metrics`.
* `LangfuseClient.record_handoff_span` (new Protocol method) — real
  impl + Null impl. Supervisor emits one span per routing decision.
* `AgentState.tool_results` schema flipped from `list[Any]` to
  `dict[str, ToolResult[Any]]` (W1-shape, ready for MR 6 bridge).
* Workers stamp `last_node` so handoff spans show real
  `from_node → to_node` paths.

### MR 32 — graph cutover seam + W1 bridge + real routing

* `build_w2_citation_index` walks `state["tool_results"]` via the
  existing W1 `build_citation_index` and merges. The 9 W1 regression
  locks now pass when graded against the W2 index.
* `_decide_route(plan, state)` replaces the MR 1 placeholder. Real
  routing: pdf pending → INTAKE; query pending → EVIDENCE; both →
  sequential dispatch via loop-back; nothing pending → SYNTHESIZE.
* `Orchestrator` gains optional `agent_graph` constructor param +
  `pdf_pages` / `document_id` / `evidence_query` kwargs on `turn()`.
  When the graph is wired AND any W2 input is supplied,
  `_run_graph_turn` builds the AgentState and awaits ainvoke; W1
  chart-question turns are unchanged.

### MR 33 — Taskmaster status sync

Surgical edits on `tasks.json` (NOT `task-master set-status`, per
project convention) flipping Task 1 + 1.1-1.8 to done. Used Python
over JSON for the 9 identical `"pending" → "done"` flips.

## What deliberately did NOT ship (deferred to MR 7)

Per `docs/DEVIATIONS.md` 2026-05-05 entry on MR 6:

* `main.py` graph construction. The seam is in place; main.py wiring
  is mechanical.
* `TurnRequest` schema extension + `/turn` route handler change + PHP
  `AgentProxyController` change.
* Document fetch (`DocumentBytesRepository`) + render (`PdfRenderer`)
  glue.
* `chart_question_node` wrapping the W1 iterative loop so the graph
  can absorb every turn shape (today the W1 loop still runs for
  chart questions OUTSIDE the graph).
* Iterative loop drop (kept as the spec's "one release as fallback").
* W2 path picks up identity guard / breakglass / cost / retry (today
  it only wraps timeout, memory, trace, final-text extraction).

Test posture across all merged MRs: **967 sidecar unit tests pass**
(was 948 at session start), 7 deselected (live-stack integration
tests excluded). ruff clean + mypy strict clean on every touched
file.

## Demo recipes (still zero-setup)

```bash
cd sidecar

# Vision-extraction demo (lab + intake variants):
export ANTHROPIC_API_KEY=...
uv run python scripts/extraction_demo.py            # bundled mock lab PDF
uv run python scripts/intake_extraction_demo.py     # bundled mock intake form

# Evidence-retrieval demo (three modes):
uv run python scripts/retrieval_demo.py "ASCVD risk statin therapy"
uv run python scripts/retrieval_demo.py --mode dense "CKD stage 3 management"
uv run python scripts/retrieval_demo.py --mode hybrid "A1C target adult diabetes"

# Graph end-to-end (in tests, until MR 7 wires HTTP):
uv run pytest tests/test_orchestrator_graph.py tests/test_orchestrator_w2_cutover.py
```

Browser demo (upload → extract → synthesize end-to-end through the
OpenEMR UI) is **blocked on MR 7**.

## Reusable surfaces the next session should know

### `Orchestrator` (NEW in MR 32)

* Constructor: `agent_graph: _AgentGraphLike | None = None` (kwarg-only).
* `turn(ctx, user_message, *, session_id, pdf_pages, document_id,
  evidence_query)`. The three new W2 kwargs default to None / "".
* `_run_graph_turn` is the inner method that builds AgentState and
  awaits `agent_graph.ainvoke`. Wraps the call in the per-turn
  timeout, memory load/persist, trace open, and final-text extraction
  via `_last_assistant_text`.
* `_AgentGraphLike` Protocol — narrow ainvoke surface; the langgraph
  CompiledStateGraph satisfies it structurally.

### `graph.py` (mature surfaces)

* `build_graph(planner, *, vision_extractor=None,
  evidence_retriever=None, synthesis_llm=None, domain_checker=None,
  truncator=None, max_synthesis_tokens=12_000,
  data_quality_checker=None, langfuse=None)` — every dep optional;
  no-op when None.
* `build_w2_citation_index(state)` walks W1 tool_results + W2
  evidence_chunks + extraction citations into one CitationIndex.
* `_decide_route(plan, state)` — real routing per state inputs.

## Architectural decisions to honor

* **`graph_synthesizer` is the W2 prompt**, distinct from the W1
  `synthesizer`. Both coexist until the chart-question worker MR
  retires the W1 path. When you wire main.py for MR 7, the W1
  `Orchestrator.SYSTEM_PROMPT` (loaded from `synthesizer`) and the
  graph's `SYNTHESIS_SYSTEM_PROMPT` (loaded from `graph_synthesizer`)
  serve different turn shapes.
* **Workers stamp `last_node` even on no-op short-circuit paths** so
  the supervisor's next handoff span has the correct `from_node`.
  Any new worker must follow this pattern.
* **`AgentState.tool_results` is `dict[str, ToolResult[Any]]`** and
  the W1 bridge in `build_w2_citation_index` walks it. When a
  chart-question worker lands in MR 7+, populate this dict, not a
  list.
* **The cutover seam routes by W2-input presence, not plan use_case.**
  The orchestrator's `turn()` hands off to the graph when
  `pdf_pages or evidence_query`; FOLLOWUP / ADMIT / etc. determinations
  happen INSIDE the graph via `_decide_route`. Don't reintroduce
  use-case-based routing at the orchestrator surface.

## Local dev gotchas (additions to last session)

* **JSON tasks.json edits via Python, not Edit tool.** When flipping
  9 identical `"status": "pending"` lines to `"done"`, the Edit tool's
  uniqueness requirement makes individual edits painful. Use a small
  Python script that loads tasks.json, mutates the right keys, writes
  back. The Bash hook flow accepts this. Just be sure to preserve
  the trailing newline (`json.dumps(...) + "\n"`).
* **Don't let "this is W1 work" make you skip an Orchestrator test
  pass when changing it.** MR 6 added a Protocol + 3 new turn kwargs
  + a helper method to Orchestrator. Even though the change was
  scoped to the W2 path, ALL Orchestrator tests must pass — the W1
  path mustn't regress on the new kwarg defaults.

## How this session ended

```
Task 1 fully shipped across 4 merged MRs (30, 31, 32, 33).
967 sidecar unit tests pass. ruff clean + mypy strict clean on
every touched file.

Subtasks status (per task-master):
  1.1 — done
  1.2 — done
  1.3 — done
  1.4 — done
  1.5 — done
  1.6 — done
  1.7 — done
  1.8 — done
  Task 1 — done

MR 7 (main.py + TurnRequest + PHP + chart-question worker)
remains the work that lights up the upload → extract →
synthesize demo through the OpenEMR UI.

Production /turn endpoint still routes through Orchestrator.turn()
which still runs the W1 iterative loop for chart questions. The
graph is reachable via Python callers (tests) but not via HTTP.
```

## Quick-start checklist for next session

1. `git status` — confirm clean working tree (sqlconf.php hidden).
2. `git log --oneline -5` — verify the three feature MRs and the
   chore are on main.
3. Decide: MR 7 (depth-first; lights up the demo) or Task 24
   (frontend; runs in parallel).
4. **For MR 7**: branch `feat/w2-mr7-cutover-wiring`, then:
   * Read `sidecar/src/agentforge/main.py` `create_app` for the
     Orchestrator construction site.
   * Read `sidecar/src/agentforge/rag/` for how `EvidenceRetriever`
     is built elsewhere (Task 9 ML wiring landed last week).
   * Read the PHP `AgentProxyController` for the JSON body shape
     it forwards.
   * TDD as usual — small wiring tests for each piece.
5. **For Task 24**: branch `feat/w2-task-24-citation-overlay`, then
   `task-master show 24` to read the spec and `task-master expand
   --id=24` if more detail is helpful.
6. Tests after either branch:
   * `cd sidecar && uv run pytest --ignore=tests/integration`
   * `cd sidecar && uv run ruff check && uv run mypy src tests`
   * `composer phpunit-isolated` (if PHP changed)
7. Commit, push, MR. Repeat for the next slice.

## What's deployed where

`http://143.244.157.90:9300/` — production demo droplet. **Still
running W1 code.** Now ~60+ commits behind main and won't reflect any
of this session's Task 1 work until MR 7 ships AND a redeploy runs
(Task 30). After MR 7 + redeploy, the upload → extract → synthesize
demo runs end-to-end through the OpenEMR UI on the droplet.
