<context>
# Overview

This is the Week 2 PRD for the AgentForge Clinical Co-Pilot — the
OpenEMR fork with a Python sidecar that adds a chart-aware clinical
agent. Week 1 shipped the agent's core (single-node iterative
tool-use loop, eight tools, streaming verifier, JWT-validated internal
endpoints, deployed droplet). Week 2 extends it in two directions:
the agent learns to read scanned clinical documents (lab PDFs and
patient intake forms), and its single-node orchestrator splits into
a supervisor with two named workers.

The full architectural rationale lives in W2_ARCHITECTURE.md and
W2_DEFENSE.md at the repo root. The pair ARCHITECTURE.md (W1 design
intent) and docs/DEVIATIONS.md (W1 as-shipped) record the baseline
this builds on. This PRD enumerates Week 2 as shippable units.

The agent's behavior changes weekly; the gate's behavior is the
contract with graders. Done for Week 2 means the 50-case eval gate
green, with a self-test demonstrating that a deliberately-injected
regression fails the rubric.

Branch-per-task off main, merged after review. Per CLAUDE.md and
the project's git workflow, each Taskmaster task gets a feature
branch. One commit per subtask for non-trivial tasks.

# Core Features

The features below are the load-bearing units of Week 2. Each closes
a specific spec requirement or established invariant.

## LangGraph supervisor wiring (completes deferred integration)
- What: refactor sidecar/src/agentforge/orchestrator/__init__.py
  from a single-node iterative tool-use loop into a LangGraph
  supervisor with two named workers (intake-extractor,
  evidence-retriever) plus a synthesize node and a verifier
  terminal node. The Planner class (already standalone with full
  unit coverage at orchestrator/planner.py) becomes the
  supervisor's routing node. The existing iterative tool-use loop
  becomes the body of intake-extractor. Verifier wraps the
  terminal node, unchanged.
- Why: DEVIATIONS 2026-05-02 explicitly defers the graph wiring
  ("Planner shipped as standalone class; LangGraph + orchestrator
  wiring deferred"). The langgraph dependency is already pinned
  (DEVIATIONS 2026-04-30). Spec requires an "inspectable
  orchestration framework" — finishing the deferred integration is
  the natural path. Native conditional edges, native handoff
  spans.
- How: ship as the FIRST W2 milestone. The nine W1 regression locks
  at sidecar/tests/eval/regression_locks.py must pass against the
  new graph before any new tools or RAG land. Each handoff is a
  Langfuse span: route_decision, route_reason, from_node, to_node,
  iteration. Bounded depth (max 3 iterations, hard-stopped) so
  cost is predictable. SynthesisInputTruncator and DataQuality
  warnings (wired-but-deferred per DEVIATIONS 2026-05-02) find
  their natural seams: truncator at synthesizer's input edge;
  data-quality warnings injected as a system reminder before
  synthesis.

## Citation contract (Pydantic schemas)
- What: shared Citation Pydantic class with source_type ∈
  {LAB_PDF, INTAKE_FORM, GUIDELINE, OPENEMR_RECORD}, source_id,
  page_or_section, field_or_chunk_id, quote_or_value, page_bbox.
  PageBBox carries normalized 0..1 coordinates plus an explicit
  bbox_confidence. Pydantic model_validator rejects any LAB_PDF
  or INTAKE_FORM citation whose page_bbox is missing or whose
  bbox_confidence < 0.7. Companion schemas: LabValue,
  LabPdfExtraction, Demographic, MedicationEntry, AllergyEntry,
  FamilyHistoryEntry, IntakeFormExtraction. unsupported_fields
  list as the anti-invention surface for low-confidence fields.
- Why: every clinical claim in the answer carries a Citation. The
  contract distinguishes patient-record facts from guideline
  evidence at the type level — UI renders them in different
  sections; eval rubric factually_consistent checks that no
  GUIDELINE citation supports a patient-specific claim. The bbox
  validator means a low-confidence field can only land in
  unsupported_fields, never as a structured value.
- How: schemas live at sidecar/src/agentforge/schemas/. Validation
  tests at sidecar/tests/test_schemas_*.py. Citation grammar
  layered on top of W1's [record_type #id] form (which stays —
  W1 cited types still use it).

## Lab PDF ingestion (vision + procedure_* persistence)
- What: end-to-end ingest of scanned lab PDFs. Browser uploads a
  PDF via a new session-authed PHP route. Sidecar fetches bytes
  by document_id through a JWT-validated internal endpoint, calls
  Claude vision with a strict-schema prompt, validates against
  LabPdfExtraction Pydantic model, then persists each LabValue
  via a new internal endpoint that writes through procedure_order
  / procedure_report / procedure_result with
  procedure_result.document_id = documents.id.
- Why: Invariant I-1. The FHIR observation service in this repo
  does not implement create/update traits reliably; the canonical
  lab path is the procedure-result tables. AgentForge writes
  through OpenEMR's existing system of record, not via FHIR REST
  (DEVIATIONS 2026-05-02 / 2026-05-01 — tool-pattern principle).
  procedure_result.document_id is set so the round-trip from
  derived row back to source PDF is explicit at the schema level.
- How: oe-module-agentforge/public/upload_document.php (session +
  CSRF, browser upload). oe-module-agentforge/public/internal/
  get_document_bytes.php (JWT, sidecar fetch). oe-module-agentforge/
  public/internal/persist_lab_result.php (JWT, persistence). The
  sidecar tool attach_and_extract holds bytes in process memory
  only; bytes are never persisted to sidecar disk and never
  logged to Langfuse. Note: the FHIR-level cross-link
  (Observation.derivedFrom, DiagnosticReport.presentedForm) needs
  transformer changes this fork doesn't make and is NOT part of
  this deliverable.

## Intake form ingestion (vision + QuestionnaireResponse)
- What: end-to-end ingest of scanned patient intake forms. Same
  upload + byte-fetch path as lab PDFs. Sidecar validates against
  IntakeFormExtraction. Persistence writes a single FHIR
  QuestionnaireResponse via OpenEMR's existing
  FhirQuestionnaireResponseFormService against a pre-seeded
  canonical "AgentForge Intake Form" Questionnaire. The agent
  panel surfaces a "Suggested updates from intake form" section
  listing each extracted demographic, medication, allergy, and
  family-history row with its citation.
- Why: Invariant I-2. OCR is fallible; an intake form's "PCN"
  misread as "Pen-V" must not become a charted allergy without a
  clinician's confirmation. The agent surfaces *suggested
  changes* with citations to the form; promotion to the clinical
  record is a separate, explicit user action that is OUT OF
  SCOPE this sprint. Nothing writes to patient_data, the
  medications table, the allergies table, or the family-history
  table during ingestion.
- How: Doctrine migration db/Migrations/Version20260504000001_
  seed_agentforge_intake_questionnaire.php seeds the canonical
  Questionnaire idempotently via SELECT-then-INSERT-or-UPDATE
  on questionnaire_repository.source_url (that column has no
  unique index in this fork — verified at sql/database.sql:14342
  — so a DB-level upsert is not available; the migration does a
  SELECT WHERE source_url = ? and inserts only if absent, updates
  if present). oe-module-agentforge/public/internal/
  persist_questionnaire_response.php asserts the seed exists at
  startup and fails closed if absent. Promotion-write-back UI is
  explicitly post-W2.

## Hybrid RAG over a clinical-guideline corpus
- What: ~30 clinical-guideline documents committed to
  sidecar/data/guidelines/ — diabetes (ADA standards of care),
  hypertension (JNC 8 / 2017 ACC/AHA), lipid management
  (2018 AHA/ACC cholesterol guideline), CKD staging, common-lab
  interpretive notes. ~600 chunks of ~500 tokens each, in memory.
  Retrieval pipeline: BM25 (rank_bm25, top 25) + dense
  (sentence-transformers all-MiniLM-L6-v2, top 25) → RRF merge
  to top 30 → cross-encoder rerank (bge-reranker-base) → top 5
  → return list[GuidelineChunk] with Citation(source_type=
  GUIDELINE) attached per chunk. Reranker behind a Protocol with
  three implementations: CrossEncoderReranker (default),
  CohereReranker (opt-in via COHERE_API_KEY), PassthroughReranker
  (testing/ablation).
- Why: spec requires retrieval over a guideline corpus and says
  "Cohere Rerank or equivalent." Local cross-encoder is the
  default — no API-key requirement in CI, predictable cost,
  ablation pair in the eval suite measures rerank contribution
  before committing to a cloud reranker. ColQwen2 / multi-vector
  retrieval is explicit stretch in the spec and DEFERRED.
- How: pyproject.toml gains rank_bm25 and sentence-transformers;
  cohere is an optional extras. sidecar Docker image gets a new
  RUN stage that primes the model cache (all-MiniLM-L6-v2 ~90MB,
  bge-reranker-base ~280MB) so first-run cold-start does not
  download weights at container start. evidence-retriever node
  in the LangGraph supervisor calls the pipeline; results join
  the synthesizer's input alongside any extraction output.

## Citation overlay (click-to-source)
- What: a small vanilla-JS component shipped alongside the
  existing agent panel that mounts on click of any citation
  link in the chat output. The browser fetches the PDF from
  OpenEMR's session-authenticated patient_file/documents path
  — never from the sidecar. The overlay reads Citation.page_bbox
  to position the highlight; the sidecar returns only metadata
  (page index, normalized bbox, bbox_confidence, field id, quote).
- Why: clinical claims must be traceable to source. The overlay
  closes the loop visually. Holding the PDF on the OpenEMR side
  preserves the trust boundary — sidecar bytes never reach the
  UI for rendering. Vanilla JS (not React/JSX) matches the
  existing module style — agent_panel.js is plain IIFE today,
  no bundler or build pipeline; introducing one for a single
  component is not justified.
- How: oe-module-agentforge/public/js/citation_overlay.js (vanilla,
  no JSX) exposes window.AgentforgeCitationOverlay.mount(container,
  citation, pdfUrl). PDF rendering uses pdf.js — a pinned bundle
  vendored at oe-module-agentforge/public/vendor/pdfjs/ (pdf.min.js,
  pdf.worker.min.js, LICENSE), loaded via <script> tags in the
  agent panel template; pdfjsLib.GlobalWorkerOptions.workerSrc set
  to the vendored worker path. Overlay positioning is an
  absolute-positioned <div> over the rendered <canvas>, computed
  from Citation.page_bbox normalized coordinates × canvas pixel
  dimensions. The component mounts on the existing
  patient-demographics agent panel surface (W1
  EVENT_SECTION_LIST_RENDER_AFTER injection). Document-bytes
  round-trip latency mitigation: HTTP cache headers on the
  OpenEMR document route.

## 50-case eval gate (boolean rubric)
- What: 50 YAML cases at sidecar/tests/eval/cases/week2/ split as
  extraction(12), evidence-retrieval(10), citations(10),
  refusals(8), missing-data(10). Five-category boolean rubric
  per case: schema_valid, citation_present, factually_consistent,
  safe_refusal, no_phi_in_logs. Aggregate threshold: any category
  drops by >5% from the pinned baseline OR drops below 90% pass
  rate → CI fails. Two scoring layers, orthogonal failure modes:
  programmatic grounding (W1 carryover — citation parser + index,
  per-case behavior callables) plus a NEW LLM-as-judge layer
  fixed at claude-sonnet-4-6 with temperature=0, one prompt per
  category at sidecar/tests/eval/judges/week2/.
- Why: spec mandates a PR-blocking gate with named rubric
  categories. The gate is the deliverable. DEVIATIONS 2026-05-01
  ("Eval framework ships with hand-authored fixtures and skips
  LLM-as-judge") deferred the LLM-as-judge entirely on cost /
  flakiness / marginal-signal grounds — Week 2 INTRODUCES it as
  a new layer on top of the existing programmatic primitives,
  not a replacement. The deterministic and the model-judged
  signals each catch what the other misses.
- How: eval_config.yaml in repo with thresholds. Pinned baseline
  at sidecar/tests/eval/baselines/week2.json. Six W1 regression
  locks stay as-is and gate separately. Boolean format
  (categories, not 1–5 scoring) is the flakiness mitigation.
  Re-run the judge with a fresh seed on disagreement.

## Gate self-test (the regression-injection probe)
- What: sidecar/tests/eval/test_gate_blocks_regression.py
  monkey-patches the intake-extractor worker to return one
  fabricated lab value, runs the full 50-case suite against the
  patched agent, and asserts that the rubric factually_consistent
  drops by more than 5% (i.e., the build *would* fail).
- Why: graders introduce a regression and expect the gate to
  catch it. Shipping the proof that the gate bites — without
  graders having to demonstrate it for us — is the contract.
  The case set itself stays clean (we do NOT seed broken cases
  into the golden set; mixing pass/fail expectations into one
  dataset makes the rubric ambiguous).
- How: separate pytest mark; runnable manually; a separate
  "gate-validation" CI job graders can trigger explicitly. Not
  part of the normal CI gate run (would always fail by design).

## CI integration (GitLab as authoritative + GitHub mirror)
- What: a new .gitlab-ci.yml runs the full 50-case suite on every
  MR; merge blocks on category thresholds and the job posts a
  comment with the diff. A new .github/workflows/agent-eval.yml
  mirrors the same job for parity. Local prek (already at
  .pre-commit-config.yaml) gets a new hook entry that runs a
  10-case smoke subset on every commit.
- Why: per CLAUDE.md ("Issues live as GitLab issues at
  labs.gauntletai.com/cameroncandelori/openemr/-/issues") and the
  spec deliverable ("GitLab Repository"), GitLab CI is the
  authoritative PR-blocker for the challenge submission. The
  existing GitHub Actions surface (~30 workflows from upstream
  OpenEMR + W1's AgentForge isolated tests) is preserved.
- How: both CI jobs reuse the same script and config so they
  cannot drift. Both use the pre-baked sidecar Docker image so
  model weights don't download at run time.

## Observability extensions (cost, vision, traces)
- What: extend sidecar/src/agentforge/observability/cost.py with
  vision-call pricing. Per-encounter trace metadata adds
  retrieval_hits (BM25 / dense / post-rerank counts),
  extraction_confidence, route_decisions list (one entry per
  supervisor handoff with reason), eval_outcome on eval runs.
  cost_report.py extends to project production spend at
  projected QPS, report p50/p95 latency by step, and flag any
  step >40% of the total budget as a bottleneck.
- Why: spec page 6 lists cost report as a deliverable. The
  bottleneck flag turns the report into actionable signal rather
  than a wall of numbers.
- How: per-supervisor-handoff Langfuse spans drop in cleanly
  alongside the LangGraph wiring (this task is a sub-deliverable
  of the LangGraph pass but lands as its own commit). Cost
  pricing entries: claude-sonnet-4-6 vision input/output token
  rates.

## PHI containment for extraction prompts
- What: prompt-body redaction enforced at the LangfuseClient
  adapter for extraction calls. Extraction call traces log
  latency, model, input/output token counts, schema-validation
  result, extraction confidence, page count, unsupported-fields
  list — but the prompt body and the extraction response body
  are stripped before leaving the sidecar. Enforcement at the
  adapter (not at call sites) so a future tool addition cannot
  accidentally leak.
- Why: extraction prompts contain raw OCR'd text from clinical
  documents — name, DOB, SSN, lab values, free-text intake
  answers. Cannot leak through observability under the W1 BAA
  posture. The eval rubric no_phi_in_logs validates this on every
  CI run by inspecting exported traces for known synthetic-PHI
  patterns.
- How: extend sidecar/src/agentforge/observability/
  langfuse_client.py with a redaction wrapper that strips
  `messages[*].content` from log payloads on extraction calls.
  Switch is per-call-type (extraction calls are a distinct type).
  Tests assert the redaction happens before any export.

## Audit-log policy extensions
- What: explicit EventAuditLogger events fired in each new
  handler body. Browser-upload route fires
  "agentforge.document_ingest" {document_id, doc_type, patient_id,
  user_id, breakglass_flag, breakglass_reason}. Persistence
  endpoints fire "agentforge.lab_persist" (with
  procedure_result_id, extraction_status) and
  "agentforge.questionnaire_persist" (with
  questionnaire_response_id, extraction_status). Break-the-glass
  continues through the W1 mechanism unchanged; events during a
  break-the-glass session inherit the session's reason capture.
- Why: AgentForge's internal endpoints don't pass through
  ApiResponseLoggerListener (DEVIATIONS 2026-05-02 — Task 44
  reframed). The audit trail comes from explicit
  EventAuditLogger events, the pattern established by
  recent_encounters.php and the breakglass flow. Three events —
  upload, extraction outcome, persistence outcome — give a
  complete trail of every document AgentForge touches.
- How: each new handler fires its event immediately after the
  primary side-effect succeeds (Document::createDocument(),
  procedure_result write, QuestionnaireResponse write). Failure
  modes also fire the event with extraction_status set
  appropriately.

# User Experience

## Reviewer / grader journey
- Lands on GitLab repo URL.
- Reads the W1+W2 README (links W1_ARCHITECTURE.md, W2_ARCHITECTURE.md,
  W2_DEFENSE.md, deployed demo).
- Visits 143.244.157.90:9300, logs in as admin/pass, opens a
  patient with both demographics and the new agent panel.
- Triggers a turn that exercises the supervisor + workers + RAG
  end-to-end (e.g. "extract this lab + ADA target for A1C").
- Clicks a citation; the overlay positions on the right page of
  the PDF.
- Triggers the gate self-test job manually; sees it fails as
  designed (proving the gate bites).
- Reads docs/eval-report-2026-05-XX.md for "does it work on
  hard cases."
- (Optional) Inspects Langfuse trace; sees route_decision spans,
  retrieval_hits, prompt-body redaction on extraction calls.

## Operator journey (us)
- Local dev: docker compose up + sidecar.sh start unchanged.
- New: `uv run pytest -m eval` runs the full eval suite against
  the local stack and writes a report.
- New: prek runs the 10-case smoke subset on every commit.
- New: cost_report shows yesterday's spend including vision
  cost.

</context>
<PRD>
# Technical Architecture

## Affected components — sidecar
- sidecar/src/agentforge/orchestrator/__init__.py — LangGraph
  supervisor refactor (Planner becomes routing node; W1 tool
  loop becomes intake-extractor body; new RAG subgraph;
  verifier wraps terminal)
- sidecar/src/agentforge/orchestrator/planner.py — already standalone,
  but check signature against the supervisor wiring
- sidecar/src/agentforge/schemas/ (NEW) — Citation, PageBBox,
  LabValue, LabPdfExtraction, Demographic, MedicationEntry,
  AllergyEntry, FamilyHistoryEntry, IntakeFormExtraction
- sidecar/src/agentforge/tools/attach_and_extract.py (NEW) — vision
  call wrapper with strict-schema prompt + Pydantic validation +
  persistence dispatch
- sidecar/src/agentforge/rag/ (NEW) — bm25.py, dense.py, rrf.py,
  reranker.py (Protocol + 3 impls), evidence_retriever.py
- sidecar/data/guidelines/ (NEW) — committed corpus + chunk index
- sidecar/src/agentforge/observability/cost.py — vision pricing
- sidecar/src/agentforge/observability/langfuse_client.py —
  prompt-body redaction wrapper
- sidecar/src/agentforge/observability/cost_report.py — bottleneck
  flagger, p50/p95 latency by step
- sidecar/tests/eval/cases/week2/ (NEW) — 50 YAML cases
- sidecar/tests/eval/judges/week2/ (NEW) — five LLM-as-judge prompts
- sidecar/tests/eval/baselines/week2.json (NEW) — pinned scores
- sidecar/tests/eval/test_gate_blocks_regression.py (NEW) — self-test
- sidecar/eval_config.yaml (NEW) — thresholds, model, paths
- sidecar/Dockerfile — RUN stage that primes embedding + reranker
  model cache
- sidecar/pyproject.toml — rank_bm25, sentence-transformers,
  cohere (optional extras)

## Affected components — OpenEMR module
- interface/modules/custom_modules/oe-module-agentforge/public/
  upload_document.php (NEW) — session-authed browser upload route
- interface/modules/custom_modules/oe-module-agentforge/public/
  internal/get_document_bytes.php (NEW) — JWT-validated byte fetch
- interface/modules/custom_modules/oe-module-agentforge/public/
  internal/persist_lab_result.php (NEW) — JWT-validated, writes
  procedure_order/report/result with procedure_result.document_id
- interface/modules/custom_modules/oe-module-agentforge/public/
  internal/persist_questionnaire_response.php (NEW) — JWT-validated,
  writes via FhirQuestionnaireResponseFormService
- db/Migrations/Version20260504000001_seed_agentforge_intake_
  questionnaire.php (NEW) — root Doctrine migration seeding the
  canonical AgentForge Intake Form Questionnaire (lives at the
  repo root /db/Migrations/, NOT under the module)
- interface/modules/custom_modules/oe-module-agentforge/public/js/
  agent_panel.js — render "Suggested updates from intake form"
  block; mount citation overlay
- interface/modules/custom_modules/oe-module-agentforge/public/js/
  citation_overlay.js (NEW) — vanilla-JS overlay component
- interface/modules/custom_modules/oe-module-agentforge/public/vendor/
  pdfjs/ (NEW) — pinned pdf.js bundle (pdf.min.js,
  pdf.worker.min.js, LICENSE)

## Affected components — CI / config
- .gitlab-ci.yml (NEW) — agent-eval job (full 50-case run, MR-blocking)
- .github/workflows/agent-eval.yml (NEW) — mirror of GitLab job
- .pre-commit-config.yaml — new hook entry for 10-case smoke subset

## Data flow

Today (W1):
  user message -> turn.php -> sidecar /turn (JWT) -> single-node
                                                     iterative
                                                     tool-use loop
                                                  -> verifier
                                                  -> stream out

Target (W2):
  user message + (optional) attached document
    -> upload_document.php (session-authed)
    -> documents table + audit
    -> turn.php -> sidecar /turn (JWT)
    -> Supervisor (LangGraph) — router LLM call
       -> route_decision in {intake-extractor, evidence-retriever,
                              both, synthesize}
       -> conditional dispatch to worker(s)
          intake-extractor:
            GET /internal/get_document_bytes (JWT)
            vision call to Anthropic (BAA — PHI crosses)
            Pydantic LabPdfExtraction or IntakeFormExtraction
              (bbox_confidence ≥ 0.7 enforced)
            POST /internal/persist_lab_result OR
                 /internal/persist_questionnaire_response (JWT)
          evidence-retriever:
            tokenize query → BM25 + dense
            RRF merge → top 30
            reranker → top 5
            attach Citation(source_type=GUIDELINE) per chunk
       -> synthesize node — final LLM call with all context
       -> verifier (W1 streaming verifier, Citation-validated)
       -> stream verified text + Citations through turn.php
       -> browser renders; citation click composites overlay over
          OpenEMR-served PDF (session-authed fetch, NOT through
          sidecar)

PHI containment crossings:
  Browser ↔ OpenEMR (session+CSRF):  PDF upload, PDF preview
  OpenEMR ↔ Sidecar (JWT):            document_id, structured
                                      facts, persistence calls;
                                      bytes in sidecar memory only,
                                      never persisted, never logged
  Sidecar ↔ Anthropic (BAA):          rendered page images cross —
                                      load-bearing W1-inherited
                                      dependency

## Eval architecture

```
sidecar/tests/eval/
  cases/
    week2/
      extraction/
        lab-cbc-normal.yaml
        lab-cmp-with-flag.yaml
        intake-clean.yaml
        intake-bbox-degraded-scan.yaml
        ... (12 total)
      evidence/
        ada-a1c-target.yaml
        ada-ckd-staging.yaml
        ... (10 total)
      citations/
        every-claim-cited.yaml
        guideline-vs-record-claim.yaml
        ... (10 total)
      refusals/
        unsafe-action-request.yaml
        prompt-injection-paste.yaml
        ... (8 total)
      missing-data/
        partial-lab-pdf.yaml
        unreadable-scan.yaml
        ... (10 total)
  judges/
    week2/
      schema_valid.txt          # programmatic — no judge
      citation_present.txt      # programmatic — no judge
      factually_consistent.txt  # LLM judge — claude-sonnet-4-6 t=0
      safe_refusal.txt          # LLM judge — claude-sonnet-4-6 t=0
      no_phi_in_logs.txt        # programmatic — regex over traces
  baselines/
    week2.json                  # pinned per-category pass rates
  test_gate_blocks_regression.py  # self-test (separate CI job)
eval_config.yaml                # thresholds, model, paths
```

## Configuration changes

- sidecar/.env: ANTHROPIC_VISION_MODEL=claude-sonnet-4-6
  (already present for chat; same model for vision)
- sidecar/.env: COHERE_API_KEY (optional, for CohereReranker)
- sidecar/.env: AGENTFORGE_RAG_TOP_K=5
- sidecar/.env: AGENTFORGE_BBOX_CONFIDENCE_FLOOR=0.7
- droplet sidecar/.env: same vars set; same defaults as local

# Development Roadmap

## Phase 0 — CI bootstrap (lands BEFORE Phase 1 — gates every commit from MR #1)
0a. Author .gitlab-ci.yml at the repo root running the existing W1
    quality + test surface on every MR: phpstan (level 10),
    phpunit-isolated (mirrors existing GitHub Actions), sidecar
    pytest excluding slow/latency/eval markers, dedicated job for
    the 9 W1 regression locks. Single `test` stage, jobs in
    parallel, composer/vendor + uv download caches keyed by
    lockfiles. No secrets needed at this stage.
0b. Verify locally before commit: `cd sidecar && uv run pytest`
    (expect 774 passed, 13 deselected); `uv run pytest
    tests/eval/regression_locks.py` (expect 10 passed in ~0.02s).
0c. User-side activation: confirm GitLab shared runners enabled;
    set main-branch protection to require passing pipelines.
0d. Add a prek hook entry (.pre-commit-config.yaml) for the 9 W1
    regression locks — fast local feedback before push. The
    10-case W2 smoke subset gets added later in Phase 7.
0e. Phase gate: Task 1 (LangGraph refactor) cannot start until
    Task 36 (CI bootstrap) is green on a feature branch.

Why CI before LangGraph: Phase 1 refactors Orchestrator.turn()
across the whole agent. Without CI in place, regressions in the
W1 regression locks land silently. With CI in place, every commit
during the refactor is gated on the same nine cases that defined
W1 correctness.

## Phase 1 — LangGraph foundation (must land first per W2_ARCHITECTURE risk #3)
1. Refactor Orchestrator.turn() into LangGraph supervisor +
   intake-extractor + synthesize + verifier (initial graph; no
   evidence-retriever yet).
2. Migrate the SynthesisInputTruncator and DataQuality warnings
   wired-but-deferred utilities (DEVIATIONS 2026-05-02) to their
   natural seams in the new graph: truncator at synthesizer's
   input edge; data-quality warnings injected as system reminder
   before synthesis.
3. Add Langfuse spans per supervisor handoff (route_decision,
   route_reason, from_node, to_node, iteration). Bound graph
   depth to max 3 iterations, hard-stopped.
4. Verify the nine W1 regression locks at
   sidecar/tests/eval/regression_locks.py pass against the new
   graph. NO new tools or RAG land before this is green.

## Phase 2 — Citation contract + schemas
5. Author Citation, PageBBox, SourceType in sidecar/src/agentforge/
   schemas/citation.py with the bbox_confidence ≥ 0.7
   model_validator. Tests at sidecar/tests/test_schemas_citation.py
   covering accept/reject paths.
6. Author LabValue, LabPdfExtraction in sidecar/src/agentforge/
   schemas/lab.py. Tests cover unsupported_fields surface,
   AbnormalFlag enum, LOINC optionality, date parsing.
7. Author Demographic, MedicationEntry, AllergyEntry,
   FamilyHistoryEntry, IntakeFormExtraction in
   sidecar/src/agentforge/schemas/intake.py. Tests cover the
   four list types and the chief_concern + chief_concern_citation
   pairing.

## Phase 3 — Lab PDF ingestion E2E
8. Create the root Doctrine migration (db/Migrations/) seeding
   the canonical AgentForge Intake Form Questionnaire idempotently
   via SELECT-then-INSERT-or-UPDATE on
   questionnaire_repository.source_url — that column has no unique
   index in this fork (verified at sql/database.sql:14342), so a
   DB-level upsert is not available. NB: this lands in Phase 3
   even though it's used by Phase 4, because both ingestion paths
   share the migration push. Verify the seed exists after running
   migrations on a fresh DB.
9. Implement oe-module-agentforge/public/upload_document.php —
   session + CSRF auth, multipart upload, calls
   Document::createDocument(), reads new id via $doc->get_id(),
   fires "agentforge.document_ingest" EventAuditLogger event,
   returns {document_id} JSON.
10. Implement oe-module-agentforge/public/internal/
    get_document_bytes.php — JWT-validated, pid-scoped, reads
    document bytes via Document API. Bytes returned in memory;
    no caching.
11. Implement oe-module-agentforge/public/internal/
    persist_lab_result.php — JWT-validated. For each LabValue,
    upsert procedure_order / procedure_report / procedure_result
    rows; set procedure_result.document_id. Fires
    "agentforge.lab_persist" event with procedure_result_id,
    extraction_status.
12. Implement sidecar tool attach_and_extract for doc_type=lab_pdf:
    fetch bytes via internal endpoint, render pages, call Claude
    vision with strict-schema prompt, validate against
    LabPdfExtraction, persist via persist_lab_result.php. PDF
    bytes never persisted to sidecar disk; never logged to
    Langfuse (covered by Phase 8 redaction).
13. End-to-end smoke test: upload a synthetic lab PDF; assert
    procedure_result rows created with document_id set; assert
    audit events fired in order.

## Phase 4 — Intake form ingestion E2E
14. Implement oe-module-agentforge/public/internal/
    persist_questionnaire_response.php — JWT-validated. Asserts
    the seed Questionnaire exists at startup; fails closed if
    absent. Writes a single QuestionnaireResponse via
    FhirQuestionnaireResponseFormService. Fires
    "agentforge.questionnaire_persist" event.
15. Extend the sidecar attach_and_extract tool for
    doc_type=intake_form: vision call with intake-form schema,
    validate against IntakeFormExtraction, persist via
    persist_questionnaire_response.php. NOTHING writes to
    patient_data, the medications table, the allergies table,
    or the family-history table.
16. Render the "Suggested updates from intake form" block on
    the agent panel surface — read-only list of extracted
    demographics, medications, allergies, family-history with
    citations. No promotion-write-back UI.
17. End-to-end smoke test: upload a synthetic intake form;
    assert one QuestionnaireResponse exists; assert no writes
    to patient_data / medications / allergies tables.

## Phase 5 — Hybrid RAG
18. Add rank_bm25 and sentence-transformers to pyproject.toml.
    Add cohere to optional extras. Verify CI installs only the
    default extras.
19. Commit the ~30-document guideline corpus to
    sidecar/data/guidelines/. Each document chunked at ~500
    tokens with metadata (doc_id, section, version, chunk_id).
20. Implement BM25 retriever (sidecar/src/agentforge/rag/bm25.py)
    over the chunked corpus.
21. Implement dense retriever (sidecar/src/agentforge/rag/dense.py)
    using sentence-transformers all-MiniLM-L6-v2 + cosine
    similarity; in-memory FAISS or numpy.argpartition-based.
22. Implement RRF merger (sidecar/src/agentforge/rag/rrf.py) —
    union with score-rank fusion, dedupe.
23. Implement Reranker Protocol with three implementations:
    CrossEncoderReranker (default — bge-reranker-base),
    CohereReranker (opt-in via env), PassthroughReranker
    (ablation/test).
24. Implement evidence_retriever LangGraph node: takes the
    query, returns list[GuidelineChunk] with
    Citation(source_type=GUIDELINE) attached. Wire into the
    supervisor graph from Phase 1.
25. Update sidecar Dockerfile with a RUN stage that primes the
    embedding model + reranker model cache (~370MB total) so
    container cold-start doesn't pull weights at run time.
26. Add a passthrough-vs-rerank ablation pair to the eval suite
    so rerank's contribution is measurable.

## Phase 6 — Citation overlay UI
27. Vendor a pinned pdf.js bundle at oe-module-agentforge/public/
    vendor/pdfjs/ (pdf.min.js + pdf.worker.min.js + LICENSE,
    pinned to a specific 4.x release). No npm/bundler — load via
    plain <script> tags from the agent panel template; set
    pdfjsLib.GlobalWorkerOptions.workerSrc to the vendored worker
    path. Document the version in the module README.
28. Implement the vanilla-JS citation_overlay.js component at
    oe-module-agentforge/public/js/citation_overlay.js. Exposes
    window.AgentforgeCitationOverlay.mount(container, citation,
    pdfUrl). Renders the PDF page via pdf.js to a <canvas>; draws
    an absolute-positioned highlight <div> over it computed from
    Citation.page_bbox normalized coordinates × canvas pixel
    dimensions. Matches the existing IIFE-style module
    convention; no React, no JSX, no build pipeline.
29. Wire the overlay into agent_panel.js. On citation click,
    construct the PDF URL using citation.source_id (which is
    documents.id for LAB_PDF / INTAKE_FORM types) pointing at
    OpenEMR's session-authed patient_file/documents path (NOT
    through sidecar) and call AgentforgeCitationOverlay.mount.
30. Add HTTP cache headers on the OpenEMR document route to
    mitigate the document-bytes round-trip latency.

## Phase 7 — Eval gate (the deliverable)
31. Author the 50 YAML cases under
    sidecar/tests/eval/cases/week2/, distributed across the
    five categories. Validate every case file against a YAML
    schema before committing.
32. Author the LLM-as-judge prompts at
    sidecar/tests/eval/judges/week2/, one per LLM-judged
    category (factually_consistent, safe_refusal). Pin model
    claude-sonnet-4-6, temperature=0.
33. Extend the harness to run programmatic + LLM-judge layers
    independently. Either failing fails the case.
34. Generate the pinned baseline at
    sidecar/tests/eval/baselines/week2.json by running the
    suite once on a known-good agent state.
35. Add eval_config.yaml with category thresholds and the
    LLM-judge model + temperature.
36. Author the gate self-test:
    sidecar/tests/eval/test_gate_blocks_regression.py.
    Monkey-patches intake-extractor to fabricate one value;
    runs the full 50; asserts factually_consistent drops >5%.

## Phase 8 — CI integration
37. Author .gitlab-ci.yml with the agent-eval job. Runs the full
    50 on every MR. Blocks merge on threshold violation. Posts a
    comment with the per-category diff against baseline. Uses
    the pre-baked sidecar Docker image (Phase 5 #25).
38. Author .github/workflows/agent-eval.yml mirroring the
    GitLab job. Same script, same eval_config.yaml. Same Docker
    image. Both must run identically.
39. Add a prek hook entry in .pre-commit-config.yaml that runs
    the 10-case smoke subset on every commit. Fast feedback;
    does not run the full 50.
40. Add a separate "gate-validation" CI job that runs the
    self-test from Phase 7 #35. Not on MR — manual trigger
    only. Document how graders trigger it.

## Phase 9 — Observability + audit
41. Extend sidecar/src/agentforge/observability/cost.py with
    vision-call pricing (claude-sonnet-4-6 vision input/output
    rates).
42. Extend sidecar/src/agentforge/observability/
    langfuse_client.py with a prompt-body redaction wrapper for
    extraction-call type. Strips messages[*].content; preserves
    latency, model, tokens, schema-validation result,
    extraction confidence, page count, unsupported-fields.
    Tests assert redaction happens before any export.
43. Extend cost_report.py to project production spend at
    projected QPS, report p50/p95 latency by step, and flag any
    step >40% of total budget as a bottleneck.
44. Verify all three audit events fire correctly in production
    by running the smoke tests on the droplet:
    agentforge.document_ingest (browser upload),
    agentforge.lab_persist (procedure_result write),
    agentforge.questionnaire_persist (QR write).

## Phase 10 — Final delivery
45. Run a full cost & latency report; commit at
    docs/eval-report-2026-05-XX.md.
46. Record a 3-5 minute demo video walking through the supervisor
    routing flow (extract this lab + ADA target).
47. Write a README diff between W1 and W2 — explicit "what's new
    this week" section.
48. Deploy to the droplet (143.244.157.90:9300). Run the smoke
    test cohort on the deployed instance.
49. (Extension if eval gate green by Thursday EOD): critic
    agent that rejects uncited claims and unsafe action
    suggestions. Reuses verifier infrastructure; adds a graph
    node.
50. (Extension if eval gate green by Thursday EOD): lab trend
    chart widget driven by extracted Observation values.

# Logical Dependency Chain

- LangGraph foundation (Phase 1) lands FIRST. All other phases
  assume the supervisor + workers + verifier graph exists. The
  nine W1 regression locks must pass against the new graph
  before any Phase 3+ work starts. If the migration
  destabilizes them, the architecture says fall back to a
  hand-written routing function — re-plan from there.
- Citation contract (Phase 2) lands second. Phases 3, 4, 5
  all depend on Citation, LabPdfExtraction, IntakeFormExtraction
  existing.
- Lab PDF ingestion (Phase 3) and Intake form ingestion (Phase 4)
  share the upload_document.php route and the
  get_document_bytes.php endpoint. Land Phase 3 first since the
  Doctrine migration goes there. Phase 4 reuses both.
- Hybrid RAG (Phase 5) is independent of Phases 3 and 4 and
  can land in parallel with them. The Docker image change
  (#25) is a forcing function: it must land before the eval
  gate runs in CI (Phase 8) so weights don't download at run
  time.
- Citation overlay (Phase 6) depends on Phase 2 (Citation
  schema) and on either Phase 3 or Phase 4 having landed (so
  there's a real Citation to render). Realistically lands after
  Phase 4.
- Eval gate (Phase 7) depends on the full pipeline being
  stable. Authoring 50 cases against a moving target wastes
  signal. Land after Phases 3, 4, 5.
- CI integration (Phase 8) depends on Phase 7. Cannot run a
  gate that doesn't exist.
- Observability + audit (Phase 9) — items 40, 41, 42 (cost,
  redaction, report) can land any time after Phase 1. Item 43
  (audit-event verification) depends on the production droplet
  having Phase 3 + Phase 4 deployed.
- Final delivery (Phase 10) is bookkeeping after the gate is
  green. Extension items (48, 49) only start after the gate is
  green by Thursday EOD; otherwise deferred.

# Risks and Mitigations

## Risk: LangGraph wiring destabilizes the W1 regression locks
- Mitigation: ship Phase 1 as the FIRST W2 milestone and require
  the six locks to pass against the new graph before any new
  tools or RAG land. If the migration destabilizes them, fall
  back to a hand-written routing function — the spec accepts
  "another inspectable orchestration framework" — and revisit
  LangGraph after the gate is green.

## Risk: VLM bbox accuracy too low to hit the 0.7 floor on real scans
- Mitigation: enforced in the schema, not just the prompt — a
  low-confidence field can only land in unsupported_fields.
  Eval case extraction-bbox-degraded-scan exercises this on a
  deliberately blurred page. If the floor proves too strict
  on real-world scans, lower it via env var
  AGENTFORGE_BBOX_CONFIDENCE_FLOOR — but only after eval data
  shows what the realistic distribution looks like.

## Risk: PHI leaks through extraction-call traces
- Mitigation: redaction at the LangfuseClient adapter (Phase 9
  #41), not at call sites. Eval rubric no_phi_in_logs catches
  failures on every CI run by inspecting trace exports for
  known synthetic-PHI patterns. Long-term mitigation (a static
  check that all LLM calls go through the adapter) is out of
  scope this sprint.

## Risk: Cohere optionality — local rerank quality is materially worse
- Mitigation: ablation case in the eval suite measures rerank
  contribution. If the cross-encoder underperforms by >5pts on
  factually_consistent versus a Cohere baseline, flip Cohere on
  via COHERE_API_KEY in the droplet .env. The eval suite is the
  forcing function.

## Risk: 50-case authoring time blows up
- Mitigation: timebox to a single half-day; case authors split
  by category. The case YAML schema is validated by a script
  before commit so we catch malformed cases before they reach
  CI. Fail loud, fail early.

## Risk: Promotion-write-back temptation
- Mitigation: invariant I-2 is enforced at the Pydantic layer,
  the persistence-test layer, AND as an eval rubric item. If
  someone writes a code path that updates patient_data /
  medications / allergies / family-history from extraction
  output, the gate fails the relevant case. Clinical state
  doesn't get mutated by VLM extraction this sprint, full stop.

## Risk: 50 LLM-judge calls per CI run get expensive at scale
- Mitigation: bounded by case count; claude-sonnet-4-6 at
  temp=0; cost report (Phase 9 #42) tracks gate-run cost
  explicitly. Boolean rubric (not 1–5 scoring) keeps the
  per-case judge call short.

## Risk: Document-bytes round-trip latency hurts UX
- Mitigation: HTTP cache headers on the OpenEMR document route
  (Phase 6 #29). The file is < 1MB in expected cases; observed
  latency goes in the cost report (Phase 9 #42). If it hurts,
  consider a sidecar-side proxy with cache eviction tied to
  document_id revalidation — but only if data shows the round
  trip is the bottleneck.

# Appendix

## Origin of this PRD

This PRD is a translation of W2_ARCHITECTURE.md (full design)
and W2_DEFENSE.md (architecture summary and defense) into
shippable units. The full architectural rationale, including
the five load-bearing decisions and the three explicit
tradeoffs, lives in W2_ARCHITECTURE.md and is not duplicated
here.

The companion docs W2_ARCHITECTURE.md and W2_DEFENSE.md are
the authoritative reference; this PRD is the planning view.
If the two diverge, W2_ARCHITECTURE.md wins and this PRD must
be reconciled.

The pair ARCHITECTURE.md (W1 design intent) and
docs/DEVIATIONS.md (W1 as-shipped) record the baseline this
builds on. Reading either in isolation will misrepresent the
state.

## Out of scope this sprint (with reasons)

- A third document type (referral fax, medication list).
  Spec page 6 explicitly warns against attempting a third type
  before two work reliably.
- Promotion write-back from intake suggestions to clinical
  state. Real workflow with audit/co-sign requirements; W2
  ends at "agent surfaces suggestion with citation."
- FHIR-level cross-link between Observation and
  DocumentReference. Requires transformer changes not in this
  fork.
- ColQwen2 / multi-vector retrieval. Explicit stretch in spec.
- On-prem vLLM vision extraction. W1's deferred production
  work; W2 inherits the BAA posture.
</PRD>
