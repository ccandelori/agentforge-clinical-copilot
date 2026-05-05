# W2_DEFENSE.md — Architecture Summary and Defense

**Project:** AgentForge Clinical Co-Pilot — Week 2
**Document date:** 2026-05-04
**Author:** Cameron Candelori
**Read first:** [`W2_ARCHITECTURE.md`](./W2_ARCHITECTURE.md) (full design)
**Companion docs:** [`ARCHITECTURE.md`](./ARCHITECTURE.md) (W1 design intent), [`docs/DEVIATIONS.md`](./docs/DEVIATIONS.md) (W1 as-shipped, with rationale)

---

## TL;DR

Week 2 adds two senses (vision over scanned clinical documents; retrieval over a small clinical-guideline corpus) and one piece of plumbing (a supervisor + 2 workers graph) to the Week 1 agent — under a citation contract and a 50-case eval gate that make the new behavior verifiable. We extend rather than reinvent: lab facts persist through OpenEMR's existing `procedure_*` tables; intake forms persist as `QuestionnaireResponse` and never silently mutate clinical state; the agent panel, JWT proxy, and audit pattern all come from the existing `oe-module-agentforge`. *Done* is the eval gate green at 50 cases with the gate's self-test demonstrating that a deliberate regression fails it.

---

## The shape, in one picture

```
Browser
  │
  │ session + CSRF (multipart upload, PDF preview fetch)
  ▼
OpenEMR PHP host — oe-module-agentforge (W1 carryover, extended)
  │   - public/upload_document.php  (NEW W2, session-authed)
  │   - public/turn.php             (W1, agent-turn proxy)
  │   - public/internal/*.php       (W1 pattern, JWT-validated)
  │       - get_document_bytes.php       (NEW W2)
  │       - persist_lab_result.php       (NEW W2)
  │       - persist_questionnaire_response.php (NEW W2)
  │   - Audit: explicit EventAuditLogger events per route
  ▼
Sidecar (FastAPI) — LangGraph supervisor + 2 workers
  │
  │   ┌──────────┐
  │   │Supervisor│  ← Planner (W1, standalone) becomes the routing node
  │   └────┬─────┘
  │        ├── intake-extractor   ← W1 tool loop, now scoped to vision+schema
  │        ├── evidence-retriever ← NEW W2: BM25 + dense + rerank
  │        └── synthesize         ← W1 path for non-document questions
  │              │
  │              ▼
  │        Verifier (W1, citation-validated)
  │              │
  │              ▼
  │        Final answer with Week2Citation per claim
  │              │
  ▼              ▼
Anthropic (BAA, vision + reasoning)        Browser (citation overlay
                                            composited over OpenEMR-served PDF)
```

---

## Five load-bearing decisions and their defenses

### 1. Scanned intake forms persist as `QuestionnaireResponse`. The agent suggests; the clinician promotes.

**Alternative considered:** Write extracted demographics, medications, allergies, and family history directly to the canonical clinical tables (`patient_data`, the medication list, the allergies list).

**Why we chose otherwise:** OCR is fallible. An intake form's "PCN" misread as "Pen-V" would become a charted allergy with no clinician in the loop; a misaligned column on a scanned form would create a false medication. The agent extracts and surfaces *suggested updates* with citations to the form; promotion to the clinical record is a separate, human action. This protects against AI-mediated chart corruption and matches how clinicians actually use intake forms today.

**Tradeoff:** The clinician has to confirm. Promotion-write-back UI is post-Week-2.

**Cite:** [`W2_ARCHITECTURE.md` §2.3 invariant I-2](./W2_ARCHITECTURE.md).

---

### 2. Lab facts persist through `procedure_*` tables, not FHIR `Observation` create.

**Alternative considered:** Write through `FhirObservationService` / the FHIR REST surface.

**Why we chose otherwise:** The project's tool-pattern principle (DEVIATIONS 2026-05-02 — encounters; 2026-05-01 — labs and allergies) is that AgentForge tools talk to OpenEMR through JWT-validated internal endpoints, not FHIR REST. Routing writes through FHIR would require provisioning OAuth2 client credentials and a token-management layer for the same trust boundary the existing internal-endpoint pattern already establishes. We follow the established pattern, write through `procedure_order` / `procedure_report` / `procedure_result`, and set `procedure_result.document_id = documents.id` so the linkage is explicit at the schema level.

**Tradeoff:** The FHIR-level cross-link (`Observation.derivedFrom`, `DiagnosticReport.presentedForm`) is post-Week-2 transformer work. We do not promise mutually-linked FHIR resources as a W2 deliverable.

**Cite:** [`W2_ARCHITECTURE.md` §2.3 invariant I-1](./W2_ARCHITECTURE.md).

---

### 3. LangGraph wiring is *completed* in W2, not introduced.

**Alternative considered:** Hand-write a routing function on top of the W1 single-node tool loop.

**Why we chose otherwise:** The `langgraph` dependency is already pinned (DEVIATIONS 2026-04-30) and the `Planner` class already ships standalone with full unit coverage (DEVIATIONS 2026-05-02 explicitly defers the graph wiring). Native conditional edges, native handoff spans, and the spec's "inspectable orchestration framework" requirement all argue for finishing the deferred integration instead of building a parallel routing layer.

**Tradeoff:** Real refactor of `Orchestrator.turn()` from an iterative loop into a graph. The nine W1 regression locks must pass against the new graph before any new tools or RAG land. If the migration destabilizes them, we fall back to the hand-written routing function — the spec accepts "another inspectable orchestration framework."

**Cite:** [`W2_ARCHITECTURE.md` §3.1, §8 risk #3](./W2_ARCHITECTURE.md).

---

### 4. PHI containment narrows three trust boundaries — and the model provider is the load-bearing exception.

**Alternative considered:** Claim "PDF bytes never leave OpenEMR's session boundary." Or build on-prem vLLM vision extraction inside Week 2.

**Why we chose otherwise:** The first is dishonest — Claude vision is on the model-provider side of a boundary the sidecar deliberately crosses for extraction. The second is W1's own deferred production work (per [`ARCHITECTURE.md`](./ARCHITECTURE.md) Executive Summary tradeoff #1: "v1 uses cloud Claude under assumed BAA; the production-hardened path swaps to self-hosted vLLM"); reaching it in a one-week sprint isn't realistic. The honest framing is three boundaries, with what crosses each named explicitly:

| Boundary | Auth | What crosses | What doesn't |
|---|---|---|---|
| Browser ↔ OpenEMR PHP | Session + CSRF | PDF upload, PDF preview for the overlay | Sidecar is not on this boundary |
| OpenEMR PHP ↔ Sidecar | Signed JWT | `document_id`, extracted facts, persistence calls | PDF bytes stay in sidecar memory only; not persisted, not logged |
| Sidecar ↔ Anthropic | HTTPS, BAA | Rendered page images for vision | Same BAA W1 already takes for chart-text reasoning |

**Tradeoff:** Cloud Claude is a real production dependency. Demo data is synthetic. The LLM-client abstraction (W1 carryover) is the swap point for on-prem vLLM in production hardening.

**Cite:** [`W2_ARCHITECTURE.md` §5.1, Executive Summary load-bearing #3](./W2_ARCHITECTURE.md).

---

### 5. The eval gate is the deliverable.

**Alternative considered:** Rely on programmatic grounding alone (cheaper, fully deterministic). Or use 1–5 LLM scoring (fuzzier, harder to act on, harder to gate).

**Why we chose otherwise:** Spec mandates a 50-case PR-blocking gate with five named rubric categories. Boolean rubrics make failures actionable in a way 1–5 scoring doesn't. The LLM-as-judge catches semantic regressions that programmatic checks miss. The W1 programmatic primitives (citation parser + index, per-case behavior callables, nine pinned regression locks at `tests/eval/regression_locks.py`) stay in place because they catch what the LLM judge misses — they're cheap, deterministic, and orthogonal in failure modes.

**Tradeoff:** Cost (50 LLM-judge calls per CI run) and authoring effort (50 cases). Cost is bounded by case count and `temperature=0`. Authoring is timeboxed.

**Cite:** [`W2_ARCHITECTURE.md` §6](./W2_ARCHITECTURE.md).

---

## Schemas at a glance

The citation contract — one Pydantic class — is the contract between extraction, retrieval, the verifier, and the UI:

```python
class Citation(BaseModel):
    source_type: SourceType    # LAB_PDF | INTAKE_FORM | GUIDELINE | OPENEMR_RECORD
    source_id: str
    page_or_section: str
    field_or_chunk_id: str
    quote_or_value: str
    page_bbox: PageBBox | None  # required for LAB_PDF / INTAKE_FORM

    @model_validator(mode="after")
    def _scanned_sources_require_high_confidence_bbox(self):
        # rejects LAB_PDF / INTAKE_FORM citations whose
        # page_bbox is missing or whose bbox_confidence < 0.7
```

`SourceType` distinguishes patient-record facts from guideline evidence at the type level; the chat panel renders them in different sections of the answer ("From this patient's chart…" vs "Per [guideline]…"), and the eval rubric `factually_consistent` checks that no `GUIDELINE` citation supports a patient-specific claim and vice versa. The `bbox_confidence` validator means a low-confidence field can only land in `unsupported_fields`, never as a structured value.

Lab and intake schemas (`LabValue`, `IntakeFormExtraction`) live in [`W2_ARCHITECTURE.md` §2.2](./W2_ARCHITECTURE.md). The required fields per the spec are all there: lab — test name, value, unit, reference range, collection date, abnormal flag, citation; intake — demographics, chief concern, medications, allergies, family history, citation.

---

## Hybrid RAG at a glance

- **Corpus:** ~30 documents committed to `sidecar/data/guidelines/` (diabetes ADA, hypertension JNC 8 / ACC/AHA, lipid management AHA/ACC, CKD staging, common-lab interpretive notes). ~600 chunks of ~500 tokens each, in memory.
- **Pipeline:** BM25 (top 25) + dense (sentence-transformers `all-MiniLM-L6-v2`, top 25) → score-rank fusion (RRF) → top 30 → cross-encoder rerank (`bge-reranker-base`) → top 5 → injected into the answer model with `Citation(source_type=GUIDELINE)` attached per chunk.
- **Reranker behind an interface:** local cross-encoder default; Cohere opt-in via `COHERE_API_KEY`; passthrough for ablation. The eval suite includes a passthrough-vs-rerank pair so the rerank step's contribution is measurable.
- **Packaging:** dependencies added to `pyproject.toml`; embedding + reranker models pre-baked into the sidecar Docker image so first-run cold-start doesn't pull ~370MB of weights at container start.
- **What's not here:** ColQwen2, multi-vector — explicit stretch in the spec, deferred.

---

## Eval gate at a glance

- **50 cases**, distributed: extraction (12), evidence retrieval (10), citations (10), refusals (8), missing-data (10).
- **Boolean rubric, five categories:** `schema_valid`, `citation_present`, `factually_consistent`, `safe_refusal`, `no_phi_in_logs`.
- **Threshold:** any category drops by >5% from the pinned baseline OR drops below 90% pass rate → CI fails.
- **Two layers, orthogonal failure modes:** programmatic grounding (W1 — deterministic, no model needed) catches schema/parser drift; LLM-as-judge (new in W2 — `claude-sonnet-4-6` at temperature 0, one prompt per category) catches semantic regressions. They run independently; either failing fails the run.
- **Three places it runs:**
  - **Local prek hook:** 10-case smoke subset, fast feedback on every commit.
  - **GitLab CI** (`.gitlab-ci.yml`, new W2): full 50, authoritative PR-blocker per the spec deliverable.
  - **GitHub Actions mirror** (new `.github/workflows/agent-eval.yml`, W2): same gate, same script, same config, so the two cannot drift. The repo already has ~30 GitHub Actions workflows from upstream OpenEMR; this is one more, scoped to AgentForge.
- **Gate self-test:** separate `tests/eval/test_gate_blocks_regression.py` that monkey-patches a tool to return wrong data, runs the full suite, and asserts the rubric drops `factually_consistent` by more than 5%. Proves the gate works without seeding broken cases into the golden set.

---

## Anticipated questions

**Q: How do you know your eval gate actually catches the regression graders will introduce?**
A: We ship a separate test (`test_gate_blocks_regression.py`) that monkey-patches the `intake-extractor` worker to return one fabricated lab value, runs the full 50-case suite against the patched agent, and asserts that `factually_consistent` drops by more than 5% — i.e., that the build *would* fail. This is run as a separate CI job (it always fails by design) and is also runnable manually. Graders can trigger it directly to confirm.

**Q: Why don't you write extracted intake fields straight to the proper clinical tables? You have the data.**
A: We have *probable* data. OCR mistakes become charted clinical state. An intake form's "Penicillin V" misread, or a misaligned column on a scanned form, would create a false allergy or medication record. The agent's job is to surface; the clinician's job is to confirm. The chart is updated by humans, not by VLM extraction.

**Q: Why introduce LangGraph in Week 2?**
A: It's already a pinned dependency, and the `Planner` class already ships standalone with full unit coverage. What's deferred is the graph that consumes it (DEVIATIONS 2026-05-02). Week 2 finishes that integration. We're closing a documented deferral, not adopting a new framework.

**Q: What's the failure mode for the LangGraph wiring?**
A: If the migration destabilizes the nine W1 regression locks, we fall back to a hand-written routing function on top of the existing tool loop — the spec accepts "another inspectable orchestration framework." We treat the wiring as the *first* Week 2 milestone so this risk surfaces before any new tools or RAG land.

**Q: How do you contain PHI in extraction prompts?**
A: Prompt-body redaction enforced at the `LangfuseClient` adapter, not at call sites. Extraction calls log latency, model, token counts, schema-validation result, extraction confidence, and unsupported-fields list — but the prompt body and the response body are stripped before they leave the sidecar. The eval rubric `no_phi_in_logs` validates this by inspecting trace exports for known synthetic-PHI patterns; the gate fails if any leak through.

**Q: What if the VLM hallucinates a bounding box?**
A: The `Citation` Pydantic validator rejects any `LAB_PDF` / `INTAKE_FORM` citation whose `page_bbox` is missing or whose `bbox_confidence < 0.7`. Low-confidence fields can only land in `unsupported_fields`, never as structured values. Eval case `extraction-bbox-degraded-scan` exercises this on a deliberately blurred page and fails the run if a low-confidence bbox slips through.

**Q: Why default to a local reranker instead of Cohere?**
A: Cost predictability, no API-key requirement in CI, and the spec accepts "or equivalent." The reranker sits behind a `Reranker` interface with three implementations (cross-encoder default, Cohere opt-in, passthrough for ablation). The eval suite measures rerank contribution before we commit to a cloud reranker.

**Q: Where does PHI cross out of OpenEMR?**
A: Three boundaries. (1) Browser ↔ OpenEMR is session + CSRF authed; PDF bytes stay inside it for upload and preview. (2) OpenEMR ↔ sidecar is JWT-authed via the established internal-endpoint pattern; the sidecar holds bytes in memory only and never persists, renders, or logs them. (3) Sidecar ↔ Anthropic is HTTPS under assumed BAA — *rendered page images do leave the sidecar at this boundary*. This is the same dependency W1 already takes for chart-text reasoning. Demo data is synthetic this sprint; on-prem vLLM is W1's deferred production work.

**Q: Why GitLab CI when the repo has 30 GitHub Actions workflows already?**
A: Per `CLAUDE.md` ("Issues live as GitLab issues") and the spec deliverable ("GitLab Repository"), GitLab is the W2 grader-facing target. The existing GitHub Actions surface is preserved (it gates the upstream OpenEMR codebase quality and the AgentForge module's isolated tests). We add the eval suite as a new GitHub Actions job for parity so the two CI surfaces cannot drift.

---

## Out of scope this sprint, with reasons

- **Third document type** (referral fax, medication list). Spec page 6 explicitly warns against this until two work reliably.
- **Promotion write-back from intake suggestions to clinical state.** Real workflow with audit/co-sign requirements; W2 ends at "agent surfaces suggestion with citation."
- **FHIR-level cross-link between `Observation` and `DocumentReference`.** Requires transformer changes not in this fork; post-W2.
- **ColQwen2 / multi-vector retrieval.** Explicit stretch in the spec.
- **Critic agent and lab trend chart widget.** Extension work; ships only after the eval gate is green Thursday.
- **On-prem vLLM vision extraction.** W1's deferred production work; W2 inherits the BAA posture.

---

## What "done" looks like Sunday at noon

- 50-case eval gate green; gate self-test demonstrates that a deliberately-injected regression fails the rubric.
- LangGraph supervisor + 2 workers wired; the nine W1 regression locks pass against the new graph.
- Lab PDF and intake form ingestion end-to-end through `procedure_*` and `QuestionnaireResponse`, with the AgentForge Intake `Questionnaire` seed migration applied.
- Citation overlay rendering on the existing patient-demographics agent panel; PDF served from OpenEMR's session-authed documents path.
- Cost & latency report; 3–5 minute demo video; deployed app.
- Critic agent and lab trend chart shipped as extensions if the eval gate cleared by Thursday EOD.
- README clearly separates Week 1 baseline behavior from Week 2 multimodal behavior; graders can run the core W2 flow without guessing which branch.

---

## Closing line for the stakeholder

OpenEMR remains the system of record. Week 2 adds two senses (vision and retrieval) and one piece of plumbing (the supervisor), under a citation contract that distinguishes patient-record facts from guideline evidence and an automated gate that catches regressions in extraction quality, citation discipline, factual consistency, refusal behavior, and PHI hygiene before code reaches the demo branch. The agent reads, extracts with citations, retrieves grounded evidence, and answers — but the chart is updated by humans, not by VLM extraction.
