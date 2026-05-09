# Defense Q&A — Week 2 (study aid)

**Study aid for the W2 defense, not a replacement for [`W2_DEFENSE.md`](../W2_DEFENSE.md).**

This document is a talking-points layer over the authoritative defense narrative. It exists so the defender can re-load the load-bearing answers under stage pressure, and so a stakeholder dropping into the call cold can grasp the architecture's defensible shape in five minutes. For the full design rationale, read [`W2_ARCHITECTURE.md`](../W2_ARCHITECTURE.md). For the canonical executive summary, read [`W2_DEFENSE.md`](../W2_DEFENSE.md). For the slide deck, open [`docs/w2-defense-slides.html`](./w2-defense-slides.html).

**Source documents this primer indexes:**

- [`W2_DEFENSE.md`](../W2_DEFENSE.md) — the defense narrative (anticipated-questions section is the parent of this primer)
- [`W2_ARCHITECTURE.md`](../W2_ARCHITECTURE.md) — full design with section pointers used below
- [`docs/w2-defense-slides.html`](./w2-defense-slides.html) — 18-slide deck; slide pointers below were verified against the as-committed file
- [`docs/DEVIATIONS.md`](./DEVIATIONS.md) — what shipped vs what was specced; cited where an answer turns on a deviation
- [`docs/eval-report-2026-05-08.md`](./eval-report-2026-05-08.md) — the W2 50-case report (Task 31 shipped); pre-dates the 2026-05-09 measured-baseline regen, so its "stub-pinned" framing is superseded by `sidecar/tests/eval/baselines/week2.json` (`_meta.status: "measured"`, $1.54 run total). The gate's correctness story is now two-leg — see Q5 and the new Q13–Q15 entries below

**Slide-pointer caveat.** A WIP stash named `presession-slides-WIP` exists at primer authoring time and may renumber slides if popped before the defense. Slide pointers below are pinned to the as-committed deck (18 slides). If the stash is popped, re-run a slide map before the defense.

**How the seven required questions map to architectural commitments:**

| # | Question topic | Decision | Slide |
|---|---|---|---|
| Q1 | QuestionnaireResponse vs direct write | Persistence policy invariant I-2 | 5/18 |
| Q2 | `procedure_*` vs FHIR Observation create | Persistence policy invariant I-1 | 7/18 |
| Q3 | LangGraph in W2 vs earlier | Closing a documented deferral | 9/18 |
| Q4 | PHI containment | Three-boundary trust model | 11/18 |
| Q5 | Gate self-test mechanics | Gate-validates-itself test | 15/18 |
| Q6 | Reranker default | Local cross-encoder behind interface | 14/18 |
| Q7 | Promotion-write-back exclusion | Scope ends at "agent surfaces suggestion" | 5/18 |

Five additional grader-likely follow-ups are covered after the required seven.

---

## Required questions

### Q1: Why do scanned intake forms persist as `QuestionnaireResponse` instead of writing extracted demographics, allergies, and medications directly to the canonical clinical tables?

**Answer.** OCR is fallible and the cost of being wrong is structural: an intake form's "PCN" misread as "Pen-V" would land as a charted allergy with no clinician in the loop, and a misaligned column on a scanned form would create a false medication. The agent therefore extracts and *surfaces* suggested updates with citations to the form, but promotion to the canonical tables (`patient_data`, the medications list, the allergies list) is an explicit human action. We considered and rejected writing through directly — the data is "probable," not authoritative, and AI-mediated chart corruption is a class of bug that isn't worth the keystroke savings. The integrity invariant is enforced at the Pydantic boundary, again at the persistence-test layer, and again by the eval rubric. The cost is that promotion-write-back UI is post-W2 (see Q7).

- **Arch link:** [`W2_ARCHITECTURE.md` §2.3 — Persistence policy, Invariant I-2](../W2_ARCHITECTURE.md)
- **Slide pointer:** 5 / 18 — *Decision 1 of 5: Intake forms → `QuestionnaireResponse`*

---

### Q2: Why do lab facts persist through `procedure_order` / `procedure_report` / `procedure_result` instead of writing through the FHIR `Observation` create endpoint?

**Answer.** The project's tool-pattern principle — established in DEVIATIONS 2026-05-01 (`get_recent_labs`, `get_active_allergies`) and 2026-05-02 (encounters) — is that AgentForge talks to OpenEMR through JWT-validated internal endpoints, not FHIR REST. Routing writes through the FHIR surface would require provisioning OAuth2 client credentials and a token-management layer for the same trust boundary the existing internal-endpoint pattern already covers; we'd be standing up a second auth surface for no semantic gain. We considered it explicitly and rejected it for that reason. Writing through `procedure_*` also matches OpenEMR's own canonical lab path: the FHIR `Observation` and `DiagnosticReport` services already *read from* `procedure_result`, so writing there gives FHIR readers their data automatically. We set `procedure_result.document_id = documents.id` so the round-trip from extracted lab back to the source PDF is explicit at the schema level. The cost we accept is that the FHIR-level cross-link (`Observation.derivedFrom`, `DiagnosticReport.presentedForm`) is post-W2 transformer work and we do not promise mutually-linked FHIR resources as a W2 deliverable.

- **Arch link:** [`W2_ARCHITECTURE.md` §2.3 — Persistence policy, Invariant I-1](../W2_ARCHITECTURE.md)
- **Slide pointer:** 7 / 18 — *Decision 2 of 5: Lab facts → `procedure_*` tables*

---

### Q3: Why is LangGraph showing up in Week 2 and not earlier?

**Answer.** It is already a pinned dependency (DEVIATIONS 2026-04-30) and the `Planner` class already ships standalone with full unit coverage at `sidecar/src/agentforge/orchestrator/planner.py` — what was deferred in W1 was the *graph* that consumes the Planner (DEVIATIONS 2026-05-02). W2's supervisor refactor finishes that wiring rather than introducing new framework surface: the existing iterative tool-use loop becomes the body of the `intake-extractor` worker, the Planner becomes the supervisor's routing node, and a new RAG subgraph becomes the `evidence-retriever`. We considered the alternative — hand-writing a routing function on top of the W1 single-node loop — and rejected it because native conditional edges, native handoff spans, and the spec's "inspectable orchestration framework" requirement all argue for finishing the deferred integration instead of building a parallel routing layer. The risk is real (the nine W1 regression locks must pass against the new graph before any new tools or RAG land), and the wiring is sequenced as the *first* W2 milestone so the risk surfaces early. If the migration destabilizes the locks, we fall back to a hand-written routing function — the spec explicitly accepts "another inspectable orchestration framework."

- **Arch link:** [`W2_ARCHITECTURE.md` §3.1 — LangGraph wiring completes a known deferred integration](../W2_ARCHITECTURE.md); see also [`docs/DEVIATIONS.md` 2026-05-02 — Planner shipped as standalone class; LangGraph + orchestrator wiring deferred](./DEVIATIONS.md)
- **Slide pointer:** 9 / 18 — *Decision 3 of 5: LangGraph wiring is completed, not introduced*

---

### Q4: Where exactly does PHI cross out of OpenEMR? Walk us through the boundaries.

**Answer.** There are three trust boundaries, not one, and the load-bearing exception is at the third — pretending otherwise would be dishonest. (1) **Browser ↔ OpenEMR PHP host** is session + CSRF authed; PDF upload, PDF preview rendering for the citation overlay, and any clinician-facing PDF view all live here. The browser never talks to the sidecar for document bytes. (2) **OpenEMR PHP host ↔ Sidecar** is JWT-authed via the established internal-endpoint pattern (`oe-module-agentforge/public/internal/get_document_bytes.php`); the sidecar holds bytes in process memory only for the duration of one extraction call and never persists, renders, or logs them. (3) **Sidecar ↔ Anthropic** is HTTPS under assumed BAA — *rendered page images do leave the sidecar at this boundary.* This is the same dependency W1 already takes for chart-text reasoning, and it carries forward W1's own deferred production work (per [`ARCHITECTURE.md`](../ARCHITECTURE.md) Executive Summary tradeoff #1: "v1 uses cloud Claude under assumed BAA; the production-hardened path swaps to self-hosted vLLM"). We considered claiming that PDF bytes never leave OpenEMR — they don't leave OpenEMR's *byte-store*, but rendered page images do leave the sidecar to the model provider, and graders will hear the honest framing or notice the omission. Demo data is synthetic; the on-prem vLLM swap point is the LLM-client abstraction inherited from W1.

- **Arch link:** [`W2_ARCHITECTURE.md` §5.1 — PHI containment: what crosses which boundary](../W2_ARCHITECTURE.md); cross-reference [`W2_DEFENSE.md` decision 4 (boundary table)](../W2_DEFENSE.md)
- **Slide pointer:** 11 / 18 — *Decision 4 of 5: PHI containment — three boundaries*

---

### Q5: How do you know the eval gate actually catches the regression graders will introduce?

**Answer.** A separate test, [`sidecar/tests/eval/gate/test_gate_blocks_regression.py`](../sidecar/tests/eval/gate/test_gate_blocks_regression.py) (Task 19 in the W2 task plan), runs the full 50-case suite against a regressed adapter and asserts that the rubric category `citation_present` drops by more than 5% — i.e., that the build *would* fail. The regressed adapter strips the citation off a clinical claim (a fabricated `A1c = 15.5%` response with the supporting Citation deliberately removed) rather than fabricating a value outright; this is because the W2 harness's `_JUDGE_BY_CATEGORY` only routes `HALLUCINATION` / `REFUSAL` `EvalCategory` values to the LLM judge, and the W2 50-case suite uses `extraction` / `evidence_retrieval` / `citations` / `refusal` / `missing_data` — so the load-bearing failure surface is the programmatic `citation_present` grader, not `factually_consistent`. The test runs as a separate gate-validation job (it always fails by design) and is also runnable by graders directly on demand. We considered the alternative — seeding intentionally-broken cases into the 50-case golden set — and rejected it because mixing pass and fail expectations into one dataset makes the rubric ambiguous and invites future engineers to "fix" the broken cases. **The baseline the gate bites against is now measured, not stubbed:** the 2026-05-09 regen of `python -m agentforge.eval.regenerate_baseline` against the production model mix landed at `sidecar/tests/eval/baselines/week2.json` with `_meta.status: "measured"`, total LLM spend `$1.54`, and per-category rates `extraction 0.417 / citations 0.500 / evidence_retrieval 0.500 / missing_data 0.600 / refusal 0.375`. The gate's correctness story is therefore two-leg: the self-test proves the gate's regression-detection logic bites, and the measured baseline proves the gate is calibrated against actual agent behaviour rather than an idealised stub.

- **Arch link:** [`W2_ARCHITECTURE.md` §6.5 — Gate self-test (the "graders will introduce a regression" requirement)](../W2_ARCHITECTURE.md); see also §6.4 for the PR-blocking gate plumbing
- **Slide pointer:** 15 / 18 — *Eval gate · How we know the gate bites*

---

### Q6: Why default to a local reranker (`bge-reranker-base`) instead of Cohere Rerank?

**Answer.** Three reasons: cost predictability (no per-call cloud spend), no API-key requirement in CI (a third API key on top of Anthropic and the JWT pair would be operational drag), and the spec text explicitly accepts "Cohere Rerank or equivalent." The reranker sits behind a `Reranker` Protocol with three implementations (`CrossEncoderReranker` default, `CohereReranker` opt-in via `COHERE_API_KEY`, `PassthroughReranker` for ablation), and the eval suite includes a passthrough-vs-rerank ablation pair so the rerank step's contribution is *measured* before we commit to a cloud reranker. We considered defaulting to Cohere — the rerank quality is generally higher — and rejected it because the `Reranker` interface gives us the option later without the day-one operational cost; if the ablation surfaces a >5pt `factually_consistent` regression on the local reranker, flipping to Cohere is a one-env-var swap. **Sizing caveat to flag honestly:** the Task 21 spec sized the pre-baked `bge-reranker-base` weights at ~280 MB; the as-shipped on-disk size is ~1.1 GB (~1.2 GB total image delta with `all-MiniLM-L6-v2`). The 280 was the model's parameter count being read as megabytes — the fp32 safetensors are ~1.1 GB regardless of format (per [`docs/DEVIATIONS.md` 2026-05-08 — Sidecar image delta is ~1.2 GB, not the spec's ~370 MB](./DEVIATIONS.md)). This surfaces as a deployment-cost note rather than an architectural change: the Cohere reranker remains the network-gated alternative for size-constrained environments, and the env-var swap path is unchanged.

- **Arch link:** [`W2_ARCHITECTURE.md` §4.3 — Reranker abstraction](../W2_ARCHITECTURE.md); packaging in [`§4.5 — Dependency and packaging plan`](../W2_ARCHITECTURE.md)
- **Slide pointer:** 14 / 18 — *Hybrid RAG · BM25 + dense + rerank, behind an interface*

---

### Q7: Why doesn't W2 ship the promotion-write-back UI for the suggested intake-form changes? You already have the data.

**Answer.** Promotion is a real clinical workflow with audit and co-sign requirements, not a one-click "apply" button — the right design needs a review surface, a per-field accept/reject, an audit-log shape that survives MAC compliance review, and probably a co-sign step depending on facility policy. None of that is one-week scope, and shipping a half-built version would be worse than not shipping it: a rushed promotion path is exactly the AI-mediated chart-corruption risk Q1's invariant exists to prevent. We considered shipping a minimal "accept all" affordance and rejected it because the failure mode (clinician clicks accept under time pressure, OCR error becomes charted allergy) is precisely what the persistence policy was designed to forbid. W2's contract is therefore explicit: the agent extracts, the panel surfaces "Suggested updates from intake form" with citations to the form region, and the clinical record is updated by humans. Promotion-write-back is enumerated in the deferred-work list and is the natural first piece of post-W2 scope.

- **Arch link:** [`W2_ARCHITECTURE.md` §9 — What's deferred (and why)](../W2_ARCHITECTURE.md); also [`§2.3 — Invariant I-2`](../W2_ARCHITECTURE.md) for the policy this preserves
- **Slide pointer:** 5 / 18 — *Decision 1 tradeoff callout (intake-form panel surfaces suggestion; promotion is post-W2)*

---

## Likely follow-up questions

The seven above are the canonical defense set. The five below are the questions a grader is likely to ask next, ordered by how often they tend to come up in defenses of this shape.

### Q8: What if the VLM hallucinates a bounding box?

**Answer.** The `Citation` Pydantic class ([`sidecar/src/agentforge/schemas/citation.py`](../sidecar/src/agentforge/schemas/citation.py)) carries a `@model_validator` that rejects any `LAB_PDF` or `INTAKE_FORM` citation whose `page_bbox` is missing or whose `bbox_confidence < 0.7`. Low-confidence fields can only land in the extraction's `unsupported_fields` list, never as a structured value. Eval case `extraction-bbox-degraded-scan` exercises this on a deliberately-blurred page and fails the run if a low-confidence bbox slips through. The validator is the load-bearing piece — it means the citation contract is checked before any clinical surface ever sees the value, and "VLM hallucinated a bbox" cannot manifest as a falsely-confident citation in the UI.

- **Arch link:** [`W2_ARCHITECTURE.md` §2.4 — Citation contract](../W2_ARCHITECTURE.md); validator code lives in §2.2 schema block
- **Slide pointer:** 13 / 18 — *The contract · One Pydantic class makes the rest verifiable*

### Q9: How is PHI contained in the extraction prompts themselves?

**Answer.** Prompt-body redaction is enforced at the `LangfuseClient` adapter, not at call sites — that's deliberate, so a future tool addition cannot accidentally leak. Extraction calls log latency, model, input/output token counts, schema-validation result, extraction confidence, page count, and unsupported-fields list, but the prompt body and the response body are stripped before they leave the sidecar. The eval rubric category `no_phi_in_logs` validates this by inspecting trace exports for known synthetic-PHI patterns (synthetic SSN, synthetic DOB shapes); the gate fails if any leak through. We considered redacting at call sites and rejected it as fragile — the adapter-level redaction makes the policy property of the *transport*, not the discipline of the caller.

- **Arch link:** [`W2_ARCHITECTURE.md` §5.2 — No PHI in logs](../W2_ARCHITECTURE.md)
- **Slide pointer:** 11 / 18 (covered alongside the three-boundary diagram)

### Q10: What's the failure mode for the LangGraph wiring, and what's the recovery path?

**Answer.** The risk surfaces as the nine W1 regression locks (`sidecar/tests/eval/regression_locks.py`) breaking against the new graph. The mitigation is sequencing: the graph wiring is the *first* W2 milestone (§10), and no new tools or RAG land until the locks are green. If the locks won't pass against the graph in the time budget, we fall back to a hand-written routing function on top of the W1 iterative tool loop — the spec explicitly accepts "another inspectable orchestration framework." Either way, inspectability comes from the explicit handoff spans logged to Langfuse (`route_decision`, `route_reason`, `from_node`, `to_node`, `iteration`), which are framework-agnostic.

- **Arch link:** [`W2_ARCHITECTURE.md` §3.1 + §3.3 (inspectability spans) + §8 risk #3](../W2_ARCHITECTURE.md)
- **Slide pointer:** 9 / 18 (Decision 3) and 10 / 18 (data-flow · supervisor routing)

### Q11: Why GitLab CI when the repo already has ~30 GitHub Actions workflows?

**Answer.** Per [`CLAUDE.md`](../CLAUDE.md) ("Issues live as GitLab issues at labs.gauntletai.com") and the spec deliverable ("GitLab Repository"), GitLab is the W2 grader-facing target — the authoritative PR-blocker for the challenge submission. The existing GitHub Actions surface is preserved (it gates upstream OpenEMR codebase quality and the AgentForge module's isolated tests), and we *also* add the eval suite as a new GitHub Actions job (`.github/workflows/agent-eval.yml`) for parity, so commits pushed to either remote run the same gate against the same script and config. The two CI surfaces cannot drift because they read the same `eval_config.yaml` and the same case set.

- **Arch link:** [`W2_ARCHITECTURE.md` §6.4 — PR-blocking gate](../W2_ARCHITECTURE.md)
- **Slide pointer:** 15 / 18 (eval-gate slide; mirrors are noted in the speaker notes), 16 / 18 (data-flow · what happens when an MR triggers the gate)

### Q12: What if the local reranker is materially worse than Cohere on real queries?

**Answer.** The eval suite already includes the ablation pair (passthrough vs rerank) as a measurement step, not just as a once-off experiment. If the local reranker shows a >5pt drop on `factually_consistent` against a Cohere baseline, flipping to Cohere is a one-env-var swap (`COHERE_API_KEY` populates, the `Reranker` factory selects `CohereReranker`, the cross-encoder weights remain pre-baked but unused). We accept the risk that the ablation surfaces a meaningful gap; we don't accept the operational cost of provisioning a third API key for CI before the data justifies it.

- **Arch link:** [`W2_ARCHITECTURE.md` §4.3 — Reranker abstraction](../W2_ARCHITECTURE.md); also [`W2_DEFENSE.md` Hybrid-RAG-at-a-glance](../W2_DEFENSE.md)
- **Slide pointer:** 14 / 18 — *Hybrid RAG · BM25 + dense + rerank, behind an interface*

### Q13: Are the eval rates measured or estimated?

**Answer.** Measured. The 50-case W2 suite ran end-to-end against `claude-haiku-4-5-20251001` (vision + text) and `claude-sonnet-4-6` (judge) on **2026-05-09**; total spend **$1.54** ($1.00 text over 142 calls, $0.54 vision over 10 calls; ~50 minutes wall-clock sequential). Per-category pass rates landed at **extraction 0.417, citations 0.500, evidence_retrieval 0.500, missing_data 0.600, refusal 0.375**, captured at [`sidecar/tests/eval/baselines/week2.json`](../sidecar/tests/eval/baselines/week2.json) with `_meta.status: "measured"`. The gate now blocks any regression from these numbers. Two documented limits explain why the rates sit where they do: (a) `SupervisorAdapter` is intake-only, so lab-PDF cases hit the wrong contract — fix is wiring the lab extractor; (b) Sonnet judge calibration is fresh, not yet recalibrated. Both are non-blocking follow-ups; the gate will tighten as they're addressed.

- **Arch link:** [`W2_ARCHITECTURE.md` §6 — Eval as deliverable](../W2_ARCHITECTURE.md); see also [`docs/w2-cost-latency-report.md` §"Measured baseline (2026-05-09)"](./w2-cost-latency-report.md)
- **Slide pointer:** 12 / 18 — *Eval gate as the deliverable*

### Q14: Why aren't the rates higher?

**Answer.** Because we measured honestly instead of pinning a stub at 1.0. The two caveats from Q13 account for ~13 of the 22 misses: (a) the eight lab-PDF cases (lipid panel, CBC, CMP, hba1c) hit the wrong contract because `SupervisorAdapter` only wires the intake `VisionExtractor` today — that is 7 of the 7 extraction failures and 5 of the 5 citation failures by itself; (b) the refusal grade is the first time the real `claude-sonnet-4-6` LLM judge has scored those cases (the stub never exercised the judge end-to-end), so the 0.375 likely reflects judge-prompt calibration drift rather than agent collapse. After the lab-extractor wiring lands and a judge-calibration pass runs, we expect the rates to lift; until then, the gate's job is *regression detection from the measured anchor*, not a quality claim about the agent.

- **Arch link:** [`docs/w2-cost-latency-report.md` §"Measured baseline (2026-05-09)"](./w2-cost-latency-report.md) — per-category table + the same two caveats; baseline `_meta.notes` carries the canonical text
- **Slide pointer:** 12 / 18 (eval gate slide) and 15 / 18 (gate self-test slide)

### Q15: What's the W2 gate's correctness claim?

**Answer.** Two-leg. **(1)** The gate self-test ([`sidecar/tests/eval/gate/test_gate_blocks_regression.py`](../sidecar/tests/eval/gate/test_gate_blocks_regression.py), Task 19) deliberately regresses an adapter via citation strip and verifies the gate catches it — this proves the gate's regression-detection logic is sound, independent of any baseline. **(2)** The baseline at [`sidecar/tests/eval/baselines/week2.json`](../sidecar/tests/eval/baselines/week2.json) is anchored against an actual `$1.54` 2026-05-09 run (`_meta.status: "measured"`, `_meta.cost_usd: 1.538`) — this proves the gate is calibrated against real agent behaviour, not an idealised stub. Either leg alone is partial: the self-test without a measured baseline can only prove the *plumbing* bites; the measured baseline without a self-test can only prove the *threshold* is real. Together they cover both "gate works" and "gate measures the right thing" — which is the correctness claim the W2 spec asks for.

- **Arch link:** [`W2_ARCHITECTURE.md` §6.4 (PR-blocking gate) + §6.5 (gate self-test)](../W2_ARCHITECTURE.md)
- **Slide pointer:** 15 / 18 — *Eval gate · How we know the gate bites*

---

## Quick-reference table — topic to slide

| Topic | Slide | Arch §  |
|---|---|---|
| TL;DR — what W2 adds | 2 / 18 | Executive Summary |
| Whole-system shape | 3 / 18 | §0–§3 (overview) |
| Five load-bearing decisions overview | 4 / 18 | Executive Summary |
| QuestionnaireResponse decision | 5 / 18 | §2.3 invariant I-2 |
| Intake-form data flow | 6 / 18 | §2.1, §2.3 |
| `procedure_*` decision | 7 / 18 | §2.3 invariant I-1 |
| Lab-PDF data flow | 8 / 18 | §2.1, §2.3 |
| LangGraph completion decision | 9 / 18 | §3.1 |
| Supervisor-routing data flow | 10 / 18 | §3.2, §3.3 |
| PHI containment (three boundaries) | 11 / 18 | §5.1 |
| Eval gate as the deliverable | 12 / 18 | §6 |
| Citation contract (Pydantic) | 13 / 18 | §2.2, §2.4 |
| Hybrid RAG / reranker | 14 / 18 | §4.2, §4.3 |
| Gate self-test ("the gate bites") | 15 / 18 | §6.5 |
| MR-triggered gate run flow | 16 / 18 | §6.4 |
| Anticipated questions | 17 / 18 | this primer |
| What "done" looks like Sunday noon | 18 / 18 | §11 |

---

## Open loose ends at primer authoring time

These are the items a careful grader will flag; surface them honestly rather than wait for the question.

- **Measured baseline landed; two follow-ups remain.** [`sidecar/tests/eval/baselines/week2.json`](../sidecar/tests/eval/baselines/week2.json) is now `_meta.status: "measured"` (2026-05-09 regen, $1.54 spend, per-category rates extraction 0.417 / citations 0.500 / evidence_retrieval 0.500 / missing_data 0.600 / refusal 0.375; gate verdict PASS). Two known shortcuts hold the rates below where they should land: **(a) `SupervisorAdapter` is intake-only** — it wires only the intake `VisionExtractor`, so the eight lab-PDF cases (lipid panel, CBC, CMP, hba1c) hit the wrong contract; wiring the lab extractor through the supervisor should lift extraction and citations materially without any agent change. **(b) Sonnet judge calibration drift** — refusal cases now route through the real `claude-sonnet-4-6` judge for the first time; a calibration pass against golden-labelled refusals is the natural next step before tightening the threshold. Both are non-blocking; the gate already bites against the measured anchor.
- **Judge routing limitation.** The LLM judge's `factually_consistent` category fires only for `HALLUCINATION` / `REFUSAL` `EvalCategory` values; the W2 case suite uses `extraction` / `evidence_retrieval` / `citations` / `refusal` / `missing_data`. The gate self-test (Q5) catches *citation-strip-shaped* fabrications via the programmatic `citation_present` grader — which is the right load-bearing claim for the gate's correctness guarantee — but extending the judge routing for genuine value-fabrication coverage is a documented follow-up.
- **Slide stash `presession-slides-WIP` is unmerged.** Slide pointers above match the as-committed deck. If the stash is popped before the defense, re-verify slide numbers — a one-pass `grep -n "slide-number" docs/w2-defense-slides.html` is sufficient.
- **Operational deferreds.** `GLAB_TOKEN` masked CI variable on the GitLab project (Task 20's MR-comment posting); GitHub branch-protection required-status-check on the public mirror; pre-baked sidecar Docker image registry path (for the future real-LLM manual eval job — current CI runs the gate on `python:3.12-slim` with a mocked supervisor since the mocked path doesn't load HF weights).

---

*Last updated 2026-05-09. Author: Cameron Candelori (with `Assisted-by: Claude Code`).*
