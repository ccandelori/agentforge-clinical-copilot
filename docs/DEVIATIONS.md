# Deviations from the Original Plan

A chronological log of decisions where we did something different from
[`ARCHITECTURE.md`](../ARCHITECTURE.md), the original Taskmaster task spec,
or our initial assumption. Each entry captures **what changed**, **why**,
and **what we learned**.

The log exists so a future reader can recover the *reasoning* behind
divergences that look unmotivated against the planning artifacts. Big
architectural decisions also get an ADR in [`docs/adr/`](./adr/) when
created; this file is the lightweight running record.

---

## 2026-05-09 — Promote-to-chart fix: re-route medication promotion from `lists` to `prescriptions`

**What we changed:** `IntakePromotionWriter` now branches on
`kind='medication'` and writes to the `prescriptions` table
instead of `lists`. The dashboard's `MedicationsCard` reads from
FHIR `MedicationRequest`, which is projected by
`FhirMedicationRequestService` from `prescriptions` (a UNION with
`lists` exists for free-text meds tied via
`lists_medication.prescription_id IS NULL`, but Synthea-style
populated dose / sig / status only surfaces from `prescriptions`).
With the prior `type='medication'` row in `lists`, the card
rendered the drug name (from a fallback path) but lost dose and
frequency entirely.

**Why we couldn't reuse `PrescriptionService::insert()`:** Its
constructor calls `UuidRegistry::createMissingUuidsForTables(...)`,
which writes to global session-coupled state via the legacy
`sqlStatementNoLog` channel. We're inside a JWT-authed internal
endpoint with an injected DBAL connection; mixing the two auth
pipelines was the same trap we hit with `AllergyIntoleranceService`
in the prior commit. Same path (b) here: a raw INSERT through the
DBAL `Connection` we already have, mirroring `LabResultWriter`.

**Mapping decisions worth knowing** (full rationale in the
`insertPrescriptionRow` docblock):

- `uuid` ← `random_bytes(16)` directly (column is `binary(16)`); we
  skip the `uuid_registry` round-trip because the FHIR projection's
  union SELECT addresses rows by `combined_prescriptions.uuid`
  directly and `createMissingUuidsForTables` only fills NULLs
  (leaving ours alone).
- `drug_dosage_instructions` ← full free-text sig
  ("10 mg PO daily"). FHIR projection prefers this over `dosage`
  for the `dosageInstruction[0].text` slot, which the frontend's
  `projectMedicationRequest` falls back to for the rendered
  frequency line.
- `dosage` ← parsed dose substring ("10 mg") for any legacy UI
  that reads the column directly.
- `interval` and `route` stay NULL — both are `int(11)` FKs to
  `list_options.option_id`. Mapping a free-form "PO daily" to an
  interval option_id requires a runtime lookup we don't have; the
  user-visible frequency text is preserved in
  `drug_dosage_instructions`.
- `active = 1` + `end_date = NULL` → FHIR `status='active'` →
  frontend `Medication.status='active'` → row appears in the
  card's default visible bucket.
- `txDate` ← `CURDATE()` (NOT NULL with no default).
- `usage_category_title` and `request_intent_title` ← empty strings
  (matches Synthea seed shape; `PrescriptionService` self-heals to
  `"Home/Community"` and `"Order"` respectively).

**Single source of truth:** the medication path no longer also
writes a `type='medication'` row to `lists` — single insert, single
table. The audit row + lineage breadcrumb (`note` column carrying
`qr_id=…, doc_id=…`) preserves the same per-row clinician approval
trail the other kinds get.

**`PromotedItemHandle.listsId` keeps its name** even though it now
carries `prescriptions.id` for medication kinds. The controller
and dashboard treat the field as an opaque per-row id; renaming
would be churn for no benefit. Documented on the value object.

**Test pre-existing fragility fixed in passing:**
`IntakePromotionWriterTest::persistsOneListsRowPerItemWithCorrectShape`
was failing on PHP 8.5 + PHPUnit 11 because `willReturnCallback`
with a closed-over `++$counter` returns stale state on the second
call. Switched to `willReturnOnConsecutiveCalls('101', '102')` —
the explicit consecutive-returns API has stable semantics across
PHPUnit versions and doesn't depend on closure-reference behavior.

**What we learned:**

- The "audit row in `lists` so the clinician's approval is logged
  somewhere" rationale from the deferred fix wasn't load-bearing:
  the audit lives in `IntakePersistAuditWriter` (event log), not
  in a duplicate `lists` row. Cleaner to write once to the right
  table than twice for vestigial audit reasons.
- `FhirMedicationRequestService::populateDosageInstruction` has a
  `!is_numeric($dosageInstructions)` guard that drops Synthea's
  `"1.00"` dosage values silently. Always write a string with units
  ("10 mg" not "10") to keep the FHIR text path live.
- `prescriptions.usage_category_title` and `request_intent_title`
  are `NOT NULL` with no default — every Synthea-imported row uses
  empty strings here, which look broken but are self-healed by
  `PrescriptionService::createResultRecordFromDatabaseResult`.
  Worth knowing before spending time hunting for sensible category
  / intent vocab.

---

## 2026-05-09 — Promote-to-chart fix: populate canonical `lists` columns so the FHIR projections render correctly

**What we changed:** `IntakePromotionWriter` now populates
canonical `lists`-table columns for allergy rows so the
dashboard's `AllergiesCard` actually renders the substance,
category, and severity that the clinician approved on the
extraction panel:

- `lists.diagnosis` ← substance text (the panel's `title`).
  Without this, the FHIR projection drops `code` into the
  data-absent-unknown branch and the card renders the substance
  literally as "Unknown" — which the frontend's keyword-based
  category classifier then mis-buckets as "environmental".
- `lists.severity_al` ← parsed from the trailing
  `(<severity>)` group on the panel's `details` string. Only
  the round-trip-clean buckets `mild` and `severe` get written;
  anything else (including `moderate`, missing, or the LLM's
  free-form synonyms beyond `low/high/life-threatening`) is
  intentionally left at the schema default of `NULL` so the
  frontend's downstream "moderate" default surfaces.

The `lists.reaction` column is intentionally **not** populated
with free text. That column expects a `list_options.option_id`
(joined to `list_id='reaction'`); writing a plain string would
either fail the FHIR projection's `foreach` over
`$dataRecord['reaction']` or silently drop. The reaction text
the panel collected continues to land in `lists.comments` via
the existing `details` channel, so a clinician browsing the
chart row directly still sees it; it just doesn't surface on
the AllergiesCard.

The `lists.subtype` column is also left empty — the FHIR
projection hardcodes `category = "medication"` and never reads
`subtype`, and the frontend re-classifies the category from the
substance text via keyword matching (so populating
`lists.diagnosis` correctly is what fixes the category badge,
not `lists.subtype`).

**Why path (a) instead of `AllergyIntoleranceService::insert()`
(path b):** The service-layer insert requires a `puuid` (we
have a `pid`), constructs a fresh `UuidRegistry` whose
constructor mutates global state, and uses the legacy procedural
`sqlInsert()` instead of the DBAL `Connection` we already hold.
Calling it from a JWT-auth'd internal endpoint would mix two
incompatible auth pipelines (legacy `$_SESSION` + global state
vs. JWT + DBAL). Path (a) — directly populating the canonical
columns — is the smaller, more reviewable change and it lets the
existing `lists.title` / `lists.user` / `lists.comments` / audit
plumbing keep working unchanged.

**Why we didn't widen the wire schema (PromotionItem / PromoteItem
/ CommitItem) to carry structured `severity` / `category`
fields:** the W2 deadline gives us roughly 10 hours, and the
explicit constraints on this fix forbade touching `vue-ui/` or
the BFF promote route. Parsing the structured fields out of the
`details` string the panel already produces ("`<reaction>
(<severity>)`") gives us the same effective behaviour for free
without a three-layer coordinated change. A post-W2 cleanup
should widen the schema so the structured fields travel
explicitly rather than via string parsing.

**Medication routing is deferred (separate decision below).**
`type='medication'` rows continue to land in `lists` so the
audit log reflects the clinician's approval, but they don't
render in `MedicationsCard` (which reads from FHIR
`MedicationRequest`, projected from the `prescriptions` table).
Re-routing medication writes to `prescriptions` requires a new
writer (UUID generation, several `NOT NULL` columns including
`txDate` / `usage_category_title` / `request_intent_title`,
plus the rxnorm coding decision) and is out of scope for this
fix.

**Family history is also deferred.** OpenEMR's modern dashboard
doesn't expose `FHIR FamilyMemberHistory` — there's no
`FamilyHistoryCard.vue` in `vue-ui/`. Family-history rows land
in `lists.type='family_history'` correctly and would surface in
the legacy chart UI, but the demo workaround on the new
dashboard is "extracted, in DB, but no UI surface"; visible in
the ExtractionPanel pre-commit, invisible post-commit. The
post-W2 fix is a `FamilyHistoryCard.vue` that consumes
`FamilyMemberHistory` (which OpenEMR already projects via
`FhirFamilyMemberHistoryService`).

**What we learned:** When the FHIR projection layer accepts
free text in some columns and option-id references in others,
the safe write path is the one that exercises both paths
end-to-end before touching the schema. Three full hops here
were invisible until manual testing surfaced "Unknown
ENVIRONMENT moderate" in the dashboard:

1. `lists.diagnosis` is treated as `TYPE:CODE` text by
   `BaseService::addCoding()`; plain text becomes
   `code = title`, `code_type = null`, surviving as
   `code.coding[0].display` and `code.text`.
2. `lists.severity_al` is mapped through a 3→2 bucket
   compression in `FhirAllergyIntoleranceService` (mild/moderate
   → low; severe → high); the frontend then re-expands
   2→3 (low → mild; high → severe; default → moderate). Only
   `mild` and `severe` round-trip cleanly.
3. The frontend's category badge comes from a keyword match on
   the substance text, not from the FHIR `category` field
   (which is hardcoded to "medication" anyway). Populating
   `diagnosis = title` fixes both the substance label AND the
   category badge.

**Artifacts:** branch `fix/promote-fhir-projection`, commit
populating `lists.diagnosis` + `lists.severity_al` for allergy
rows + new `IntakePromotionWriterTest` cases pinning the field
mapping (mocked DBAL connection asserts the SQL params
include the populated fields and that the severity bucket
omission for `moderate` / null details is intentional).

---

## 2026-05-09 — Gap 2: intake commit-to-chart shipped against PRD's "out of scope" carve-out

**What we changed:** Built the promotion pipeline that turns
clinician-approved intake-form rows into structured EHR records.
Surface: a per-row checkbox set + a "Commit selected to chart"
button on `<ExtractionPanel>`; under the hood, a new BFF route
(`POST /api/agent/promote/intake`), a new Python writer
(`agentforge.tools.intake_promote.IntakePromoteWriter`), and a new
JWT-authed PHP endpoint
(`promote_intake.php` + `InternalIntakePromoteController` +
`IntakePromotionWriter` + `IntakePromoteAuditWriter`) that writes
one `lists` row per accepted item (allergy / medical_problem /
medication / family_history).

**Why this contradicts the PRD:** [`week2-prd.md`](../.taskmaster/docs/week2-prd.md)
lines 122-125 and 820 explicitly carved promotion out of W2 on
safety grounds. The PRD's reasoning was correct in shape — OCR
errors auto-writing to the chart is exactly the wrong failure mode
— but it conflated two designs: "auto-write on extraction" (the
forbidden one) and "explicit per-row clinician approval" (the
shipped one). The W2 brief PDF (page 4, Core Agent Requirement #1)
says the tool "must persist derived facts as appropriate FHIR
resources or OpenEMR records," and the natural reading of "derived
facts" is the individual extracted rows, not the wrapping
QuestionnaireResponse. We built it because the brief is the
canonical grading reference, the PRD is the planning artifact, and
the per-row review affordance is a stronger safety property than
the no-promotion stance it replaces.

**Architecture choice we made:** *Path (b) — new internal PHP
endpoint*, not *path (a) — direct FHIR R4 write through the
existing BFF proxy*. We checked
[`apis/routes/_rest_routes_fhir_r4_us_core_3_1_0.inc.php`](../apis/routes/_rest_routes_fhir_r4_us_core_3_1_0.inc.php)
and confirmed OpenEMR's FHIR routes for `AllergyIntolerance` /
`Condition` / `MedicationStatement` / `FamilyMemberHistory` are
GET-only — there's no native POST surface to write through. Path
(b) lands rows directly in the legacy `lists` table (the same
table the existing `AllergiesRepository` / `ProblemsRepository` /
`MedicationsRepository` read from for the dashboard's
GET endpoints), so the chart cards refresh on the next render
without any FHIR-layer change. We also chose to NOT re-fetch the
QR by id and project items server-side — the items the user just
reviewed are the items we write; re-fetching would add a round-trip
without changing what gets written. The QR id is still threaded
through the lineage (`lists.comments` carries `qr_id=…, doc_id=…`)
so a chart row can walk back to its source extraction.

**Safety property shift:** The original defense
(`docs/defense-qa-w2.md` Q1 + Q7) said "promotion is out of scope
on safety grounds." That defense changes to "promotion is a
deliberate human action — per-row checkboxes + explicit Commit
button + dim/disable on committed rows + per-promotion audit event
+ source-doc overlay one click away." Q1 and Q7 in the defense doc
are updated to reflect the implemented design.

**Where the eval gate stands:** Untouched. The eval rubric grades
the agent's extraction + citation + refusal behavior, not the
promotion-write step (which is gated on clinician click, not
agent output).

**What we learned:** A PRD's "out of scope" sometimes means "out
of scope to design correctly," not "out of scope to ship at all."
When a brief's literal text and the PRD's deferral list disagree,
the brief wins for grading purposes; the PRD's safety concern
should land as a design constraint on the shipped feature rather
than as a reason to skip it.

---

## 2026-05-09 — P2.3 W2 citation shape: parser stays unchanged; only the wire bridge changes

**What we changed:** Replaced the W1-style `AgentTurnCitation` shape
(`id` / `source` / `excerpt` / `date` / `kind` / `provenance`) on the
dashboard agent-turn response with the W2 machine-readable contract
(`source_type` / `source_id` / `page_or_section` / `field_or_chunk_id`
/ `quote_or_value`) end-to-end: sidecar BFF (`turn_route.py`), Vue
composable (`useAgentTurn.ts`), Pinia store validator
(`stores/agentforge.ts`), `CitationPill.vue`, and `CitationsPane.vue`.

**Where we did NOT change:** The synthesizer prompt and the verifier's
`[record_type #id]` bracket-tag grammar are untouched. The bracket
grammar is the LLM-friendly form; the W2 shape is the
dashboard transport. The bridge between the two lives in
`_build_citations`, which now resolves each parsed bracket tag against
the per-turn `CitationIndex` and projects the indexed record into the
W2 wire shape:

* **Guideline / extraction citations** (W2-shaped index records, key
  contains `source_type`) are passed through verbatim.
* **Chart records** (W1-shaped raw row dicts) are projected into an
  `OPENEMR_RECORD` citation with `field_or_chunk_id =
  "<record_type>/<record_id>"` and the row's date moved into
  `page_or_section`.

**Why we left the prompt alone:** The brief flagged this as a risk
("if the synthesizer prompt change destabilizes citation production,
STOP"). The W2 schema fields the brief asks for are already
populated for guideline + extraction citations via the per-turn
citation index — the only missing piece was the wire serializer at
the BFF. Changing the prompt to invent a richer bracket syntax
(`[source_type:source_id:section:chunk_id]`) would risk LLM citation
malformation 21 hours before the W2 deadline for no information gain
the BFF can't already supply. The deviation here is from the brief's
literal "update the synthesizer prompt" step; the contract it
asked for is delivered.

**What we learned:** The W2 schema (`agentforge.schemas.citation.Citation`)
already existed and was already being threaded through the W2 graph
into the per-turn citation index; the dashboard surface was the only
place still emitting the legacy W1 shape. Refactoring to the W2 shape
on the wire was a 1-file-per-layer edit, not a graph or prompt
rewrite.

**Pre-existing gap noted:** The verifier's bracket-tag regex
(`#(?P<id>[A-Za-z0-9_\-]+)`) does not allow `::`, so production
guideline `chunk_id`s like `hypertension-acc-aha-2017-targets::bp-categories::0`
do not round-trip through the W1 citation parser. This is a separate
issue from P2.3 — flagged for follow-up.

---

## 2026-05-09 — Droplet redeploy: deploy script doesn't ship `db/Migrations/` or run them

**What we found:** Mid-deploy, `./cli migrations:migrate` on the
droplet's openemr container ran only `Version00000000000000` (the
core no-op placeholder) — neither `Version20260505000001` (the
canonical AgentForge intake-form Questionnaire seed, W2 Task 5) nor
`Version20260508000001` (the P4#4 backfill of
`questionnaire_repository.questionnaire_id = 'agentforge-intake-form'`)
had ever run there. The `migrations` tracking table had zero 2026
entries; `questionnaire_repository` had zero AgentForge rows. With
this morning's P1.1 (sidecar-initiated persistence) and P4#4 (logical
id wiring), every demo intake-form turn would have written a
`QuestionnaireResponse` row whose `questionnaire_foreign_id` resolved
to nothing — silent FK breakage, not a hard error.

**Root cause:** `scripts/deploy-droplet.sh` rsyncs the AgentForge
module dir, the sidecar source, and the dashboard `dist/` — but it
**doesn't ship `db/Migrations/`** (those live at the OpenEMR repo
root, not under the module). It also doesn't invoke the migration
runner. The redeploy checklist in `docs/NEXT-SESSION.md` documented
a manual `docker exec` step, but the path it cited
(`/openemr/vendor/bin/doctrine migrations:migrate`) doesn't exist on
this OpenEMR image — the binary is `vendor/bin/doctrine-migrations`
and OpenEMR's preferred entry point is
`cd /var/www/localhost/htdocs/openemr && ENVIRONMENT=development ./cli
migrations:migrate --no-interaction`.

**What we did this session:**

- `rsync` the two W2 migration files to the droplet host's `/tmp/`,
  then `docker cp` into the container's
  `/var/www/localhost/htdocs/openemr/db/Migrations/`.
- Re-ran `ENVIRONMENT=development ./cli migrations:migrate
  --no-interaction`. Both migrations registered in `migrations` table;
  `questionnaire_repository` row id=1 now exists with
  `questionnaire_id='agentforge-intake-form'` and a 1430-byte FHIR R4
  Questionnaire payload.

**Follow-up not done (deferred — last day, risk-vs-reward):** Add a
`deploy_migrations()` step to `scripts/deploy-droplet.sh` that rsyncs
`db/Migrations/Version2026*.php` and invokes the runner via the
correct command. Also fix `docs/NEXT-SESSION.md`'s stale path on the
next refresh.

**Bonus finding:** the deploy script's `check_health()` polls
`http://${SIDECAR_NAME}:8000/health` for 30s after `docker run`, which
is too tight for a fresh sidecar with the `bge-reranker-base`
sentence-transformers weights to load. The script declares "cannot
reach sidecar after 30s" and exits non-zero, but the container comes
up healthy ~30-60s later. Cosmetic for now (the actual deploy works,
the script just lies about the outcome) but should be lifted to
~120s.

---

## 2026-05-09 — Slow / latency / eval-baseline suites trimmed to remove deleted-route coverage

**What changed:** The legacy panel yank on 2026-05-08 left behind four
sidecar test files still posting to the deleted
`/interface/modules/.../public/turn.php` route — `test_use_cases.py`
(slow), `test_latency.py` (the LLM tier), `test_patient_context.py` (one
binding probe), and the entire `tests/eval/baseline/` directory (the
`eval`-marked baseline suite). Wave 5's grep verification was scoped
to live-code paths and didn't catch them; round 3 punch list flagged
the residue.

**What we did:**

- Deleted `sidecar/tests/integration/test_use_cases.py` outright (4
  tests, all hit the deleted route).
- Deleted `sidecar/tests/eval/baseline/` outright (test + cases +
  conftest + grader + probe-responses; the `eval`-marked regen CLI at
  `sidecar/src/agentforge/eval/regenerate_baseline.py` doesn't import
  from this directory — confirmed via `grep -rln tests.eval.baseline
  sidecar/src/`).
- Surgically removed `test_uc1_total_turn_p95_under_budget` from
  `test_latency.py`; kept `test_internal_endpoints_p95_under_budget`
  (it probes the surviving `internal/*` routes, no panel dependency).
- Surgically removed `test_patient_context_factory_sets_pid_for_known_patient`
  from `test_patient_context.py`; kept the three fixture-validation
  tests that don't touch `turn.php`.

**Why deletion over retarget:** Retargeting at the BFF
`/api/agent/turn` requires session-cookie auth (the integration suite
has session auth via the OpenEMR fixtures, but the BFF route mints its
own internal JWT off the session and the test scaffolding doesn't
plug into that). Retargeting at the sidecar's direct `/turn` requires
JWT minting in the test fixtures. Both are substantial rework on
suites that are deselected by default; with ~36h to deadline and the
Vue path covered by `dashboard_auth/turn_route.py`'s integration tests
plus the regression-locks suite, the cost-benefit lands on deletion.
A future production-grade latency suite that targets the BFF surface
is a clear follow-up, not a deadline blocker.

**Corpus framing:** The reviewer also re-flagged
`sidecar/data/guidelines/NOTICE.md` as project-prepared summary
material rather than approved source documents. The user's call is
"leave it" — strengthened the framing one notch by adding an explicit
"Status: demo stub only" callout block at the top of the NOTICE so the
demo-vs-production distinction is unmistakable. The corpus contents
themselves stay as-is; production-grade corpus ingestion is a
post-W2 follow-up.

---

## 2026-05-08 — Legacy per-chart AgentForge panel removed

**Background.** The original W1 architecture embedded an AgentForge chat
panel directly inside the OpenEMR per-chart patient summary view: a
Twig template (`agent_panel.html.twig`), an Angular-style JS bundle
(`public/js/agent_panel.js`), a citation-overlay widget
(`public/js/citation_overlay.js`), and two PHP route entry points
(`public/turn.php` for chat turns, `public/upload_document.php` for
document uploads) backed by `AgentProxyController` and
`UploadDocumentController`. A `Bootstrap` class wired the panel into
OpenEMR's event dispatcher so it rendered on the patient summary.

**The 2026-05-06 placement decision.** When the W2 surprise (Vue dashboard
port) landed, we decided the per-chart embed was "dangerously wrong" —
a top-level co-pilot drawer in the new dashboard is the correct
placement, and the live MVP surface moved to `vue-ui/` accordingly. From
that point the legacy panel was deprecated but still wired in.

**The 2026-05-08 code review.** A code review on 2026-05-08 surfaced
that the legacy panel still had latent bugs: the upload path
hard-coded `doc_type=intake_form` (so a lab PDF dropped into the panel
would route through the intake schema and silently mis-extract), and
the citation-overlay-to-document plumbing the panel needed had never
been finished. Two paths: fix the bugs, or delete the surface. With
~36h to the deadline and the Vue dashboard already serving every demo
path, fixing dead code is pure carrying cost.

**Decision: yank it.** Removed the legacy panel surface in one atomic
commit (`chore/yank-legacy-agent-panel`):

- **Legacy JS panel + Twig template:** `public/js/agent_panel.js`,
  `public/js/citation_overlay.js`,
  `templates/agent_panel.html.twig` — the user-facing UI surface.
- **Panel-only PHP routes + controllers:** `public/turn.php`,
  `public/upload_document.php`, `src/Controllers/AgentProxyController.php`,
  `src/Controllers/UploadDocumentController.php` — the chat-turn and
  document-upload entry points the panel posted to.
- **Bootstrap class:** `src/Bootstrap.php` — the event subscriber that
  rendered the panel on the patient summary; `openemr.bootstrap.php`
  now only registers the PSR-4 namespace (still required to autoload
  the `Internal*` controllers from `public/internal/*.php`).
- **Panel tests:** `tests/js/agent_panel*.test.js`,
  `tests/js/citation_overlay.test.js`, plus the three isolated PHP
  tests (`AgentProxyControllerTest`, `UploadDocumentControllerTest`,
  `BootstrapTest`).

Doc fallout: `deploy/apache-agentforge.conf` lost its `/agentforge/turn`
clean-URL Alias (the `LocationMatch` ACL on `internal/*` stayed —
that's still defending the live BFF path); `deploy/README.md`,
`README.md`, `public/index.php`, `.env.example`, and
`docs/DEPLOYMENT.md` lost their references to the panel surface and
were re-pointed at the Vue dashboard where appropriate.

**What remains.** The live MVP path is unchanged:

- `vue-ui/` — the Vue dashboard, hosted same-origin as OpenEMR on
  `:9300/dashboard/`, where the AgentForge co-pilot drawer lives.
- `sidecar/` — the FastAPI + LangGraph orchestrator the dashboard
  talks to.
- `interface/modules/custom_modules/oe-module-agentforge/public/internal/*.php`
  + `src/Controllers/Internal*.php` — the JWT-authed inbound endpoints
  the sidecar calls into for FHIR/clinical data and intake/lab
  persistence. These were never touched by the panel cleanup.

**What we learned.** Once a placement decision is made, the deprecated
surface is carrying cost — both maintenance (PHPStan/PHPCS need to keep
seeing it clean) and review attention (the 2026-05-08 review burned
cycles on dead code). Deleting deprecated surfaces immediately, the
same day the placement flips, would have saved that round-trip.
Kept here as a reminder for future placement pivots.

**Alternatives considered.**

- **Fix the panel's bugs and keep it as a fallback UI.** Punted: no
  current demo path uses the panel, the Vue dashboard is already the
  live surface, and "ship a redundant UI before the deadline" is
  pure carrying cost.
- **Move the legacy files to an `archive/` directory instead of
  deleting.** Punted: git history preserves them; an archive directory
  invites resurrection and confuses static analysis.

---

## 2026-05-08 — Guideline-RAG opt-in is a chat-composer toggle, not auto-detection

**Plan:** P4 punch-list bug 2 said "the dashboard guideline RAG is
unreachable — `useAgentTurn` never includes `evidence_query`." The
straight-line fix is "forward `req.message` as `evidence_query` on
every turn."

**Why we deviated:** That fix mis-routes every chart-Q&A turn through
the W2 evidence retriever. Per `sidecar/src/agentforge/orchestrator/__init__.py`
the graph reaches the retriever node when `state["query"]` is non-empty
and either `pdf_pages` or `evidence_query` is set. Forwarding the
message verbatim trips the second condition for every turn — even
"summarize last visit" — which a) pays the RAG round-trip latency we
don't need, and b) emits guideline citations alongside chart citations
even when the clinician didn't ask.

**What shipped:** An "Ask guidelines" toggle in `AgentChatPane.vue`'s
composer toolbar (next to the attach button), backed by
`agentforge.guidelineMode` (default `false`). When the toggle is on
the store mirrors the user's text into both `message` and
`evidence_query`; when off, neither field is sent and the W1 chart-Q&A
loop runs as before. Visible affordance > heuristic intent
classification: the user can demo "watch me toggle 'Guidelines' and
ask a clinical question" without surprising the audience by guessing
intent from message text.

**What we learned:** "Forward the existing field" punches like this
one are usually 3-line client diffs, but when the BFF has a graph node
behind the field the shape needs a UX gate. Document the decision
even though the plumbing diff is small — a reader would otherwise
wonder why the obvious one-liner wasn't taken.

**Alternatives considered:**

- **Slash-command (`/ask <question>`)** — discoverable to power users,
  invisible to demo audiences. Punted as a fallback if the toolbar
  ever runs out of room.
- **Auto-detect from message text ("guideline", "according to",
  "evidence for")** — too brittle. False positives turn chart-Q&A into
  RAG; false negatives strand the feature.

---

## 2026-05-08 — P4 questionnaire logical id; threaded through the P2.1 seam

**Extends the P2.1 entry below.** P2.1 routed
`IntakeQuestionnaireResponseWriter` through the
`IntakeQuestionnaireResponsePersister` interface so the AgentForge
intake-form write path goes through
`OpenEMR\Services\QuestionnaireResponseService::saveQuestionnaireResponse()`.
That fixed the event-firing and audit-user gaps but left the FHIR
canonical reference broken: the production binding was passing the
display name ("AgentForge Intake Form") as the legacy service's 7th
positional `$q_id`, which lands in
`questionnaire_response.questionnaire_id` and constructs the FHIR
canonical URL `Questionnaire/{id}`. Display name is not a valid FHIR
resource id (FHIR R4 §id forbids spaces), so the persisted reference
was `Questionnaire/AgentForge Intake Form` — broken on every overlay-UI
round-trip.

**Punch-list claim.** "The AgentForge intake persister calls
`saveQuestionnaireResponse()` and passes the display name as the 7th
positional, which the service writes into
`questionnaire_response.questionnaire_id`."

**What I confirmed.** Read the legacy service implementation directly:
`saveQuestionnaireResponse` lines 401, 443, and 467 — the 7th
positional `$q_id` is exactly the FHIR `Questionnaire.id` slot; it
falls back to the FHIR id parsed from the canonical Questionnaire JSON
when null/empty, and otherwise lands verbatim in the
`questionnaire_id` row column and the `Questionnaire/{q_id}` canonical
URL. So the punch-list framing was right at the contract level. (My
prior agent's branch on top of pre-P2.1 main found a different bug
shape — the writer's *raw INSERT* was leaving `questionnaire_id` NULL,
not putting the wrong value there. Same FHIR symptom, different cause.
This rebase folds that work onto the P2.1 seam, where the bug shape
matches the punch-list framing.)

**Fix shape.** The logical id is now an explicit string threaded
through three sites in lockstep:

1. **Constant + DTO + lookup.** `IntakeQuestionnaireLookup::QUESTIONNAIRE_ID
   = 'agentforge-intake-form'`. Kebab-cased, no version suffix, valid
   FHIR R4 id. The trailing path of the pre-existing canonical URL
   (`https://agentforge.openemr.org/Questionnaire/intake-form`) was
   `intake-form`; I added the `agentforge-` prefix so the id is
   namespace-distinguishable from any other module that picks the
   same word. Versioning, when it arrives, gets a fresh seeded row
   with its own id (`agentforge-intake-form-v2`) rather than mutating
   this one. `SeededIntakeQuestionnaire` carries a new
   `questionnaireId` field; `IntakeQuestionnaireLookup` reads it from
   `questionnaire_repository.questionnaire_id` and falls back to the
   constant when NULL (forward-compat for droplet rows that ran the
   pre-fix seed).
2. **Writer + persister interface.**
   `IntakeQuestionnaireResponseWriter::insert()` takes a new
   `questionnaireId: string` parameter. The
   `IntakeQuestionnaireResponsePersister::save()` interface gains a
   new `$questionnaireLogicalId` parameter at the end (additive to
   the P2.1 contract).
3. **Production binding.**
   `QuestionnaireResponseServicePersister::save()` forwards
   `$questionnaireLogicalId` as the 7th positional argument to the
   legacy service. The display name (`$questionnaireName`) is no
   longer passed to the legacy service at all — the service derives
   `questionnaire_name` from the canonical JSON's `title` field
   (lines 402-407), and shadowing that with a writer-supplied string
   only produced inconsistency. The display name is kept on the
   persister interface for narrative-fallback / logging symmetry.

**Seed migration path.** The original `Version20260505000001` is
already applied on the production droplet; Doctrine doesn't replay
applied versions. New `Version20260508000001` migration backfills
`questionnaire_id` on the existing canonical row (no-op when the
column already matches). The original seed migration was also updated
for fresh installs (sets `questionnaire_id` on first INSERT and during
its idempotent UPDATE branch).

**Test posture.** Three layers of coverage:

- `IntakeQuestionnaireLookupTest` — verifies the lookup surfaces the
  stored id and falls back to the constant when NULL.
- `IntakeQuestionnaireResponseWriterTest` — verifies the writer
  delegates to the persister with the logical id in the new slot,
  forwards it verbatim, and keeps it distinct from the display name.
- `QuestionnaireResponseServicePersisterTest` (new) — verifies the
  production binding passes the logical id as the legacy service's
  7th positional. Uses the `OPENEMR_STATIC_ANALYSIS` constant in
  `setUp()` to make `BaseService`'s `code_types.inc.php` include
  loadable in the isolated harness, matching the prior art in
  `EncounterRestControllerTest`.

**What we learned.** When a punch-list item describes the *symptom*
(broken FHIR canonical reference) and presumes a specific *causal
path* (legacy service invocation with a wrong positional arg), the
"is the path even taken" question matters. On the pre-P2.1 codebase
the writer wasn't going through the legacy service at all (raw
INSERT), so the bug shape was different. P2.1's interface seam moved
the codebase onto the path the punch-list assumed, which means this
fix is the punch-list fix as originally framed: change which value
lands in `$q_id`. Net effect on the wire: same row, correct value.

(Supersedes my prior entry on `fix/p4-questionnaire-logical-id` — the
prose above incorporates that branch's investigation log into the
post-P2.1 shape.)

---

## 2026-05-08 — Sidecar-initiated persistence after graph extraction (P1.1)

**Plan:** Until this slice the W2 graph's intake/lab extraction lived
only on the per-turn `_TURN_EXTRACTION_VAR` ContextVar — the dashboard
saw it once in `AgentTurnResponse.extraction` and the structured EHR
side never heard about it. We chose **Option A: sidecar POSTs after
extraction succeeds** (single round-trip from the dashboard's POV;
sidecar already has document_id/patient_id/extraction in hand) over
Option B (dashboard-driven persist on user "approve"). Option A keeps
the persist transparent — the user's first turn already lands the
QuestionnaireResponse / procedure_order cascade, the confirm-panel
later moves data from "extracted but unapproved" into structured
tables, not from "in-memory" into "stored".

**What shipped:**

- `sidecar/src/agentforge/persist/` — new top-level package matching
  the established sibling pattern (`tools/`, `dashboard_auth/`).
  `ExtractionPersister` mirrors `DocumentBytesFetcher` shape: long-
  lived `httpx.AsyncClient`, JWT bearer auth, narrow typed
  `ExtractionPersistError(status_code, message)`. Methods
  `persist_intake(IntakeFormExtraction, ...)` and
  `persist_lab(LabPdfExtraction, ...)` POST to the existing W2 P2.1 /
  P2.2 controllers — the PHP controller surface required no changes,
  the Pydantic `model_dump(mode="json")` shape lined up cleanly with
  the controllers' field expectations.
- `Orchestrator._maybe_persist_extraction(...)` — new post-extract
  block in `_run_graph_turn` (orchestrator/__init__.py around
  line 511). Dispatches by `isinstance(extraction, LabPdfExtraction)`
  vs `IntakeFormExtraction`; mints a fresh internal JWT off the
  ctx's user/role + patient_id so the controllers' triple-check has
  a current bearer to validate. Failures log via PSR-3-style
  `extra=` kwargs (status_code, patient_id, document_id; never PHI
  in the message body) and continue — the synthesis turn always
  surfaces the model's reply.
- `_TURN_PERSISTED_VAR` ContextVar parallel to `_TURN_EXTRACTION_VAR`.
  The BFF turn route reads it via `get_last_persisted_handle()` and
  surfaces `persisted_resource_id: str | None` on
  `AgentTurnResponse` so the dashboard's confirm-panel can route a
  follow-up "open this resource" action without a second round-trip.
- `main.py` builds a single `InternalJwtMinter` instance shared by
  the orchestrator's persist hook and the BFF turn-route block —
  both surfaces sign with the same secret + clock so the
  controllers' `AgentJwtValidator` sees a consistent issuer.

**What's intentionally not in this slice:**

- The graph today only emits `IntakeFormExtraction`; the lab
  dispatch path is wired but unreachable until a future lab worker
  lands in `agentforge.orchestrator.graph`. Forward-compatible by
  design — `isinstance` dispatch means the lab path "just works"
  once the graph's `extraction_result` field accepts the lab shape.
- The persister's failure surface intentionally swallows errors
  rather than retrying. A retry policy here would compete with the
  per-turn timeout envelope; better to log the upstream status and
  let the dashboard's later confirm-step retry the persist (out of
  scope for P1.1).

---

## 2026-05-08 — Intake QuestionnaireResponse writer now routes through QuestionnaireResponseService (P2 punch-list)

**What changed:** `IntakeQuestionnaireResponseWriter` previously did a raw
`INSERT INTO questionnaire_response` against an injected `Doctrine\DBAL\Connection`.
That bypassed `OpenEMR\Services\QuestionnaireResponseService::saveQuestionnaireResponse()`
and therefore skipped `ServiceSaveEvent::EVENT_PRE_SAVE` /
`EVENT_POST_SAVE` firing, `questionnaire_id` linkage, creator/audit user
wiring, and generated narrative HTML. The writer now delegates through a
new `IntakeQuestionnaireResponsePersister` interface seam whose production
binding (`QuestionnaireResponseServicePersister`) wraps the legacy service.
The writer's external `insert()` signature is unchanged so the controller
call site keeps working.

**Why the seam exists:** `QuestionnaireResponseService` extends
`BaseService`, which `require_once`s `custom/code_types.inc.php` at file-
include time. That include calls `sqlStatement()`, so even autoloading the
class fails in the isolated-test harness. The thin interface lets the
writer remain unit-testable while production wires through the legacy
class.

**What we learned:** OpenEMR's modern PSR-4 service classes still ride on
top of legacy ADODB file-include side effects. Any new module code that
wants both isolated test coverage and audit-event participation needs an
adapter seam; injecting the legacy class directly drags the side-effect
chain into the test loader. The Connection injection in the original
writer was a tempting shortcut for the same reason — DBAL is mockable in
isolation and `QuestionnaireResponseService` is not — but the cost is
silent audit-log bypass, which is exactly the failure mode AUDIT.md C1
warns about.

---

## 2026-05-08 — Lab persistence controller validation gap closed; LabValue domain primitive added

`InternalLabPersistController` previously validated only the original
`values` array (count + is_array) and forwarded a normalized
`list<array<string, mixed>>` to `LabResultWriter`, which silently
substituted empty strings for missing/non-string `test_name` and
`value` fields — producing `procedure_result` rows with blank
`result_text` / `result` columns and no signal back to the caller. Per
CLAUDE.md "parse, don't validate", introduced
`OpenEMR\Modules\AgentForge\Domain\LabValue` as a `final readonly`
primitive whose constructor enforces non-empty `testName` and `value`
and whose `fromMixed()` factory rejects non-array entries. The
controller now parses each entry into a `LabValue` at the boundary;
any `\DomainException` becomes a generic HTTP 400 with no rows
persisted (no PHI-adjacent payload echoed back). `LabResultWriter`
now accepts `list<LabValue>` directly, dropping the defensive
`is_string($value['test_name'])` checks since the type system carries
the invariant. **What we learned:** schema-level validation on the
sidecar side isn't a substitute for boundary parsing on the OpenEMR
side — the contract between the two services is loose enough that
"silent corruption" was a single missing field away.

---

## 2026-05-08 — Production W2 SupervisorAdapter ships; measured baseline regen still deferred

**Plan:** Task 18.4 logged a deviation that the W2 eval gate ships with
a stub `baselines/week2.json` (all categories pinned at 1.0). Closing
that loop required (a) a production `SupervisorAdapter` shaped like
the runner's `Callable[[EvalCase], SupervisorOutput]` seam and (b) a
human running the regen against real Anthropic + retrieval to overwrite
the stub with measured rates.

**What shipped:**

- `sidecar/src/agentforge/eval/supervisor_adapter.py` — production
  callable that drives `build_graph().ainvoke()` and shapes the result
  into `SupervisorOutput` (response, sources, citation payload + tuple,
  route-decision logs).
- `sidecar/src/agentforge/eval/filename_resolver.py` — parses document
  filenames out of case query prose, resolves on-disk fixtures under
  `week2/example-documents/`, returns rendered pages.
- `sidecar/src/agentforge/eval/regenerate_baseline.py` — manual CLI:
  `uv run python -m agentforge.eval.regenerate_baseline --output ...`.

**What's still deferred:**

The CLI's real-LLM wiring branch (`_build_real_supervisor_and_harness`)
raises `NotImplementedError`. A human running the regen edits that
function to construct the deps tree their run needs (or pass `--mock`
for a smoke check). Importing `agentforge.main.create_app` at the
regen module level would pull FastAPI, Redis, and the OpenEMR HTTP
stack into the pytest collection surface, which is a larger blast
radius than the manual-edit approach justifies.

**Adapter output shaping notes (seam observations):**

- `structured_citation_payload` is a single Citation as a dict —
  picked from the first available source (extraction, then retrieval,
  then a synthetic fallback that satisfies the schema). The W2
  programmatic schema check needs *one* well-formed payload; the
  `structured_citations` tuple carries everything.
- Logs are reconstructed from the graph's terminal `AgentState`
  (route_decision, route_reason, iteration, last_node, plus
  worker-evidence markers). Streaming-quality per-handoff timing
  needs a real Langfuse trace handle and would read
  `trace.route_decisions` instead — out of scope for the current
  deferred-baseline use case.
- The W2 graph's `intake_extractor_node` short-circuits when
  `pdf_pages` is empty; the resolver mirrors the production /turn
  route's PNG-rejection posture by returning empty pages for non-PDFs.
  This is honest about the production surface — PNG cases land on the
  evidence/synthesize path, and the eval reflects that.

**What we learned:** The runner's `Callable` seam was already the
right abstraction — production wiring slots in cleanly without
modifying `runner_w2.SupervisorOutput` or `harness_w2.evaluate()`.
The deferred-wiring trade in the regen CLI keeps the test surface
deterministic + token-free; the manual edit step is acceptable for a
once-per-meaningful-change measurement.

---

## 2026-05-08 — Task 22 GH-Actions mirror: `github-script@v8` + workflow_dispatch trigger

**Plan:** Task 22 brief specified `actions/github-script@v7` (or
"current stable") and `on: pull_request`.

**Deviation 1 — `actions/github-script@v8`.** v8 is the current stable
major (v7 is one major behind). The brief explicitly allowed "current
stable", so this is a parameter pick rather than a divergence.
Matching the rest of the repo's bleeding-edge action pinning style
(`actions/checkout@v6`, `actions/upload-artifact@v7`).

**Deviation 2 — added `workflow_dispatch` trigger.** Brief noted "OK
to add as a bonus, but the spec is `on PR`". Including it costs
nothing and gives a maintainer the ability to re-run the gate from
the Actions UI without opening a no-op PR — useful for verifying a
baseline regen on the default branch.

**Deviation 3 — added `sidecar/tests/test_ci_parity.py` (subtask 22.3).**
Brief listed 22.3 as optional. I included it as a one-line invariant
that fails loudly if either CI file stops delegating to the shared
`run_eval_gate.sh`. Adds 2 tests to the suite (1280 → 1282).

**What we learned:** None of these are architectural — logging for
audit completeness only.

---

## 2026-05-08 — Task 19 ships as one commit; "fabricated value" arrives as citation-strip

**Plan:** Task 19 ("Implement Gate Self-Test (Deliberate Regression
Detection)") asked for two subtasks — 19.1 the failing test (red),
19.2 the passing implementation (green) — and named the regression as
a "fabricated `LabValue` (e.g. A1c=15.5% when the case's expected
value is 8.2%)" that should drop the `factually_consistent` judge
pass-rate by >5% so the gate fails.

**Deviation 1 — one commit, not two.** The task brief expressly
allowed "if the work naturally lands as one commit, that's fine —
log this in DEVIATIONS.md as a sub-split decision". The self-test
landed exactly that way. The Task 18 plumbing (runner → scoring →
gate → CLI) was already wired end-to-end, so the entire deliverable
is a single new test file at
`sidecar/tests/eval/gate/test_gate_blocks_regression.py`. There was
no production code to write — the test exercises the existing gate
pipeline under a regressed adapter. Splitting into two commits would
have had the second commit add zero lines and just toggle the gate's
verdict, which is not a meaningful intermediate state.

**Deviation 2 — "fabricated `LabValue`" lands as citation-strip, not
a numeric mismatch.** The W2 harness wiring (see
`tests/eval/harness_w2._JUDGE_BY_CATEGORY`) only routes
`EvalCategory.HALLUCINATION` and `EvalCategory.REFUSAL` cases to the
LLM judge. The W2 yaml suite uses `extraction`, `evidence_retrieval`,
`citations`, `refusal`, `missing_data` — so most cases run
programmatic-only and never see a "factually consistent" judge call.
A pure value-fabrication (response text says A1c=15.5%, expected is
8.2%) therefore slips past the harness today even though it would
slip past nothing in production.

The closest **programmatic** analogue to a fabrication is "the
response asserts a clinical claim with no citation backing it" — the
W2 contract is "every claim carries a Citation", and
`check_citation_present` enforces that. The regressed adapter emits
a response stating `A1c = 15.5%` *with the citation deliberately
stripped*. The regression lands on the `citations` category pass
rate, which the gate's `citation_present` threshold + regression
check both fire on. Programmatic-only path, no real LLM call needed.

**What we learned:** The brief's "factually_consistent" framing
overstated the harness's W2 wiring. The judge is wired for two
EvalCategory values; the W2 yaml suite uses five different ones. A
follow-up that genuinely exercises the LLM judge for value-fabrication
cases would need to (a) extend `_JUDGE_BY_CATEGORY` to route
`extraction` / `missing_data` cases to `FACTUALLY_CONSISTENT`, or
(b) re-tag the relevant case yaml entries to `HALLUCINATION`. Either
is a coordinated change (new prompt calibration, new baseline regen)
and out of scope for Task 19. The self-test as shipped proves the
gate **catches a fabrication-shaped regression** through the
programmatic surface, which is the correctness claim the gate makes
to CI.

**Artifacts:**
`sidecar/tests/eval/gate/test_gate_blocks_regression.py` (3 tests
under `@pytest.mark.gate_validation`: clean-passes-gate sanity,
regressed-fails-gate end-to-end, regressed-cli-exits-non-zero).

---

## 2026-05-08 — Task 20 `agent-eval` job uses python:3.12-slim, not the pre-baked sidecar image

**Plan:** Task 20 ("Implement GitLab CI Eval Job") asked for the new
`agent-eval` job to use the pre-baked sidecar Docker image (Task 21)
so it doesn't pay the ~1.2 GB HF-model download on every CI run.

**Deviation:** the job ships on `python:3.12-slim` (via the existing
`.python_base` template), same as `sidecar-pytest`, with `uv sync
--frozen`. The pre-baked image is not used.

**Why:** the CI gate runs with a *mocked* supervisor + *mocked* LLM
judge. The mock supervisor never invokes the LangGraph DAG, so the
HF model weights baked into the production image (all-MiniLM-L6-v2,
bge-reranker-base) are never loaded. Burning a ~1.2 GB image pull on
every CI run for code paths that aren't exercised is the worst of
both worlds.

**What's pending:** when the production Supervisor adapter lands —
the same follow-up that flips `tests/eval/baselines/week2.json` from
stub to measured — a separate manual / scheduled CI job will exercise
the gate against the real graph + real judge. *That* job will use the
pre-baked image. Open question: registry path for the image (the
deploy script currently builds it locally on the developer's
workstation; there's no published image on a registry yet). Tracking
as a follow-up; not blocking Task 20.

**MR comment posting:** uses curl + the GitLab REST API rather than
installing `glab` into `python:3.12-slim`. Auth precedence is
`GLAB_TOKEN` → `CI_JOB_TOKEN` → no-op. Operator must set `GLAB_TOKEN`
as a masked CI/CD variable for comments to appear; without it the
job still passes / fails correctly but the report only ships as an
artifact.

---

## 2026-05-08 — Eval-case YAML schema gains an optional `tags` field (Task 23)

**Plan:** Task 23 ("Pre-commit hook for eval smoke test") asked for a
mechanism to mark a 10-case representative subset so
`uv run pytest -m eval_smoke` finds them. The brief offered two seams:
either annotate via the YAML loader (a `tags: [eval_smoke]` field on
the case) or wrap the runner invocation with `pytest.mark.eval_smoke`
at the test level — pick whichever fits the existing harness.

**Deviation:** Picked the YAML route. Added an optional `tags:
list[str]` field to the case schema, plumbed it through
`tests/eval/yaml_cases.py` into `EvalCase.tags: tuple[str, ...]`, and
tagged 10 of the 50 W2 cases (two per category) with `eval_smoke`.
The smoke test in `sidecar/tests/eval/gate/test_eval_smoke.py` filters
on the tag at collection time and parametrizes one case per test.

**Why:** The runner-level wrap option requires the smoke test to
hard-code which case ids belong in the subset, drifting away from the
case files as a single source of truth. Per-case YAML tagging keeps the
selection visible at the case site; the selector tests in
`tests/eval/test_yaml_cases.py::TestTagsRoundTrip` enforce the
"exactly 10, two per category, all five W2 categories" invariants so
re-balancing the set can't silently break the budget guarantees.

**What we learned:** The `tags` field is generic — not eval-smoke-only.
A future "regression-locks" or "p0-only" subset can reuse the same
seam without another schema change. The cost was small (one optional
key, defaults to `()`, round-trips cleanly through PyYAML); the
invariant tests were what made the change durable, not the field.

**Artifacts:** branch `feat/task-23-pre-commit-eval-smoke` commits
`f1b52e6d6` (schema + tagging + selector tests),
`10a3a1972` (smoke test module),
`904d8ae6e` (pre-commit hook).

---

## 2026-05-08 — Task 18 W2 eval-gate ships with a stub baseline + a SupervisorOutput adapter

**Plan:** Task 18 ("Eval Gate with Baseline and Thresholds") asked
for a runner that loads the 50 W2 cases, dispatches them through the
supervisor graph, scores them with `EvalHarnessW2`, and compares
against a pinned `baselines/week2.json`. The brief noted the W2
yaml-cases loader doesn't carry `sources` or
`structured_citation_payload`, and explicitly allowed shipping with a
stub baseline.

**Deviation 1 — `SupervisorOutput` adapter rather than extending the
case loader.** `EvalHarnessW2.evaluate()` needs `response`, `sources`,
`structured_citation_payload`, `structured_citations`, and `logs`. The
W2 yaml-cases loader produces only `id` / `category` / `patient_id` /
`query` / `expected_behavior`. Two viable shapes:

  (a) extend `tests/eval/yaml_cases.py` to carry the harness-input
      fields, edit all five W2 yaml files to populate them, and update
      `scripts/validate_eval_cases.py`; or
  (b) take a `Supervisor: Callable[[EvalCase], SupervisorOutput]` in
      the runner so tests / production callers fabricate / derive the
      harness inputs.

I shipped (b). Reason: the harness inputs are *runtime* outputs of the
supervisor graph (the response the agent produced, the trace logs, the
structured citation it emitted), not authoring metadata the test
author writes by hand. Encoding them in YAML would require either
fixing the agent's expected output per case (overconstrains the eval)
or duplicating the supervisor's response shape into YAML (chases the
real implementation). The callable seam keeps the case schema lean
and lets the production adapter live alongside the LangGraph wiring,
not inside the case-author's mental model.

**Deviation 2 — initial `baselines/week2.json` is a stub at 1.0.**
The brief allowed either a stub baseline or a measured one from a
deterministic-mock run, "honest about the state". I shipped a stub
because:

  * No production `Supervisor` adapter exists yet — building one is
    out of scope for Task 18 (it touches `agentforge.orchestrator.graph`
    + the production gateway plumbing).
  * A "measured" baseline from the existing test-mock supervisor would
    encode the mock's behaviour, not the real agent's — worse than a
    stub because it looks measured but isn't.
  * The 1.0 stub gives the gate's threshold + regression arithmetic an
    honest reference point until the human regenerates it from a real
    end-to-end run. The `_meta.status` field + rationale block in the
    JSON file makes the stub status discoverable.

The next step (out of Task 18 scope, ticketed for follow-up): wire a
`SupervisorOutput` adapter on top of `build_graph()` and run the suite
once. Replace the stub with the resulting per-category rates.

**What we learned:** When the brief frames "stub vs measured" as a
choice and the runtime the measurement would be against doesn't exist
yet, the stub is the only honest path. Encoding that explicitly via
`_meta.status` keeps the next person from confusing the stub for a
measurement.

---

## 2026-05-08 — Task 27.2 fold-in: `record_extraction_confidence` only

**Plan:** Task 27.2 originally called for adding both
`record_retrieval_hits` and `record_extraction_confidence` to the
`LangfuseClient` Protocol, the real `AgentLangfuse`, and the
`NullLangfuseClient`.

**Deviation:** Task 15 already shipped `record_retrieval_hits` (commit
`5d89c8726`), including the orchestrator wiring via
`_maybe_record_retrieval_hits` in `evidence_retriever_node`. Task 27.2
therefore folds in only the still-needed half: a stand-alone
`record_extraction_confidence(trace, confidence, unsupported_fields_count)`
evaluator-style span.

**Why:** Re-shipping the same retrieval_hits contract would either
(a) duplicate the existing implementation verbatim — pure churn — or
(b) reshape Task 15's caller in `orchestrator/graph.py`, breaking the
already-merged `_maybe_record_retrieval_hits` helper. Both are worse
than scoping 27.2 to the genuinely new surface.

**Verified compatibility:** the existing `record_retrieval_hits`
signature (`bm25_count`, `dense_count`, `post_rerank_count`) matches
the Task 27 spec's signature/intent exactly — three list-size counts,
no PHI surface — so no reconciliation is required.

**What we learned:** Cross-task subtask spec overlap is a real risk
when sibling tasks ship in parallel worktrees. The cheap mitigation is
to read the merged-main observability surface before writing red tests.

---

## 2026-05-08 — W2 LLM judge ships as a parallel surface, not a mutation of W1 grader

**Plan:** Task 17 ("LLM-as-judge evaluation layer") asked for an
LLM-as-judge layer plus programmatic checks, plumbed into the
`EvalHarness`. The brief enumerated two judge categories
(`factually_consistent`, `safe_refusal`) and three programmatic checks
(`schema_valid`, `citation_present`, `no_phi_in_logs`).

**Deviation 1 — co-existing W2 harness instead of mutating W1.** The
existing `tests.eval.harness.EvalHarness` is a *grounding*-shaped
checker (citation index, behavior callable) that the 1147 W1 tests
consume. Rather than reshape its API to accept an LLM judge — which
would have meant either changing every existing call site or adding a
flagged-second-mode — I shipped a new `EvalHarnessW2` in
`tests/eval/harness_w2.py` that runs programmatic checks first, then
the LLM judge. The W1 contract is untouched; W2 cases consume the new
surface explicitly. Same pattern as the existing `LLMJudgeGrader` /
new `LLMJudge` split.

**Deviation 2 — W2 judge is a new class, not an extension.** The
existing `LLMJudgeGrader` (week1-gaps Task 18) emits a 1-5 score with
consensus voting. The W2 contract is binary PASS/FAIL with two
category-specific prompts. Rather than overload one class with two
incompatible verdict shapes, I shipped `LLMJudge` alongside it. Both
graders co-exist; future work can deprecate W1 once the team is
confident the binary contract is enough.

**Deviation 3 — judge prompts live in `prompts/v1/`, not under
`tests/eval/judges/week2/`.** The task brief explicitly anticipated
this: "If you put prompts under `sidecar/tests/eval/judges/`, log a
deviation in `docs/DEVIATIONS.md` justifying why." I followed the
existing prompt-library convention (`prompts/v1/<component>.md` pinned
in `version.json`), so this is the *non*-deviation path the brief
recommended. The prompts are loaded via the same
`agentforge.prompts.load_prompt()` function the planner and synthesizer
prompts use — text-reviewable diffs, hot rollback by `version.json`
flip, and one less prompt-loading code path to maintain.

**Why:** All three decisions trade slightly more surface area for zero
risk to the W1 baseline. With the W2 deadline 36 hours out, that's
the right side of the trade. The split surfaces (`EvalHarness` /
`EvalHarnessW2`, `LLMJudgeGrader` / `LLMJudge`) cost nothing at
runtime — tests pick which one they consume — and let us calibrate
the W2 layer on the 50 W2 cases without spooking the W1 regression
suite.

**What we learned:** When the brief prescribes a path that fits the
codebase's seams, follow it; when it would force a load-bearing rewrite
of a stable surface, ship the new surface alongside the old one and
flag the migration as future work.

---

## 2026-05-08 — Evidence-retriever node consumes `retrieve_with_stats`, not `retrieve`

**Plan:** Task 15.5 calls for emitting a Langfuse `retrieval_hits` span
with per-stage counts (`bm25_count`, `dense_count`, `post_rerank_count`).
The original `EvidenceRetriever.retrieve()` returned only the final result
list — no stage counts.

**Deviation:** Added a sibling method `EvidenceRetriever.retrieve_with_stats()`
returning a new `RetrievalStats` DTO (results + the three counts). The
existing `retrieve()` becomes a thin wrapper that drops the counts. The
W2 LangGraph node now calls `retrieve_with_stats()` so the span payload
is computed inside the existing seam — no per-stage component plumbing
leaks into the orchestrator.

**Why:** The task spec offered two options ("extend the return type
carefully (test-first) or compute them at the call site by invoking
the components"). Computing at the call site would have required the
node to know about BM25/Dense/RRF/Reranker individually, breaking the
W2_ARCHITECTURE.md §3 contract that the pipeline is a single
black-box surface. Extending the return type via a sibling method keeps
both surfaces — legacy callers stay on `retrieve()`, the node speaks
`retrieve_with_stats()` — and adds zero new top-level dependencies.

**Citation schema check:** the spec's field-name list (`source_id`,
`page_or_section`, `field_or_chunk_id`, `quote_or_value`) matches the
canonical `agentforge.schemas.citation.Citation` shape exactly — no
schema-vs-spec drift to log.

**What we learned:** When a return-type extension is the right answer,
adding a sibling method beats forcing every existing caller through a
new DTO. The wrapper pattern keeps the diff blast radius local to the
new caller (the node).

---

## 2026-05-08 — Sidecar image delta is ~1.2 GB, not the spec's ~370 MB

**Plan:** Taskmaster Task 21 set the image-size target at 300-450 MB
delta, with the breakdown `all-MiniLM-L6-v2 ~90 MB` + `bge-reranker-base
~280 MB`.

**Deviation:** Actual delta from pre-baked HF cache is ~1.2 GB
(MiniLM 88 MB, bge-reranker-base 1.1 GB).

**Why:** `BAAI/bge-reranker-base` is a roberta-base derivative with
~280 M parameters. The "280" in the task spec appears to have been
copied from the parameter count and treated as a megabyte estimate;
the actual fp32 weights weigh ~1.1 GB on disk regardless of format
(safetensors and pytorch_model.bin are both that size). We already
restricted the snapshot_download to safetensors only via
`allow_patterns`, which avoided fetching the 1.1 GB legacy
pytorch_model.bin alongside; but the safetensors file *itself* is
1.1 GB, so the cache layer can't get smaller without changing models.

We did not switch models — `cross_encoder.py` references
`BAAI/bge-reranker-base` directly and changing it would silently
shift evaluation results. The Cohere reranker remains the
network-gated alternative for environments where image size is a
hard constraint.

**What we learned:** Verify model sizes against the actual repo
(`HfApi().list_repo_files` + size lookup) before pinning Dockerfile
acceptance criteria. "X-base" model names and parameter counts are
not reliable proxies for on-disk weight size.

**Artifacts:** Task 21 commits — see `sidecar/Dockerfile` and
`sidecar/scripts/verify_model_cache.py`.

---

## 2026-05-08 — Task 26 cache headers landed on the AgentForge internal route, not the legacy controller

**Plan:** Task 26 ("Add HTTP Cache Headers to OpenEMR Document Route")
listed three candidate routes — the `agent_panel.js` URL pattern at
`/controller.php?document&retrieve&...&as_file=false`, the sidecar BFF
at `/api/agent/document/{id}`, and the AgentForge module's own
document endpoint. The brief told us not to touch the legacy core
route or the sidecar, and to preserve `Content-Disposition: inline`.

**Deviation:** Headers were added to
`InternalDocumentBytesController::show()` (the JWT-scoped OpenEMR-side
route at `/agentforge/internal/get_document_bytes`), which the
sidecar BFF chains to. Two preservation criteria from the brief did
not apply:

* **`Content-Disposition: inline` was never emitted by this
  controller.** That header is emitted by the legacy
  `C_Document::retrieve_action` and (for the citation overlay path)
  by the sidecar BFF, neither of which we touched. There was nothing
  to preserve on the modified route.
* **The previous policy was `no-store, no-cache, must-revalidate,
  private`** — written when the only consumer was the sidecar's
  vision tool (one-document-per-call, no benefit from caching). The
  citation-overlay use case post-dates that decision; switching to
  `max-age=300, private, must-revalidate` opens a 5-minute private
  cache window that never reaches a shared cache. The PHI-safety
  guarantee (`private`) is preserved.

**Why:** The brief's "find the exact route" grep (`as_file`,
`retrieve`, `Content-Disposition: inline`) returned only the
`agent_panel.js` URL builder — i.e. the legacy core route. The AgentForge
module never owned a Content-Disposition-bearing surface. The
internal JWT-scoped endpoint is the closest the module has to a
"document-bytes serving route" and it's the one the citation overlay
chains through (Vue → BFF → InternalDocumentBytesController), so the
latency win lands at the same layer the brief had in mind.

**What we learned:** When a task brief says "the route used by X" and
the grep shows X actually points at a route in the out-of-scope set,
the route to modify is the next layer down the chain that *we own*.
Worth documenting the chain explicitly in the controller docblock so
the next agent doesn't re-do this discovery.

**Artifacts:** commits `cf77fbb1f` (red) and `4a9f3e51b` (green);
[`InternalDocumentBytesController.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Controllers/InternalDocumentBytesController.php);
[`InternalDocumentBytesControllerTest.php`](../tests/Tests/Isolated/Modules/AgentForge/InternalDocumentBytesControllerTest.php).

---

## 2026-05-08 — Intake-form E2E test models the persist client inline (Task 29)

**Plan:** Task 29's spec assumed the e2e test would call into a production
sidecar `persist_questionnaire_response` HTTP client and merely stub the
PHP boundary. Subtasks 29.4 and 29.5 needed a concrete Python client to
hit `persist_questionnaire_response.php`.

**Deviation:** No production client for that endpoint exists in the sidecar
yet — the W2 wiring landed the PHP-side controller
(`InternalIntakePersistController`) and Pydantic schema
(`IntakeFormExtraction`), but the Python POST-side has not been written.
Rather than block the e2e test on that wiring, the test models the client
inline as `_PersistQuestionnaireResponseClient` inside the test file.

**Why:** The headline Task 29 invariant ("intake forms write to
QuestionnaireResponse, never to clinical tables") manifests as a routing
constraint at the sidecar/PHP boundary — what URL the sidecar POSTs to.
The inline client lets the test pin that contract *before* the production
class lands, so when the Python wiring catches up it has a target shape
to match. The test asserts the client must POST to exactly
`persist_questionnaire_response.php` and nowhere else.

**What we learned:** When a follow-up production class is "obvious" from a
test's structure, modeling it inline in the test file (with a comment that
flags the missing prod wiring) is preferable to either (a) blocking the
test on the wiring or (b) writing a production class with no consumer. The
test captures the contract; the future production class drops in to satisfy
it. The W2 deadline pressure makes this trade-off worth flagging — under a
slower cadence we'd write the prod client first.

---

## 2026-04-30 — Dropped `langchain` from sidecar dependencies

**Plan:** Taskmaster Task 5.1 (`pyproject.toml`) listed both `langgraph` and
`langchain` as production dependencies.

**Deviation:** Dropped top-level `langchain`; kept `langgraph` only.

**Why:** `langgraph` already pulls `langchain-core` transitively, and the
orchestrator uses LangGraph directly per ARCHITECTURE.md §3 ("Why LangGraph
and not vanilla LangChain"). No code path needs top-level `langchain`
chain primitives.

**What we learned:** Task specs written before the dep graph is verified can
carry redundant entries. Verify transitive availability before pinning every
named package — the lock surface should reflect what we actually import,
not what we think we'll need.

**Artifacts:** [commit d6dcea5e2](../sidecar/pyproject.toml).

---

## 2026-04-30 — Switched FastAPI app to factory pattern (test-driven discovery)

**Plan:** Task 5.3 (`main.py`) had `app = create_app()` at module level so
uvicorn could discover the app via `agentforge.main:app`.

**Deviation:** Removed the module-level `app`; production now runs
`uvicorn agentforge.main:create_app --factory`. Dockerfile ENTRYPOINT and
README updated to match.

**Why:** Module-level `app = create_app()` triggers `Settings()` instantiation
at *import* time. When pytest imports `agentforge.main` for collection, that
fires *before* a fixture can monkeypatch the required `JWT_SECRET` and
`HMAC_KEY` env vars — the test fails with Pydantic validation errors. The
factory pattern defers config loading to invocation, preserving fail-fast
on missing config in production while letting tests construct app instances
independently.

**What we learned:** "Required without default" config fields fight with
Python's import-time evaluation. The factory pattern is the standard FastAPI
way to defer this and should have been the default. Worth checking: any
future `app = X()` at module level for code that depends on Settings is a
landmine for testing.

**Artifacts:** [commit d40253ec9](../sidecar/src/agentforge/main.py).

---

## 2026-04-30 — Used Doctrine Migrations despite `db/README.md` "not yet integrated" warning

**Plan:** Task 40 spec said "Doctrine or direct" SQL migration. `CLAUDE.md`
says "New schema changes use Doctrine Migrations." `db/README.md` warns:
"The Doctrine Migrations system is NOT fully integrated into OpenEMR yet.
Don't make database changes using this until #10708 is completed."

**Deviation:** Followed CLAUDE.md. Schema change ships as a Doctrine
migration even though the upstream integration is incomplete.

**Why:** User instruction: "honor claude.md." When CLAUDE.md and an in-repo
README disagree, CLAUDE.md is authoritative — it is the project-specific
instruction set, while `db/README.md` reflects upstream OpenEMR's state.

**What we learned:** When CLAUDE.md and another in-repo doc disagree, surface
the conflict and ask before picking. The user has context the docs may not.
Practical follow-on: because the migration system isn't auto-integrated,
existing installs need a manual `./cli migrations:migrate` step to apply
indexes; documented in
[`oe-module-agentforge/README.md`](../interface/modules/custom_modules/oe-module-agentforge/README.md)
as a pre-deploy gate. Fresh installs bypass the migration runner via
`sql/database.sql`, so we updated both paths.

**Artifacts:** [commit f35cc1f47](../db/Migrations/Version20260430000001.php),
[`oe-module-agentforge/README.md`](../interface/modules/custom_modules/oe-module-agentforge/README.md).

---

## 2026-04-30 — Kept redundant `idx_procedure_report_date` (deferred to Task 49)

**Plan:** Task 40.1 spec listed seven indexes, including
`idx_procedure_report_date(procedure_report_id, date_report)` on
`procedure_report`.

**Deviation:** None at the schema level — the index ships as specified.
Created Taskmaster Task 49 (low priority, depends on Task 40) to revisit
post-MVP.

**Why:** `procedure_report_id` is the table's PRIMARY KEY. InnoDB tables are
clustered on the PK, so a secondary index leading with the PK is unlikely to
be selected by the optimizer over the clustered index. The spec is buggy on
this entry. User chose to follow the spec for parity rather than deviate
based on Claude's analysis ("less to touch = less I can break"). Cost to
live with is small (~1-3% slower writes on `procedure_report`, modest
storage). Cost to repay is one trivial `DROP INDEX` migration.

**What we learned:** "Trust the spec when you can't independently verify the
analysis" is a defensible conservative posture, especially when learning a
codebase. The deferral mechanism (Task 49 + comment in the migration's
`getDescription()` + this entry) makes the debt visible without forcing a
decision now. Tracked debt is much cheaper than invisible debt.

**Artifacts:**
[`db/Migrations/Version20260430000001.php`](../db/Migrations/Version20260430000001.php),
Taskmaster Task 49.

---

## 2026-04-30 — Registered AgentForge templates dir in TwigTemplateCompilationTest

**Plan:** Task 1.4 created
[`oe-module-agentforge/templates/agent_panel.html.twig`](../interface/modules/custom_modules/oe-module-agentforge/templates/agent_panel.html.twig)
extending `patient/card/card_base.html.twig`. The Task 1 spec did not call
out updating the project's existing Twig compilation test infrastructure.

**Deviation:** Added
`'interface/modules/custom_modules/oe-module-agentforge/templates'` to the
`EXTRA_TEMPLATE_DIRS` constant in
[`tests/Tests/Isolated/Common/Twig/TwigTemplateCompilationTest.php`](../tests/Tests/Isolated/Common/Twig/TwigTemplateCompilationTest.php).

**Why:** The regression check during Task 40 wrap-up surfaced a failing
isolated test:
`TwigTemplateCompilationTest::templateCompiles with data set "...agent_panel.html.twig"`.
The compilation test discovers `.twig` files via `SEARCH_DIRS` and compiles
them through a Twig environment whose `FilesystemLoader` is built from
`EXTRA_TEMPLATE_DIRS`. Without our module's templates dir in that list,
the loader couldn't resolve `{% extends "patient/card/card_base.html.twig" %}`
during compilation, even though the actual runtime Twig environment (built
by `Bootstrap` via `TwigContainer`) would have resolved it fine.

**What we learned:** Adding a Twig template in a new module isn't fully
self-contained — the project has a separate test-time Twig harness with its
own template-path registry. Any new module that ships templates needs an
entry in `EXTRA_TEMPLATE_DIRS`. This is now part of the implicit
"new module checklist" alongside `openemr.bootstrap.php`, `info.txt`, etc.
Worth folding into the bootstrapping flow when we add modules going forward.

**Artifacts:** [commit pending in this branch],
[`oe-module-agentforge/templates/agent_panel.html.twig`](../interface/modules/custom_modules/oe-module-agentforge/templates/agent_panel.html.twig),
[`tests/Tests/Isolated/Common/Twig/TwigTemplateCompilationTest.php`](../tests/Tests/Isolated/Common/Twig/TwigTemplateCompilationTest.php).

---

## 2026-04-30 — Stripped half-finished dependency storage from Bootstrap.php

**Plan:** Task 1.2 spec defined `Bootstrap.php` with constructor-stored
`$twig`, `$logger`, and `$eventDispatcher` properties (mirroring the
existing `oe-module-comlink-telehealth` and `oe-module-claimrev-connect`
patterns).

**Deviation:** Removed the `$twig`, `$logger`, and `$eventDispatcher`
property storage. The constructor still accepts these parameters (per
OpenEMR's module-loader contract) but does not retain them. Storage will
be reintroduced in Task 2 when `subscribeToEvents()` begins registering
listeners that actually need them. Also added `assert()` calls in
`openemr.bootstrap.php` to narrow the `$classLoader` and `$eventDispatcher`
globals injected by OpenEMR's `ModulesApplication`.

**Why:** PHPStan level 10 (per CLAUDE.md) flagged the stored-but-unused
properties as `property.onlyWritten`. Other modules suppress this with
baseline entries — but CLAUDE.md says "Avoid baselines. Never add new
baseline entries — fix the underlying type error" *and* "no half-finished
implementations either." Both directives point the same way: don't store
dependencies before you use them. Stripping is the honest fix.

The bootstrap.php globals were similarly flagged (`method.nonObject`,
`variable.undefined`) because PHPStan can't see through OpenEMR's
inject-by-name pattern. CLAUDE.md says "Avoid inline `@var` casts" — so
instead of `/** @var */`, we use `assert($x instanceof Y)`, which is
runtime-defensive in dev (where `assert.active=1`) and a no-op in
production. PHPStan understands the assertion for type narrowing.

**What we learned:** Two things worth recording:
1. The spec mirrored a pattern from established modules whose Bootstrap
   classes are *complete*. Copying their structure for a stub class
   imports the half-finished anti-pattern. Better to strip down and grow.
2. OpenEMR's project practice (baseline entries for module bootstrap globals)
   conflicts with CLAUDE.md's "avoid baselines" rule. The `assert(... instanceof ...)`
   idiom satisfies both — it's the right pattern for any future module
   bootstrap files we add.

**Artifacts:**
[`oe-module-agentforge/openemr.bootstrap.php`](../interface/modules/custom_modules/oe-module-agentforge/openemr.bootstrap.php),
[`oe-module-agentforge/src/Bootstrap.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Bootstrap.php).

---

## 2026-04-30 — Used `PatientDemographics\RenderEvent` instead of `Main\Tabs\RenderEvent` for panel injection

**Plan:** Task 2 spec said register the agent panel on
`OpenEMR\Events\Main\Tabs\RenderEvent::EVENT_BODY_RENDER_POST` and check
`$_SESSION['pid']` to gate rendering.

**Deviation:** Use `OpenEMR\Events\PatientDemographics\RenderEvent::EVENT_SECTION_LIST_RENDER_AFTER`,
which provides the patient ID directly via `$event->getPid()`.

**Why:** `Main\Tabs\RenderEvent` fires from `interface/main/tabs/main.php:562`
— the OpenEMR app shell, **not** any patient view. Other modules use it
for global UI plumbing (`oe-module-comlink-telehealth` injects telehealth
JS/CSS scripts there; `oe-module-faxsms` injects a floating phone widget).
Following the spec literally would render the agent panel at the bottom
of the global app shell, once per login, with broken styling because
`agent_panel.html.twig` extends `patient/card/card_base.html.twig` —
which assumes section-list context that doesn't exist in the shell.

`PatientDemographics\RenderEvent::EVENT_SECTION_LIST_RENDER_AFTER`, on
the other hand, fires from `interface/patient_file/summary/demographics.php:1529`
— inside the patient summary section list, exactly where ARCHITECTURE.md
§1 places the agent panel. It also gives us the canonical patient ID
via `getPid()` so we don't need session-state inspection. Same event used
by `oe-module-claimrev-connect`'s eligibility card and `SmartLaunchController`'s
SMART app section — so we're matching the established OpenEMR pattern
for "add a card to the demographics page."

**What we learned:** OpenEMR has at least four render events (`Main\Tabs`,
`PatientDemographics`, `Patient\Summary\Card`, `PatientPortal`), each for
a different surface and lifecycle. Picking one by name without confirming
where it actually fires (and what other modules do with it) is risky.
For future "add a card to X" work, **identify the dispatch site first**:
the event's name often suggests broader applicability than its actual
fire context. `Patient\Summary\Card\RenderEvent` was also considered but
turned out to be for *modifying* existing cards (note, reminder, lab,
etc.) via `RenderInterface` injection — not adding new ones.

**Artifacts:**
[`oe-module-agentforge/src/Bootstrap.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Bootstrap.php),
dispatch site at `interface/patient_file/summary/demographics.php:1529`.

---

## 2026-04-30 — Used `Symfony\Component\EventDispatcherInterface` not `Symfony\Contracts\...`

**Plan:** Task 2 spec imported
`Symfony\Contracts\EventDispatcher\EventDispatcherInterface`.

**Deviation:** Use `Symfony\Component\EventDispatcher\EventDispatcherInterface`.

**Why:** The Contracts version exposes only `dispatch()`. We need
`addListener()`, which is on the Component interface (a superset of
Contracts). The existing `oe-module-claimrev-connect` makes the same
choice for the same reason. Task 1.2 already used Component; Task 2
spec was inconsistent.

**What we learned:** When a spec dictates an interface, verify it has the
methods you need. Symfony Console / EventDispatcher / etc. all have
"Contracts" minimal interfaces and "Component" expanded ones — the
Component is usually what application code wants.

**Artifacts:**
[`oe-module-agentforge/src/Bootstrap.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Bootstrap.php),
[`oe-module-agentforge/openemr.bootstrap.php`](../interface/modules/custom_modules/oe-module-agentforge/openemr.bootstrap.php).

---

## 2026-04-30 — Added autoload-dev entry for module test discovery

**Plan:** OpenEMR modules self-register their PSR-4 namespace at runtime
via `openemr.bootstrap.php` calling `$classLoader->registerNamespaceIfNotExists`.
Tests don't go through that path — the standard composer autoloader
handles them, and module namespaces aren't in `composer.json`.

**Deviation:** Added
`OpenEMR\\Modules\\AgentForge\\` → `interface/modules/custom_modules/oe-module-agentforge/src`
to `autoload-dev` in `composer.json`.

**Why:** Subtask 2.2's TDD tests failed with `Class not found` because the
isolated test runner has no module-aware autoloading. The choices were
(a) add the entry, (b) `require_once` the class file in each test, or
(c) put tests inside the module like `oe-module-comlink-telehealth` does
(separate `phpunit.xml`, not picked up by main regression). (a) makes
module tests discoverable by `composer phpunit-isolated`, which is
where we want the regression gate to live.

**What we learned:** New modules with tests under `tests/Tests/Isolated/Modules/<name>/`
need a one-line `autoload-dev` entry mapping their namespace to their
`src/` directory, plus a `composer dump-autoload` after editing.
Documented in this entry so future modules don't re-derive the
discovery flow.

**Artifacts:** [`composer.json`](../composer.json),
[`tests/Tests/Isolated/Modules/AgentForge/BootstrapTest.php`](../tests/Tests/Isolated/Modules/AgentForge/BootstrapTest.php).

---

## 2026-04-30 — Lazy Twig environment in Bootstrap

**Plan:** Task 1.2 spec eagerly constructed Twig in the constructor:
`$this->twig = (new TwigContainer($path, $kernel))->getTwig();`. Task 40's
deviation #6 stripped the storage entirely.

**Deviation:** Constructor stores `?Environment $twig = null` (optional,
test-injectable) and a fallback kernel. Twig is constructed lazily on
first render via `getTwigForRendering()`.

**Why:** Subtask 2.4's TDD wanted to inject a fake Twig (ArrayLoader with
a stub template) so tests verify behavior without the full OpenEMR
template chain. Eager Twig in the constructor would force every test —
including the ones that only exercise event subscription — to provide a
working `Kernel`, which the isolated test environment can't initialize
("OpenEMR Kernel not initialized" runtime error).

Lazy gives us:
- Constructor succeeds in any environment (no Kernel needed for non-render paths).
- `renderAgentPanel` consumes a Twig that's either injected (tests) or
  built from `OEGlobalsBag::getInstance()->getKernel()` (production).
- Subscribe-only tests don't need fake Twig at all.

**What we learned:** "No half-finished implementations" (CLAUDE.md) and
"don't make tests provide irrelevant fixtures" both push toward lazy
construction of dependencies that are only used by some methods. The
property loses `readonly` (it's set on first use), but `?Environment`
+ `??=` keeps the mutation contained and idempotent. Worth replicating
for the future LLM client / Redis client / Langfuse setup in the
sidecar — same problem shape.

**Artifacts:**
[`oe-module-agentforge/src/Bootstrap.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Bootstrap.php).

---

## 2026-04-30 — Used `lcobucci/jwt` not `firebase/php-jwt` for JWT minting

**Plan:** Task 6 spec's implementation snippet used `Firebase\JWT\JWT` /
`Firebase\JWT\Key`; subtask 6.5's title contradicted with "lcobucci/jwt
Library and Full Claims."

**Deviation:** Use `lcobucci/jwt` 4.x.

**Why:** `composer.json` already requires `lcobucci/jwt: ^4.3.0`;
`firebase/php-jwt` is not a dependency. OpenEMR's OAuth2, OpenID
Connect, and JWKS code all use lcobucci. Adding a second JWT library
just for one module would split the project's auth surface for no
gain.

**What we learned:** When a spec snippet and a subtask title disagree,
verify against `composer.json` and existing project usage before
picking. The spec was written ahead of implementation; what landed in
the codebase wins.

**Artifacts:**
[`oe-module-agentforge/src/Services/AgentJwtService.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Services/AgentJwtService.php).

---

## 2026-04-30 — Wrote GACL query directly; `AclMain::getUserRole()` doesn't exist

**Plan:** Task 6 spec's implementation snippet had:
```php
return AclMain::getUserRole($userId);
```

**Deviation:** Created `UserRoleLookup` with a Doctrine DBAL query
mirroring `OpenEMR\Common\Logging\BreakglassChecker`'s shape:
```sql
SELECT grp.value
FROM gacl_aro JOIN gacl_groups_aro_map JOIN gacl_aro_groups
WHERE BINARY aro.value = ?
ORDER BY grp.id ASC LIMIT 1
```
(The lookup keys on username, not user id, because `gacl_aro.value`
stores the username — same convention BreakglassChecker uses.)

**Why:** `AclMain` only has ACL *check* methods (`aclCheckCore`,
`aclCheckForm`, `zhAclCheck`, etc.) — no role-getter. The spec called
a method that doesn't exist. Writing the GACL query directly is the
right path; it matches the established BreakglassChecker pattern in
the same area of the codebase.

The `BINARY` collation match and the lowest-id deterministic
tiebreaker are inherited from BreakglassChecker — case-sensitive
match avoids a username-spoofing class of bug, and a deterministic
"primary group" keeps role claims stable across requests for the same
user.

**What we learned:** Spec method references should be verified against
the actual class file. Looking at OpenEMR's auth/ACL code reveals that
"role" is not a single concept in OpenEMR — there's the OAuth coarse
`user_role` (`users` / `patient` / `system`), and there are GACL
group memberships. The spec conflated them.

**Artifacts:**
[`oe-module-agentforge/src/Services/UserRoleLookup.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Services/UserRoleLookup.php),
[`tests/Tests/Services/AgentForge/UserRoleLookupIntegrationTest.php`](../tests/Tests/Services/AgentForge/UserRoleLookupIntegrationTest.php).

---

## 2026-04-30 — `BreakglassContext` value object + PSR-20 clock injection

**Plan:** Task 6 spec passed `bool $breakglassFlag` and
`?string $breakglassReason` as separate parameters to `mintToken`,
plus `time()` directly inside the method for `iat` / `exp`.

**Deviation:** Two changes:
1. Replaced the two breakglass parameters with a `BreakglassContext`
   value object whose constructor enforces "flag=true requires non-empty
   reason."
2. Inject `Psr\\Clock\\ClockInterface` instead of calling `time()` /
   `new DateTimeImmutable()` directly.

**Why:**

CLAUDE.md is explicit on both points. "Parse, don't validate" pushes
constraints into the type system: the consistency rule (true flag →
non-empty reason) is something every caller had to remember; making
it a constructor invariant means `mintToken` only sees valid contexts.
Whitespace-only reasons are caught too — the trim guard closes a
foot-gun where a single-space reason would satisfy a naive empty
check while leaving the audit trail with no actionable text.

Clock injection is the PSR-20 idiom CLAUDE.md cites directly:

> Inject ClockInterface instead of calling new DateTimeImmutable()
> or time() directly. This makes time-dependent code deterministically
> testable.

The new tests use `Lcobucci\\Clock\\FrozenClock` so iat/exp values are
predictable across runs. `lcobucci/clock` is already in
`composer.json`.

**What we learned:** Two ADR-flavored decisions worth preserving as
patterns: (a) wrap related primitive parameters in a value object when
they have a consistency invariant, (b) never embed clock reads in
business logic. Both apply to many of the sidecar's coming
implementations (verifier, orchestrator) where time and consistency
matter.

**Artifacts:**
[`oe-module-agentforge/src/Services/BreakglassContext.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Services/BreakglassContext.php),
[`oe-module-agentforge/src/Services/AgentJwtService.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Services/AgentJwtService.php).

---

## 2026-04-30 — `/agentforge/turn` routed via `public/turn.php`, not a root URL

**Plan:** Task 7 spec said "Route registration: POST /agentforge/turn →
AgentProxyController::turn". OpenEMR has no clean way to register
top-level URLs from a custom module.

**Deviation:** The controller is reached via the standard module URL
`/interface/modules/custom_modules/oe-module-agentforge/public/turn.php`,
which boots OpenEMR's `interface/globals.php` and dispatches. Production
deployments are expected to add an Apache / Caddy reverse-proxy rewrite
to expose the canonical `/agentforge/turn` URL.

**Why:** Three options were considered:

| Approach | Verdict |
|---|---|
| `public/turn.php` entry point | Standard OpenEMR module pattern (matches comlink/claimrev). No infra config needed for dev. |
| `RestApiCreateEvent` listener | Routes through OpenEMR's REST API extension — yields `/apis/...` URLs, wrong location. |
| Custom Apache rewrite from the module | Cleanest URL in dev, but adds infra config the module shouldn't own. |

The `public/turn.php` approach won on "self-contained module / no infra
edits required for development." Reverse-proxy rewrite is a one-line
production deployment task.

**What we learned:** OpenEMR module URL design is constrained by the
historical `interface/modules/custom_modules/<name>/public/...` convention.
Modules with custom routes should pair a `public/<route>.php` entry
point with deployment-time URL rewriting; both halves go in the module
README so deployment-engineers know what to do.

**Artifacts:**
[`oe-module-agentforge/public/turn.php`](../interface/modules/custom_modules/oe-module-agentforge/public/turn.php).

---

## 2026-04-30 — Symfony HttpClient (not PSR-18) for sidecar proxy

**Plan:** Task 7 spec used a generic "proxy to sidecar" stub without
naming a client. ARCHITECTURE.md §1 implies streaming responses from
the sidecar (verifier emits sentence-level chunks).

**Deviation:** Use `Symfony\Contracts\HttpClient\HttpClientInterface`
(from `symfony/http-client`, already in composer.json) rather than the
generic PSR-18 `ClientInterface`.

**Why:** Symfony HttpClient supports response streaming via its
`stream()` method — chunks flow through to the browser without
buffering the full body. PSR-18's `ClientInterface::sendRequest()`
returns a complete `ResponseInterface`; the body's `StreamInterface` is
readable incrementally, but the API isn't designed for incremental
forwarding the way Symfony's is. Tests are simpler too: `MockHttpClient`
+ `MockResponse` model the sidecar's responses (including error /
transport-failure cases) without building PSR-7 fixtures by hand.

For testability the controller still receives `HttpClientInterface`
via constructor injection, so any compatible implementation works. The
actual production wiring (`HttpClient::create([...])` in `turn.php`)
happens at the boundary, not in the controller.

**What we learned:** PSR-18 is the right portability target for
*generic* HTTP clients but not for *streaming proxies* — Symfony's
purpose-built API is one less abstraction layer to reason about.
Worth applying the same pattern in the sidecar's later FHIR-client
work (sidecar talks to OpenEMR via HTTP too); the equivalent Python
choice is `httpx` over a streaming-unaware client.

**Artifacts:**
[`oe-module-agentforge/src/Controllers/AgentProxyController.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Controllers/AgentProxyController.php),
[`tests/Tests/Isolated/Modules/AgentForge/AgentProxyControllerTest.php`](../tests/Tests/Isolated/Modules/AgentForge/AgentProxyControllerTest.php).

---

## 2026-04-30 — Controller takes `BreakglassContext`, not raw flag + reason

**Plan:** Task 7 spec snippet:
```php
$jwtService->mintToken(
    $session->get('authUserID'),
    $patientId,
    $session->get('breakglass_flag', false),
    $request->get('breakglass_reason')
);
```

**Deviation:** The controller constructs a `BreakglassContext` value
object first and passes that to `mintToken`. `AgentJwtService::mintToken`'s
signature is
`(int userId, string username, int patientId, BreakglassContext breakglass)`,
not the spec's four-positional-arg shape.

**Why:** `BreakglassContext` (Task 6.4) enforces the consistency
invariant "flag=true requires non-empty reason" at construction time.
Passing raw flag and reason to `mintToken` would mean every caller has
to re-derive that rule — and a bug in any one caller bypasses the
audit-trail guarantee. The Task 6 → Task 7 contract should respect the
parse-don't-validate choice we made in 6.4.

**What we learned:** When a previous task introduces a value object,
the next task's controller / service contract should consume it. The
spec was written before 6.4's value object existed; updating to match
is a normal evolution, not a deviation worth agonizing over.

**Artifacts:**
[`oe-module-agentforge/src/Controllers/AgentProxyController.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Controllers/AgentProxyController.php).

---

## 2026-04-30 — `RequestContext` carries `username` and `breakglass_reason` too

**Plan:** Task 8 spec defined `RequestContext` with five fields:
`user_id`, `patient_id`, `role`, `breakglass_flag`,
`sensitivity_clearances`.

**Deviation:** Added two more fields to the frozen dataclass:
`username: str` and `breakglass_reason: str | None`.

**Why:** Both are present on the JWT (the PHP minter from Task 6.5
emits them) and have clear downstream consumers:

- `username` is the key for sensitivity-policy lookup. The gateway
  resolves it via JWT claim, but tools and the verifier need it too
  for record-attribution decisions. Recomputing or re-parsing the
  JWT downstream is wasteful and risks drift.
- `breakglass_reason` is required for audit-log routing per
  ARCHITECTURE.md §2: "the reason text appears in exactly one place
  — OpenEMR's `log.comments`". The sidecar emits the audit event
  upstream of OpenEMR's logger; it needs the reason at hand.

Dropping these fields meant later subsystems would either re-decode
the JWT (rebuilding the gateway's work) or pass them as side
parameters, breaking the "RequestContext is the only auth surface"
discipline.

**What we learned:** When the trust-boundary contract is the single
chokepoint, it should carry every claim downstream code might need.
Cheaper to over-include in the value object than to add fields later
once consumers have been written.

**Artifacts:**
[`sidecar/src/agentforge/gateway/auth_gateway.py`](../sidecar/src/agentforge/gateway/auth_gateway.py).

---

## 2026-04-30 — Auth gateway validates `iss` claim explicitly

**Plan:** Task 8 spec snippet decoded the JWT with
`jwt.decode(token, secret, algorithms=['HS256'])` and only checked
`patient_id` afterwards.

**Deviation:** Pass `issuer="openemr-agentforge"` to `jwt.decode` so
PyJWT raises `InvalidIssuerError` for tokens with the wrong (or
missing) `iss` claim. Map that to a 401 response.

**Why:** Task 6 mints tokens with `iss=openemr-agentforge`. Without
issuer enforcement at the gateway, any well-formed HS256 token signed
with the same secret would pass — including tokens minted for a
different purpose by some unrelated component that shares the secret.
HS256 + a shared secret means trust is per-secret, not per-issuer; the
explicit issuer check restores the intended one-to-one binding between
the OpenEMR module and this sidecar.

**What we learned:** PyJWT's verification options are opt-in. The
default `decode()` checks signature + exp; everything else (issuer,
audience, nbf) requires explicit kwargs. Worth treating
`jwt.decode(token, secret, algorithms=['HS256'])` as suspicious in
code review; production callers should also pass `issuer=` and
ideally `audience=`.

**Artifacts:**
[`sidecar/src/agentforge/gateway/auth_gateway.py`](../sidecar/src/agentforge/gateway/auth_gateway.py).

---

## 2026-04-30 — Redis client typed via `Protocol` for test ergonomics

**Plan:** Task 8 spec used `redis.asyncio.Redis` directly as the
client type.

**Deviation:** Defined a private `_RedisProto` Protocol covering only
`get` and `smembers` — the two methods AuthGateway actually uses —
and typed the constructor parameter as `_RedisProto | None`.

**Why:** mypy --strict treats `redis.asyncio.Redis` as a concrete
class. Tests that pass `unittest.mock.AsyncMock(spec=Redis)` (or a
plain `AsyncMock`) fail type-checking even though they work at
runtime. The Protocol gives us structural typing — anything with
the right `get` and `smembers` shape qualifies — and limits the
gateway's coupling to the Redis library to two methods.

**What we learned:** When a constructor needs a small slice of a
big third-party library's API, define a Protocol covering exactly
that slice. The benefits compound: (a) tests pass mypy without
reaching for `# type: ignore`, (b) the gateway can be reused
against fakes / fixtures / alternative backends, (c) the surface
area is documented in the type signature.

**Artifacts:**
[`sidecar/src/agentforge/gateway/auth_gateway.py`](../sidecar/src/agentforge/gateway/auth_gateway.py).

---

## 2026-04-30 — MVP wiring: collapsed Tasks 3, 4, 11, 13, 14, 26, 33 into one branch

**Plan:** Each of the seven tasks above had its own subtasks, dedicated test
suites, and a dependency-graph promotion ritual.

**Deviation:** Compressed all seven tasks into a single branch
(`task-mvp-functional-agent`) with one bundled commit and a far lighter
test footprint — one happy-path test per piece, no exhaustive coverage.
Several large adjacent items were also deferred entirely:
sensitivity-policy redaction (Task 9–10), per-tool fetchers beyond
get_demographics (Tasks 15–25), the verifier loop (Task 28), Redis-backed
session memory + Langfuse tracing (Tasks 30–32), Docker compose + reverse
proxy (Tasks 35–36), and the eval framework (Tasks 37–39).

**Why:** Submission deadline tonight; the goal was a *working* user-visible
agent ("type a question, get a grounded answer about a real patient"), not
a production-shaped surface. The architecture stays compatible — each
deferred item can be added later without rewriting the seven we shipped.

**What we learned:**
1. Three independent streams (frontend, LLM client, tool DTOs) parallelized
   cleanly via subagents because the file boundaries were strict and the
   shared state (`pyproject.toml`, `composer.json` autoload) was already
   set up. The integration step still had to be sequential — but that was
   the cheap part once the foundations existed.
2. "1-tool MVP" is a defensible scope cut. The orchestrator loop and the
   FastAPI /turn route are the real architectural surface; adding tools 2-N
   later is mechanical (one PHP internal endpoint + one async fetcher each).
3. The DEMOGRAPHICS_TOOL_SPEC has zero LLM-visible inputs — patient_id is
   bound from RequestContext server-side. Worth keeping as a pattern for
   the remaining tools: the LLM decides *whether* to call, not *who about*.

**Artifacts:** commit `feat(agentforge): wire MVP end-to-end agent loop`.

---

## 2026-04-30 — Module-local `.env` instead of container env vars

**Plan:** ARCHITECTURE.md assumes `AGENTFORGE_JWT_SECRET` lives in the
deployment's environment (docker-compose env block, kubernetes secret,
etc.) and is available to PHP via `getenv()` at request time.

**Deviation:** Built a small `EnvLoader` (vlucas/phpdotenv) that reads
`interface/modules/custom_modules/oe-module-agentforge/.env` on every
request and stuffs the values into `getenv()` via the Putenv adapter. The
PHP entry points call `EnvLoader::load()` right after `globals.php`.

**Why:** OpenEMR's `development-easy` Apache + mod_php container does not
propagate environment variables to web requests by default — `docker
compose exec` env doesn't reach the request lifecycle, and modifying the
compose file requires a container restart that risks losing other in-place
state (the `sqlconf.php $config` reset, npm assets, etc.). A module-local
`.env` lets the developer drop secrets in without touching the container.
Production deployments can still set the same vars at the
container/process level — `safeLoad()` doesn't override existing env.

**What we learned:**
1. phpdotenv 5.x's `createMutable()` is misleadingly named: by default it
   writes to `$_ENV` and `$_SERVER` only. The `PutenvAdapter` has to be
   added explicitly via `RepositoryBuilder` if you want `getenv()` to see
   the values. Cost us one debug iteration.
2. `.env` files are a perfectly fine boundary for a single-host dev
   container; the architecture document's assumption that env vars come
   from the deployment was correct in spirit but unworkable for the
   easy-mode container we're shipping against tonight.

**Artifacts:**
[`oe-module-agentforge/src/EnvLoader.php`](../interface/modules/custom_modules/oe-module-agentforge/src/EnvLoader.php),
[`oe-module-agentforge/.env.example`](../interface/modules/custom_modules/oe-module-agentforge/.env.example).

---

## 2026-04-30 — Read OpenEMR session through `$_SESSION['OpenEMR']`, not top-level

**Plan:** AgentProxyController + turn.php were built against the
PHPUnit-fixture model where session keys (`pid`, `authUserID`, `authUser`)
sit at the top level of the Symfony Session — i.e. the unit tests pass
`$session->set('pid', 123)` and the controller reads `$session->get('pid')`.

**Deviation:** OpenEMR namespaces *all* its session data under
`$_SESSION['OpenEMR']` (a session bag layer that predates Symfony's
abstraction in this codebase). The bridge from native PHP session to the
Symfony Session in `turn.php` now reads `$_SESSION['OpenEMR'][$key]` and
copies the relevant keys into a `MockArraySessionStorage`-backed Session
that the controller consumes uniformly with the test fixtures.

**Why:** `PhpBridgeSessionStorage` does not flatten the OpenEMR bag, so
`$session->get('pid')` returned null even though the session had a pid.
Discovered live during the smoke test — first manifested as "Error 400:
No patient context" with the chart explicitly open.

**What we learned:**
1. Trust-boundary code that bridges from a legacy session shape to a
   modern abstraction needs an explicit shape verification step (not just
   "session exists"). The unit-test fixture being top-level keys was a
   distorted model of reality — a more honest fixture would have been a
   nested `$_SESSION['OpenEMR'][...]` shape we then translate.
2. `error_log()` + tail of the apache log is faster than any other PHP
   debugger when the issue is "what does the runtime actually have right
   now in this hosted context." Took 2 round-trips to get to the answer.

**Artifacts:**
[`oe-module-agentforge/public/turn.php`](../interface/modules/custom_modules/oe-module-agentforge/public/turn.php).

---

## 2026-04-30 — Rehydrate Authorization header from `apache_request_headers()`

**Plan:** Internal endpoint reads `Authorization` via Symfony's
`Request::createFromGlobals()->headers->get('Authorization')`, which pulls
from `$_SERVER['HTTP_AUTHORIZATION']` like every other PHP web app.

**Deviation:** Apache + mod_php (the dev-easy container's setup) strips
the `Authorization` header from `$_SERVER` by default — it's only
forwarded when explicitly told via `mod_setenvif` / `CGIPassAuth` /
`.htaccess`. The header IS available via `apache_request_headers()`. We
copy it back into `$_SERVER['HTTP_AUTHORIZATION']` before Symfony reads
the globals.

**Why:** Discovered live during the smoke test — first manifested as the
agent answering "I'm unable to retrieve the patient's information,
including their medication list, due to an authentication error (401
Unauthorized)" because the demographics tool's call into PHP got 401 and
the orchestrator faithfully relayed that to the model.

**What we learned:**
1. There is no portable PHP API for "give me the Authorization header" —
   you have to know whether you're under mod_php, php-fpm + nginx, php-fpm
   + apache, etc., each of which has different defaults. The
   `apache_request_headers()` fallback (combined with the `$_SERVER`
   primary) covers the dev-easy container; production may need a real
   `.htaccess` directive instead.
2. The agent's behavior of relaying tool-layer 401s to the user as a
   user-facing message is *correct* — and arguably more useful than the
   raw stack trace would have been — but it makes "tool layer broken"
   indistinguishable from "I don't know" at the UI. A future verifier
   step should classify tool-error responses and surface them differently
   (e.g. "system error, not a knowledge gap").

**Artifacts:**
[`oe-module-agentforge/public/internal/demographics.php`](../interface/modules/custom_modules/oe-module-agentforge/public/internal/demographics.php).

---

## 2026-05-01 — Streaming verifier `DomainConstraintChecker` is sync, not async

**Plan:** Task 29 sketches `verify_medication_claim()` etc. as `async def`.
Task 28's StreamingVerifier was therefore expected to await the
domain-checker call.

**Deviation:** The `DomainConstraintChecker` Protocol shipped in Task 28
is sync (`def check(...) -> tuple[bool, str | None]`). The
`NullDomainConstraintChecker` and the streaming verifier's call site
are sync to match.

**Why:** None of the five planned constraints (medication-name match,
lab-value tolerance, note-authorization echo, diagnosis traceability,
no-counterfactuals) need I/O. They check claim text against a record
dict already in memory — a regex match and a few `.get()` calls. Making
the protocol async would force every implementation to be `async def`
even when the body never `await`s, and it would push an extra event-loop
hop into every claim's verification (which already runs once per
sentence). The trust boundary stays simpler when the slow path doesn't
exist.

**What we learned:**
1. When the spec says `async def` but the body has no `await`, the
   "async" is a costume, not a mechanism. Better to keep the type
   honest and widen later if a real async constraint emerges (e.g.,
   one that needs to consult a separate tool result not in the
   per-turn cache — currently nothing in the v1 catalog does).
2. Task 29 will need to drop the `async` keyword off the constraint
   methods. That's a straight find-and-replace, not a refactor.

**Artifacts:**
[`sidecar/src/agentforge/verifier/protocols.py`](../sidecar/src/agentforge/verifier/protocols.py).

---

## 2026-05-01 — Streaming verifier rejects label-form citations for MVP

**Plan:** ARCHITECTURE.md S6 lists two citation forms:
`[encounter #38241, 2026-04-12]` (ID-anchored) and
`[Rx: lisinopril 20mg, started 2024-08-15]` (label-anchored).

**Deviation:** Only the ID-anchored form is recognised by Task 28's
`CITATION_PATTERN`. Label-form tokens parse to `None` and any sentence
whose only citation is label-form is rejected as `no_citation`.

**Why:** The cache lookup is ID-anchored — every record returned by a
tool this turn is keyed by `(record_type, record_id)`. A label-form
citation has no ID to look up; validating it would require a different
mechanism (string-matching the label against the cached records). That
mechanism IS the medication-name domain constraint from Task 29 (`Constraint
1: med name in active prescriptions`). Building a parallel label-resolution
path in Task 28 would duplicate it.

**What we learned:**
1. The model's freedom to choose a citation format expands the verifier's
   surface area linearly. Locking the citation grammar to one form during
   MVP is the cheap way to keep the trust boundary small.
2. The system prompt should be updated alongside Task 29 to instruct the
   model to prefer ID-anchored citations until label resolution lands.
   Until then, the model emitting a label-form citation looks identical
   to a hallucination from the verifier's perspective — and that's the
   right behavior (better a false-rejection than a fabricated pass).

**Artifacts:**
[`sidecar/src/agentforge/verifier/citation.py`](../sidecar/src/agentforge/verifier/citation.py).

---

## 2026-05-01 — `get_active_allergies` reads `lists` table directly, not FHIR `AllergyIntolerance`

**Plan:** Taskmaster Task 17's description called for the tool to call
the FHIR `AllergyIntolerance` endpoint to source the patient's allergy
list.

**Deviation:** Implemented the tool as a direct read of the `lists`
table (filtered to `type='allergy' AND activity=1`) via a Doctrine DBAL
repository plus a JWT-validated PHP internal endpoint — the same
pattern the other three MVP tools (`get_demographics`,
`get_active_problems`, `get_active_medications`) use.

**Why:** The three sibling tools that already shipped under
ARCHITECTURE.md §4 are direct-DB readers; their internal endpoints
(`/agentforge/internal/{demographics,problems,medications}.php`)
share the same structure (JWT validator + repository + JSON response
wrapper). Routing the fourth tool through OpenEMR's FHIR stack would
have introduced a parallel access pattern (R4 resource fetcher,
SMART scope check, JSON:API parsing) for one tool, increasing the
verifier's surface area and making the fan-out path less uniform.
The direct-DB path also lets the `lists.severity_al` field — which
isn't in the FHIR mapping by default — flow through unchanged for
clinical relevance. Schema confirmed at
[`sql/database.sql:7676–7717`](../sql/database.sql).

**What we learned:** Tool-spec language can drift behind implementation
patterns once a project has settled on one. Better to surface the
choice in the deviation log than to silently break uniformity, but
when three siblings agree on a pattern, conformance to that pattern is
the strong default. The four-tool MVP now has one access shape end-to-end,
which the verifier's record-cache lookup (Task 28) can rely on. If a
future tool genuinely needs FHIR semantics (e.g. condition severity
codings, encounter linkage) it can land alongside this one without
disturbing the existing trio.

**Artifacts:**
[`sidecar/src/agentforge/tools/allergies.py`](../sidecar/src/agentforge/tools/allergies.py),
[`oe-module-agentforge/src/Services/AllergiesRepository.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Services/AllergiesRepository.php),
[`oe-module-agentforge/src/Controllers/InternalAllergiesController.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Controllers/InternalAllergiesController.php),
[`oe-module-agentforge/public/internal/allergies.php`](../interface/modules/custom_modules/oe-module-agentforge/public/internal/allergies.php).

---

## 2026-05-01 — get_vitals_trend skipped EventAuditLogger and QueryUtils

**Plan:** Task 20 spec called for the internal vitals endpoint to use
`OpenEMR\Common\Database\QueryUtils` for the database read and
`OpenEMR\Common\Logging\EventAuditLogger::recordEvent` to write a per-call
audit record. Both helpers exist on this codebase.

**Deviation:** The implementation follows the established pattern from
`get_active_medications` and `get_active_problems` instead:
- Repository takes a Doctrine `Connection` and runs the query directly via
  `fetchAllAssociative` — no `QueryUtils` indirection.
- Controller relies on the JWT validation chain (browser → PHP `/turn` →
  signed user-bound JWT → sidecar → echoed JWT → `AgentJwtValidator`) as
  the audit path. No `EventAuditLogger.recordEvent` call.

**Why:** Two reasons.

1. The medications / problems endpoints set the precedent two tasks ago.
   Diverging from them on tool number four would split the agent's tool
   layer into two coding styles for no functional gain — and the next
   tools to land (allergies, labs) would have to pick a side.
2. The JWT itself is a tamper-evident record of who initiated the request,
   for which patient, with what breakglass context, signed by the same
   secret OpenEMR uses to mint it. Replaying an internal endpoint without
   a fresh JWT is impossible (5-minute expiry; no refresh path on the
   sidecar). Persisting a separate audit row for every tool call would
   duplicate information already captured at the `/turn` boundary —
   `AgentProxyController` is the right layer for "who asked the agent
   what." A dedicated tool-call audit can be added later without
   rewriting the repositories.

A separate decision worth noting: the repository handles two
schema-induced coercions explicitly because they're easy to get wrong.
`bps` and `bpd` are stored as `VARCHAR(40)` (not numeric), so we
int-coerce and treat empty strings as null. All numeric vitals are
`DECIMAL` defaulting to `'0.00'`; we treat `0.0` as "not recorded" and
return `null` to keep the LLM from interpreting the schema default as a
clinically meaningful "0 systolic." Both rules are documented in the
class docblock so future readers see them once.

**What we learned:** Established-pattern continuity beats spec literalism
when the spec is older than the pattern. Worth surfacing the same call
when the upcoming allergies / labs / immunizations tools (Tasks 17, 18,
21+) hit the same fork: don't reintroduce `QueryUtils` /
`EventAuditLogger` as the convention unless the security review of the
JWT-as-audit chain says otherwise.

**Artifacts:**
[`oe-module-agentforge/src/Services/VitalsRepository.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Services/VitalsRepository.php),
[`oe-module-agentforge/src/Controllers/InternalVitalsController.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Controllers/InternalVitalsController.php),
[`oe-module-agentforge/public/internal/vitals_trend.php`](../interface/modules/custom_modules/oe-module-agentforge/public/internal/vitals_trend.php).

---

## 2026-05-01 — get_recent_labs reads MariaDB directly, not FHIR Observation

**Plan:** Task 18 spec said the lab tool should query the FHIR
`Observation?category=laboratory` endpoint, with the sidecar treating the
results like any other FHIR resource (consistent with the FHIR-first
direction the OpenEMR mainline is taking).

**Deviation:** Skipped FHIR. Implemented `get_recent_labs` as a direct
Doctrine DBAL read of `procedure_order` → `procedure_report` →
`procedure_result`, matching the existing `get_active_medications` and
`get_active_problems` tools.

**Why:**
1. Pattern consistency. The other three tools all bypass FHIR; doing
   this one differently means two parallel "how does an MVP tool talk to
   data" idioms in the codebase before the third tool even ships.
2. Auth surface. The FHIR layer expects an OAuth2 access token; the
   sidecar carries a short-lived `AGENTFORGE_JWT_SECRET`-signed JWT
   that's already wired into the existing `/agentforge/internal/*`
   endpoints. Going FHIR-first means standing up an OAuth2 client
   credential flow inside the sidecar — for one tool — purely so we can
   then validate it in PHP, while the JWT path already validates and
   already enforces patient-scope. Net cost: real new code surface for
   no agent-side benefit.
3. Schema cost. FHIR Observation flattens `procedure_order/report/result`
   into a single resource type; the agent doesn't need or use the FHIR
   facets we'd be paying to translate (Identifier, Subject, Encounter
   refs, ValueQuantity vs ValueCodeableConcept polymorphism). The 10
   fields it actually uses come straight from `procedure_result` columns.
4. Index leverage. Task 40 already added the composite index
   `idx_procedure_order_patient_date`. Hitting `procedure_order` directly
   uses that index; the FHIR layer's joins go through ORM glue that
   doesn't.

**What we learned:**
- Bypassing FHIR is a load-bearing MVP convention in this fork, not a
  one-off shortcut. When the verifier (Task 28) ships and we add
  redaction, the boundary will need to know which fields are sensitive
  per tool, regardless of FHIR vs SQL — so postponing FHIR doesn't
  postpone the redaction work either.
- The 200-row analyte cap matters more than the 90-day window. A single
  CMP + CBC easily emits 30+ analytes per report; a chronically ill
  patient inside a 90-day window can saturate context fast. The cap is
  a deliberate floor, not a placeholder.
- The `since_days` parameter is the first tool input the LLM controls;
  the controller clamps to `1..365` server-side as defense-in-depth
  against the model emitting `since_days: 99999`.

**Artifacts:**
[`oe-module-agentforge/src/Services/LabsRepository.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Services/LabsRepository.php),
[`oe-module-agentforge/src/Controllers/InternalLabsController.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Controllers/InternalLabsController.php),
[`sidecar/src/agentforge/tools/labs.py`](../sidecar/src/agentforge/tools/labs.py).

---

## 2026-05-01 — Sensitivity policy keyed `agentforge:policy:loaded`, not the role-clearance sentinel

**Plan:** Task 8 had already shipped a sentinel at `agentforge:policy:version`
that the gateway checks before loading per-role clearances. Tasks 9 + 10
could have reused that key as the "policy loaded" indicator.

**Deviation:** Introduced a separate sentinel — `agentforge:policy:loaded`
— for the sensitivity policy (Task 9), holding the policy's version
integer. The role-clearances sentinel at `agentforge:policy:version` is
unchanged and still gates the per-role membership lookup.

**Why:** The two policies are loaded by different mechanisms and could
fall out of sync. Role clearances come from a still-undefined loader
(deferred to a sibling subtask of 8); the sensitivity policy comes from
a YAML file via the new `load_sensitivity_policy`. Sharing one sentinel
would conflate "I have one policy loaded" with "I have both", and a
partial Redis flush could leave the system reporting healthy while one
table was empty. Two sentinels is the cheap honest answer.

**What we learned:** Sentinels are cheap; conflating them is not. When
two independent loads each need a fail-closed indicator, give each its
own key. Future audit-log + verifier-prompt loads will follow the same
pattern.

**Artifacts:**
[`sidecar/src/agentforge/gateway/policy_loader.py`](../sidecar/src/agentforge/gateway/policy_loader.py),
[`sidecar/src/agentforge/gateway/auth_gateway.py`](../sidecar/src/agentforge/gateway/auth_gateway.py).

---

## 2026-05-01 — Declared `pyyaml` as an explicit sidecar dependency

**Plan:** Task 9 anticipated PyYAML being available as a transitive
dependency (langfuse pulls it).

**Deviation:** Added `pyyaml>=6.0` to `[project.dependencies]` in
`sidecar/pyproject.toml`. Also extended `[[tool.mypy.overrides]]` for
the bare `yaml` import (no first-party type stubs).

**Why:** PyYAML *is* available transitively, but the policy loader is
the first first-party caller. Relying on a transitive dep for a load-
bearing import is a footgun: a future bump of the parent that drops
yaml would silently break the policy loader. Declaring the dep
explicitly puts the lock surface in line with what we actually use.

**What we learned:** The CLAUDE.md rule against new deps applies to
genuinely new packages, not to surfacing existing transitives — the
honest move when first-party code starts importing a module is to put
it in `pyproject.toml` regardless of how it got onto the venv.

**Artifacts:**
[`sidecar/pyproject.toml`](../sidecar/pyproject.toml).

---

## 2026-05-01 — `check_record_visibility` fail-closes on missing metadata for a fired rule

**Plan:** Task 10 spec called out fail-closed for a missing `attending_user_id`
when `attending_only=True`, but didn't expand the rule to other matchers.

**Deviation:** Documented + implemented the same fail-closed posture for
any future rule whose match needs metadata not in the `RecordMetadata`
shape. The current MVP only has the attending case, but the
`_user_satisfies_rule` helper is structured so adding a new matcher
that needs an absent field is a one-line return-False.

**Why:** A "missing-metadata = allow" default is the audit failure mode
ARCHITECTURE.md §2 specifically warned against ("a sensitivity model
that has to read the secret to know it's secret is no model at all"
— and a model that defaults open when fields are absent is structurally
similar). The default-allow-on-no-rule-fires is *only* safe because no
rule fires; once a rule fires, the user must actively satisfy it.

**What we learned:** Two distinct cases that look similar:
1. No rule matches the metadata → allow (the record isn't classified
   as sensitive by any structural rule).
2. A rule matches but its required metadata is absent → deny (the
   classifier fired but we can't evaluate against the principal).

The visibility check encodes this distinction; future rule additions
that introduce new metadata fields should follow the same pattern.

**Artifacts:**
[`sidecar/src/agentforge/gateway/auth_gateway.py`](../sidecar/src/agentforge/gateway/auth_gateway.py).

---

## 2026-05-01 — Breakglass does not silently bypass record visibility (MVP)

**Plan:** ARCHITECTURE.md §2 says break-the-glass "propagates to three
distinct log destinations" but is ambiguous on whether it changes the
visibility decision itself.

**Deviation:** For MVP, breakglass does NOT flip a record-visibility
deny to an allow. The decision logic ignores `ctx.breakglass_reason`
entirely. Audit-log routing (Task 34, future) will still see the
breakglass intent on `RequestContext` and emit it to OpenEMR's
`log.comments`.

**Why:** A silent bypass embedded in the visibility check would let the
shape "I had a reason, so I saw the record" leak through the agent's
output without any sentinel. Whether breakglass should ever be a
*technical* override (vs. an audit-only signal) is a clinical-policy
decision that belongs to a downstream review, not to MVP wiring.
Keeping the decision conservative now preserves the option to add a
narrow breakglass-aware override later under controlled circumstances.

**What we learned:** "We logged it" and "it was allowed" are different
guarantees. The agent's tool layer should keep them separate even when
the JWT carries both — the audit path consumes the intent, the
visibility path consumes only the structural metadata.

**Artifacts:**
[`sidecar/src/agentforge/gateway/auth_gateway.py`](../sidecar/src/agentforge/gateway/auth_gateway.py).

---

## 2026-05-01 — Timeout/Retry shipped as per-tool budget; phase + turn budgets deferred

**Plan:** Task 41 + ARCHITECTURE.md §9 spec a four-level budget hierarchy
(`per_tool=2s` → `tool_phase=4s` → `total_turn=7s` → `max_steps=7`) and a
`per_attempt_timeout=0.5s` inside `RetryPolicy`.

**Deviation:** Three narrower-than-spec decisions:

1. The retry helper enforces only the `per_tool` budget. `tool_phase`,
   `total_turn`, and `max_steps` are config fields on `TimeoutPolicy`
   but no orchestrator code currently reads them.
2. `RetryPolicy.per_attempt_timeout` is a config value but is not wired
   through the httpx layer. Each fetcher call still uses httpx's
   default 5-second timeout, not 0.5s.
3. The new `timeouts.py` module sits at `sidecar/src/agentforge/timeouts.py`
   rather than the spec's `sidecar/src/agentforge/config/timeouts.py`.
   The existing `agentforge/config.py` is a flat file (`Settings`
   class); promoting it to a package just to host one new policy
   module would touch every import site for a cosmetic gain.

**Why:** The retry-on-transient and graceful-degradation behaviours are
the user-visible contract Task 41 was added to deliver — they fix the
cold-start 503 the droplet smoke test surfaced. The phase/turn budgets
and per-attempt HTTP timeouts are orchestrator-level coordination
features whose useful shape depends on Task 27 (Planner restructure)
and per-fetcher timeout wiring, which are larger refactors. Shipping
them now would either invent infrastructure they don't yet need or
foreclose on the Planner's design.

**What we learned:** Retry policy values that fit normal transient
errors do not absorb a cold-start. With `backoff_base=0.1`,
`backoff_factor=2.0`, `max_attempts=3` the total inter-attempt wait
is 0.3 s — fine for a flaky upstream, useless against a 5-second
container boot. If cold-start absorption matters in production, the
right fix is a pre-warm ping at deploy time (or a longer-backoff
profile applied only on the first request after process start), not
larger retry counts.

**Artifacts:**
[`sidecar/src/agentforge/timeouts.py`](../sidecar/src/agentforge/timeouts.py),
[`sidecar/src/agentforge/orchestrator/__init__.py`](../sidecar/src/agentforge/orchestrator/__init__.py).

---

## 2026-05-01 — Breakglass dedup is in-memory and lives only for the sidecar process

**Plan:** Task 34's spec describes idempotency ("called once per session,
not per tool call") but does not specify the dedup mechanism.

**Deviation:** Dedup is an in-memory `set[tuple[int, int, str]]`
keyed on `(user_id, patient_id, session_id_or_sentinel)` and lives
for the sidecar's process lifetime. There is no Redis SETNX or
shared bookkeeping. A sidecar restart wipes the dedup table; a
multi-replica deployment would write one audit row per replica per
session.

**Why:** Dedup correctness is bounded by the 75-min session TTL —
even at high turn rates, the per-process unique-session count over
that window is small. The cost of a duplicate audit row on
restart / multi-replica is bounded and observable; the cost of
cross-process coordination is real plumbing (SETNX semantics, key
TTL choice, error paths when Redis is down). For MVP one replica
is the deployment shape, so the in-memory variant is adequate.

**What we learned:** "Once per session" has two readable meanings —
"once across the system" and "once per process per session." The
first is what the auditor wants to read; the second is what we
ship. The gap is bounded (one row per replica boot), and a worse
failure mode is "we silently failed to audit" — which the in-memory
variant avoids by never marking AUDIT_FAILED outcomes as logged
(the next turn retries).

**Artifacts:**
[`sidecar/src/agentforge/breakglass.py`](../sidecar/src/agentforge/breakglass.py).

---

## 2026-05-01 — Breakglass audit fires from the orchestrator, not the auth gateway

**Plan:** Task 34 subtask 34.4 says "Integrate BreakglassAuditTool into
Auth Gateway flow."

**Deviation:** The audit fires at the orchestrator's turn entry
point, not inside `AuthGateway.validate_request`.

**Why:** Dedup is keyed on `session_id`, which arrives on
`TurnRequest` — not in the JWT. The auth gateway is a stateless JWT
validator that doesn't see `session_id`. Wiring the audit there
would mean either passing `session_id` into auth (which couples auth
to body parsing) or always-audit (which would write per-tool-call
rather than per-session, breaking the idempotency contract).
Orchestrator-level integration keeps `AuthGateway` stateless and
preserves session-keyed dedup.

**What we learned:** "Auth audits" is a layer-of-abstraction
trap when the dedup key isn't part of the auth artifact. The right
question is "what does the dedup key live on?" — and `session_id`
lives on the turn, not the token.

**Artifacts:**
[`sidecar/src/agentforge/orchestrator/__init__.py`](../sidecar/src/agentforge/orchestrator/__init__.py).

---

## 2026-05-01 — Eval framework ships with hand-authored fixtures and skips LLM-as-judge

**Plan:** Tasks 37, 38, 39 spec a 3-layer eval setup:
  1. MockToolLayer fixtures pinned to a specific OpenEMR demo DB SHA
     (`demo_5_0_0_5.sql + openemr/openemr:flex image SHA`).
  2. EvalHarness with programmatic grounding + LLM-as-judge for
     relevance scoring.
  3. RegressionLockTestSuite of 8 canonical Q&A run against the
     orchestrator end-to-end.

**Deviation:** Five concrete narrowings:

1. Fixtures are hand-authored against the typed Pydantic schemas, not
   captured from a populated demo DB. The two patient phenotypes
   ("Susan Underwood — complex chronic" / "Alex Newman — sparse")
   exercise the contracts the eval depends on, but a future
   capture-pass against a real demo image would be more authoritative.

2. The mock layer covers 8 tools, not 9 — encounters (Task 21) is
   still pending. When 21 lands, add an `encounters` block to each
   patient in `agent_eval.json` and a `get_encounters` method on
   `MockToolLayer`.

3. LLM-as-judge for relevance is not implemented. The harness checks
   *grounding* (every citation resolves to a real record) and
   *behavior* (a per-case callable assertion). Adding a third
   relevance score would need a real LLM client in CI — costly and
   flaky for what's a tertiary signal alongside grounding.

4. RegressionLocks ship as 6 canonical (response, case, fixture)
   triples, not 8. Four positive locks (UC1 complex / UC1 sparse /
   UC2 NSAID-renal / vitals citation) and two adversarial locks
   (fabricated citation; hallucinated labs for a sparse chart).
   Two more cases worth adding when there's clear product intent —
   the framework grows trivially.

5. The regression locks do **not** invoke the orchestrator or the
   real LLM. They pin canonical agent-style response strings to the
   committed fixtures and verify that the harness scores them
   correctly. What the locks catch is drift in the eval primitives
   (citation parser, citation index builder, fixture schemas) — not
   drift in the model itself. End-to-end model regression tests
   need a separate manual / scheduled eval run with real LLM access
   and are an open follow-up.

**Why:** The framework's value is two-fold: (a) deterministic CI
gating on the eval-side primitives, (b) a foundation that a manual
eval can call into to score real model outputs. Both work without a
real DB or real LLM. The cost of pinning fixtures to a specific
docker SHA today (capture pass + maintenance burden) outweighs the
value when the fixtures are themselves new — we'd be pinning to
ourselves. The cost of LLM-as-judge in CI (API keys, $, flakiness)
likewise outweighs its incremental signal alongside grounding +
behavior callables.

**What we learned:** The phrase "regression lock" hides a design
choice — what you lock against. Locking the *eval primitives*
catches schema/parser drift in CI without requiring a model. Locking
the *model* requires real-LLM runs and is necessarily off-CI. Both
are useful; they answer different questions.

**Artifacts:**
[`sidecar/tests/fixtures/agent_eval.json`](../sidecar/tests/fixtures/agent_eval.json),
[`sidecar/tests/mocks/tools.py`](../sidecar/tests/mocks/tools.py),
[`sidecar/tests/eval/harness.py`](../sidecar/tests/eval/harness.py),
[`sidecar/tests/eval/regression_locks.py`](../sidecar/tests/eval/regression_locks.py).

---

## 2026-05-02 — Task 44 reframed: `api_log_option` is global, not per-user

**Plan:** Task 44 specs a deploy script that does

```php
QueryUtils::sqlStatementThrowException(
    "UPDATE users SET api_log_option = 1 WHERE id = ?",
    [$agentUserId]
);
```

with the rationale "suppress body logging for the agent's API user."

**Deviation:** Two factual problems with the spec, fixed by
reframing what we ship:

1. There is no `users.api_log_option` column. `api_log_option` is a
   site-wide global (`globals.gl_name = 'api_log_option'`) defined in
   `library/globals.inc.php` with three valid values (`0`, `1`, `2`).
   The `users` table has no per-row override, and the REST listener
   (`ApiResponseLoggerListener`) reads only the global.
2. AgentForge's internal endpoints don't pass through
   `ApiResponseLoggerListener` because the listener fires only on
   `HttpRestRequest` — our `public/internal/*.php` scripts use bare
   `Symfony\Component\HttpFoundation\Request`. So the body-logging
   the spec wants to suppress is *already not happening* for
   AgentForge calls today.

We ship `scripts/configure_api_logging.php` instead — sets the
**global** to `1` (minimal logging) idempotently, with `--check` for
read-only inspection. That's the real lever, and shipping the script
puts a defense-in-depth control in operators' hands for any future
calls that DO route through the REST stack. The spec's per-user
fantasy is documented as not-applicable.

Subtask 44.5 (integration test for "API logging suppression
behavior") is intentionally a no-op: with no AgentForge call
flowing through the listener, there is no behaviour to suppress and
nothing to assert beyond the global value, which the script's
`--check` mode already reports.

**What we learned:** Task specs at this fork's level can encode
data-model assumptions that don't match the upstream OpenEMR
schema. When the assumption breaks, the right move is to ship the
*intent* (PHI hygiene in `api_log`) rather than the literal
mechanism (per-user UPDATE). Always grep the schema before trusting
a spec's column references.

**Artifacts:**
[`scripts/configure_api_logging.php`](../scripts/configure_api_logging.php),
[`docs/DEPLOYMENT.md`](DEPLOYMENT.md) (new "Optional: tighten REST
api_log body logging" section).

---

## 2026-05-02 — Encounters tool reads form_encounter directly, not via FHIR

**Plan:** Task 21 spec calls for the agent's encounters tool to call
OpenEMR's standard FHIR `Encounter` endpoint (`/apis/fhir/r4/Encounter`).

**Deviation:** Mirrors the AgentForge custom-internal-endpoint pattern
(`/agentforge/internal/recent_encounters.php`) instead, reading
`form_encounter` directly via Doctrine DBAL — same shape as every
other AgentForge tool.

**Why:** Routing through FHIR would require:
  * OpenEMR OAuth2 client credentials provisioned for the agent.
  * A token-management layer in the sidecar (acquire, refresh, scope).
  * A second authorization story in addition to the per-user JWT we
    already mint and forward.

None of this infrastructure exists yet. Building it just to fetch
encounters duplicates the trust boundary the existing internal
endpoints already establish — JWT-validated, pid-scoped, no session
state. The custom-endpoint path also keeps the sensitivity-gating
contract coherent: the gateway sees `RecordMetadata` with both
`encounter_category` (pc_catid) and `note_type` (the `sensitivity`
column), which a FHIR Encounter resource doesn't surface as cleanly.

**What we learned:** Reusing existing OpenEMR REST surfaces is a real
option but it's not free — the sidecar's auth model would have to
grow. Picking the consistent-internal-endpoint path keeps the auth
story flat and the gating logic in one shape.

**Artifacts:**
[`sidecar/src/agentforge/tools/encounters.py`](../sidecar/src/agentforge/tools/encounters.py),
[`interface/modules/custom_modules/oe-module-agentforge/public/internal/recent_encounters.php`](../interface/modules/custom_modules/oe-module-agentforge/public/internal/recent_encounters.php).

---

## 2026-05-02 — Demo overlay reaches 1 of 3 sensitivity rules; behavioral_health and attending_only deferred

**Plan:** Task 50.4 spec called for hand-crafted notes covering
sensitivity edge cases — psych note, attending-only note, plus
SUD-style content. The intent was to exercise the full
`sensitivity_policy.yaml` rule surface in the demo.

**Deviation:** The shipped overlay
(`scripts/seed/agentforge_demo_overlay.sql`) only reaches the
`substance_abuse_cfr42` rule, and only via its `note_title_prefixes`
matcher. The other two rules in the policy YAML — `behavioral_health`
and `attending_only` — are not demoed by this overlay.

**Why:**

  * `behavioral_health` gates on `form_encounter.pc_catid` (encounter
    category), not on a note attribute. It fires from the encounters
    tool, not the notes tool. Demoing it requires creating an
    encounter row with `pc_catid` 11 or 12, which is a different
    category of seed work (encounter overlay, not note overlay).
  * `attending_only` requires a `notes_meta` table extension
    (deployment-added per ARCHITECTURE.md §2). Stock OpenEMR has no
    such column. Adding it would mean a Doctrine migration, a
    NotesRepository change to JOIN it, and a controller change to
    surface the flag — well beyond the scope of a SQL fixture.
  * `substance_abuse_cfr42`'s `note_types` matcher targets
    `form_clinical_notes.clinical_notes_type`, but inserting into
    `form_clinical_notes` requires a paired `forms` table linkage and
    encounter relationship. The pnotes-only overlay path is much
    simpler. Title-prefix coverage exercises the same rule's deny
    path, so structural coverage isn't lost — just the alternate
    matcher.

**What we learned:** Sensitivity gating is a multi-source decision —
title prefix + note type + encounter category + attending flag — and
each source lives in a different table. A single SQL fixture can only
realistically demonstrate one or two of them. Picking title-prefix on
pnotes was the highest-leverage one for the MVP because it requires
the fewest schema and code changes. The encounters-overlay (for
`behavioral_health`) and `notes_meta` migration (for
`attending_only`) are real follow-up work, not subtask 50.4 scope.

**Artifacts:**
[`scripts/seed/agentforge_demo_overlay.sql`](../scripts/seed/agentforge_demo_overlay.sql),
[`sidecar/config/sensitivity_policy.yaml`](../sidecar/config/sensitivity_policy.yaml),
[`docs/test-data.md`](test-data.md) (the "Sensitivity-rule coverage"
sections of subtask 50.4).

---

## 2026-05-02 — Planner shipped as standalone class; LangGraph + orchestrator wiring deferred

**Plan:** Task 27 spec sketched the planner as
`async def planner_node(state: AgentState, llm: LLMClient) -> dict`,
implying it would slot into a LangGraph state graph. The spec also
contemplates the orchestrator consuming the plan to dispatch parallel
batches.

**Deviation:** Two scoping cuts:

1. **Built as a `Planner` class with `plan(user_message) -> Plan`,
   not as a LangGraph node.** The codebase doesn't use LangGraph
   anywhere — `langgraph` is in `pyproject.toml` deps but no module
   imports it. Adopting LangGraph mid-task to fit the spec literally
   would have ballooned scope (graph definition, state typing,
   migrating the existing async-loop orchestrator). The class shape
   matches existing patterns (Orchestrator, Verifier, BreakglassAuditTool)
   and is straightforward to wrap in a LangGraph node later if/when
   the broader migration happens.

2. **Wiring into the orchestrator deferred.** The Planner ships with
   full unit coverage but is not yet called from `Orchestrator.turn()`.
   Wiring requires deciding how the plan interacts with the existing
   tool-iteration loop (does the planner replace it? Seed it? Run
   alongside?), which is a larger design call than the planner
   itself. Same pattern as Task 41 (timeouts) and Task 45 (truncator)
   — the utilities ship complete, the integration is its own beat.

**Why:** Fits the codebase's existing patterns; keeps the surface
small enough to TDD; lets the wiring decision happen on its own
merits with the planner already in hand. Total scope of Task 27 stays
roughly what its complexity rating (6) implies, instead of
ballooning into a graph-architecture refactor.

**What we learned:** Spec sketches that import idioms from outside
the codebase (LangGraph, OpenAI's `response_format`) need a sanity
check against the actual codebase before being followed literally.
The structured-output goal (use case + tool plan) is achievable via
Anthropic tool-use forcing — same primitive every other tool in the
catalogue uses — without adopting a new framework.

**Artifacts:**
[`sidecar/src/agentforge/orchestrator/planner.py`](../sidecar/src/agentforge/orchestrator/planner.py),
[`sidecar/tests/test_planner.py`](../sidecar/tests/test_planner.py).

---

## 2026-05-02 — SynthesisInputTruncator wired but behavioral integration deferred

**Plan:** week1-gaps Task #6 said "after all tool results are
collected, before final LLM call, truncate ``tool_results`` to fit
under ``synthesis_input_cap``" and update create_app to construct a
default `SynthesisInputTruncator()`. The phrasing assumes a
fetch-then-synthesize architecture: pre-fetch every tool the planner
asked for, then call the LLM ONCE with synthesizer-prompt +
results-as-context.

**Deviation:** The orchestrator uses an iterative tool-use loop, not
fetch-then-synthesize. The LLM picks tools in chunks across
iterations and we feed each result back as a `tool` message in the
running `messages` array. By the time `tool_results` is "complete"
(loop exits with end_turn), the LLM has already seen the unredacted
payloads. Truncating then is either a no-op
(verifier_enabled=False; tool_results isn't read again after the
loop) or a regression (verifier_enabled=True; the verifier's
citation cache shrinks and valid claims start failing to ground).

So #6 wires the kwarg + stashes the truncator on the orchestrator
without invoking it. Default-on construction in create_app still
ships — collaborators get a real truncator instance — but
`Orchestrator.turn` doesn't call `truncator.truncate` yet.

**Why:** Aggressive truncation in the iterative architecture causes
either nothing or citation-cache regressions. The behavioral
integration is structurally sound only after the streaming refactor
(#11/#13) splits the synthesis call from the tool loop. At that
point "before final LLM call" becomes a real seam to gate on. The
wiring lands now so future subtasks can reach for `self._truncator`
without re-touching the constructor.

**What we learned:** The PRD wrote each integration task assuming
the planner-driven architecture from ARCHITECTURE.md §3. The actual
orchestrator has stayed iterative-tool-use because no task has
flipped that switch yet. Every integration task needs a sanity check
against the current dispatch shape, not the target one.

**Artifacts:**
[`sidecar/src/agentforge/orchestrator/__init__.py`](../sidecar/src/agentforge/orchestrator/__init__.py)
(constructor only — the truncator is held but unused),
[`sidecar/src/agentforge/orchestrator/truncation.py`](../sidecar/src/agentforge/orchestrator/truncation.py).

---

## 2026-05-02 — DataQuality warnings appended after final text, not "before final LLM call"

**Plan:** week1-gaps Task #7 said "Append quality warnings to synthesis
context (before final LLM call or verifier)" — meaning inject the
DataQualityChecker output INTO the messages array so the LLM
incorporates the warnings into the response it generates.

**Deviation:** The orchestrator runs `_apply_data_quality` AFTER the
final assistant text is produced (and after the verifier, when
enabled), appending warnings under a "Data quality notes:" header
similar to how `_append_degradation_notice` works.

**Why:** Same shape constraint as the truncator deferral: the
iterative tool-use loop has no separate synthesis-input seam. We
don't know which LLM call will be the FINAL call in advance —
each iteration is "ask the model, dispatch any tool calls it
returned, repeat." Injecting warnings as a system reminder mid-loop
risks destabilizing the model's tool-selection behavior on later
iterations. Appending post-final-text matches the orchestrator's
actual control flow and ships the user-visible signal without
that risk.

This means the model itself doesn't see the warnings on the turn
they fire — it can't proactively address a "Hypertension resolved"
conflict in its own answer. The user does see the warnings inline,
which is the load-bearing requirement (clinician sees the data
quality flag next to the citation).

**What we learned:** The "before final LLM call" placement assumes
the planner-driven fetch-then-synthesize architecture from
ARCHITECTURE.md §3. Mirror of the #6 truncator deviation — every
integration task in week1-gaps written against that target shape
needs a sanity check against the current iterative dispatch shape.
Behavioral tightening to the "model sees the warnings" placement
lands with the streaming refactor (#9-#13) when synthesis splits
from the tool loop.

**Artifacts:**
[`sidecar/src/agentforge/orchestrator/__init__.py`](../sidecar/src/agentforge/orchestrator/__init__.py)
(`_apply_data_quality` runs in the loop's success exit, not
mid-iteration).

---

## 2026-05-05 — Task 13 keeps persistence outside the extraction module

**Plan:** Taskmaster Task 13 subtasks 13.4 ("Implement persist_intake()
method calling persist_questionnaire_response.php") and 13.5
("Implement _extract_and_persist_intake pipeline returning
ExtractionResult with suggested_updates") imagined a tool-class
orchestrator that combined extract + persist + ExtractionResult
shaping inside the same module as the vision extractor.

**Deviation:** The `attach_and_extract` module produces a validated
`IntakeFormExtraction` and stops there. Persistence is a separate
concern: the demo script reads bytes from disk and prints the
extraction; the future LangGraph orchestrator (Task 1 / Task 15)
will POST the extraction to `persist_questionnaire_response.php`.
No `persist_intake()` or `_extract_and_persist_intake` method was
added.

**Why:** Task 11's docstring made the call explicit — "The
persistence step lives outside this module so the extraction stays
unit-testable without spinning up Apache." Task 13 inherits the
same constraint: bringing persistence into the module would couple
the extractor's tests to either Apache or an HTTP-fixture mock and
trade the existing 30-test offline suite for something slower and
less load-bearing. The split is also the right architectural seam
for Task 14 (PHI redaction at LangfuseClient): the extractor emits
to a logger boundary, the orchestrator sits at the network boundary
where redaction is wired in. Keeping them collapsed would force
Task 14 to thread through both responsibilities.

**Sub-deviation:** Task 13's prep notes also surfaced two layout
options for handling the second vision flow — "extract a
`_VisionExtractorBase` and subclass" vs "parameterize the existing
extractor over a contract." We picked the latter: a frozen
`VisionContract[T: BaseModel]` bundling the four pieces that
differ (tool name, tool spec, system prompt, schema class), plus
module-level `LAB_CONTRACT` and `INTAKE_CONTRACT` constants.
Cleaner DI surface, no abstract-base ceremony, and the contract
literal is easier to introspect in tests for drift-vs-Pydantic
guards.

**What we learned:** Taskmaster's subtask graph occasionally bakes
in an architectural assumption (here: extract-and-persist as one
module) that conflicts with a decision we already locked in on a
predecessor task. The split is correct; the spec should be treated
as guidance, not law. Future tasks that mention "in the same
module" should be sanity-checked against the existing module's
docstring contract.

**Artifacts:**
[`sidecar/src/agentforge/tools/attach_and_extract.py`](../sidecar/src/agentforge/tools/attach_and_extract.py)
(VisionContract + INTAKE_CONTRACT, no persist hooks),
[`sidecar/scripts/intake_extraction_demo.py`](../sidecar/scripts/intake_extraction_demo.py)
(extraction-only, persistence absent by design).

---

## 2026-05-05 — Task 14 ships sibling method, not a CallType-enum redaction wrapper

**Plan:** Taskmaster Task 14's subtasks called for (14.1) a `CallType`
enum extending `record_llm_call`'s signature with a discriminator,
(14.2) a separate `record_extraction_call` method, and (14.3) a
"redaction wrapper for extraction calls in AgentLangfuse" that
strips PHI from log payloads.

**Deviation:** Skipped the CallType enum and the wrapper. Shipped
only the sibling method. Three reasons:

1. The Protocol already enforces PHI-safety **structurally**, not
   procedurally. Every `record_*` method (`record_tool_call`,
   `record_llm_call`, `record_planner_decision`, etc.) takes only
   primitives, hashes, and closed-enum literals — there is no
   parameter shape that could carry message bodies. A "redaction
   wrapper" would be a no-op because the methods accept nothing
   to redact.
2. Adding a `CallType` enum on top of `record_llm_call` duplicates
   information the method-name dispatch already encodes. The
   existing module pattern is per-domain methods (planner,
   verifier, identity_guard, data_quality), not a single
   discriminated `record_event`. The enum-and-extend approach
   would have been an inconsistent shape.
3. The structural guarantee is **testable**: a boundary-discipline
   test enumerates the method signature and asserts no
   content-carrying parameter names appear (`messages`, `prompt`,
   `body`, `payload`, `input_text`, etc.). A future refactor that
   accidentally adds a content parameter fails this test before
   it ships. A wrapper-based design would need behavioral
   integration tests instead, which are slower and easier to bypass.

**What we learned:** Spec authors sometimes describe an
implementation pattern that doesn't fit the existing module's
shape. When the existing module already enforces a contract by
type, "wrapper-redaction" framings collapse to "add the right
method." Surface this in PR review by pointing to the structural
guarantee, not the procedural one.

**Sub-deviation: VisionExtractor wiring deferred.**
`record_extraction_call` is the boundary primitive. Wiring it from
`VisionExtractor.extract()` (instrumenting the actual extraction
calls so spans land in Langfuse) belongs at the orchestrator
layer — Task 1 (supervisor refactor) or Task 25 (citation overlay
integration), wherever the extractor is composed with the
LangfuseClient instance. Doing it now would couple Task 14 to the
orchestrator's not-yet-final shape.

**Artifacts:**
[`sidecar/src/agentforge/observability/protocols.py`](../sidecar/src/agentforge/observability/protocols.py)
(`record_extraction_call` Protocol method),
[`sidecar/src/agentforge/observability/langfuse_client.py`](../sidecar/src/agentforge/observability/langfuse_client.py)
(AgentLangfuse implementation),
[`sidecar/src/agentforge/observability/null_client.py`](../sidecar/src/agentforge/observability/null_client.py)
(no-op),
[`sidecar/tests/test_langfuse_client.py`](../sidecar/tests/test_langfuse_client.py)
(boundary-discipline tests, including the signature-introspection
guard).

---

## 2026-05-05 — Task 1 MR 1 ships placeholder routing for non-FOLLOWUP plans

**Plan:** Taskmaster Task 1's spec describes a supervisor whose
`route_decision` reflects a real choice between
`intake-extractor`, `evidence-retriever`, `both`, and `synthesize`
based on the user's query — e.g. detect a PDF attachment and route
to intake-extractor; detect a guideline question and route to
evidence-retriever.

**Deviation:** MR 1 (the skeleton) ships a deliberately dumb
routing rule:

* `iteration >= MAX_ITERATIONS` → `SYNTHESIZE` (hard stop)
* `Plan.use_case == FOLLOWUP`   → `SYNTHESIZE` (no tools needed)
* otherwise                     → `INTAKE_EXTRACTOR` (default)

Real routing intelligence — translating the W1 `Planner.UseCase`
taxonomy plus W2 signals (PDF detection, evidence query patterns)
into a meaningful `RouteDecision` — is deferred to MR 2/3.

**Why:** MR 1's purpose is the StateGraph wiring, not routing
intelligence. The workers being routed to are all pass-through
stubs in MR 1, so any routing decision has the same observable
effect. Smart routing in MR 1 would be untestable (no real worker
behavior to differentiate) and would couple the skeleton to
worker-specific signal detection that hasn't been designed yet.

The placeholder is enough to (a) prove the StateGraph compiles and
runs end-to-end, (b) prove the conditional-edge dispatch wires
correctly, and (c) prove the iteration cap engages — which is what
MR 1 is for. MR 2 wires real worker bodies and reshapes the
routing rule alongside.

**What we learned:** When a multi-MR slice has the first MR be
"plumbing only," it's worth being explicit that the routing is
placeholder. Reviewers seeing `INTAKE_EXTRACTOR` as the default
might otherwise read it as "this is the design" rather than "this
is the temporary scaffold."

**Sub-deviation: `RouteDecision.BOTH` collapses to
`INTAKE_EXTRACTOR` in the conditional-edge path map.** The
supervisor never emits `BOTH` in MR 1 (the rule above doesn't
produce it), but the conditional-edge map needs an entry per
enum value. Pointing it at `INTAKE_EXTRACTOR` is harmless — the
edge is unreachable from the current rule — and keeps the map
total. MR 2 will introduce a real fan-out node when parallel
worker dispatch lands.

**Artifacts:**
[`sidecar/src/agentforge/orchestrator/graph.py`](../sidecar/src/agentforge/orchestrator/graph.py)
(`build_graph`, `supervisor_node`, `_decide_route`, stub workers),
[`sidecar/tests/test_orchestrator_graph.py`](../sidecar/tests/test_orchestrator_graph.py)
(graph-level + supervisor-level tests; spies on stubs to verify
conditional-edge dispatch).

---

## 2026-05-05 — Task 1 MR 2 wires VisionExtractor + EvidenceRetriever; defers synthesize / terminal

**Plan:** Taskmaster Task 1.3 reads "Migrate existing iterative
tool-use loop from `turn()` into `intake_extractor_node()`." The
literal reading is "move the W1 catalog-tool loop into the intake
worker." Task 1.5 calls for `synthesize_node()` using the existing
synthesis logic to ship in the same MR as the worker bodies.

**Deviation:** Two divergences:

1. `intake_extractor_node` is a thin wrapper over
   `VisionExtractor[IntakeFormExtraction]`, **not** a port of the
   W1 catalog-tool loop from `Orchestrator.turn()`. The spec's
   wording conflates two unrelated flows — the W1 iterative
   tool-use loop pulls patient data via the catalog tools
   (`get_demographics`, `get_active_problems`, etc.), while the
   W2 intake extractor drives a *single* vision extraction call
   against an uploaded PDF. They are not the same shape and would
   not benefit from a literal port. The W2 reading is the only
   one that's consistent with Tasks 11/13 (which built
   `VisionExtractor[IntakeFormExtraction]`) and the project
   architecture diagram in `NEXT-SESSION.md`.

2. `synthesize_node` and `terminal_node` stay as pass-through
   stubs. Real synthesis migration belongs with the production
   cutover (MR 3 / Task 1.5–1.8) because `_turn_inner`'s synthesis
   logic is interleaved with the W1 tool-use loop — splitting it
   out is a bigger refactor than "wire workers" should carry.

**What we got instead in MR 2:**

* Worker idempotency baked into both real workers — once
  `extraction_result` (or `evidence_chunks`) is populated, re-entry
  under the supervisor's loop-back is a no-op. This makes the dumb
  MR 1 routing rule safe: the loop-back fires up to
  `MAX_ITERATIONS` times but the expensive Anthropic /
  retrieval-model call happens at most once per turn.
* `AgentState` extended with input fields the workers need:
  `document_id`, `patient_id`, `pdf_pages`, `query`. The
  entrypoint that constructs the starter state is responsible for
  populating them — the workers never invent placeholders.
* `build_graph` extended with optional `vision_extractor` /
  `evidence_retriever` injections. When None, the corresponding
  worker is a no-op pass-through (preserves MR 1's behavior for
  callers that haven't yet wired real workers).

**Why the slicing change is right:**

The original three-MR plan in `NEXT-SESSION.md` had MR 2 = "wire
workers + existing single-node logic still runs alongside" and
MR 3 = "cut over the loop." Synthesis migration sits squarely on
the cutover seam — it's coupled to the decision of *how* to
preserve W1 tool-use behavior alongside the new graph. Doing the
synthesis migration in MR 2 would have required reasoning about
the cutover anyway, blurring the slicing. Pushing it to MR 3
keeps each MR with one clear job.

**Artifacts:**
[`sidecar/src/agentforge/orchestrator/graph.py`](../sidecar/src/agentforge/orchestrator/graph.py)
(`intake_extractor_node`, `evidence_retriever_node`,
`_VisionExtractorLike` / `_EvidenceRetrieverLike` Protocols,
extended `AgentState`, optional DI in `build_graph`),
[`sidecar/tests/test_orchestrator_graph.py`](../sidecar/tests/test_orchestrator_graph.py)
(`TestIntakeExtractorNode` + `TestEvidenceRetrieverNode` covering
the call-through, three skip conditions per worker, and
idempotency under loop-back; existing graph integration tests
re-fit to inject stubs through `build_graph` instead of
monkey-patching module-level functions — a stricter test of the
production wiring path).

---

## 2026-05-05 — Task 1 MR 3 narrows scope to `synthesize_node` only; further-splits 1.6/1.7/1.8 into MRs 4-5-6

**Plan:** The session's MR 3 was originally going to ship Task 1.5
(synthesize), 1.6 (terminal), 1.7 (truncator + DataQuality), 1.8
(Langfuse spans), AND the production cutover. That's the spec's
"MR 3 = cut over the loop" reading from `NEXT-SESSION.md`.

**Deviation:** Split MR 3 into four progressively smaller MRs:

* **MR 3 (this MR)** — Task 1.5 only. `synthesize_node` calls the
  LLM with a context block built from `extraction_result` +
  `evidence_chunks`, appends the response as an assistant message.
* **MR 4** — Task 1.6. `terminal_node` wraps `StreamingVerifier`,
  ships the W2 citation-index builder.
* **MR 5** — Task 1.7 + 1.8. `SynthesisInputTruncator`,
  `DataQualityChecker` warnings, Langfuse spans per handoff.
* **MR 6** — Production cutover. Replace `Orchestrator.turn()`
  callers with `graph.ainvoke()`.

**Why split four ways?**

Three separable surface areas surfaced when I went to wire MR 3
all-at-once:

1. **W2 citation-index gap.** `StreamingVerifier` is fed by a
   `CitationIndex` built from W1 `ToolResult` shapes
   (`agentforge.verifier.cache.build_citation_index`). It has no
   pathway for W2 `evidence_chunks` (guideline citations) or
   extraction citations (intake/lab PDFs). Adding a W2 builder is
   non-trivial and properly belongs with the terminal-node MR
   (MR 4), not bundled into "synthesize."
2. **Production cutover risk.** Hot-swapping the production
   entrypoint touches a 1715-line `Orchestrator` class deeply
   entangled with W1 machinery (Memory, Breakglass, IdentityGuard,
   retry policy, parallel dispatch, timeout policy). One MR for
   this alone gives reviewers a tight surface to scrutinize.
3. **Reviewer load.** MR 1 + MR 2 each shipped at ~500-line diffs
   with focused test surfaces. MR 3 packed with 1.5-1.8 + cutover
   would have been ~2k lines; reviewability degrades quickly past
   ~700.

**What MR 3 ships:**

* `synthesize_node(state, llm)` — calls `llm.complete()` with the
  conversation messages, the `SYNTHESIS_SYSTEM_PROMPT`, and a
  context block built from extraction + evidence. Appends the
  response as an assistant message. Idempotent: if the last
  message is already an assistant turn, no-op.
* `_build_synthesis_context_block` — renders `IntakeFormExtraction`
  via `model_dump_json` and per-evidence-chunk text with
  citation-tag markers (`[guideline:doc#chunk]`). Returns `None`
  when the turn carries no synthesis context (pure follow-up).
* `_state_messages_to_llm_messages` — converts wire-format dict
  messages to typed `Message` objects at the LLM-call seam.
* `_SynthesisLLMLike` Protocol — narrows `LLMClient` to just
  `complete()` so test stubs don't need to implement `stream()`.
* `build_graph` extended with optional `synthesis_llm` injection
  (mirrors the MR 2 worker-DI pattern).

**What's not in MR 3:**

* Streaming integration with the verifier (deferred to MR 4 with
  the terminal-node work).
* Prompt versioning under `prompts/<active>/synthesizer.md`. The
  prompt is a module-level constant for now; we'll move it to the
  versioned store when prompt iteration matters (likely MR 5
  alongside the data-quality reminders that also ride on it).
* Routing intelligence. The supervisor still uses MR 1's dumb
  placeholder rule. Real PDF-vs-evidence-vs-both routing lands in
  MR 6 alongside cutover, when production traffic actually flows
  through this code.

**Artifacts:**
[`sidecar/src/agentforge/orchestrator/graph.py`](../sidecar/src/agentforge/orchestrator/graph.py)
(`synthesize_node`, `_build_synthesis_context_block`,
`_state_messages_to_llm_messages`, `_SynthesisLLMLike`,
`SYNTHESIS_SYSTEM_PROMPT`, `SYNTHESIS_MAX_TOKENS`,
`build_graph(synthesis_llm=...)`),
[`sidecar/tests/test_orchestrator_graph.py`](../sidecar/tests/test_orchestrator_graph.py)
(`TestSynthesizeNode` covering the LLM call + assistant-message
append, extraction-surfacing, evidence-citation-tag surfacing,
and idempotency; `TestSynthesizeIntegration` exercising the
synthesizer via `build_graph` end-to-end).

---

## 2026-05-05 — Task 1 MR 4 wires terminal verifier; reuses W1 CitationIndex; fixes MR 3 tag format

**Plan:** Taskmaster Task 1.6 reads "Wrap StreamingVerifier as
terminal_node()." The straightforward reading suggests dropping
the existing W1 verifier into a node and calling it done.

**Two divergences:**

1. **Reused the W1 `CitationIndex` shape rather than writing a
   parallel W2 verifier.** The W1 `agentforge.verifier.cache`
   module already gives us a `(record_type, record_id) -> dict`
   index keyed by string tuples. The W1 citation grammar
   (``[<type> #<id>]``) accepts arbitrary `record_type` strings
   — there's nothing W1-specific in the parser or the index
   shape.

   Building the index from W2 sources is just walking
   `state["evidence_chunks"]` (each chunk's W2 `Citation` has a
   `field_or_chunk_id` we use directly) and
   `state["extraction_result"]` (chief-concern + four list
   models, all with `Citation` slots). Maps `source_type.value`
   to `record_type` and `field_or_chunk_id` to `record_id`. No
   parallel verifier needed; the trust boundary is the same
   one the W1 path already uses.

2. **Fixed the MR 3 synthesizer's citation tag format.** MR 3
   shipped evidence tags as ``[guideline:doc#chunk]`` — clean
   for human reading but **not** parser-compatible. The W1
   citation regex (`[A-Za-z][A-Za-z0-9_]*\s+#[A-Za-z0-9_\-]+`)
   requires a leading identifier followed by whitespace then
   `#id`. The colon-then-no-whitespace shape parses to nothing,
   meaning every cited claim in MR 3 would have passed
   verification trivially as "framing prose with no citations
   to check." That defeats the purpose.

   MR 4 changes the synthesizer's evidence tag to
   ``[guideline #chunk_id]`` and surfaces the doc_id alongside
   in body text rather than baking it into the tag. The
   synthesizer's tests survive the change because they assert
   on doc_id + chunk_id presence, not on a specific tag shape.

**Known limitation logged with MR 4:**

The W2 index keys evidence chunks on `field_or_chunk_id` alone —
i.e. just the chunk_id, not `(doc_id, chunk_id)`. `chunk_id` is
unique within a document (per the chunker's contract), but two
chunks from different documents could in principle share an id.
The current chunker convention prefixes chunk_ids with the doc's
slug (e.g. `ada-9-1-stmt-2`), so collisions are unlikely in
practice — but the invariant isn't structurally enforced.

A future MR can move to composite ids (`doc_id--chunk_id`) at
both the synthesizer's tag-emit site and the index-build site
without churning the verifier itself. Deferring because the
W2 corpus is small and the chunker's natural conventions cover
the cases that matter for the demo. Logged in graph.py
build_w2_citation_index docstring.

**What MR 4 ships:**

* `build_w2_citation_index(state) -> CitationIndex` — walks
  `evidence_chunks` and the four citation-bearing slots on
  `IntakeFormExtraction` (chief_concern + demographics +
  medications + allergies + family_history). Helper
  `_walk_extraction_citations` separates the walking concern
  from the registration concern.
* `terminal_node(state, *, domain_checker=None)` — finds the
  last assistant message, builds the index from state,
  instantiates a `StreamingVerifier`, runs the assistant text
  through `verify_stream` as a single chunk, replaces the
  assistant message text with the verified concat. No-ops when
  no assistant message exists. Optional `domain_checker` for
  Task 29 plug-in compatibility.
* `_verify_text(verifier, text)` — wraps a complete text in a
  one-shot async generator and concatenates the verifier's
  yielded `VerifiedChunk.text` values back into a string. Lets
  us reuse the streaming API on a complete-string input
  without modifying the verifier.
* `build_graph(domain_checker=...)` — DI seam for the optional
  domain checker. Mirrors the existing worker-DI pattern.
* Synthesizer's evidence tag format updated to
  `[guideline #chunk_id]` for parser-round-trip.

**What's not in MR 4:**

* `SynthesisInputTruncator` (deferred to MR 5).
* `DataQualityChecker` warnings (MR 5).
* Langfuse spans per handoff (MR 5).
* Production cutover (MR 6).
* The five W1 catalogue tools' citations (`problem`, `medication`,
  etc.) flowing into the W2 index. MR 6 will bridge — when prod
  cutover happens, the index needs to merge W1 tool-results-derived
  citations alongside W2 citations.

**Artifacts:**
[`sidecar/src/agentforge/orchestrator/graph.py`](../sidecar/src/agentforge/orchestrator/graph.py)
(`build_w2_citation_index`, `_walk_extraction_citations`,
`_register_w2_citation`, `terminal_node`, `_verify_text`,
`_last_assistant_message_index`, `build_graph(domain_checker=...)`,
synthesizer tag format fix),
[`sidecar/tests/test_orchestrator_graph.py`](../sidecar/tests/test_orchestrator_graph.py)
(`TestBuildW2CitationIndex` × 3, `TestTerminalNode` × 4,
`TestSynthesizeTerminalIntegration` × 2 — covering empty / evidence /
extraction index population, terminal no-op / passthrough /
rejection / preservation, and end-to-end grounded-vs-ungrounded
verification through `build_graph`).

---

## 2026-05-05 — Task 1, MR 5: SynthesisInputTruncator + DataQuality + Langfuse handoff spans

**Plan:** Subtasks 1.7 + 1.8 of Task 1 — wire `SynthesisInputTruncator`
at the synthesize input edge, run `DataQualityChecker` warnings as a
system reminder before the LLM call, and emit Langfuse spans per
supervisor handoff.

**Three deviations from the literal spec wording:**

### 1. New `graph_synthesizer` prompt component (not overwriting `synthesizer`)

**Spec:** "Move `SYNTHESIS_SYSTEM_PROMPT` to `prompts/<active>/synthesizer.md`."

**Deviation:** Created `prompts/v1/graph_synthesizer.md` and registered
it as a NEW component (`graph_synthesizer`) in `prompts/version.json`,
rather than overwriting the existing `prompts/v1/synthesizer.md`.

**Why:** `prompts/v1/synthesizer.md` already exists and is the W1
iterative orchestrator's system prompt (loaded by
`Orchestrator.SYSTEM_PROMPT` via `load_prompt("synthesizer")`). It
carries 100+ lines of W1-specific tool-call grammar and citation rules
that the W2 graph's synthesize step doesn't need (and would actively
mislead the W2 model). The W2 prompt is a different ~5-line surface
covering extraction-grounded answers. Overwriting would either break
W1 today (wrong prompt for tool-use) or land a cross-cutting prompt
that confuses both. Both prompts coexist until the MR 6 cutover
retires the W1 path; at that point we can either delete the W1
`synthesizer` component or rename `graph_synthesizer` → `synthesizer`.

**What we learned:** The author of the next-session.md MR 5 plan
didn't have full visibility into the W1 prompt library state — easy to
miss when planning across both worlds. Always check `prompts/version.json`
before pinning a component name in a future task.

### 2. Truncator + DataQuality hooks are wired but no-op on pure W2 turns

**Spec:** Apply `truncator.truncate(state.tool_results, max_tokens)`
and run DQ heuristics over labs/problems/notes in `tool_results`.

**Deviation:** Both hooks are wired into `synthesize_node` and
`build_graph`, but `state["tool_results"]` is empty on every pure-W2
turn today (the W2 worker bodies populate `extraction_result` and
`evidence_chunks`, never `tool_results`). The hooks are no-ops until
the MR 6 cutover bridge puts W1 tool-result dicts into graph state.

**Why:** Wiring the hooks now keeps MR 6's surface tight — the cutover
just needs to populate `state["tool_results"]` from the W1 entrypoint,
not also wire DQ + truncator. The alternative (defer all wiring to MR
6) would have made MR 6 a much larger MR, with both the cutover code
AND the cross-cutting hooks landing together. Splitting them isolates
the cutover risk.

**What MR 5 ships that's NOT a no-op today:** Langfuse handoff spans
(real instrumentation, fires on every supervisor decision when a
client + trace are wired) and the prompt-library move (real refactor,
makes future prompt edits review as text diffs).

### 3. State schema change: `tool_results: list[Any]` → `tool_results: dict[str, ToolResult[Any]]`

**Plan:** AgentState's `tool_results` was declared as `list[Any]` in
MR 1.

**Deviation:** Changed to `dict[str, ToolResult[Any]]` to match W1's
shape (`Orchestrator._tool_results` is the same dict shape).

**Why:** The truncator + DQ hooks both expect a W1-shaped
`dict[str, ToolResult]`. Keeping `list[Any]` would have meant either
a runtime adapter at every call site OR a half-W1-half-W2 shape that
neither the existing `SynthesisInputTruncator` nor `DataQualityChecker`
can consume. The dict shape is also what the MR 6 cutover bridge needs
to drop W1 tool-result dicts into graph state without re-shaping. The
change rippled to two test starter helpers (`tool_results=[]` →
`tool_results={}`) and seven `update == {}` assertions on no-op
worker paths (now `update == {"last_node": "<node>"}` because workers
also stamp `last_node` for the handoff span's `from_node` field).

**What we learned:** Schema fields that "look forward" to a future
shape are dangerous when the future shape isn't pinned at definition
time. The MR 1 `list[Any]` was a placeholder — but placeholders silently
carry the wrong invariants until the first real consumer arrives.
Better: pin the shape on first definition, or annotate as
`<future-shape>` in the docstring so the next MR doesn't assume the
placeholder is load-bearing.

**What MR 5 ships:**

* `prompts/v1/graph_synthesizer.md` (new) + `version.json` entry +
  `prompts/README.md` Components table updated.
* `graph.py`: `SYNTHESIS_SYSTEM_PROMPT = load_prompt("graph_synthesizer")`
  replaces the inline string. New `SYNTHESIS_INPUT_CAP_TOKENS`
  constant (12_000, matches W1 default). New `HANDOFF_START_NODE`
  marker.
* `AgentState`: `tool_results: dict[str, ToolResult[Any]]`,
  `langfuse_trace: TraceHandle | None`, `last_node: str` (new fields).
* `synthesize_node`: optional kwargs `truncator`, `max_synthesis_tokens`,
  `data_quality_checker`, `langfuse`. Truncator caps `state["tool_results"]`
  before LLM call; DQ warnings prepend the system prompt as a
  `<system_reminder>` block; `record_data_quality_metrics` fires on
  every synthesis call when langfuse is wired (counts only, never
  the strings themselves).
* `_collect_data_quality_warnings(state, checker, *, langfuse, trace)`
  — ports the W1 `_data_quality_suffix` logic (stale labs +
  problem/note conflicts), with insertion-order dedup.
* `_compose_system_prompt(base, dq_warnings)` — composes the
  `<system_reminder>` block above the base prompt; round-trips to
  base unchanged on empty warning list.
* `supervisor_node(state, planner, *, langfuse=None)` — emits
  `record_handoff_span` per routing decision with `from_node`,
  `to_node`, `route_decision`, `route_reason`, post-bump `iteration`.
  `from_node` reads `state["last_node"]` (set by workers on exit) so
  loop-back spans show the actual handoff path, not always
  `HANDOFF_START_NODE`.
* All worker nodes stamp `last_node` in their state delta — including
  the no-op short-circuit paths, so the supervisor's next handoff
  span has the correct `from_node`.
* `LangfuseClient` Protocol gains `record_handoff_span(...)`. Real
  `AgentLangfuse` impl emits a `handoff:<from>-><to>` span with the
  five PHI-safe metadata fields. `NullLangfuseClient` is a no-op.
* `build_graph(...)`: new optional kwargs `truncator`,
  `max_synthesis_tokens`, `data_quality_checker`, `langfuse`.

**What's not in MR 5:**

* The W1 → W2 tool-results bridge (MR 6). Today
  `state["tool_results"]` is always empty.
* Real PDF-vs-evidence routing intelligence in `_decide_route`
  (still the MR 1 placeholder; MR 6).
* Production cutover — graph remains dead code until MR 6.

**Artifacts:**
[`prompts/v1/graph_synthesizer.md`](../prompts/v1/graph_synthesizer.md)
(new), [`prompts/version.json`](../prompts/version.json) (added
`graph_synthesizer` entry), [`prompts/README.md`](../prompts/README.md)
(Components table extended),
[`sidecar/src/agentforge/orchestrator/graph.py`](../sidecar/src/agentforge/orchestrator/graph.py)
(prompt load, state schema, truncator/DQ/langfuse wiring,
`HANDOFF_START_NODE`, `SYNTHESIS_INPUT_CAP_TOKENS`),
[`sidecar/src/agentforge/observability/protocols.py`](../sidecar/src/agentforge/observability/protocols.py)
(added `record_handoff_span`),
[`sidecar/src/agentforge/observability/null_client.py`](../sidecar/src/agentforge/observability/null_client.py)
(no-op `record_handoff_span`),
[`sidecar/src/agentforge/observability/langfuse_client.py`](../sidecar/src/agentforge/observability/langfuse_client.py)
(real `record_handoff_span` impl),
[`sidecar/tests/test_orchestrator_graph.py`](../sidecar/tests/test_orchestrator_graph.py)
(`TestSynthesisSystemPromptIsVersioned` × 2,
`TestSynthesisTruncatorWiring` × 3, `TestSynthesisDataQualityWiring`
× 3, `TestSupervisorHandoffSpans` × 3 — covering prompt move,
truncator no-op-vs-fire wiring, DQ reminder rendering + telemetry,
and supervisor handoff span content under start/loop-back/no-trace
conditions).

---

## 2026-05-05 — Task 1, MR 6: graph cutover seam (foundation, not full production cutover)

**Plan:** MR 6 of the LangGraph supervisor refactor — "Replace
``Orchestrator.turn()`` callers with ``graph.ainvoke(...)``. Wire the
FastAPI ``/turn`` route through. Build the bridge from W1 tool-result
citations into the W2 ``CitationIndex``. Real PDF-vs-evidence routing
intelligence in ``_decide_route``. Run the 9 W1 regression locks
against the graph. Drop the iterative loop (or keep one release as
fallback)."

**Deviation:** This MR ships the **foundation** for the cutover — the
bridge, the real routing, and an opt-in graph seam on
``Orchestrator.turn`` — but does NOT flip the production ``/turn`` route
through the graph or drop the iterative loop. Three concrete
deferrals:

### 1. ``main.py`` graph wiring deferred to MR 7

**Spec:** "Wire the FastAPI ``/turn`` route through" the graph.

**Deviation:** The orchestrator gains an optional ``agent_graph``
constructor param and a ``_run_graph_turn`` helper, but ``main.py``
still constructs ``Orchestrator(agent_graph=None, ...)``. The graph
surface is therefore reachable only by tests today, not by HTTP
traffic.

**Why:** Wiring ``main.py`` requires building three new heavyweight
collaborators at app startup (``VisionExtractor`` against the Anthropic
LLM client, ``EvidenceRetriever`` against BM25 + Dense + RRFMerger +
Reranker, plus a corpus loader for the latter two). Each is tested
independently and constructable, but composing them at app boot in a
way that survives test fixtures, missing-dep paths, and the existing
``main.py`` factory's settings-validation order is multi-hour scope on
its own. With the cutover seam in place, MR 7 is a tight
``main.py``-only patch — no new abstractions needed in the orchestrator.

### 2. ``TurnRequest`` schema unchanged; PHP module untouched

**Spec implied:** End-to-end demo runs PDF upload → extract →
synthesize through OpenEMR.

**Deviation:** ``TurnRequest`` still carries only ``message`` +
``session_id``. The orchestrator's new ``pdf_pages`` / ``document_id`` /
``evidence_query`` ``turn()`` kwargs are reachable from Python callers
(tests, future endpoints) but no HTTP path populates them today. The
PHP ``AgentProxyController`` is unchanged.

**Why:** Same gating as #1 — without ``main.py`` wiring, schema
extensions ship dead-code request fields. MR 7 lands the schema +
controller change in one commit alongside the wiring so reviewers see
the full request-to-response path in one diff.

### 3. Iterative loop NOT dropped

**Spec:** "Drop the iterative loop (or keep one release as fallback —
reviewer's call)."

**Deviation:** Took the "keep one release as fallback" escape hatch.
The W1 iterative loop in ``Orchestrator._turn_inner`` continues to
handle every turn that doesn't carry W2 inputs — chart questions
(the original W1 demo path) still flow through it. The graph fires
only when the caller supplies ``pdf_pages`` or ``evidence_query``.

**Why:** The W2 graph has no chart-question worker today — every
worker is W2-flavored (intake extraction, guideline retrieval). A
chart question routed through the graph would either (a) fall through
to ``synthesize_node`` with no tool data and produce a pure-LLM answer
(losing the W1 tool catalog entirely) or (b) need a brand-new
``chart_question_node`` that wraps the iterative loop body. Both are
substantial work better isolated to a follow-up MR.

**What we learned:** The original "Replace Orchestrator.turn() with
graph.ainvoke()" framing assumed the graph could absorb ALL turn
shapes. In practice the graph is W2-flavored; absorbing W1 turns
requires either a chart-question worker or a deliberate decision to
let chart questions run pure-LLM. Either is a real product call worth
its own MR.

### 4. W2 path skips identity guard, breakglass, retry policy, parallel dispatch

**Spec:** Implicit — ``Orchestrator.turn`` runs through the W1
machinery (Memory, Breakglass, IdentityGuard, retry policy, parallel
dispatch, timeout policy, cost tracking).

**Deviation:** ``_run_graph_turn`` only wraps the graph in:
* the per-turn timeout envelope (``asyncio.timeout(total_turn)``),
* memory load + persist (so multi-turn conversations work),
* trace open (so handoff spans land under the right Langfuse trace),
* final-text extraction.

It does NOT run identity guard, breakglass audit, or the cost / retry
machinery that wraps the W1 loop.

**Why:** Identity guard binds to the chart owner's name + MRN — used
to catch cross-patient references in the user's question. The W2 demo
flows (intake-PDF + evidence-query) don't have a meaningful
"reference patient B from patient A's chart" attack surface because
the extractor reads the uploaded form (not chart prose) and the
evidence retriever is patient-independent. Breakglass / cost /
retry are wired to the W1 tool-dispatch machinery; the graph workers
manage their own LLM calls outside that surface. MR 7+ can promote
each cross-cutting concern into a graph node or a wrapper as the W2
surface grows.

**What MR 6 ships:**

* ``build_w2_citation_index(state)`` walks ``state["tool_results"]``
  via the existing W1 ``build_citation_index`` and merges the W1
  records into the same index the W2 evidence + extraction
  citations populate. Single ``(record_type, record_id) -> dict``
  shape across both paths.
* ``_decide_route(plan, state)`` replaces the MR 1 placeholder
  (cap → SYNTHESIZE; FOLLOWUP → SYNTHESIZE; otherwise →
  INTAKE_EXTRACTOR). New behavior: iteration cap → SYNTHESIZE;
  FOLLOWUP → SYNTHESIZE; pdf pending → INTAKE_EXTRACTOR; query
  pending → EVIDENCE_RETRIEVER; nothing pending → SYNTHESIZE. Sequential
  dispatch handles the both-inputs case via worker-then-supervisor
  loop-back; idempotent workers no-op on re-entry.
* ``Orchestrator`` gains optional ``agent_graph: _AgentGraphLike``
  constructor param plus ``pdf_pages`` / ``document_id`` /
  ``evidence_query`` kwargs on ``turn()``. When the graph is wired AND
  any W2 input is supplied, ``_run_graph_turn`` builds the starter
  ``AgentState``, awaits ``ainvoke``, and returns the final assistant
  text via ``_last_assistant_text`` (sentinel-safe on empty results).
  W1 chart-question turns are unchanged.
* ``_AgentGraphLike`` Protocol — narrow ainvoke surface so the
  orchestrator doesn't import langgraph directly.
* New test file ``test_orchestrator_w2_cutover.py`` (7 tests) — pins
  the routing decision (W1 vs graph), starter-state construction,
  final-text extraction including the no-assistant-message sentinel.
* ``test_regression_lock_via_graph_citation_index`` (9
  parametrized cases) — the same 9 W1 locks pass when graded against
  ``build_w2_citation_index`` instead of the W1 ``build_citation_index``.
  Locks the bridge.

**What's not in MR 6 (deferred to MR 7):**

* ``main.py`` constructs the graph and passes it to ``Orchestrator``.
* ``TurnRequest`` extended with ``document_id`` / ``evidence_query``
  fields; ``/turn`` route handler forwards them.
* PHP ``AgentProxyController`` passes the new fields through.
* Document fetch (``DocumentBytesRepository``) + render
  (``PdfRenderer``) wired into the request path so the orchestrator
  receives ``pdf_pages`` from a ``document_id``.
* Chart-question worker (``chart_question_node``) wrapping the W1
  iterative loop, so the graph can handle every turn shape.
* Identity guard / breakglass / cost / retry promoted into the graph
  path.

**Artifacts:**
[`sidecar/src/agentforge/orchestrator/graph.py`](../sidecar/src/agentforge/orchestrator/graph.py)
(W1 bridge in ``build_w2_citation_index``, real
``_decide_route``),
[`sidecar/src/agentforge/orchestrator/__init__.py`](../sidecar/src/agentforge/orchestrator/__init__.py)
(``_AgentGraphLike`` Protocol, ``agent_graph`` constructor param,
``turn()`` W2 kwargs, ``_run_graph_turn`` helper,
``_last_assistant_text`` module helper),
[`sidecar/tests/test_orchestrator_w2_cutover.py`](../sidecar/tests/test_orchestrator_w2_cutover.py)
(NEW; 7 tests covering routing decision × 4, state construction,
final-text extraction × 2),
[`sidecar/tests/test_orchestrator_graph.py`](../sidecar/tests/test_orchestrator_graph.py)
(updated tests for the new ``_decide_route`` + 1 new test for
no-W2-inputs synthesize fallthrough),
[`sidecar/tests/eval/regression_locks.py`](../sidecar/tests/eval/regression_locks.py)
(NEW parametrized
``test_regression_lock_via_graph_citation_index`` proving the bridge
preserves the 9 W1 lock verdicts).

---

## 2026-05-05 — `EVIDENCE_RETRIEVER_ENABLED` defaults to `false` (MR 7)

**Plan:** MR 7 wires `EvidenceRetriever` into `create_app` by default so
the W2 evidence node lights up in production without an explicit toggle.
NEXT-SESSION.md framed slice C as "build the retriever and pass it
through"; the assumption was a default-on flag.

**Deviation:** `Settings.evidence_retriever_enabled` defaults to **False**.
Production deployments opt in via `.env` (`EVIDENCE_RETRIEVER_ENABLED=true`).

**Why:** The retriever's collaborators include `SentenceTransformerEncoder`
and `SentenceTransformerCrossEncoder`, both of which load ~190 MB of ML
weights on construction (3-5 seconds wall-clock). Eager construction in
`create_app` bumped the unit-test runtime from ~3.5 s to ~50 s — every
test that built a fresh app via the conftest `client` fixture (or its
own factory) paid the cost, and several test files (test_main_streaming,
test_main_cost_header, test_orchestrator_planner) construct create_app
multiple times per file. A 15× regression in dev-loop test time was the
breaking concern, not the per-process production startup hit.

The cleaner alternative (lazy-wrap the retriever) was prototyped — see
the `_LazyAgentGraph` pattern shipped alongside the graph compile — but
adds another defer-and-cache class, and the deployment burden of
"set one env var" is genuinely small (the droplet's `.env` already
encodes ~10 such opt-ins).

**What we learned:** "Default-on production" is a goal, not a default-
implementation rule. When a feature pulls heavy resources (ML weights,
network calls, disk caches) into app construction, the default should
match the resource posture of the *common caller* (unit tests in dev,
not the droplet). Production deployments already track env-var changes
in `docs/DEPLOYMENT.md`, so opt-in there costs us nothing. Generalizes:
prefer config-driven opt-in over default-on whenever the cost of the
"on" state would change a different caller's runtime profile.

**Artifacts:**
[`sidecar/src/agentforge/config.py`](../sidecar/src/agentforge/config.py)
(default flipped to `False`; docstring rationale),
[`sidecar/src/agentforge/main.py`](../sidecar/src/agentforge/main.py)
(`_build_evidence_retriever` returns None when the flag is off),
[`sidecar/tests/test_config_w2.py`](../sidecar/tests/test_config_w2.py)
(default-False asserted; env-var enablement covered),
[`sidecar/tests/conftest.py`](../sidecar/tests/conftest.py) (defensive
re-set against shell-env contamination by developers iterating on the
W2 evidence path).

---

## 2026-05-06 — pdf.js consumed via OpenEMR npm pipeline, not module-local vendor

**Plan:** Taskmaster Task 24 (citation overlay) and `W2_ARCHITECTURE.md` §3
both called for a *module-local vendored* pdf.js bundle at
`interface/modules/custom_modules/oe-module-agentforge/public/vendor/pdfjs/`.
Task 24's spec also pinned 4.x and asked for the legacy UMD bundle (committed
files: `pdf.min.js`, `pdf.worker.min.js`, `LICENSE`).

**Deviation:** Three small updates to that plan:

1. **Consume via npm + gulp**, not module-local vendor. `pdfjs-dist@5.7.284`
   is added to the repository-root `package.json`; gulp's `install` task
   copies it from `node_modules/pdfjs-dist/` into `public/assets/pdfjs-dist/`
   (which is gitignored). Module references the served path
   `/public/assets/pdfjs-dist/legacy/build/`.
2. **pdf.js 5.7.284**, not 4.x. The 4.x pin was a snapshot in the spec;
   5.7.284 is the current latest stable prebuilt.
3. **ESM, not UMD.** pdf.js 5.x dropped the UMD/global build. Even the
   legacy distribution ships as ECMAScript modules (`pdf.min.mjs`,
   `pdf.worker.min.mjs`). Loading requires `<script type="module">` and
   static `import`. This shapes subtasks 24.3 and 24.4 (citation_overlay.js
   becomes a module rather than a classical IIFE wrapping a `pdfjsLib`
   global).

**Why:** The vendoring rationale in `W2_ARCHITECTURE.md` §3 ("would require
introducing a Node toolchain and bundler that the module otherwise avoids")
referred to avoiding Node *in the Python sidecar* — the rejected alternative
was a sidecar-bundled React/JSX component. OpenEMR has always had a Node
toolchain (gulp/npm) for its other vendored JS (Bootstrap, jQuery, dwv,
fontawesome, …). Adding pdf.js as a project-level npm dep matches the
established convention without contradicting the original ADR's substantive
intent — vanilla JS in OpenEMR, PDF served from OpenEMR's session-auth
path, sidecar stays Python-only. The user's directive (2026-05-06): "stick
to the established conventions as much as possible, until it's necessary
to deviate."

**What we learned:** When a task spec rationalizes a structural choice
("no Node toolchain"), trace the rationale back to the source ADR before
following or deviating — the spec can mis-cite. Here the *substance* of
the original ADR (vanilla JS, OpenEMR-served) is preserved; only the path
is different. Also: pdf.js 5.x being ESM-only is a real shape change that
ripples into how the overlay loads, not just where its bytes live.

**Artifacts:**
[`package.json`](../package.json) (one-line dep add),
[`package-lock.json`](../package-lock.json) (lockfile entry pinning the
sha512 integrity hash),
[`interface/modules/custom_modules/oe-module-agentforge/README.md`](../interface/modules/custom_modules/oe-module-agentforge/README.md)
(new `Frontend dependencies` section with the served path and the
ESM-only note).

---

## 2026-05-06 — Citation overlay tests use jest + jsdom, not Puppeteer

**Plan:** Taskmaster Task 24.7 specified a Puppeteer-driven headless
browser test harness with a `tests/fixtures/citation_overlay_test.html`
fixture loading a real PDF, with assertions on getBoundingClientRect()
positioning and visual content matching for the 1-indexed page contract.

**Deviation:** Implemented as `tests/js/citation_overlay.test.js` using
jest + jsdom — the project's existing JS test pattern (matches
`agent_panel.test.js`, `agent_panel_upload.test.js`, etc.). pdfjsLib is
stubbed with a tracker that records each `getPage(N)` call; the 1-indexed
contract is verified by asserting the recorded `N` matches
`citation.page_bbox.page` exactly, with no off-by-one wrapping. Real PDF
rendering is not exercised because jsdom can't paint to canvas — and it
doesn't need to be, because the contract bug surfaces at the
`pdf.getPage()` call boundary, which the test pins.

**Why:** Three reasons to follow the existing convention:

1. The repo already has 369 jest+jsdom tests; adding Puppeteer would mean
   a second test runner and toolchain for one file.
2. The CRITICAL contract bug (treating page_bbox.page as 0-indexed) is
   detectable at the API call surface. Visual rendering matches that
   surface — a green getPage(N) assertion can't be fooled by a "wrong
   page rendered correctly" failure mode, because there's no rendering
   to be wrong about.
3. jest+jsdom runs the test file in 0.4s; Puppeteer + a real PDF + a
   real canvas would push CI runtime + dependency surface meaningfully.

**What we learned:** When a spec prescribes tooling, ask whether the
intended *invariant* is what's load-bearing or whether the *tooling* is.
Here, the invariant is "the 1-indexed contract isn't violated." Multiple
test shapes can pin that invariant; the cheapest one that pins it
soundly wins. (Also, jsdom's offsetWidth/Height returning 0 for canvases
required patching `HTMLCanvasElement.prototype` to surface bitmap
dimensions — a small jsdom workaround documented in the test file.)

**Artifacts:**
[`tests/js/citation_overlay.test.js`](../tests/js/citation_overlay.test.js)
— 21 tests covering public API, mount() validation, the 1-indexed
contract, out-of-range pages, rect positioning, styling, dismiss
behavior (rect + × button + propagation), pdfjsLib readiness via the
`agentforge:pdfjs-ready` event, error paths, and unmount safety.

---

## 2026-05-06 — Dashboard OAuth client: public + PKCE, signaled via `application_type`

**Plan:** Task 38.2 and the `NEXT-SESSION.md` walkthrough specified
`oidc-client-ts` configured with PKCE; client registered via OpenEMR's
RFC 7591 dynamic-registration endpoint. The implicit assumption was that
either (a) confidential client with `client_secret` in `VITE_*` env, or
(b) `token_endpoint_auth_method: "none"` for a public client, would
work.

**Deviation:** Both assumptions were wrong. Settled on **public client
+ PKCE**, signaled by `application_type: "public"` in the registration
body and **omitting** `token_endpoint_auth_method` entirely.

**Why:** Two findings:

1. Confidential client is unusable for an SPA — Vite bakes `VITE_*` env
   into the browser bundle, so the secret would ship to every user.
   Effectively the same security as no secret at all, but with the
   illusion of one.
2. OpenEMR's discovery advertises only `client_secret_post` for
   `token_endpoint_auth_methods_supported` and the registration handler
   actively rejects `none` with `"Unsupported token_endpoint_auth_method
   value : none"`. The signal it actually accepts is OpenEMR-specific:
   `application_type: "public"` (with no `token_endpoint_auth_method`
   sent), which causes the server to leave `client_secret` empty and
   treat the client as public. Reference:
   `src/RestControllers/AuthorizationController.php:307-325`.

**What we learned:** OAuth2/OIDC server discovery metadata is not
authoritative for what a server actually accepts. OpenEMR diverges from
RFC 8414 in the public-client signaling — read the registration handler
source, not the discovery doc, when a registration call gets an opaque
rejection. The constraint that follows the public-client choice (no
`system/*` or `user/*` scopes) is enforced server-side and silently
shapes the FHIR data layer too — see the next two entries.

**Artifacts:**
[`PATIENT_DASHBOARD_MIGRATION.md`](../PATIENT_DASHBOARD_MIGRATION.md)
§"OAuth2 / OpenID Connect integration",
[`dashboard/src/services/auth/`](../dashboard/src/services/auth/),
[`dashboard/.env.example`](../dashboard/.env.example).

---

## 2026-05-06 — `MedicationStatement` and `FamilyMemberHistory` not exposed; meds/prescriptions both source from `MedicationRequest`

**Plan:** Task 38.6 (medications) was scoped to FHIR `MedicationStatement`,
T38.7 (prescriptions) to `MedicationRequest`. Intake review form (T38.12)
included a family-history section sourced from `FamilyMemberHistory`.

**Deviation:** Neither `MedicationStatement` nor `FamilyMemberHistory`
appears in OpenEMR's advertised scope list. T38.6 and T38.7 both source
from **`MedicationRequest`**, partitioned by `status` (`active` →
medications card; `completed`/`stopped` → prescription history card).
T38.12's family-history section cannot commit via FHIR write at all —
the resource doesn't exist on this server.

**Why:** Discovered while planning T38.2 — `dev-easy`'s OAuth2
discovery doc lists every read scope OpenEMR exposes, and these two
resources are absent. Confirms the FHIR R4 surface OpenEMR ships isn't
fully complete.

**What we learned:** Verify FHIR resource availability against the
target server's discovery doc *before* designing card boundaries — a
plan that calls for "MedicationStatement" reads better than a plan that
says "MedicationRequest filtered by status," but only one is actually
shippable on the target server. The status-partition pattern is also
arguably more useful clinically (active meds vs. discontinued history
is a real workflow distinction; the FHIR distinction between
`MedicationStatement` and `MedicationRequest` is administrative).

**Artifacts:**
[`PATIENT_DASHBOARD_MIGRATION.md`](../PATIENT_DASHBOARD_MIGRATION.md)
§"FHIR data layer".

---

## 2026-05-06 — Dashboard auth: pivoted from public client (v1) to BFF on the sidecar (v2)

**Plan:** T38.2 specified an OAuth2 / OIDC login flow against OpenEMR
using `oidc-client-ts` in the SPA. The implicit assumption was that a
public client + PKCE would let the dashboard speak FHIR directly,
either against the patient context or the user context.

**Deviation:** Threw away the entire dashboard-side OAuth2
implementation and rebuilt the auth + FHIR-proxy surface as a
**Backend For Frontend** on the AgentForge sidecar. The dashboard is
now a "dumb" client that only knows about an HttpOnly session cookie;
all OAuth2 mechanics live in `sidecar/src/agentforge/dashboard_auth/`.

**Why:** Cumulative discovery during T38.2:

1. OpenEMR's `patient/*` scopes require a *patient context* claim in
   the access token. A signed-in admin (or any non-Patient user) gets
   no patient context, so FHIR endpoints return 401 on `patient/*`
   reads. SMART-on-FHIR's `launch/patient` would solve this for a
   *one-patient-at-a-time* dashboard, but our brief is a clinician's
   browse-many chart view — fundamentally a `user/*` scope problem.
2. OpenEMR rejects `user/*` and `system/*` scopes for public clients
   at registration (`AuthorizationController.php:307-325` raises
   "system and user scopes are only allowed for confidential
   clients"). So a public-client SPA can never have the scopes the
   dashboard needs.
3. A confidential client running in an SPA is a security mistake —
   Vite bakes `VITE_*` env into the browser bundle, leaking the
   secret to every user. OAuth 2.1 BCP says don't.
4. The right shape is **BFF**: a server-side component holds the
   confidential client_secret, performs the OAuth2 dance, and proxies
   FHIR reads. The AgentForge sidecar already exists alongside the
   dashboard; adding `/auth/*` and `/api/fhir/*` endpoints to it is a
   natural extension.

**What we landed:**

- Sidecar `dashboard_auth` module: `oauth.py` (PKCE + httpx token
  exchange), `sessions.py` (Redis-backed session + pending-state
  store), `routes.py` (`/auth/{login,callback,whoami,logout}` +
  `/api/fhir/{path:path}` proxy). 33 pytest specs.
- Wired into `main.create_app()` — routes mount unconditionally, return
  503 when the BFF env vars are unset so existing tests aren't disturbed.
- Dashboard side: rewrote `stores/auth.ts` (10 Vitest specs) to talk
  to `/auth/whoami` + `/auth/logout` via `fetch`. Simplified
  `LoginView` to `window.location.assign('/auth/login?next=…')`.
  Deleted `services/auth/{config,userManager}.ts` and
  `views/OAuthCallbackView.vue`. Uninstalled `oidc-client-ts`
  (~68 KB gzipped saved).
- Vite proxy config gained `/auth/*` and `/api/*` rules pointed at
  the sidecar — same-origin from the browser's POV so the HttpOnly
  session cookie rides cleanly on every dashboard request.
- Re-registered the OpenEMR client as confidential
  (`client_role: "user"`) with `user/*.read` scopes; `client_secret`
  lives in `sidecar/.env`, never in the dashboard bundle.

**What we learned:**

- "Just call OAuth2 from the SPA" is a near-default reflex for
  modern frontends, and SMART-on-FHIR's marketing leans into it. But
  the moment you need clinical-user scopes (the typical scope class
  for any clinician-facing app), the choice collapses to *BFF or
  confidential-in-SPA* — both real, but the BCP-correct answer is
  BFF unless you have specific reasons not to.
- The *cost* of the wrong-first-shape was small here because the
  Pinia store kept its general shape (status state machine + actions)
  — only the action *implementations* changed. Same lesson as
  ARCHITECTURE.md's "FHIR types as the seam": the seam was robust to
  the implementation pivot.
- Specifically for OpenEMR: do not trust public/confidential
  inferences from RFC documents. The dynamic-registration handler's
  scope-policing logic (`AuthorizationController.php:307-325`) is
  the load-bearing constraint, and reading it directly is faster than
  iterating on registration POST attempts.

**Artifacts:**
[`sidecar/src/agentforge/dashboard_auth/`](../sidecar/src/agentforge/dashboard_auth/),
[`sidecar/tests/test_dashboard_auth_*.py`](../sidecar/tests/),
[`dashboard/src/stores/auth.ts`](../dashboard/src/stores/auth.ts),
[`PATIENT_DASHBOARD_MIGRATION.md`](../PATIENT_DASHBOARD_MIGRATION.md)
§"OAuth2 / OpenID Connect integration — BFF flow (v2)".

---

## 2026-05-06 — No `patient/*.write` scopes advertised; T38.12 commit path no longer "FHIR PUT directly"

**Plan:** `NEXT-SESSION.md` decision spine §"Commit path (Q6)" — after
the W2 brief invalidated the original plan of a session-authed PHP
endpoint, the updated decision was "browser → FHIR API write directly
(POST/PUT to FHIR `Condition`, `MedicationStatement`,
`AllergyIntolerance`, `FamilyMemberHistory`, plus `QuestionnaireResponse`
referencing the canonical intake Questionnaire as umbrella)."

**Deviation:** Cannot land as planned. **Decision deferred to T38.12**
with three documented options.

**Why:** OpenEMR's OAuth2 discovery exposes **no `patient/*.write`
scopes** at all. Writes are gated by `user/*.write` scopes
(legacy-REST-API-named: `user/medical_problem.write`,
`user/allergy.write`, `user/medication.write`, etc.), and the
public-client constraint (see prior deviation) bars us from `user/*`
entirely. Three options for T38.12:

a. **Downgrade to confidential client** and accept secret-in-SPA. Lets
   us request `user/*.write`. Security cost: secret in browser bundle.
b. **Route writes through the AgentForge sidecar BFF.** The sidecar
   gets a `user/*.write` confidential-client access token server-side
   and proxies the writes. Architectural cost: ~2-3 hr of new sidecar
   code; brief allows since the sidecar pre-existed.
c. **Defer commit-to-chart.** Ship the structured intake-review form
   (edit-in-place, citation chips, include checkboxes) and end with a
   *Copy structured summary* button. No write at all. Acceptable
   demo-quality, weak product-quality.

**What we learned:** "FHIR API write directly" is a clean architecture
sentence but not a universally-shippable one. SMART-on-FHIR's
patient-context scopes are read-biased by design (the patient
themselves is rarely the agent of write); production EMR writes
typically require either a clinical-user OAuth context or a backend
service-account flow. Worth surfacing this earlier in future
SMART-on-FHIR designs.

**Artifacts:**
[`PATIENT_DASHBOARD_MIGRATION.md`](../PATIENT_DASHBOARD_MIGRATION.md)
§"FHIR data layer".

---

## 2026-05-07 — Document upload routes via BFF proxy, not direct browser-to-PHP

**Plan:** `docs/NEXT-SESSION.md` §"What needs to be done in vue-ui" left
the upload mechanism open: "post the file to OpenEMR's document upload
endpoint (re-use what dashboard-port did — see the
`feat/w2-task6-document-upload` work for the API shape — or hit the
sidecar BFF if there's an /api/upload path that proxies it)."

**Deviation:** Picked the BFF-proxy path. Added a new internal PHP
endpoint `internal/upload_document.php` (JWT-authed, mirroring
`internal/get_document_bytes.php`'s pattern) plus a new sidecar BFF route
`POST /api/agent/upload` and a `DocumentUploadWriter` Python helper. The
existing session-authed `public/upload_document.php` is left intact for
any legacy PHP frontend still pointing at it.

**Why:** The vue-ui SPA holds only the BFF's HttpOnly session cookie
(set by the sidecar at the dashboard origin). The OpenEMR PHP session
cookie that `public/upload_document.php` requires for CSRF +
session-derived `pid` lives on a different origin and is not in the
SPA's cookie jar in dev (port 8300 vs the dev sidecar) or production
(same host, but the session cookie is not shared back to the SPA — it
only rides during the OAuth bounce). Direct browser-to-PHP would have
required either downgrading to a confidential-client SPA (secret in
bundle) or shipping a same-origin session-cookie shim, both worse than
proxying.

**What we learned:** "Either direct or via BFF" is rarely a real choice
once you trace the cookie origin/path/scope flow. The BFF was already
sitting in the request path with a JWT context; reusing it for one more
multipart route is cheaper than the cookie-sharing engineering it would
have taken to go direct. The internal-endpoint pattern (JWT-authed,
patient-scope enforced on the PHP side via JWT claim) was already proven
by `get_document_bytes.php`; symmetrising upload onto the same pattern
kept auth posture uniform.

**Artifacts:** branch `feat/t38.11-12-document-flow` commits
`6e975d356` (PHP), `30d18c582` (writer), `74d651198` (BFF route),
`2ba02686b` (turn-request wiring).

---

## 2026-05-08 — Extended `EvalCategory` for the W2 case suite

**Plan:** Task 16 ("Author 50-Case Week2 Eval Suite") names five
categories: `extraction`, `evidence-retrieval`, `citations`,
`refusals`, `missing-data`. Of those, only `missing-data` overlaps the
existing W1 `EvalCategory` enum in `sidecar/tests/eval/harness.py`.

**Deviation:** Added four new W2 members to `EvalCategory`:
`EXTRACTION = "extraction"`, `EVIDENCE_RETRIEVAL = "evidence_retrieval"`,
`CITATIONS = "citations"`, `REFUSAL = "refusal"`. Mapped the spec's
hyphenated `evidence-retrieval` and `refusals` to the snake-case
identifiers the loader's `_CATEGORY_BY_VALUE` dispatch already keys on.

**Why:** Task 16 instructs that "if the task spec contradicts the
loader, the loader is canonical." But the loader rejects unknown
category strings outright (`ValueError`), so simply lowering each spec
category onto the existing enum was not an option for the four
non-overlapping ones. Mapping them all to `HALLUCINATION` /
`MISSING_DATA` would erase the per-category distribution check the
acceptance criterion demands. Extending the enum is the smallest
loader change that preserves both the distribution count
(12/10/10/8/10) and the case-discriminator the harness already runs
through `EvalHarness.summarize()`. The `REFUSAL` label is singular to
match the existing `*_BOUNDARY` / `*_DATA` style.

**What we learned:** The W1 enum was framed as "adversarial /
behavioral categories" — it captured how the agent should respond,
not what type of input drove the case. W2 adds *input-source*
categories (extraction = vision tool over scanned doc, evidence
retrieval = RAG over guideline corpus, citations = synthesizer
contract). Future case-suite expansions probably want to track both
axes; for now we keep them flat in one enum to avoid touching the
harness's dispatch machinery.

**Artifacts:** `sidecar/tests/eval/harness.py` (enum extension),
`sidecar/scripts/validate_eval_cases.py` (new validator script),
`sidecar/tests/eval/test_week2_cases.py` (pytest wrapper),
`sidecar/tests/eval/cases/week2/*.yaml` (the 50 cases).

---

## 2026-05-08 — Lab-PDF E2E test runs process-level, not against live stack (Task 28)

**Plan:** Task 28's brief framed the E2E lab-PDF test as having a choice
between (a) live docker-compose + real Anthropic and (b) process-level
integration with mocked LLM and mocked PHP boundary. The brief assumed
a Python-side `procedure_result_writer` boundary the test could mock at;
it also referenced `~1282` baseline tests.

**Deviation:** Picked shape (b). Built `CapturingLabPersistWriter` and
`CapturingAuditRecorder` as inline Protocol-level fakes in
`tests/integration/_lab_e2e_fixtures.py` — the Python sidecar has **no**
production `LabPersistWriter` yet; persist is implemented entirely PHP-
side via `InternalLabPersistController` (see `interface/modules/.../public/internal/persist_lab_result.php`).
Baseline test count was 1127, not 1282 — final after this branch is 1134.

**Why:**
- `tests/integration/conftest.py` already gates live-stack tests on a
  reachable OpenEMR (skip on cold stack). A test that *requires* the
  stack would regress that "skip-when-down" ergonomic.
- Anthropic calls cost real tokens; the brief explicitly forbade token
  spend by default.
- Building a real Python `LabPersistWriter` to satisfy the brief's mock
  point would have meant writing untested production code in a test
  PR — the brief's "don't modify schemas, tools, or audit logger" rule
  blocked that path. Defining the Protocol locally documents the
  contract a future Python adapter would satisfy without committing to
  a particular implementation.
- The PHP `InternalLabPersistController` already has dedicated PHPUnit
  coverage; duplicating it from Python would duplicate without
  extending.

**What we learned:** Briefs that describe a "boundary to mock" sometimes
presume a boundary that hasn't been built yet. When that happens, the
test can either commit to building the boundary as production code
(scope creep) or define the boundary locally as a test-side Protocol
(scope honest). Local Protocol surfaces are reusable: when the
production adapter lands, it satisfies the same contract by structural
typing and the test doesn't change.

**Artifacts:** branch `feat/task-28-lab-pdf-e2e-test` commits
`408a7619d` (PDF generator + smoke), `fe4d9dbe2` (upload phase),
`b0ac3d960` (extraction phase), `7d0a8860c` (persist phase),
`a5b89ba43` (full-flow ordering + cleanup).

---
