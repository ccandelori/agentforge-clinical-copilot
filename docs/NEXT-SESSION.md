# Where we left off — 2026-05-06 (MR 7 shipped, droplet redeployed, demo lit up)

Read me first when picking the project back up. Update or delete me
when the state captured here goes stale.

## Headline

**MR 7 — production cutover wiring — is DONE and DEPLOYED.** The
upload → extract → synthesize flow now runs end-to-end through the
OpenEMR UI on the production droplet. The W2 evidence-retriever is
live (BM25 + SentenceTransformer dense + RRF + cross-encoder rerank),
loaded from the bundled guideline corpus.

| MR | Branch | Status |
|---|---|---|
| !35 | `feat/w2-mr7-cutover-wiring` | open, awaiting review |

`task-master next` proposes **Task 24** (Citation Overlay vanilla-JS
with vendored pdf.js) as the next pickup.

## What runs end-to-end on the droplet now

1. **Chart questions** (W1 path, unchanged): "show recent labs",
   "what meds is she on", etc. → orchestrator's iterative tool-use
   loop hits the existing fetchers.
2. **Intake-form extraction** (W2 graph, INTAKE flow): user clicks
   "Attach intake form", picks a PDF, types a message → frontend
   POSTs the upload to OpenEMR's `upload_document.php`, gets a
   `document_id` back, attaches it to the next /turn → sidecar
   fetches the bytes via `DocumentBytesFetcher`, renders pages with
   `PdfRenderer`, hands them to the graph's intake-extractor node →
   final assistant text returned through the existing chat panel.
3. **Guideline retrieval** (W2 graph, EVIDENCE flow): user toggles
   "Search guidelines", types a clinical-knowledge question → next
   /turn ships the message as `evidence_query` → graph's
   evidence-retriever node runs the full RAG pipeline against the
   bundled corpus → synthesize node grounds the answer in retrieved
   chunks.

Mixed turns (both upload AND evidence) sequence-dispatch in the
graph: INTAKE first, then EVIDENCE, then SYNTHESIZE.

## Deploy state

* **Commits on branch (8):** 5 feature slices (DocumentBytesFetcher,
  Settings, create_app graph wiring, /turn route W2 inputs,
  frontend upload + toggle) + 3 supporting (DEVIATIONS,
  DEPLOYMENT, Dockerfile corpus-bake fix).
* **Droplet (`http://143.244.157.90:9300/`):** sidecar container
  running the MR-7 image. Boot logs confirm `Loading weights:
  100%` for both the SentenceTransformer encoder (~80 MB) and the
  cross-encoder reranker (~110 MB). `/health` returns
  `{"status":"healthy","policy_loaded":true}` from inside the
  openemr container.
* **Sidecar `.env` on droplet:** `EVIDENCE_RETRIEVER_ENABLED=true`
  added. (See `docs/DEPLOYMENT.md` for the full env-var list.)
* **PHP module:** unchanged (the `AgentProxyController` already
  forwarded JSON bodies verbatim — see `docs/DEVIATIONS.md`
  2026-05-05).
* **Containers running:** 5 (matches the canonical layout per the
  memory note) — agentforge-sidecar, agentforge-redis, openemr,
  mysql, phpmyadmin.

## Caveats for the next operator

* **Deploy script's 30 s health check is too tight for the W2 path.**
  On a clean container start, the retriever build loads ~190 MB of
  ML weights synchronously, which takes 30-60 s on the droplet's
  bandwidth. The deploy script reports an error exit, but the
  container is fine — verify with `docker ps` and
  `docker logs agentforge-sidecar`. Fixing this properly means
  either bumping the timeout or mounting a Hugging Face cache
  volume so the weights persist across redeploys (see "Follow-ups"
  below).
* **HF cache is not mounted as a volume.** Every `docker rm -f &&
  docker run` cycle re-downloads the weights. ~1 minute per
  redeploy is the current cost.
* **Disk pressure on the droplet.** `/dev/vda1` was at 79 % after
  the second build. Consider a `docker system prune -f` if a
  future deploy hangs in the export phase — buildkit may stall
  silently when storage runs short.

## Pick up here

Two choices for the next session:

* **Option A — Task 24 (recommended).** Citation Overlay vanilla-JS
  component with vendored pdf.js. The W2 graph already produces
  citations with bbox coordinates; this MR makes them clickable
  in the chat surface (overlay highlights the cited region of the
  source PDF). Independent of any backend work; high leverage on
  demo polish. Branch: `feat/w2-task-24-citation-overlay`. Run
  `task-master show 24` for the spec; `task-master expand --id=24`
  if more detail helps.
* **Option B — MR-7 follow-ups.** Several small things would
  smooth out production behavior:
  * Bump deploy script's health-check timeout to 120 s OR mount
    `/opt/agentforge/hf_cache:/home/agentforge/.cache/huggingface`
    on the sidecar container so the model weights survive redeploys.
  * `chart_question_node` wrapping the W1 iterative loop — once
    that lands, every turn flows through the graph and the W1
    code path can retire.
  * Streaming integration on the W2 path — today /turn forces
    non-streaming when `document_id` or `evidence_query` is set.
  * Identity guard / breakglass / cost / retry promotion into
    the graph path.
  * Iterative-loop drop (the spec's "one release as fallback").

Memory note (`feedback_task_ordering.md`): "When `task-master next`
proposes a context switch and a depth-first alternative exists,
surface the choice; tiebreaker goes to staying in current context."
Both options are roughly equal-leverage today: Task 24 ships a
visible demo win, follow-ups harden the path that just shipped.
The recommendation is Task 24 unless you want to fortify the W2
path before showing it.

## What shipped this session (MR !35)

### Slice A — `DocumentBytesFetcher` (sidecar Python)

JWT-authed httpx GET against the existing PHP
`InternalDocumentBytesController`. Forwards the user-bound JWT
verbatim so the patient-scope check on the PHP side stays
load-bearing. Returns `DocumentBytes(content, mimetype)`. Errors
carry an HTTP status (or 0 for transport failures) so the route
handler can map cleanly to 502 vs 503.

### Slice B — Settings additions

`guidelines_index_path` (default points at the bundled corpus) and
`evidence_retriever_enabled` (default **off**, see DEVIATIONS for
why).

### Slice C — `create_app` graph wiring

Builds `PdfRenderer`, `DocumentBytesFetcher`, `VisionExtractor`
(only when an Anthropic key is set), and `EvidenceRetriever`
(gated on flag + corpus presence). The graph itself is wrapped
in `_LazyAgentGraph` so the langgraph compile defers to the first
W2-routed /turn — keeps test-fixture setup fast.

### Slice D — `/turn` route accepts W2 inputs

`TurnRequest` extended with `document_id` and `evidence_query`.
When `document_id` is set, the route chains
`DocumentBytesFetcher` → `PdfRenderer` → `orchestrator.turn`.
Errors map to 503 (transport) / 502 (upstream) / 422 (non-PDF).
Streaming SSE is suppressed on W2 turns. Chart-question turns
take the byte-identical W1 code path so existing stub
orchestrators (which don't accept the new kwargs) keep working.

### Slice E — Frontend upload + send wiring

Twig template grows a file-input + "Attach intake form" button +
"Search guidelines" toggle + pending-doc indicator. JS handles
upload → stash `document_id` on panel dataset → next send attaches
it (and clears immediately so a follow-up message doesn't
re-attach). Guidelines toggle is sticky-on so a clinician can
ask several research questions in a row.

### Supporting commits

* `docs/DEVIATIONS.md` entry on the `EVIDENCE_RETRIEVER_ENABLED`
  default-off rationale.
* `docs/DEPLOYMENT.md` documents the new env var + the cold-start
  caveat.
* `sidecar/Dockerfile` patched to `COPY data/ ./data/` so the
  guideline corpus ships in the image. Caught after the first
  droplet deploy showed "guideline corpus missing" in the logs;
  fix verified with the second deploy.

Test posture across the MR: 993 sidecar unit tests pass (was 967),
9 new jsdom tests cover the upload widget + W2 send paths (366
total tests/js). ruff clean + mypy strict clean on every touched
file.

## Demo recipes (still zero-setup, plus one new browser flow)

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

# Graph end-to-end via tests:
uv run pytest tests/test_orchestrator_graph.py tests/test_orchestrator_w2_cutover.py
```

**NEW: browser demo at http://143.244.157.90:9300/** — open a
patient chart, click "Attach intake form" in the Co-Pilot panel,
upload a PDF, type "extract this", send. Or toggle "Search
guidelines" and type a clinical-knowledge question.

## Reusable surfaces the next session should know

### `Orchestrator` (unchanged from MR 6)

* Constructor: `agent_graph: _AgentGraphLike | None = None` (kwarg-only).
* `turn(ctx, user_message, *, session_id, pdf_pages, document_id,
  evidence_query)`. The three new W2 kwargs default to None / "".
* `_run_graph_turn` builds the AgentState and awaits `agent_graph.ainvoke`.
* `_AgentGraphLike` Protocol — narrow ainvoke surface; the langgraph
  CompiledStateGraph satisfies it structurally; `_LazyAgentGraph`
  (in `agentforge.main`) does too.

### `agentforge.main` (NEW in MR 7)

* `create_app(...)` adds five injectable kwargs:
  `pdf_renderer`, `document_bytes_fetcher`, `vision_extractor`,
  `evidence_retriever`, `agent_graph`. Each is built from settings
  by default; tests inject overrides.
* `get_document_bytes_fetcher` and `get_pdf_renderer` are FastAPI
  dependencies that pull instances off `app.state` so the /turn
  handler and tests can override them via
  `app.dependency_overrides`.
* `_build_evidence_retriever(settings)` — returns None when the
  flag is off OR the corpus file is missing. The graph node
  no-ops on None.
* `_LazyAgentGraph(builder)` — defers `build_graph()` until the
  first `ainvoke` call. Keeps create_app cheap.

### `agentforge.tools.document_bytes` (NEW)

* `DocumentBytesFetcher(base_url, *, http_client=None, path=...)`.
* `fetch(*, document_id, raw_token) -> DocumentBytes`.
* `DocumentBytes(content: bytes, mimetype: str)`.
* `DocumentBytesFetchError(status_code, message)` — `status_code=0`
  means transport failure.

## Architectural decisions to honor

* **`graph_synthesizer` is the W2 prompt**, distinct from the W1
  `synthesizer`. Both coexist until the chart-question worker MR
  retires the W1 path.
* **Workers stamp `last_node` even on no-op short-circuit paths.**
  Any new worker must follow this pattern.
* **`AgentState.tool_results` is `dict[str, ToolResult[Any]]`.**
  When a chart-question worker lands, populate this dict, not a
  list.
* **The cutover seam routes by W2-input presence, not plan use_case.**
  `turn()` hands off to the graph when `pdf_pages or evidence_query`;
  FOLLOWUP / ADMIT / etc. happens INSIDE the graph via
  `_decide_route`. Don't reintroduce use-case-based routing at the
  orchestrator surface.
* **`EVIDENCE_RETRIEVER_ENABLED` defaults off.** Production opts
  in via `.env`. Tests don't pay the ML-weight load. See
  `docs/DEVIATIONS.md` 2026-05-05 for the full rationale.

## Local dev gotchas (additions to last session)

* **Deploy script's 30 s health check is too tight for first-time
  W2 startup.** When the script exits non-zero, verify the
  container manually before assuming a real failure:
  ```bash
  ssh root@143.244.157.90 "docker ps; docker logs agentforge-sidecar | tail -20"
  ```
* **The corpus must be in the container image, not just on disk.**
  `Dockerfile` now `COPY data/ ./data/` — easy to forget when
  adding a new bundled-data layer.
* **Test runtime is sensitive to eager ML loads.** When adding any
  feature that pulls Hugging Face weights at construction, gate
  it behind a flag that defaults off, OR wrap it in a lazy holder
  like `_LazyAgentGraph`.

## How this session ended

```
MR !35 (feat/w2-mr7-cutover-wiring) shipped 8 commits and is
deployed to the droplet. The upload → extract → synthesize flow
runs end-to-end through the OpenEMR UI for the first time.

Test posture:
  993 sidecar unit tests pass (was 967)
  366 JS tests pass (added 9)
  ruff clean + mypy strict clean
  Droplet container healthy; corpus loaded; ML weights loaded.

Production /turn endpoint now routes W2-input turns through the
LangGraph supervisor; chart-question turns continue through the
W1 iterative loop until ``chart_question_node`` lands.
```

## Quick-start checklist for next session

1. `git status` — confirm clean working tree.
2. `git log --oneline -5` — verify the latest is on main.
3. Decide: Task 24 (frontend, demo polish) or MR-7 follow-ups
   (HF cache volume, deploy timeout bump, chart_question_node).
4. **For Task 24**: branch `feat/w2-task-24-citation-overlay`,
   `task-master show 24` for spec, vendor pdf.js if not already
   pulled.
5. **For follow-ups**: `feat/sidecar-hf-cache-volume` is a quick
   win — adds `-v /opt/agentforge/hf_cache:/home/agentforge/.cache/huggingface`
   to the deploy script's docker run. ~30 min including verification.
6. Tests after either branch:
   * `cd sidecar && uv run pytest --ignore=tests/integration`
   * `cd sidecar && uv run ruff check && uv run mypy src tests`
   * `npx jest tests/js/` (if JS changed)
   * `composer phpunit-isolated` (if PHP changed)
7. Commit, push, MR. Repeat for the next slice.

## What's deployed where

`http://143.244.157.90:9300/` — production demo droplet, now
running the MR-7 image. The upload → extract → synthesize demo
runs end-to-end through the OpenEMR UI. Sidecar logs confirm
the W2 evidence retriever is loaded and serving.
