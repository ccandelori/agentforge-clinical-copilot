# AgentForge — W2 Final-readiness manual test plan

Sequenced manual test plan that exercises every callout in the Early
Submission feedback (stable deploy, ingestion, retrieval, citation
enforcement, eval gates + regression blocking, PHI-safe logging,
latency, runtime observability, retrieval grounding/reranking, bbox +
source tracing, EMR consistency, demo walkthrough surfaces). Designed
to run end-to-end in ~2 hours; each section stands alone.

**Live URL:** [https://143.244.157.90:9300/dashboard/](https://143.244.157.90:9300/dashboard/)
**Login:** `admin` / `LESZoHXpasV3LL9LP5uQjWs2`

**Two persona pools — they exercise different code paths:**

**Pool A — Synthea-rich personas** (use for dashboard cards, chart Q&A, guideline RAG; have full pre-loaded clinical data):

| Persona | pubpid | pid | Allergies | Problems | Meds | Vitals | Encounters |
|---|---|---|---|---|---|---|---|
| Eula461 Crist667 | `8` | 8 | 11 | 55 | 13 | 6 | 92 |
| Nichelle912 Johnston597 | `22` | 22 | 6 | 43 | 12 | 1 | 55 |
| Ed239 Reilly981 | `7` | 7 | 9 | 42 | 10 | 1 | 53 |

(Synthea names carry numeric suffixes — that's a Synthea convention, not a bug. Live patient_data names can be `UPDATE`d if you want polished display names for recording.)

**Pool B — W2 demo personas** (use for intake-form / lab-PDF upload + Care Team card; intentionally have **no pre-loaded clinical data** — they're fresh-patient shells per `scripts/seed-demo-patients.php`. Empty AllergyIntolerance / Condition / MedicationRequest cards are correct behavior, not a bug):

| Persona | MRN | pid | Notes |
|---|---|---|---|
| Margaret Chen | `MRN-2026-04481` | 29 | Typed PDF — primary demo + Care Team seeded |
| James Whitaker | `MRN-2026-04492` | 30 | Typed PDF — fallback + Care Team seeded |
| Sofia Reyes | `MRN-2026-DEMO-03` | 31 | PNG — chart-only, no bbox-overlay demo + Care Team seeded |
| Robert Kowalski | `MRN-2026-DEMO-04` | 32 | PNG — chart-only + Care Team seeded |

**Critical distinction:** the **intake-form upload persists a `QuestionnaireResponse`** but does NOT promote into `AllergyIntolerance` / `Condition` / `MedicationStatement` (the "promote to chart" pipeline is the deferred T38.12 gap). So uploading an intake form for Chen will show up in `questionnaire_response` but won't populate the standard FHIR cards. To see populated cards, you must use a Pool A persona.

**Mapping legend** (right column of each table):
`[D]` deployed app · `[I]` ingestion · `[R]` retrieval · `[C]` citations ·
`[E]` eval · `[O]` observability · `[L]` latency · `[P]` PHI-safe logging ·
`[B]` bbox / source tracing · `[S]` supervisor / orchestration ·
`[CI]` CI gates blocking

---

## 1. Pre-flight smoke — 5 min   `[D]`

```bash
./scripts/deploy-droplet.sh check
ssh root@143.244.157.90 'docker ps --format "{{.Names}} {{.Status}}"'
```

**Expect:** sidecar `Up Xm (healthy)`; openemr / mysql / phpmyadmin /
redis all `Up`. Health body `{"status":"healthy","policy_loaded":true}`.

Then in the browser: navigate to the live URL → accept cert → log in.

**Pass:** dashboard SPA loads, you land on patients view; no console
red-flags.
**Fail:** capture network tab + sidecar logs.

---

## 2. Dashboard surface — 15 min   `[D]`

**Two passes — different code paths.**

### 2a. Standard FHIR cards (Pool A — Synthea-rich) — 10 min

Open **pid 22** (Nichelle912 Johnston597) — recommended; or pid 8 if you want maximum data. For each card, confirm it renders populated:

| Card | Source | Pass criteria |
|---|---|---|
| Patient header | FHIR `Patient` | name, DOB, MRN, sex |
| Allergies | FHIR `AllergyIntolerance` | ≥1 row populated |
| Problem List | FHIR `Condition` (problem-list) | ≥1 row |
| Medications | FHIR `MedicationRequest` (active) | ≥1 row |
| **Prescriptions** (P1.3) | FHIR `MedicationRequest` (completed/stopped/on-hold) | ≥1 row OR honest empty state |
| Lab Results | FHIR `Observation` (laboratory) | ≥1 row |
| Vitals (ride-along) | FHIR `Observation` (vital-signs) | ≥1 row |
| Encounters (ride-along) | FHIR `Encounter` | ≥1 row (Pool A pid 22 has 55) |

**Known Synthea data quirks (per `project_dashboard_data_gaps` memory — correct behavior):**
- Problem List items are all `category=encounter-diagnosis` rather than `problem-list-item` for Synthea data; if the dashboard filters strictly on the latter you'll see fewer rows than the raw count
- Active Meds may render fewer rows than expected because Synthea data is mostly `status=completed` (these flow into Prescriptions instead)
- Lab observations have no `interpretation` / `referenceRange` — render plain values
- **Don't loosen filters reflexively** — the empty/sparse state IS the data

### 2b. Demo personas — empty-by-design verification + Care Team — 5 min

Open Chen (pid 29), then Whitaker (pid 30). For each, confirm:

| Card | Pass criteria |
|---|---|
| Patient header | Renders with the persona's name/DOB/MRN |
| Allergies / Problem List / Medications / Prescriptions / Labs / Vitals / Encounters | **EMPTY — that's correct.** These personas are fresh-patient shells; no FHIR data was ever loaded. (`scripts/seed-demo-patients.php` docstring: "Creates four minimal patient shells.") |
| **Care Team** (P1.3 seed) | Chen=3 members, Whitaker=3 members; roles render (Physician / Nurse Practitioner / Case Manager / Social Worker) |

Then on Reyes (pid 31) + Kowalski (pid 32): confirm Care Team renders with 2 members each.

**Fail mode for Care Team:** if it's empty, the seed didn't take — re-run:
```bash
ssh root@143.244.157.90 'docker exec development-easy-mysql-1 mariadb -uopenemr -popenemr openemr -e "source /tmp/care_team.sql"'
```

**Fail mode for FHIR cards on Pool A (pid 22):** if cards are empty for a Synthea-rich persona, that IS a regression — check sidecar logs + FHIR endpoint directly.

---

## 3. Chart Q&A + W2 citations — 10 min   `[R][C][S]`

**Hard reload first** (Cmd+Shift+R) — first turn after the citation-shape
merge.

**Use a Pool A persona (pid 22 or pid 8)** — Chen has no chart data so chart Q&A would have nothing to cite. With **"Ask guidelines" toggle OFF**, in the drawer ask:

1. *"What allergies does this patient have?"*
2. *"Summarize this patient's active medications."*
3. *"Has this patient been seen in the last year?"*

For each turn, expect:

| Surface | Pass criteria | Maps to |
|---|---|---|
| Streaming response | tokens stream within ~2-3s of send (chart Q&A budget) | `[L]` |
| Citation pills | render with **`source_type`** (e.g. `OPENEMR_RECORD` or `FHIR`), `source_id`, optional `field_or_chunk_id` mono caption | `[C]` |
| Pill content | NOT the W1 shape (no `excerpt:` / `kind:` / `provenance:` labels) | `[C]` |
| Click pill | drills to source — FHIR resource ID resolvable in the chart | `[B]` |

**Fail mode:** if citations render with old labels OR pills are empty,
the SPA bundle didn't refresh — hard reload again. If the sidecar
emits empty citations, check `docker logs --tail 50 agentforge-sidecar`
on droplet.

---

## 4. Guideline RAG — 10 min   `[R][C][S]`

Stay on the Pool A persona, **toggle "Ask guidelines" ON**. Ask:

1. *"How should I manage CKD stage 3?"*
2. *"What are the JNC8 / ACC-AHA blood-pressure targets for this patient?"*
3. *"What's the diabetes screening recommendation given this patient's profile?"*

| Surface | Pass criteria |
|---|---|
| Routing | response references guideline content (not just FHIR rows) — proves planner routed through `EvidenceRetriever` node |
| Citation pills | at least one pill with `source_type: GUIDELINE`, `source_id` matching a doc in `sidecar/data/guidelines/`, `page_or_section` populated |
| Citation latency | retrieval-augmented turn ≤ ~5-7s warm |

**Known parser gap (out of scope, pre-existing):** if `chunk_id`
contains `::`, the bracket-tag regex drops the citation silently. If a
guideline reference is missing from pills despite being mentioned in
the synthesizer text, this is the most likely cause — note as data,
not a regression.

**Fail mode:** if NO guideline pill appears for any of the 3 questions,
check droplet env:
```bash
ssh root@143.244.157.90 'docker exec agentforge-sidecar env | grep RETRIEVER'
```
should be `EVIDENCE_RETRIEVER_ENABLED=true`.

---

## 5. Intake-form ingestion + bbox overlay — 15 min   `[I][C][B][S][L]`

On Chen:

1. Click paperclip → attach `week2/example-documents/intake-forms/p01-chen-intake-typed.pdf`
2. Send: *"Extract this intake form."*

| Surface | Pass criteria | Maps to |
|---|---|---|
| Extraction wall-clock | first-call cold ~12-15s; subsequent ~8-10s | `[L]` |
| Chat reply | one or two sentences ("Extracted N fields…") — NOT a re-listing of every field (verifies P4 synthesizer-defer-to-panel landed) | `[I]` |
| `ExtractionPanel` below bubble | renders demographics + medications + allergies + family history | `[I]` |
| Field labels | **humanized** ("Date of Birth" not `date_of_birth`, "MRN" not `mrn`) | `[I]` |
| **"View source (N)"** button | N matches the count of bbox-bearing fields | `[B]` |
| Click "View source" | modal opens, original PDF renders, **blue rectangles overlay extracted regions** | `[B]` |
| Bbox accuracy | rectangles in the right region (may be a row off — Haiku is approximate, documented) | `[B]` |
| Persistence | run the SQL below; last row should be `questionnaire_id='agentforge-intake-form'`, `patient_id=29` (Chen), `status='completed'` | `[I][S]` |

```bash
ssh root@143.244.157.90 'docker exec development-easy-mysql-1 mariadb -uopenemr -popenemr openemr -e "SELECT id, questionnaire_id, patient_id, status FROM questionnaire_response ORDER BY id DESC LIMIT 3;"'
```

Then repeat with Whitaker (`p02-whitaker-intake.pdf`) to confirm
the path is reproducible.

**DON'T attempt PNG personas** (Reyes / Kowalski) here — DocumentViewer
can't render PNG, documented gap.

---

## 6. Lab PDF ingestion + bbox overlay (P1.2 — newly shipped) — 10 min   `[I][C][B][S]`

On any persona:

1. Click paperclip → attach a lab PDF (use `week2/example-documents/labs/<name>.pdf`)
2. Send: *"Extract this lab report."*

| Surface | Pass criteria | Maps to |
|---|---|---|
| Routing | sidecar's `intake_extractor_node` dispatches to **lab** branch (P1.2 dispatch); verify via `ssh root@143.244.157.90 'docker logs --tail 100 agentforge-sidecar 2>&1 \| grep -iE "lab\|doc_type"'` | `[I][S]` |
| **`LabPanel`** (NOT `ExtractionPanel`) renders | shows `values[]` rows: test name, value, unit, reference range | `[I]` |
| **Discriminator fix verified** | the panel that renders is unmistakably the lab one — table layout, not the demographics/groups layout | `[I]` |
| **"View source (N)"** | bbox overlay on lab PDF — values highlighted | `[B]` |

**Known limitation:** `SupervisorAdapter` is intake-only in eval, so
eval rates for lab-PDF cases dragged down (documented in baseline
`_meta.notes`). The runtime path here works; the eval scoring is the
gap. Don't conflate.

---

## 7. Source-tracing reliability — 10 min   `[C][B]`

Pick one turn from each section above (chart Q&A, guideline RAG,
intake extraction, lab extraction). For each citation pill that
renders, verify bidirectional traceability:

| Citation type | Trace-back action | Pass |
|---|---|---|
| `OPENEMR_RECORD` (FHIR) | `source_id` matches a real FHIR resource id; query it via the curl below | resource exists, fields match what the agent claimed |
| `GUIDELINE` | `source_id` matches a file under `sidecar/data/guidelines/`; `field_or_chunk_id` matches a chunk identifier | content of chunk supports the agent's claim |
| `INTAKE_EXTRACTION` (after intake upload) | `source_id` matches the extraction's document id; "View source" overlay points at the same field | bbox lands on the same value the citation quotes |
| `LAB_EXTRACTION` (after lab upload) | same as above for lab | bbox + value match the citation |

```bash
ssh root@143.244.157.90 'curl -ks https://localhost:9300/apis/default/fhir/<Type>/<id>'
```

**Pass:** every citation either has a verifiable source OR is honestly
absent (no fabricated pills with empty `source_id`).
**Fail:** any pill with non-verifiable `source_id` is a
citation-enforcement break — capture the turn payload + sidecar logs.

---

## 8. Eval gate execution — 10 min   `[E][CI]`

From your local checkout:
```bash
cd sidecar
uv run pytest tests/eval/gate/ -q
```

| Pass criteria |
|---|
| All gate tests green |
| Includes `test_gate_blocks_regression.py` (the self-test) |
| The gate's comparison runs against the new **measured** baseline (`_meta.status: "measured"`) — open `sidecar/tests/eval/baselines/week2.json` to verify |

Then run the full eval-runner against the existing baseline (no LLM
cost — uses the recorded baseline):

```bash
cd sidecar
uv run python -m agentforge.eval.gate.cli  # or the canonical entry point
```

**Pass:** exit 0, "0 violations."
**Fail:** capture the violation report.

---

## 9. Eval gate self-test (regression-injection) — 5 min   `[E][CI]`

The gate self-test deliberately strips a citation from a probe response
and verifies the gate catches it. Run:

```bash
cd sidecar
uv run pytest tests/eval/gate/test_gate_blocks_regression.py -m gate_validation -v
```

**Note the `-m gate_validation` marker — it's deselected by default;
without it pytest collects 0 tests** (documented protect-from-accidental-burn
behavior).

**Pass:** the test asserts the gate FAILS on the regressed adapter —
and it does.
**Fail:** if the gate passes when fed a regressed adapter, the gate's
correctness claim is broken.

---

## 10. CI gates blocking (verify in GitLab + GitHub) — 5 min   `[CI]`

1. **GitLab pipeline:** open the latest `main` pipeline at
   [https://labs.gauntletai.com/cameroncandelori/openemr/-/pipelines](https://labs.gauntletai.com/cameroncandelori/openemr/-/pipelines)
    — confirm the `agent-eval` job ran and passed.
2. **GitHub Actions:** open
   [https://github.com/ccandelori/agentforge-clinical-copilot/actions](https://github.com/ccandelori/agentforge-clinical-copilot/actions)
    — confirm the latest `agent-eval` workflow run is green.
3. **Optional but recommended:** open a throwaway MR on a scratch
   branch with a deliberately-broken citation parser (one-line edit) —
   confirm the gate **blocks** the merge. Revert before going further.

**Known operational gap:** `GLAB_TOKEN` not yet set as a CI variable,
so MR-comment-on-failure doesn't post (gate still blocks via job
status; just no inline comment). Set as masked variable in GitLab
project settings if you want it before Final.

---

## 11. PHI-safe logging — 5 min   `[P]`

Tail the sidecar log on the droplet:
```bash
ssh root@143.244.157.90 'docker logs -f --tail 0 agentforge-sidecar' &
```

In the dashboard, send a turn with simulated PHI in the prompt:
*"What's Margaret Chen's home address and SSN 123-45-6789?"*

| Pass criteria |
|---|
| Sidecar log entry for the turn is present |
| The literal PHI strings (`123-45-6789`, full names, MRNs) do **not** appear in log output |
| Either redacted (`[REDACTED]` / `***`) or replaced with structural placeholders |
| Patient identifiers in logs use opaque IDs (`patient_uuid` / `pid`), never `pubpid` (MRN) or full name |

**Fail mode:** if any PHI string appears verbatim in the sidecar log,
capture the line + send to `sidecar/src/agentforge/observability/redaction*.py`
for triage.

Kill the log tail (`fg`, then Ctrl+C) when done.

---

## 12. Latency budgets — 5 min   `[L]`

```bash
cd sidecar
uv run pytest -m latency -q
```

**Pass:** `test_internal_endpoints_p95_under_budget` (and any sibling
p95 budget tests) green.
**Fail:** capture which endpoint blew the budget; check whether the
droplet is hot or cold (cold-sidecar HF model load can push latency
by 3-5s on first turn).

Then in the dashboard: time three back-to-back chart Q&A turns.
**Pass:** turns 2 + 3 (warm) ≤ ~3-5s end-to-end; first turn after
sidecar restart can be slower (HF model load).

---

## 13. Observability traces — 5 min   `[O]`

If Langfuse is configured (env `LANGFUSE_*`):

1. Open the Langfuse project dashboard for AgentForge.
2. Send one turn of each kind: chart Q&A, guideline-RAG, intake
   extraction, lab extraction.
3. **Pass:** each turn surfaces a trace with:
   - `record_extraction_call` span (extraction turns)
   - `route_decisions` annotation (planner picks)
   - per-LLM-call cost + latency
   - per-node timing (planner / extractor / retriever / synthesizer / judge)

If Langfuse is NOT wired (using `NullLangfuseClient`): verify the
spans are at least logged structurally in the sidecar log:
```bash
ssh root@143.244.157.90 'docker logs --tail 200 agentforge-sidecar 2>&1 | grep -iE "span\|trace\|record_"'
```

---

## 14. Orchestration routing — 10 min   `[S]`

Tail the sidecar log while sending three intentionally-different turns:
```bash
ssh root@143.244.157.90 'docker logs -f --tail 0 agentforge-sidecar 2>&1 | grep -iE "planner\|route\|use_case\|worker"' &
```

Send:

1. *"What's this patient's allergy list?"* — chart Q&A → expect
   `route_decisions: chart_qa` or similar; planner should not invoke
   retriever/extractor.
2. (Toggle guidelines ON) *"What's the JNC8 BP target?"* — guideline
   RAG → expect retriever invoked.
3. (Upload an intake PDF) *"Extract this."* — extraction → expect
   intake/lab extractor invoked, then synthesizer.

**Pass:** each turn's planner picks the correct workers.

**Known gap:** the planner Haiku occasionally returns no `submit_plan`
tool call and falls back to `default_plan_for(use_case)` — log line
`planner LLM returned no submit_plan tool call; falling back`.
Acceptable; tracked as ~half-day fix (separate `PLANNER_MODEL` env,
pin to Sonnet). If you see this firing on EVERY turn, that IS a
regression.

Kill the log tail (`fg` then Ctrl+C).

---

## 15. Repo correctness — 10 min   `[D]`

1. Open [https://github.com/ccandelori/agentforge-clinical-copilot](https://github.com/ccandelori/agentforge-clinical-copilot)
    — verify README renders, all internal links resolve.
2. Click `PATIENT_DASHBOARD_MIGRATION.md` — defense doc renders;
   status table shows Prescriptions + Care Team as **done** (with
   the one-line notes from the P1.3 commit).
3. Click `docs/w2-defense-slides.html` — slides render in browser.
4. Click `docs/w2-demo-script.md` — demo script renders.
5. Click `docs/w2-cost-latency-report.md` — cost/latency report
   renders; "Measured baseline (2026-05-09)" section lists per-category
   rates.
6. Click `docs/defense-qa-w2.md` — Q&A primer; Q13/Q14/Q15 (measured
   baseline) present.
7. Click `docs/DEVIATIONS.md` — recent entries cover P1.1, P1.2, P1.3,
   P2.3 + the deploy-script gap.

**Pass:** every link works, no 404s, no rendered raw-markdown leaks.

---

## 16. (Final, post-recording) Demo audio + walkthrough sanity — 5 min

When recording the demo per `docs/w2-demo-script.md`:

| Checklist |
|---|
| Audio present + intelligible |
| Each section the script names is on screen + narrated: ingestion (intake + lab), retrieval (chart Q&A + guideline), citations (W2 pills + bbox), eval gate (run the self-test on camera), traces (Langfuse OR sidecar logs), orchestration (planner picking workers, visible in the route_decisions log) |
| Hard reload before first turn (W2 citation parser) |
| OAuth `client_secret` blurred / not on screen if the env file is ever opened |

---

## What this plan does NOT exercise

- **P2.1 real guideline corpus** — held; corpus stays demo-stub. If a
  grader presses on "are these real guidelines?" the answer is "honest
  stub, framed as such in `NOTICE.md`; ingestion pipeline is real and
  ready for production sourcing."
- **Any non-Chen / non-Whitaker bbox overlay** — Reyes / Kowalski are
  PNG; PDF.js can't render them. Acceptable.

---

## Failure-triage one-liners

| Symptom | First check |
|---|---|
| Citations have empty pills | Hard reload — cached W1 SPA |
| Care Team empty for all personas | Re-run seed: `ssh root@143.244.157.90 'docker exec development-easy-mysql-1 mariadb -uopenemr -popenemr openemr -e "source /tmp/care_team.sql"'` |
| Sidecar 502 / unreachable | `./scripts/deploy-droplet.sh check`; if container down, `./scripts/deploy-droplet.sh sidecar` |
| Dashboard 404 on a card | Hard reload; if persists, redeploy dashboard |
| OAuth login bounces to localhost | Apache vhost lost — re-inject per `docs/NEXT-SESSION.md` "Apache reverse proxy" |
| Eval gate fails locally | Check whether you're on `main` post-rebase; baseline shape may have changed |
| Intake/lab extraction 0 fields | Sidecar restart needed — HF weights may not have loaded; `./scripts/deploy-droplet.sh sidecar` then wait 60s |
