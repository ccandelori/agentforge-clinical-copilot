# W2_ARCHITECTURE.md — Multimodal Evidence Agent

**Project:** AgentForge Clinical Co-Pilot — Week 2
**Document date:** 2026-05-04
**Author:** Cameron Candelori
**Inputs:** [`ARCHITECTURE.md`](./ARCHITECTURE.md) (Week 1), Week 2 PRD, [`AUDIT.md`](./AUDIT.md), [`USERS.md`](./USERS.md)

---

## Executive Summary

Week 2 extends the Week 1 agent in two specific directions: it learns to read scanned clinical documents (lab PDFs and patient intake forms) and it splits its single-node orchestrator into a supervisor with two named workers. The spec frames this as an exercise in *seeing*, *routing*, and *gating*. None of those three are about adding new frameworks; they are about widening the trust boundary without losing it.

The shape of this turn is determined by two repo realities and three policy commitments. The repo realities are that OpenEMR has a real lab round-trip path (`procedure_order` → `procedure_report` → `procedure_result`, with `procedure_result.document_id` linking back to the uploaded PDF — verified in `sql/database.sql:10521`), and a real questionnaire path (`src/Services/FHIR/QuestionnaireResponse/FhirQuestionnaireResponseFormService.php` — verified to exist). We use both, rather than inventing a parallel store. The policy commitments are that the agent does not mutate clinical state from scanned inputs, that PHI rendering stays inside OpenEMR's session boundary, and that no extraction prompt or document text reaches our observability layer.

**Five load-bearing decisions for Week 2:**

1. **Scanned intake forms persist as `QuestionnaireResponse`, not as silent updates to demographics, allergies, or medications.** OCR is fallible; an intake form's "PCN" allergy is not allowed to become a charted allergy without a clinician's confirmation. The agent surfaces *suggested changes* with citations to the form; promotion to the clinical record is a separate, explicit user action. (Spec §2 — vision extraction without invention; §1 — FHIR/OpenEMR integrity.)
2. **Lab facts persist via OpenEMR's existing `procedure_*` tables, not via FHIR `Observation` create.** The FHIR observation service in this repo does not implement create/update traits reliably; the canonical lab path is the procedure-result tables, which the FHIR `Observation` and `DiagnosticReport` services already read from. We write through the system of record and let the FHIR resources materialize as a reader concern. `procedure_result.document_id` is set to the uploaded PDF's `documents.id` so the round-trip is explicit. (Spec §1 — FHIR/OpenEMR integrity.)
3. **PHI containment narrows three boundaries; the model provider is the load-bearing exception.** PDF rendering and preview stay inside OpenEMR — the browser fetches the file from `patient_file/documents` with the existing session cookie, never from the sidecar. The sidecar holds bytes in memory only for the duration of one extraction call, and never persists or logs them. Rendered page images *do* leave the sidecar to Claude vision under the W1 BAA posture (same dependency W1 already takes for chart-text reasoning); §5.1 details all three boundaries. The citation overlay is a vanilla-JS component shipped from the OpenEMR module (using a vendored pdf.js bundle) composited over an OpenEMR-served PDF — the sidecar never serves UI bytes; observability stores metadata only. (Spec §1 — HIPAA-minded development.)
4. **LangGraph wiring is completed in Week 2 — explicitly deferred in Week 1.** Per [`DEVIATIONS.md` 2026-05-02 "Planner shipped as standalone class; LangGraph + orchestrator wiring deferred"](./docs/DEVIATIONS.md), the `Planner` class is already standalone with full unit coverage at `sidecar/src/agentforge/orchestrator/planner.py`; what was deferred was the graph that consumes it, and the `langgraph` dependency is already pinned (DEVIATIONS 2026-04-30). Week 2's supervisor refactor finishes the wiring: the existing iterative tool-use loop becomes the body of the `intake-extractor` worker, the Planner becomes the supervisor's routing node, and a new evidence-retriever subgraph joins the graph. This satisfies the spec's "inspectable orchestration framework" requirement and resolves a known deferred integration — not net-new framework adoption. (Spec §3 — multi-agent architecture.)
5. **The eval gate is the deliverable.** The agent's behavior changes weekly; the gate's behavior is the contract with graders. Week 2 ships a 50-case golden set with boolean rubrics, a PR-blocking GitLab CI step plus a fast local prek hook, and a self-test (`test_gate_blocks_regression.py`) that monkey-patches a tool to return wrong data and asserts the rubric fires — so we know the gate works before graders demonstrate that it doesn't. (Spec §3 — eval-driven development; §4 — hard gate.)

**Three explicit tradeoffs:**

- **LangGraph wiring vs in-place routing.** The dependency is already pinned (DEVIATIONS 2026-04-30) and the Planner already ships standalone (DEVIATIONS 2026-05-02); what's deferred is the graph that consumes the Planner. The alternative — adding a hand-written routing function on top of the existing tool loop — would be smaller but would leave the inspectability story informal. We accept the wiring cost for native handoff spans, conditional edges, and the spec's "inspectable orchestration framework" line.
- **Cohere Rerank vs local cross-encoder.** Spec says "Cohere Rerank or equivalent." We default to a local `bge-reranker-base` cross-encoder behind a `Reranker` interface; Cohere is swappable but not a day-one dependency. This avoids a third API key, predictable cost, and lets the eval suite measure rerank impact before we commit to a cloud reranker.
- **Vanilla JS in the OpenEMR module vs sidecar-bundled framework component.** The viewer is a vanilla-JS overlay shipped from the OpenEMR module (`oe-module-agentforge/public/js/citation_overlay.js`) using a pinned, vendored pdf.js bundle (`oe-module-agentforge/public/vendor/pdfjs/`); it loads in the existing patient chart and fetches the PDF from OpenEMR's session-authenticated path. The alternative — a sidecar-bundled React/JSX component — would require introducing a Node toolchain and bundler that the module otherwise avoids; the existing `agent_panel.js` is plain IIFE-style JS. We stay vanilla to match the module's conventions and keep PHI bytes flowing through OpenEMR's existing auth, not the sidecar's. The cost is that the citation contract must include `page_bbox` in normalized coordinates so the overlay can position itself without the sidecar ever touching the file.

**What Week 2 does not ship:** a third document type (referral fax, medication list); a critic agent and lab trend chart widget (extension work, not before the eval gate is green); ColQwen2 or multi-vector indexing (explicit stretch in the spec); a promotion-write-back UI for the suggested intake changes (the agent surfaces suggestions; the clinical-state write path is post-Week-2 work).

---

## 0. The Week 1 baseline this builds on

The authoritative record of the Week 1 baseline is the pair [`ARCHITECTURE.md`](./ARCHITECTURE.md) (intent) and [`docs/DEVIATIONS.md`](./docs/DEVIATIONS.md) (what shipped, with rationale per change). Week 2 builds on the deviations-current state. Reading `ARCHITECTURE.md` without `DEVIATIONS.md` will misrepresent what's in code; this document treats both as inputs.

### The OpenEMR side (`oe-module-agentforge`)

A custom module already lives at `interface/modules/custom_modules/oe-module-agentforge/`. Its surface as of Week 2 kickoff:

- **Bootstrap and panel injection.** `src/Bootstrap.php` subscribes to `OpenEMR\Events\PatientDemographics\RenderEvent::EVENT_SECTION_LIST_RENDER_AFTER` (chosen over the spec's `Main\Tabs\RenderEvent` per DEVIATIONS 2026-04-30) and renders the agent panel inline on the patient summary section list (`templates/agent_panel.html.twig`).
- **Sidecar proxy.** `public/turn.php` accepts the agent turn from the panel, validates the OpenEMR session, mints a per-call JWT via `src/Services/AgentJwtService.php` (`lcobucci/jwt` 4.x, the project's existing JWT library — DEVIATIONS 2026-04-30), and proxies to the sidecar with streaming response support (Symfony HttpClient).
- **Internal endpoint pattern.** `public/internal/<route>.php` is the established pattern for sidecar→OpenEMR calls — see `recent_encounters.php`. JWT-validated, pid-scoped, no session state, and per DEVIATIONS 2026-05-02 (Task 44) does *not* pass through `ApiResponseLoggerListener` — so PHI body logging is already not happening on these paths.
- **Auth and identity primitives.** `BreakglassContext` value object (DEVIATIONS 2026-04-30 — invariant-enforced "flag=true requires non-empty reason"), `UserRoleLookup` (direct GACL query mirroring `BreakglassChecker`), and a sensitivity policy keyed `agentforge:policy:loaded` in Redis.

### The sidecar side (`sidecar/src/agentforge/`)

- **FastAPI app with factory pattern** (`main.py:create_app`, DEVIATIONS 2026-04-30 — defers `Settings()` instantiation past import-time so tests can monkeypatch env vars).
- **Orchestrator** at `orchestrator/__init__.py` running a single-node iterative tool-use loop with the streaming verifier wired in optionally.
- **Planner** at `orchestrator/planner.py` — standalone class, fully unit-tested, *not yet wired* into the orchestrator (DEVIATIONS 2026-05-02). Accepts a user message, returns a `Plan`. The graph that consumes it is Week 2 work.
- **Eight typed tool adapters** reading OpenEMR via the corresponding `public/internal/*.php` endpoints, not via FHIR (DEVIATIONS 2026-05-01 — `get_recent_labs`; 2026-05-01 — `get_active_allergies`; 2026-05-02 — encounters).
- **Streaming verifier** (`verifier/streaming_verifier.py`) with `Citation` (`verifier/citation.py:61`) and `CitationIndex` (`verifier/cache.py:55`). Current citation grammar is `[record_type #id]` (label-form citations rejected per DEVIATIONS 2026-05-01); Week 2's contract is a richer model layered on top (§2.4).
- **Auth gateway** that loads the sensitivity policy and validates the per-call JWT.
- **Self-hosted Langfuse** with HMAC-keyed pseudonyms; metadata-only logging.
- **Eval harness** at `tests/eval/harness.py` with programmatic grounding (citation parser + index) plus per-case behavior callables, locked against nine canonical regression cases at `tests/eval/regression_locks.py`. **No LLM-as-judge in CI today** — deliberately skipped on cost/flakiness grounds (DEVIATIONS 2026-05-01).
- **Wired-but-deferred integrations** for the streaming-refactor era: `SynthesisInputTruncator` (held on the orchestrator, not invoked yet — DEVIATIONS 2026-05-02) and `DataQuality` warnings (appended post-final-text, not mid-loop — DEVIATIONS 2026-05-02). Both unblock cleanly only after synthesis splits from the tool loop.

### Four DEVIATIONS entries that load-bear on Week 2

- **2026-05-02 — Planner shipped as standalone class; LangGraph + orchestrator wiring deferred.** Week 2's supervisor refactor completes this wiring (§3).
- **2026-05-01 — Eval framework ships with hand-authored fixtures and skips LLM-as-judge.** Week 2 *introduces* the LLM-as-judge with boolean rubric on top of the existing programmatic primitives (§6).
- **2026-05-02 — Encounters tool reads `form_encounter` directly, not via FHIR.** Establishes the project's tool-pattern principle: AgentForge tools talk to OpenEMR through JWT-validated internal endpoints, not FHIR REST. Avoids provisioning a second auth surface (OAuth2 client credentials + token management) for the same trust boundary. Week 2's write paths follow the same principle (§2.3).
- **2026-05-02 — Task 44 reframed: `api_log_option` is global, not per-user.** AgentForge calls already bypass `ApiResponseLoggerListener`; the audit trail is added by explicit `EventAuditLogger` events in handler bodies (§5.3).

---

## 1. Goals and non-goals

**In scope (core):**

- Two document types — lab PDF and patient intake form — with strict-schema Pydantic extraction.
- A citation contract sufficient to render a click-to-source overlay over the original PDF, with the visual bounding box drawn from per-field coordinates returned by the extractor.
- Hybrid RAG (BM25 + dense + rerank) over a small clinical-guideline corpus committed to the repo.
- A supervisor with two named worker subgraphs: `intake-extractor` and `evidence-retriever`. Routing decisions are explicit and logged.
- 50-case golden eval set, boolean rubrics in five categories, PR-blocking gate.
- Final-answer citations that distinguish patient-record facts from guideline evidence.

**Extensions (post-eval-gate, time permitting):**

- Critic agent that rejects uncited claims and unsafe action suggestions.
- Lab trend chart widget driven by extracted Observation values.

**Out of scope this sprint:**

- A third document type.
- Promotion-write-back from suggested intake changes into clinical state.
- ColQwen2 / multi-vector retrieval.
- Production-hardened on-prem inference (carries over from W1's deferred list).

---

## 2. Document ingestion architecture

### 2.1 The flow

```
┌──────────────────────────────────────────────────────────────────┐
│  Browser (clinician — patient summary page)                      │
│  Upload control → POST to OpenEMR ingest endpoint                │
└────────────────────────┬─────────────────────────────────────────┘
                         │ multipart/form-data, OpenEMR session + CSRF
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│  OpenEMR PHP host — browser upload (session-authed, NOT JWT)     │
│  Route: oe-module-agentforge/public/upload_document.php          │
│  1. Validates OpenEMR session + CSRF token. Distinct trust       │
│     boundary from the sidecar's JWT-validated /internal/* path.  │
│  2. Instantiates a Document and calls $doc->createDocument(...)  │
│     at library/classes/Document.class.php:940. The method's      │
│     docblock declares: "" on success, error string on failure.   │
│     The new id is read off the instance via $doc->get_id()       │
│     after persist. Writes to the `documents` table, bound to     │
│     patient_id and encounter_id.                                 │
│  3. Fires explicit EventAuditLogger event                        │
│     "agentforge.document_ingest"                                 │
│     {document_id, doc_type, patient_id, user_id,                 │
│      breakglass_flag, breakglass_reason}                         │
│  4. Returns {document_id} JSON to the agent panel.               │
└────────────────────────┬─────────────────────────────────────────┘
                         │
                         ▼
              Agent panel surfaces a chat turn:
              "Extract document_id=N (doc_type=lab_pdf)"
                         │
                         │ HTTPS through existing turn.php proxy
                         │ (session-authed → JWT-mint → sidecar /turn)
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│  Agent Sidecar — extraction (JWT-authed via internal endpoints)  │
│  Tool: attach_and_extract(patient_id, document_id, doc_type)     │
│  a. Calls oe-module-agentforge/public/internal/                  │
│       get_document_bytes.php (JWT-validated, pid-scoped)         │
│       to fetch the PDF. Bytes held in process memory only;       │
│       never persisted to sidecar disk; never logged to Langfuse. │
│  b. Renders pages → Claude vision call with strict schema prompt │
│     (see §2.2). Coordinates per field, normalized 0..1, each     │
│     with explicit bbox_confidence.                               │
│  c. Validates against Pydantic model. The Citation validator     │
│     (§2.2) rejects scanned-source citations whose page_bbox is   │
│     missing or whose bbox_confidence < 0.7, so low-confidence    │
│     fields can only land in `unsupported_fields`.                │
│  d. Persists derived facts via JWT-validated internal endpoints: │
│       lab_pdf      → public/internal/persist_lab_result.php      │
│                      (writes procedure_order/report/result;      │
│                       procedure_result.document_id = document_id)│
│       intake_form  → public/internal/persist_questionnaire_      │
│                      response.php (FhirQuestionnaireResponse-    │
│                      FormService against the seeded AgentForge   │
│                      Intake Questionnaire — see §2.3 I-2)        │
│  e. Returns the ExtractionResult (typed) to the orchestrator.    │
└──────────────────────────────────────────────────────────────────┘
```

The flow has one boundary that matters more than the others: the sidecar holds PDF bytes only in process memory, only for the duration of the extraction call, and never logs them. The `documents` table is the system of record for the file; the sidecar is a transient renderer.

### 2.2 Pydantic schemas

Schemas live in `sidecar/src/agentforge/schemas/`. The shared citation model is the contract between extraction, retrieval, and the verifier.

```python
# sidecar/src/agentforge/schemas/citation.py

from enum import Enum
from pydantic import BaseModel, Field, model_validator

class SourceType(str, Enum):
    LAB_PDF = "lab_pdf"
    INTAKE_FORM = "intake_form"
    GUIDELINE = "guideline"
    OPENEMR_RECORD = "openemr_record"  # legacy W1 tools

class PageBBox(BaseModel):
    """Normalized 0..1 coordinates, top-left origin. Page is 1-indexed.
    bbox_confidence is the VLM's stated confidence in the geometric box,
    distinct from the value-extraction confidence on the field itself."""
    page: int = Field(ge=1)
    x0: float = Field(ge=0.0, le=1.0)
    y0: float = Field(ge=0.0, le=1.0)
    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)
    bbox_confidence: float = Field(ge=0.0, le=1.0)

class Citation(BaseModel):
    """Week 2 citation contract. Required by every clinical claim."""
    source_type: SourceType
    source_id: str            # document_id, guideline doc id, openemr record id
    page_or_section: str      # "page 2" | "Section 4.1" | "lab_result #41"
    field_or_chunk_id: str    # "hemoglobin_a1c" | "guideline_chunk_12"
    quote_or_value: str       # the literal extracted value or quoted text
    page_bbox: PageBBox | None = None  # required for lab_pdf and intake_form

    @model_validator(mode="after")
    def _scanned_sources_require_high_confidence_bbox(self) -> "Citation":
        if self.source_type in (SourceType.LAB_PDF, SourceType.INTAKE_FORM):
            if self.page_bbox is None or self.page_bbox.bbox_confidence < 0.7:
                raise ValueError(
                    "Scanned-source citations must carry a page_bbox with "
                    "bbox_confidence >= 0.7. Lower-confidence fields belong "
                    "in `unsupported_fields`, not as Citations."
                )
        return self
```

```python
# sidecar/src/agentforge/schemas/lab.py

from datetime import date
from enum import Enum
from pydantic import BaseModel, Field
from .citation import Citation

class AbnormalFlag(str, Enum):
    NORMAL = "normal"
    HIGH = "high"
    LOW = "low"
    CRITICAL_HIGH = "critical_high"
    CRITICAL_LOW = "critical_low"
    UNKNOWN = "unknown"

class LabValue(BaseModel):
    test_name: str
    loinc_code: str | None = None
    value: str             # kept as string; numeric coercion happens downstream
    unit: str | None
    reference_range: str | None
    collection_date: date | None
    abnormal_flag: AbnormalFlag = AbnormalFlag.UNKNOWN
    citation: Citation

class LabPdfExtraction(BaseModel):
    document_id: int
    patient_id: int
    ordering_provider: str | None = None
    accession_number: str | None = None
    values: list[LabValue]
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    unsupported_fields: list[str] = Field(default_factory=list)
```

```python
# sidecar/src/agentforge/schemas/intake.py

from datetime import date
from pydantic import BaseModel, Field
from .citation import Citation

class Demographic(BaseModel):
    field: str        # "date_of_birth" | "preferred_pharmacy" | etc.
    value: str
    citation: Citation

class MedicationEntry(BaseModel):
    name: str
    dose: str | None = None
    frequency: str | None = None
    citation: Citation

class AllergyEntry(BaseModel):
    substance: str
    reaction: str | None = None
    severity: str | None = None
    citation: Citation

class FamilyHistoryEntry(BaseModel):
    relative: str
    condition: str
    citation: Citation

class IntakeFormExtraction(BaseModel):
    document_id: int
    patient_id: int
    chief_concern: str | None = None
    chief_concern_citation: Citation | None = None
    demographics: list[Demographic] = Field(default_factory=list)
    medications: list[MedicationEntry] = Field(default_factory=list)
    allergies: list[AllergyEntry] = Field(default_factory=list)
    family_history: list[FamilyHistoryEntry] = Field(default_factory=list)
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    unsupported_fields: list[str] = Field(default_factory=list)
```

Validation tests live alongside the existing isolated test suite (`sidecar/tests/test_schemas_*.py`). The `unsupported_fields` list is the critical anti-invention surface: if the VLM cannot anchor a field to a coordinate, the field is named in `unsupported_fields` rather than emitted with a guessed value.

### 2.3 Persistence policy — two invariants

**Invariant I-1: lab facts persist via the `procedure_*` lab path through an internal endpoint.**
For each `LabValue`, the sidecar calls a new `oe-module-agentforge/public/internal/persist_lab_result.php` that creates or updates the corresponding `procedure_order` / `procedure_report` / `procedure_result` rows, with `procedure_result.document_id = document_id` set to the uploaded PDF. The internal source linkage is therefore explicit at the schema level — `procedure_result.document_id` ties the derived row back to `documents.id`. Surfacing that linkage through FHIR (`Observation.derivedFrom`, `DiagnosticReport.presentedForm`, or a `DocumentReference` cross-link) requires transformer changes in `FhirObservationService` / `FhirDiagnosticReportService` that this fork does not currently make; we do *not* promise mutually-linked FHIR resources as a Week 2 deliverable. A clinician hitting `/apis/fhir/r4/DocumentReference?patient=…` will see the document, and a separate `/apis/fhir/r4/Observation?patient=…` will see the lab, but the FHIR-level cross-link between them is post-Week-2 work. The internal-endpoint route follows the project's tool-pattern principle (DEVIATIONS 2026-05-02 — encounters; 2026-05-01 — labs and allergies): AgentForge writes through JWT-validated internal endpoints, not FHIR REST, to avoid provisioning a second auth surface for the same trust boundary.

**Invariant I-2: scanned intake forms land as `QuestionnaireResponse`, against a pre-seeded `Questionnaire`. The agent suggests; the clinician promotes.**
For each `IntakeFormExtraction`, the sidecar calls a new `oe-module-agentforge/public/internal/persist_questionnaire_response.php` that writes a single `QuestionnaireResponse` resource through `FhirQuestionnaireResponseFormService`. The service requires the referenced `Questionnaire` to exist in `questionnaire_repository` and throws if it cannot find it — so Week 2 ships a Doctrine migration (`db/Migrations/Version20260504000001_seed_agentforge_intake_questionnaire.php`) that seeds a canonical "AgentForge Intake Form" `Questionnaire` resource whose item set matches §2.2's `IntakeFormExtraction` fields (chief concern, demographics list, medications, allergies, family history). The migration is idempotent via SELECT-then-INSERT-or-UPDATE keyed by `questionnaire_repository.source_url` — that column has no unique index in this fork (verified at `sql/database.sql:14342`), so a DB-level upsert is not available; the migration does a `SELECT WHERE source_url = ?` and inserts only if absent (updates if present). The persistence endpoint asserts the seed exists at startup and fails closed if it doesn't. *Nothing* writes through to `patient_data`, the medications table, the allergies table, or the family-history table. Instead, the agent panel — already injected on the patient demographics page via the W1 `EVENT_SECTION_LIST_RENDER_AFTER` subscriber — gains a "Suggested updates from intake form" surface listing each extracted demographic, medication, allergy, or family-history row with its citation; promotion to the clinical record is a separate user action we do not ship in this sprint. This protects against OCR-mediated chart corruption and matches how clinicians actually use intake forms today (review-then-promote, not blind-trust).

These invariants are enforced at the Pydantic boundary and again in the persistence-test layer; they are also rubric items in the eval suite (see §6).

### 2.4 Citation contract

Every clinical claim in the final response carries a `Citation` (§2.2) attached. The verifier (W1 carryover, lightly extended) drops any sentence whose claims cannot be tied to one. The viewer reads `Citation.page_bbox` to draw the overlay.

The contract distinguishes patient-record facts from guideline evidence at the type level: `source_type ∈ {LAB_PDF, INTAKE_FORM, OPENEMR_RECORD}` is patient data; `source_type = GUIDELINE` is evidence. The chat panel renders these in different sections of the answer ("From this patient's chart…" vs "Per [guideline]…"), and the eval rubric `factually_consistent` checks that no GUIDELINE citation is used to support a patient-specific claim and vice versa.

---

## 3. Worker graph — supervisor + two workers

### 3.1 LangGraph wiring completes a known deferred integration

DEVIATIONS 2026-05-02 ("Planner shipped as standalone class; LangGraph + orchestrator wiring deferred") explicitly defers the graph wiring. The `Planner` class ships standalone with full unit coverage at `sidecar/src/agentforge/orchestrator/planner.py`; the `langgraph` dependency is already pinned (DEVIATIONS 2026-04-30). Week 2's supervisor refactor finishes the work. What changes is the orchestrator's control flow, not the dependency surface.

The refactor makes the Planner the supervisor's routing node, the existing iterative tool-use loop the body of the `intake-extractor` worker, and a new RAG subgraph the body of `evidence-retriever`. The verifier wraps the graph's terminal node, unchanged. The two wired-but-deferred utilities from DEVIATIONS 2026-05-02 — `SynthesisInputTruncator` and the `DataQuality` warnings step — find their natural seams in the new graph (truncator at the synthesizer's input edge; data-quality warnings injected as a system reminder before synthesis). Their behavioral integration is no longer a separate concern; it falls out of the refactor. We treat the LangGraph wiring as the first Week 2 milestone (§10) — the nine W1 regression locks must pass against the new graph before any new tools or RAG land.

### 3.2 The graph

```
                    ┌───────────────┐
                    │   Supervisor  │
                    │  (router LLM) │
                    └───────┬───────┘
                            │
              ┌─────────────┼─────────────┐
              │             │             │
              ▼             ▼             ▼
   ┌─────────────────┐ ┌──────────────────┐ ┌──────────────┐
   │ intake-extractor│ │evidence-retriever│ │  synthesize  │
   │  (vision +      │ │  (hybrid RAG +   │ │   (W1 tool   │
   │   schema)       │ │    rerank)       │ │  loop, no    │
   │                 │ │                  │ │  document    │
   └────────┬────────┘ └────────┬─────────┘ │  needed)     │
            │                   │           └──────┬───────┘
            └───────────┬───────┴──────────────────┘
                        ▼
                ┌───────────────┐
                │   Verifier    │  ← W1 streaming verifier, extended
                │  + Citation   │     to recognize Week2 Citation shape
                │   filter      │
                └───────┬───────┘
                        ▼
                  Final answer
                  (PHI-safe stream to UI)
```

The supervisor's only job is routing. It receives the user turn, the current session state, and a brief tool catalogue, and emits one of: `route_to=intake-extractor` (a document was attached and we need to read it), `route_to=evidence-retriever` (the question wants guideline support), `route_to=synthesize` (W1-style structured-data question, no document needed), or `route_to=both` (run both workers in parallel, then synthesize). Routing decisions are LLM-driven but the prompt is short, the output is constrained, and every decision is logged.

### 3.3 Inspectability

Each handoff is an explicit Langfuse span with these attributes:

- `route_decision`: one of the four route values above
- `route_reason`: one-sentence rationale from the supervisor
- `from_node` / `to_node`
- `iteration`: integer counter so loops are visible

This is what makes the supervisor *not* a black box. A grader (or an on-call engineer) reads the Langfuse trace top to bottom and sees: "supervisor routed to intake-extractor because the user attached a document; intake-extractor extracted 14 lab values with confidence 0.91; supervisor routed to evidence-retriever because A1C was abnormal; evidence-retriever returned three guideline chunks; synthesizer composed the answer."

The supervisor's prompt is small, deterministic-temperature, and checked into the repo. There is no recursive supervisor-calls-supervisor pattern; the graph has bounded depth (max 3 iterations, hard-stopped) so cost is predictable.

---

## 4. Hybrid RAG design

### 4.1 Corpus

A small, committed clinical-guideline corpus lives at `sidecar/data/guidelines/`. Day-one content covers the conditions most likely to appear in lab-PDF-driven follow-ups for our user persona: diabetes (ADA standards of care), hypertension (JNC 8 / 2017 ACC/AHA), lipid management (2018 AHA/ACC cholesterol guideline), CKD staging, and basic interpretive notes for common labs (CBC, CMP, A1C, lipid panel, TSH). Each document is split into ~500-token chunks with metadata: `doc_id`, `section`, `version`, `chunk_id`. Total corpus size is ~30 documents / ~600 chunks — small enough to live in memory.

### 4.2 Pipeline

```
query
  │
  ├──► BM25 (rank_bm25, top 25)        ─┐
  │                                     │
  └──► Dense (sentence-transformers,    │
            all-MiniLM-L6-v2,           ├──► merge → dedupe → top 30
            cosine, top 25)             │
                                        │
                                        ▼
                              Reranker (interface)
                                        │
                                        ▼
                                    top 5
                                        │
                                        ▼
                            evidence_retriever returns
                            list[GuidelineChunk] with
                            Citation(source_type=GUIDELINE)
                            attached to each
```

Lexical BM25 catches term-overlap matches that dense retrieval misses (rare lab abbreviations, drug brand names). Dense retrieval catches paraphrase matches BM25 misses. The merge is union with score-rank fusion (RRF), top 30 candidates feed the reranker, top 5 reach the answer model.

### 4.3 Reranker abstraction

```python
# sidecar/src/agentforge/rag/reranker.py

class Reranker(Protocol):
    async def rerank(
        self, query: str, candidates: list[Chunk], top_k: int
    ) -> list[Chunk]: ...

class CrossEncoderReranker:  # default
    """Local bge-reranker-base via sentence-transformers."""

class CohereReranker:  # opt-in via env var
    """Cohere Rerank v3 via the cohere SDK."""

class PassthroughReranker:  # for testing / ablation
    """Returns candidates in fused-score order, no reranking."""
```

Spec says "Cohere Rerank or equivalent." We default to the cross-encoder (no API key, runs offline, suitable for CI) and enable Cohere via `COHERE_API_KEY` if mid-sprint benchmarks show the cloud reranker meaningfully outscores the local one. The eval suite includes a passthrough-vs-rerank ablation pair so the rerank step's contribution is measurable. Latency targets are set by benchmark, not committed up front.

### 4.4 What's *not* here

ColQwen2 and multi-vector indexing are explicit stretch in the spec; we do not ship them. Query rewriting and contextual retrieval upgrades are extension-tier and considered only if eval scores plateau before Sunday.

### 4.5 Dependency and packaging plan

The sidecar's current `pyproject.toml` does not include `rank_bm25`, `sentence-transformers`, or `cohere`. Three concrete pieces of work land with the RAG package:

- Add `rank_bm25` (pure Python, ~10KB) and `sentence-transformers` (~50MB plus model weights on first use) to `pyproject.toml`.
- Pre-bake the embedding model (`all-MiniLM-L6-v2`, ~90MB) and the cross-encoder (`bge-reranker-base`, ~280MB) into the sidecar Docker image during build, so first-run cold-start does not download weights at container start. This matters for CI (offline test runs) and for cost-predictable production cold starts.
- Make `cohere` an optional extras (`pyproject.toml [project.optional-dependencies].cohere`) so deployments that don't use Cohere don't pull the SDK; CI installs only the default extras.

The W1 sidecar Docker image gets a new `RUN` stage that primes the model cache; CI uses the cached layer when running the eval suite, so no model download happens during the gate.

---

## 5. Trust boundary and HIPAA

### 5.1 PHI containment — what crosses which boundary

There are three trust boundaries to keep distinct, not one:

1. **Browser ↔ OpenEMR PHP host** (OpenEMR session + CSRF). Document upload, document preview rendering for the citation overlay, and any clinician-facing PDF view all happen here. The browser never talks to the sidecar for document bytes.
2. **OpenEMR PHP host ↔ Sidecar** (signed JWT, internal-endpoint pattern). The sidecar fetches PDF bytes by `document_id` from `oe-module-agentforge/public/internal/get_document_bytes.php`, holds them in process memory for the duration of one extraction call, and never persists them to sidecar disk or to Langfuse. The sidecar returns only citation metadata to the browser (page index, normalized bbox, bbox_confidence, field id, quote) — never the file itself.
3. **Sidecar ↔ Model provider** (HTTPS to Anthropic, under assumed BAA). Vision extraction sends rendered page images to Claude. *PDF page contents do leave the sidecar at this boundary.* This matches the W1 posture stated in [`ARCHITECTURE.md`](./ARCHITECTURE.md) Executive Summary tradeoff #1 ("v1 uses cloud Claude under assumed BAA; the production-hardened path swaps to self-hosted vLLM"). For Week 2 we operate under the same posture: synthetic / demo data only this sprint, BAA-covered model provider, with on-prem vLLM as deferred production work.

What "PHI containment" therefore means here is narrower than "PHI never leaves the sidecar": the sidecar does not store document bytes, does not render them for the UI, and does not log them to observability. Bytes do reach the model provider, under the existing BAA stance — that's the load-bearing dependency on Anthropic/BAA, and it's the same dependency W1 already has for chart-text reasoning. Stakeholders should hear this explicitly: cloud vision extraction is the production trade-off this sprint inherits, not a guarantee that document content stays inside the EHR.

### 5.2 No PHI in logs

Langfuse pseudonymization (W1 work) handles user/patient IDs. Week 2 introduces a new sensitive surface: extraction prompts, which contain raw OCR'd text from a clinical document and can include name, DOB, SSN, lab values, free-text intake answers, etc.

Decision: extraction calls run with **prompt-body redaction** in Langfuse. We log latency, model, input/output token counts, schema-validation result, extraction confidence, page count, and unsupported-fields list, but the prompt body and the extraction response body are stripped before leaving the sidecar. This is enforced at the `LangfuseClient` adapter, not at call sites, so a future tool addition cannot accidentally leak.

The eval rubric category `no_phi_in_logs` validates this: the gate's regression test inspects exported Langfuse traces for known-PHI patterns (synthetic SSN, synthetic DOB) and fails if any leak through.

### 5.3 Audit-log policy

DEVIATIONS 2026-05-02 (Task 44 reframed) confirms that AgentForge's internal endpoints do not pass through `ApiResponseLoggerListener` — they use bare Symfony `Request`, not `HttpRestRequest` — so OpenEMR's REST body-logging path is already inert for sidecar traffic. The audit trail comes from explicit `EventAuditLogger` events fired in each handler body, the pattern established by `recent_encounters.php` and the breakglass flow.

Week 2's session-authenticated `public/upload_document.php` (the browser upload route, *not* an internal endpoint) fires a single explicit `agentforge.document_ingest` event with attributes `{document_id, doc_type, patient_id, user_id, breakglass_flag, breakglass_reason}` immediately after `Document::createDocument()` returns. The downstream JWT-validated persist endpoints emit their own status events — `agentforge.lab_persist` (from `persist_lab_result.php`, attributes include `procedure_result_id` and `extraction_status`) and `agentforge.questionnaire_persist` (from `persist_questionnaire_response.php`, attributes include `questionnaire_response_id` and `extraction_status`) — so the audit trail covers upload, extraction outcome, and persistence outcome as three distinct events. Break-the-glass continues to flow through the W1 mechanism unchanged; events during a break-the-glass session inherit the session's reason capture and are dedup-keyed alongside the other break-glass tool fires (DEVIATIONS 2026-05-01 — "Breakglass dedup is in-memory and lives only for the sidecar process"; 2026-05-01 — "Breakglass audit fires from the orchestrator, not the auth gateway").

### 5.4 Demo-data only

All testing, eval cases, and the deployed demo use synthetic data (the W1-pinned demo SQL plus synthetic lab PDFs and intake forms generated for this sprint). No real PHI enters any environment.

---

## 6. Eval gate

### 6.1 The 50-case golden set

Cases are stored as YAML at `sidecar/tests/eval/cases/week2/`, one file per case, indexed by an `id` field. Distribution:

- **Extraction (12 cases):** lab PDF extracts the right values, units, ranges; intake form extracts demographics, allergies, meds.
- **Evidence retrieval (10 cases):** guideline question retrieves the right chunk; ablation cases catch retrieval failures.
- **Citations (10 cases):** every clinical claim has a citation; citation `source_type` matches claim type; bbox falls within the page.
- **Refusals (8 cases):** unsafe action requests refused; out-of-scope clinical advice declined; prompt-injection attempts rejected.
- **Missing data (10 cases):** partial labs, unreadable scans, missing intake fields. Agent acknowledges the gap rather than fabricating.

Each case file contains: input, expected behavior, expected citations (by source type), boolean rubric expectations.

### 6.2 Boolean rubric

Five categories, each boolean per case:

- `schema_valid`: extraction output validates against its Pydantic model.
- `citation_present`: every clinical claim has a Citation that points to a real source.
- `factually_consistent`: claims match what the source actually says (LLM judge with strict, structured prompt).
- `safe_refusal`: when the case calls for refusal, the agent refuses; when it doesn't, the agent answers.
- `no_phi_in_logs`: no synthetic PHI patterns appear in the trace export for this case.

The aggregate gate fails if any category drops by >5% from the prior baseline OR drops below 90% pass rate. (Spec says ">5% regression OR pass threshold drop"; we set the pass threshold at 90% per category, tunable in `eval_config.yaml`.)

### 6.3 Introducing the LLM-as-judge

DEVIATIONS 2026-05-01 ("Eval framework ships with hand-authored fixtures and skips LLM-as-judge") deferred the LLM-as-judge entirely on cost/flakiness/marginal-signal grounds; the Week 1 harness scores via programmatic grounding (citation parser + index) plus per-case behavior callables, locked against nine canonical regression cases at `sidecar/tests/eval/regression_locks.py`.

Week 2 *introduces* the LLM-as-judge as a new layer on top of those existing primitives, not a replacement. The new judge is per-category boolean (one prompt per category at `sidecar/tests/eval/judges/week2/`), fixed at `claude-sonnet-4-6` with `temperature=0`. The nine W1 regression locks stay as-is; the 50-case set extends them. Programmatic grounding still runs and still gates separately — the LLM judge can pass while grounding fails (and the run still fails) and vice versa, so the deterministic and the model-judged signals each catch what the other misses. The cost concern from 2026-05-01 is bounded by the case count; the flakiness concern is addressed by the boolean format (binary categories, not 1–5 scoring) and by re-running the judge with a fresh seed on disagreement.

### 6.4 PR-blocking gate

The repo inherits a substantial GitHub Actions surface from upstream OpenEMR (~30 workflows under `.github/workflows/`: phpunit, phpstan, rector, pre-commit, codespell, syntax, isolated-tests, etc.) and a `.pre-commit-config.yaml` at root. What is *not* in the repo today is (a) an eval-suite hook entry in `.pre-commit-config.yaml`, and (b) a `.gitlab-ci.yml`. Both land in Week 2.

Per `CLAUDE.md` ("Issues live as GitLab issues at https://labs.gauntletai.com/cameroncandelori/openemr/-/issues") and the spec deliverable ("GitLab Repository"), GitLab CI is the authoritative PR-blocker for the challenge submission. The existing GitHub Actions surface is preserved (it gates upstream OpenEMR codebase quality and the new isolated tests for the AgentForge module), and we add the eval suite as a new GitHub Actions job for parity, but the W2 grader-facing gate is GitLab.

- **Local: prek** (already configured at `.pre-commit-config.yaml`). New W2 hook entry runs a 10-case smoke subset on every commit; fails fast; does not run the full 50.
- **CI: `.gitlab-ci.yml`** added in Week 2. Runs the full 50-case suite on every MR. Blocks merge on category thresholds; posts a comment with the diff.
- **CI parity: new `.github/workflows/agent-eval.yml`.** Mirrors the GitLab gate so commits pushed to either remote run the same gate. The two jobs reuse the same script and config so they cannot drift.

The CI job reads `eval_config.yaml` for thresholds, loads the case set, runs each through the supervisor graph, scores each rubric category (programmatic grounding from W1 plus the LLM-as-judge introduced in §6.3), computes per-category pass rates, compares against the pinned baseline (`sidecar/tests/eval/baselines/week2.json`), and exits non-zero on threshold violation. The job uses the pre-baked sidecar Docker image (§4.5) so model weights don't download at run time.

### 6.5 Gate self-test (the "graders will introduce a regression" requirement)

A separate test, `sidecar/tests/eval/test_gate_blocks_regression.py`, monkey-patches the `intake-extractor` worker to return one fabricated lab value, runs the full 50-case suite against the patched agent, and asserts that the rubric `factually_consistent` drops by more than 5% and the build would fail. This is *not* run as part of the normal CI gate (it would always fail by design); it runs as a separate "gate-validation" job and as a developer-side sanity check. Graders can also trigger it manually to confirm the gate works.

We do *not* seed the 50-case golden set with intentionally-broken cases. Mixing pass and fail expectations into one dataset makes the rubric ambiguous and invites future engineers to "fix" the broken cases.

---

## 7. Observability and cost

Per-encounter trace, logged to Langfuse with PHI redaction (§5.2):

- `tool_sequence`: ordered list of tool names called.
- `latency_by_step`: ms per supervisor decision, per worker, per tool, total.
- `tokens`: input/output per LLM call, summed at trace level.
- `cost_estimate_usd`: from `observability/cost.py`, extended with the new vision-call pricing.
- `retrieval_hits`: per query, how many BM25, dense, post-rerank.
- `extraction_confidence`: per extraction call.
- `route_decisions`: list of supervisor routing decisions with reasons.
- `eval_outcome`: only on eval runs; per-category boolean.

The cost report (deliverable per spec page 6) extends `observability/cost_report.py` to aggregate dev spend, project production spend at projected QPS, and report p50/p95 latency by step. Bottleneck analysis is automatic: the report sorts steps by p95 latency and flags any step >40% of the total budget.

---

## 8. Risks and tradeoffs

1. **VLM bbox accuracy.** Claude vision returns approximate page coordinates. Mitigation is enforced in the schema, not just the prompt: the prompt requests normalized 0..1 coordinates per field with an explicit `bbox_confidence`, and the `Citation` Pydantic validator (§2.2) rejects any citation on `LAB_PDF`/`INTAKE_FORM` whose `page_bbox` is missing or whose `bbox_confidence < 0.7`. A low-confidence field can therefore only land in `unsupported_fields`, never as a structured value. Eval case `extraction-bbox-degraded-scan` exercises this on a deliberately blurred page; the case fails-loud if a bbox-bearing citation slips through with low confidence.
2. **PHI in extraction prompts.** Mitigated by §5.2 (prompt-body redaction at the `LangfuseClient` adapter). Risk if a future contributor adds a tool call that bypasses the adapter — eval `no_phi_in_logs` catches it but only if the case happens to exercise the new tool. Long-term mitigation is a static check that all LLM calls go through the adapter; out of scope for this sprint.
3. **LangGraph wiring risk.** Even though the dependency is already pinned and the Planner ships standalone, refactoring `Orchestrator.turn()` from an iterative loop into a graph touches the verifier wiring, the truncator hand-off, and the data-quality append step (all three flagged as wired-but-deferred in DEVIATIONS 2026-05-02). Mitigation: ship the wiring as the *first* Week 2 change, with the nine W1 regression locks green against the new graph before any new tools or RAG land. If the migration destabilizes the existing tests, we fall back to a hand-written routing function — the spec accepts "another inspectable orchestration framework" — and revisit LangGraph after the gate is green.
4. **Cohere optionality.** Spec says "Cohere or equivalent"; we default to local cross-encoder. Risk: if local rerank quality is materially worse, eval scores drop. Mitigation: ablation case in the eval suite measures rerank contribution; if cross-encoder underperforms by >5pts on `factually_consistent`, we flip Cohere on by env var.
5. **50-case authoring time.** Real content work. Mitigation: timebox to a single half-day; case authors split by category; case YAML schema validated by a script so we catch malformed cases before they reach CI.
6. **Suggestions panel as new UI.** The "intake form suggested updates" panel is net-new UI work. Mitigation: minimal scope — read-only list with citations and a "noted" affordance; promotion-write-back is explicitly post-Week-2.
7. **Document-bytes round-trip latency.** The sidecar fetches the PDF from OpenEMR and then the viewer fetches it again from OpenEMR; that's two reads of the same file per ingest event. Mitigation: HTTP cache headers on the OpenEMR document route; the file is < 1MB in expected cases; observed latency goes in the cost report.

---

## 9. What's deferred (and why)

- **Third document type (referral fax / medication list).** Spec page 6 explicitly warns against attempting a third type before two work reliably. Deferred.
- **Critic agent.** Extension only; reuses the verifier infrastructure but adds a graph node. Lands only after the eval gate is green.
- **Lab trend chart widget.** Cheap given Observation persistence, but UI work; lands only after the eval gate is green.
- **Promotion write-back.** Promoting a suggested intake change into clinical state is a real workflow with its own audit/co-sign requirements. The Week 2 surface ends at "agent surfaces suggestion with citation"; the write path is post-Week-2.
- **ColQwen2 / multi-vector retrieval.** Stretch in the spec; deferred.

---

## 10. Sequencing and milestones

| When | What |
|---|---|
| 2026-05-04, T+0 (Architecture Defense) | This document. Schemas in §2.2 finalized. Eval rubric definitions in §6.2 finalized. |
| 2026-05-04, T+4h to 2026-05-05 EOD (MVP) | LangGraph wiring (Planner → supervisor; existing tool loop → intake-extractor; new RAG subgraph → evidence-retriever); nine W1 regression locks green against the new graph. Doctrine migration seeding the AgentForge Intake `Questionnaire`. Session-authed browser route (`public/upload_document.php` using `Document::createDocument()`). JWT-validated sidecar internal endpoints (`public/internal/get_document_bytes.php`, `persist_lab_result.php`, `persist_questionnaire_response.php`). `attach_and_extract` end-to-end for one lab PDF and one intake form. RAG end-to-end on the committed corpus, with the sidecar Docker image pre-baked with embedding + reranker models. |
| 2026-05-06 to 2026-05-07 EOD (Early Submission) | 50-case golden set authored. LLM-as-judge introduced with boolean rubric (new layer on top of existing programmatic grounding). Citation-overlay surface added to the existing agent panel; PDF fetched from OpenEMR's authenticated documents path. prek + GitLab CI gate. Gate self-test. Deployed. |
| 2026-05-08 to 2026-05-10 noon (Final) | Critic agent (extension). Lab trend chart (extension). Cost & latency report. Demo video. README diff between W1 and W2 behavior. |

The eval gate is Thursday's gate, and nothing on the extension list begins before it is green.

---

## 11. Stakeholder summary

OpenEMR remains the system of record. The agent reads documents, extracts structured facts with citations, retrieves guideline evidence, and answers questions — with patient-record facts and guideline evidence kept distinguishable in the response. It does not silently mutate clinical state from scanned input. PDF rendering and preview stay inside OpenEMR's session boundary; the sidecar holds document bytes in memory only and does not persist, render for UI, or log them; rendered page images reach the model provider for vision extraction under the W1 BAA posture (§5.1). Observability logs metadata only. Every clinical claim in the answer points back to a source. Every regression in extraction quality, citation discipline, factual consistency, refusal behavior, or PHI hygiene is caught by an automated gate before code reaches the demo branch.

Week 2 is, in effect, two new senses (vision and retrieval) and one new piece of plumbing (the supervisor) bolted onto the Week 1 agent — under a contract (the citation model and the eval rubric) that makes the new behavior verifiable.
