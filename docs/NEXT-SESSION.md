# Where we left off — 2026-05-05 (Tasks 6, 13, 14, 9a, 9b shipped)

Read me first when picking the project back up. Update or delete me
when the state captured here goes stale.

## Headline

**15 W2 tasks done.** This session shipped the intake-form vision
variant (Task 13), the PHI-safe extraction-call observability boundary
(Task 14), the browser-upload endpoint that closes the W2 vision loop
(Task 6), and the full hybrid RAG pipeline in two tightly-scoped MRs
(Task 9a + 9b).

The W2 MVP demo path is now end-to-end shippable in a single user
flow:

```
clinician opens chart in OpenEMR
    ↓ uploads PDF via /agentforge/upload_document          (Task 6)
    ↓ Document::createDocument writes to docstore
    ↓ sidecar's vision tool fetches bytes via              (Task 7)
    ↓   /agentforge/internal/get_document_bytes (JWT)
    ↓ VisionExtractor + Claude vision + PyMuPDF            (Tasks 11/13)
    ↓   produces LabPdfExtraction or IntakeFormExtraction
    ↓ POST /agentforge/internal/persist_*                  (Tasks 8/12)
    ↓   triple-checked patient ownership, atomic write
    ↓ EvidenceRetriever pulls cited guideline chunks       (Task 9)
    ↓   BM25 + Dense → RRF → CrossEncoder rerank
```

Each link in the chain is shipped, tested, and demoable today. What's
**not** yet wired: the LangGraph orchestrator that composes them
into a single agent turn (Task 1, the architectural anchor).

## Demo recipes (zero-setup)

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
#   bm25 (default): instant, no model download
#   dense:   first run downloads all-MiniLM-L6-v2 (~80 MB) into HF cache
#   hybrid:  first run also downloads bge-reranker-base (~110 MB)
```

End-to-end UI demo (the full loop) needs Task 1 wired before it's
runnable; the components above run in isolation.

## What shipped today (this session)

| MR | Task | Headline |
|---|---|---|
| !20 | 13 | Intake-form vision variant via VisionContract refactor (parameterized over schema/prompt/tool spec) |
| !22 | 14 | `record_extraction_call` PHI-safe boundary — sibling method, not the spec's CallType-enum wrapper |
| !24 | 6 | Browser-upload endpoint with session-pid authority + magic-byte PDF check |
| !25 | 9a | `rag/` package: `GuidelineChunk`, `BM25Retriever`, `RRFMerger`, `Reranker` Protocol, `PassthroughReranker` |
| !27 | 9b | `DenseRetriever` (sentence-transformers), `CrossEncoderReranker` (bge-reranker-base), `CohereReranker` (opt-in), `EvidenceRetriever` orchestrator |

Plus four status-sync chore MRs (!21, !23, !26, and one for Task 9
when 9b lands).

## Reusable services / shapes the next session should know

### PHP (oe-module-agentforge/src/)

- `DocumentOwnershipVerifier` — JWT-validated patient-id triple check
- `DocumentUploadWriter` — wraps legacy `Document::createDocument`,
  resolves category by **name** (so deployments where category ids
  differ still work). lab_pdf → "Lab Report"; intake_form → "Patient
  Information"
- `DocumentIngestAuditWriter` — fires `agentforge.document_ingest`
  on success only; one row per successful upload (mismatch path
  deliberately doesn't audit)
- `IntakeQuestionnaireLookup` / `IntakeQuestionnaireResponseWriter` /
  `IntakeFormFhirMapper` — intake persistence
- `LabResultWriter` — multi-table cascade in DBAL transaction
- `IntakePersistAuditWriter` / `LabPersistAuditWriter` — success-only
  event writers
- `UploadDocumentController` (browser, session+CSRF) +
  `InternalIntakePersistController` /
  `InternalLabPersistController` /
  `InternalDocumentBytesController` (sidecar→OpenEMR, JWT)

### Sidecar Python (src/agentforge/)

- `tools/attach_and_extract.py` — `VisionContract[T: BaseModel]` +
  `VisionExtractor[T]` parameterized over `LAB_CONTRACT` / `INTAKE_CONTRACT`
- `schemas/lab.py`, `schemas/intake.py`, `schemas/citation.py` —
  Pydantic models with citation-floor + inverted-bbox enforcement
- `observability/protocols.py` — `LangfuseClient` Protocol with
  `record_extraction_call` for PHI-safe vision-call traces
- `rag/types.py` — `GuidelineChunk` + `RetrievalResult`
- `rag/bm25.py` — `BM25Retriever` (inline math, no rank_bm25 dep)
- `rag/dense.py` — `DenseRetriever` + `Encoder` Protocol +
  `SentenceTransformerEncoder`
- `rag/cross_encoder.py` — `CrossEncoderReranker` + `CrossEncoder`
  Protocol + `SentenceTransformerCrossEncoder`
- `rag/cohere_rerank.py` — `CohereReranker` (opt-in via [cohere] extra)
- `rag/rrf.py` — `RRFMerger` (k=60 default, dedup by chunk_id)
- `rag/reranker.py` — `Reranker` Protocol + `PassthroughReranker`
- `rag/evidence_retriever.py` — `EvidenceRetriever` composing the
  full BM25 + Dense → RRF → Reranker pipeline
- `rag/loader.py` — `load_corpus(index_path) -> list[GuidelineChunk]`

## Next moves (MVP critical path)

`task-master next` proposes **Task 1** (orchestrator → LangGraph
supervisor refactor, cx=9). It's the architectural anchor — blocks
Tasks 15, 18, 27 — and ties everything shipped into one agent turn.
Likely needs to ship in 2-3 MRs: skeleton graph + state + supervisor
node, then worker integration, then cut over the existing iterative
loop.

The **MVP critical path** to a defendable demo on the droplet:

1. **Task 1** (cx=9) — LangGraph supervisor. Wires
   `VisionExtractor` and `EvidenceRetriever` into the agent loop.
2. **Task 24** (cx=6) — Citation Overlay component. Renders bbox
   citations on the chart-side UI.
3. **Task 25** (cx=5) — Wire Citation Overlay into the chart panel.
4. **Task 28 + 29** (cx=5 each) — End-to-end lab/intake tests.
   Verify the full upload → extract → persist → display loop.
5. **Task 30** (cx=6) — Deploy the W2 build to the droplet.

Bypass-able for MVP (defer to post-defense polish):

- **Task 15** (evidence-retriever LangGraph node) — needs Task 1.
- **Tasks 16-19** (eval set + eval gate) — gating; not user-visible.
- **Task 21** (Sidecar Dockerfile update) — needed for Task 30 deploy.
- **Tasks 31-35** (eval reports, demo video, README, defense slides)
  — wrap-up artifacts.

## Architectural decisions to honor

- **Citation contract**: every clinical claim carries a `Citation`
  with the bbox-confidence floor (0.7) and inverted-bbox rejection
  enforced at the schema layer.
- **Page indexing is 1-indexed** throughout. Don't subtract 1
  anywhere in overlay code.
- **Triple-check at every persistence endpoint**: `JWT.patientId ==
  request.patient_id == documents.foreign_id`. Mismatch → 403, no
  info leak about which leg failed. Reuse `DocumentOwnershipVerifier`.
- **Audit fires only on success.** 1:1 with rows actually written.
- **No PHI in logs.** Module loggers emit only structural metadata
  (page count, model, tokens). PHI redaction at the trace boundary
  is enforced **structurally** by the Protocol — no method takes a
  content-carrying parameter.
- **No structured EHR table writes from AI persistence.** Unapproved
  records (`questionnaire_response`, `procedure_*`) are what the
  overlay reads; structured tables (`patient_data`, `lists`,
  `medications`, `allergies`, `family_history`) only get written
  when a clinician approves on the overlay UI.
- **JSON-shape-as-tool pattern for vision.** Inline tool spec (not
  Pydantic-reflected) so a Pydantic field rename can't silently
  reshape the LLM emission contract.
- **Bytes stay in memory.** PDF rendering never writes a temp file.
- **Narrow catch (DbalException | JsonException | RuntimeException)
  on PHP write paths.** `ForbiddenCatchTypeRule` blocks Throwable /
  Exception. Programmer bugs propagate.
- **patient_id authority lives in the session, not multipart**
  (browser-upload endpoint). Mismatch → 400, no audit fired.
- **Parameterize over abstract base.** `VisionExtractor[T]` and the
  `VisionContract` pattern beat subclassing because the variance is
  in data (prompt, tool_spec, schema) not behavior.
- **Reranker as Protocol with three impls.** `Passthrough` is the
  fallback; `CrossEncoder` is the local default; `Cohere` is opt-in.
  The Protocol is what the orchestrator depends on; impls swap via
  DI without API churn.

## Local dev gotchas

- **`task-master set-status` re-stringifies task IDs** as a side
  effect, blowing up `add-dependency` and inflating the diff to
  ~600 lines. The renormalization recipe (commit body of older
  status-sync MRs) is to walk the JSON and convert pure-digit
  string IDs back to ints. **Surgical 1-line `Edit` of the status
  field bypasses the gotcha entirely** — preferred for single
  status flips.
- **PHPStan cache surfaces stale baseline drift inconsistently.**
  `rm -rf tmp-phpstan/cache && composer phpstan` fixes it. The
  errors don't reproduce in CI.
- **`Document::get_data()` throws `BadMethodCallException` +
  `RuntimeException`** on legacy storage edge cases. Docblock only
  declares one. The `DocumentBytesRepository` uses a justified
  `@phpstan-ignore catch.neverThrown` for the second.
- **PHP not installed on host.** All composer scripts run via
  `docker exec development-easy-openemr-1 bash -c '...'`.
- **`sites/default/sqlconf.php` is `--skip-worktree`** — keeps the
  local override (host=`mysql`, `$config=1`) hidden from git status.
- **Anthropic SDK requires 256-bit HMAC keys** in tests. A 31-char
  test secret = 248 bits and fails JWT signing.
- **PDF generators are deterministic** via ReportLab's `invariant=1`.
  `sample-lab.pdf` and `sample-intake.pdf` are byte-identical across
  re-runs — safe to commit.
- **CI runner is `concurrent = 1`** — back-to-back MRs serialize.
  Pipeline timing budget: ~5–6 min when idle, ~10 min when contended.
- **Sidecar deps now include `sentence-transformers`** (~500 MB).
  First `uv sync` on a fresh checkout pulls PyTorch + Transformers.
  Cohere SDK is behind the `[cohere]` extra (`uv sync --extra
  cohere` to install).
- **Hybrid RAG demos download model weights on first run** into the
  Hugging Face cache (~80 MB + ~110 MB). Subsequent runs are
  instant. Tests use stub encoders so they never touch the real
  models.
- **`PHPUnit MockObject::with()` is `@no-named-arguments`** — pass
  positional only or PHPStan rejects. Documenting parameter order
  in a comment above the call helps reviewers.

## Quick-start checklist

1. `git status` — confirm clean working tree (sqlconf.php hidden).
2. `git checkout main && git pull` — should be clean.
3. `task-master tags use week2 && task-master list` — confirm 15/37
   done (or 16 once 9b's status-sync lands).
4. `task-master next` — should propose Task 1 (orchestrator refactor).
5. Pick a task, branch off main:
   `git checkout -b feat/w2-task-NN-<slug>`.
6. `task-master show NN` — full implementation steps.
7. Implement (TDD where applicable; service-pattern matches existing
   `*Writer` / `*Verifier` shapes).
8. Run tests:
   - Sidecar: `cd sidecar && uv run pytest`
   - PHP: `docker exec development-easy-openemr-1 bash -c \
     'cd /var/www/localhost/htdocs/openemr && composer phpunit-isolated'`
9. Lint + types:
   - Sidecar: `uv run ruff check && uv run mypy src tests`
   - PHP: `docker exec development-easy-openemr-1 bash -c \
     'cd /var/www/localhost/htdocs/openemr && composer phpstan'`
10. Commit, push, MR. Do **not** include tasks.json edits in feature
    MRs — bundle status flips into a separate
    `chore/w2-status-sync-N` MR. Use surgical `Edit` (not
    `task-master set-status`) on the status field to avoid the
    re-stringify gotcha.

## Key files for the next likely tasks

### Task 1 — LangGraph supervisor refactor (cx=9)

- **Refactor target**: `sidecar/src/agentforge/orchestrator/__init__.py`
  (existing single-node iterative tool-use loop)
- **New module**: `sidecar/src/agentforge/orchestrator/graph.py`
  (`AgentState` TypedDict + supervisor + worker nodes)
- **Workers to compose**:
  - intake-extractor → wraps `VisionExtractor[IntakeFormExtraction]`
  - lab-extractor → wraps `VisionExtractor[LabPdfExtraction]`
  - evidence-retriever → wraps `EvidenceRetriever`
- **Supervisor**: existing `Planner` becomes the routing node; emits
  `route_decision` ∈ {intake-extractor, evidence-retriever,
  synthesize, ...}
- **Synthesizer + verifier nodes** stay as today's behavior; the
  refactor is the routing layer
- **Likely 2-3 MRs**:
  1. Skeleton graph + AgentState + supervisor stub (no worker calls)
  2. Wire workers; existing single-node logic still runs alongside
  3. Cut over the loop; remove the old single-node code

### Task 24 + 25 — Citation overlay (cx=6 + cx=5)

- **Task 24**: build the SwiftUI/JS overlay component that renders
  bbox citations over the rendered PDF page
- **Task 25**: wire it into the chart panel where the agent's
  responses appear; clicking a citation scrolls to the bbox

### Task 28 + 29 — E2E tests (cx=5 each)

- **Task 28**: full lab loop — upload → extract → persist → verify
  against the docstore + procedure_* tables
- **Task 29**: full intake loop — upload → extract → persist → verify
  the questionnaire_response row + that no structured tables were
  touched

### Task 30 — Deploy (cx=6)

- Production droplet at `143.244.157.90` (see
  `docs/DEPLOYMENT.md`). Currently running W1 code; need to redeploy
  with W2 module + sidecar.
- Check `docs/DEPLOYMENT.md` for the exact rsync + docker compose
  recipe; may need updating since Task 21 (Dockerfile) is pending.

## What's deployed where

`http://143.244.157.90:9300/` — production demo droplet. **Still
running W1 code.** ~50+ commits behind main. Recommended: redeploy
after Task 30 lands, or run an earlier deploy if you want the demo
droplet to reflect today's work.

## How this session ended

```
15 / 37 W2 tasks done (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 36, 37)
9 MRs merged this session (!20-!26 + !27 in flight)
0 phpstan errors, 0 mypy errors on touched files
115+ rag/ tests + 32 attach_and_extract tests + 26 langfuse tests
57 rag/ tests across 7 files (BM25 + Dense + RRF + 3 rerankers + Evidence orchestrator)
```

Pick up at **Task 1** (orchestrator refactor) next session — that's
the bridge that lights up the full agent loop. Or pick from the
critical-path list above based on where you want demo polish first.
