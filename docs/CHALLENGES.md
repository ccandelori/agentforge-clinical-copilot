# Challenges & hard parts

A running log of the **classes of friction** this project has encountered —
not a bug list, but the kinds of problems that took real time to work
through and that would be useful to reference when talking about what
was hard. Each entry has a concrete example, what we did, and what we
learned.

Update this file whenever a new *category* of friction surfaces. Don't
log every individual bug — log the patterns.

---

## 1. Specifications that imported foreign idioms

**The challenge.** The Taskmaster task specs were written with the
right product intent but in some cases sketched their implementations
using framework idioms that didn't exist in the codebase. Following
the spec literally would have meant adopting whole new frameworks
mid-task.

**Examples.**

- **Task 27 (Planner)** — spec sketched `async def planner_node(state:
  AgentState, llm: LLMClient)`, importing LangGraph's `AgentState`
  TypedDict pattern. The codebase has `langgraph` listed as a
  dependency in `pyproject.toml` but no module imports it. Adopting
  LangGraph mid-task would have meant rewriting `Orchestrator.turn()`
  as a state graph, restructuring the agent loop, and re-typing every
  node — well beyond the planner's actual scope.
- **Task 45 (Truncator)** — spec used OpenAI's
  `response_format={'type': 'json_object'}`, an OpenAI-only API.
  We're on Anthropic. The structured-output goal (use case + tool
  plan) is achievable via Anthropic's tool-use forcing — same
  primitive every other tool in the catalogue uses.

**What we did.** Built each as a standalone class (`Planner`,
`SynthesisInputTruncator`) that fits existing patterns
(`Orchestrator`, `Verifier`, `BreakglassAuditTool`). Logged each
divergence in `docs/DEVIATIONS.md` with the rationale. The wiring is
deferred to a later integration pass.

**What we learned.** Spec sketches that import idioms from outside
the codebase need a sanity check against the actual codebase before
being followed literally. The product intent is usually right; the
literal implementation often isn't. *"Build it the way this codebase
already builds things"* is almost always cheaper than *"build it the
way the spec literally describes."*

---

## 2. Cross-stack data shape contracts

**The challenge.** The agent's runtime data path crosses three
languages and four type systems: MariaDB schema → PHP repository
output → JSON wire → Python Pydantic models. A type mismatch at any
boundary fails silently or crashes loudly somewhere unexpected.

**Examples.**

- **DATETIME vs `date`.** `lists.begdate` is a MariaDB DATETIME column
  that ships as `"2026-02-06 17:32:52"`. The Python side
  (`ProblemItem.begin_date: date | None`) rejected datetime strings
  with non-zero time. Allergies happened to work because Synthea uses
  midnight (`00:00:00`) for allergy begdates. Symptom: pid=8's "what
  are the patient's medical problems?" returned a 503 with a generic
  technical-error message; allergies returned cleanly. Fix: cast to
  `DATE()` in the SQL projection — the wire format is now
  unambiguously `YYYY-MM-DD` across four repositories.
- **Sensitivity gating spans multiple components.** A note's
  visibility decision pulls from: the auth gateway's clearance set,
  the sensitivity-policy YAML's title-prefix matchers, the PHP
  repository's `clinical_notes_type` column, and the Python tool's
  `RecordMetadata` construction. Three of four layers must surface
  the same attribute for a single rule to fire. Symptom in the live
  demo: `substance_abuse_cfr42` fires on `pnotes` titles via
  `note_title_prefixes` but not on the `note_types` matcher because
  `pnotes` doesn't have a `note_type` column.

**What we did.** Picked the wire format as the contract and pushed
fixes into the layer closest to the source data (SQL `DATE()` cast,
not Python validators). Documented per-tool sensitivity-rule reach
in `docs/test-data.md` and `docs/DEVIATIONS.md`.

**What we learned.** When a bug appears at the Pydantic boundary, the
right fix is usually upstream of Pydantic, not at it. Pydantic's
strictness is a feature; loosening it to accept whatever the upstream
sends defeats the purpose. The SQL layer is the cheapest place to
shape data because the change is one line and it's the same shape
regardless of how many readers exist.

---

## 3. Synthetic data realism — Synthea looks great until you import it

**The challenge.** Synthea is peer-reviewed, statistically
calibrated, and produces enormous volumes of structured clinical
data. It's also a templated population-level generator, which means
individual patient records carry artifacts that real clinical data
wouldn't.

**Examples.**

- **Per-encounter problem duplication.** Synthea creates a new
  `lists` row every time an encounter "touches" a condition. A
  single chronic problem like "Stress" can repeat 6+ times across a
  patient's history. Real EMR problem lists show one entry per
  distinct condition.
- **Administrative codes mixed into the problem list.**
  `(situation)` SNOMED concept-class rows like "Medication review
  due (situation)" appear alongside real `(disorder)` rows. They're
  technically valid SNOMED but they don't belong on a clinician-
  facing problem list. A demo with these visible looks like an
  unfiltered codebook dump.
- **CCDA importer leaves clinical notes empty.** OpenEMR's built-in
  CCDA importer (`bin/console openemr:ccda-newpatient-import`)
  populates structured tables (encounters, problems, meds, vitals,
  procedures) but doesn't touch `pnotes` or `form_clinical_notes`.
  Synthea's CCDA explicitly *doesn't* contain free-text notes — the
  C-CDA spec uses `<text>` blocks for HTML render-narratives, not
  for clinical notes. Notes live only in Synthea's FHIR R4 output as
  `DocumentReference` resources. We had to build a custom Python
  loader (`scripts/seed/load_synthea_notes.py`) that reads the FHIR
  bundles and INSERTs into `pnotes`.
- **Vitals over-collapse.** The CCDA importer kept exactly one
  vitals reading per patient out of the 62+ observations Synthea
  generated. The vitals trend tool returned single-point "trends."
  We added a hand-crafted overlay with 5 readings per demo patient.

**What we did.**
- For problem-list duplication: SQL `ROW_NUMBER() OVER (PARTITION BY
  diagnosis ORDER BY begdate DESC)` window with `WHERE rn = 1` to
  keep the most-recent occurrence per code.
- For admin codes: `WHERE title NOT LIKE '%(situation)%'` filter at
  the SQL level. `(disorder)` and `(finding)` rows survive — the
  latter cover legitimate SDOH and safety screens.
- For missing notes: separate FHIR `DocumentReference` loader
  (`scripts/seed/load_synthea_notes.py`) running over an SSH tunnel
  to the droplet's mariadb.
- For vitals: hand-crafted overlay (`agentforge_demo_overlay.sql`)
  with realistic 12-month trends for the two demo patients.

**What we learned.** Synthea is statistically valid at the
population level but produces individual records that look
mechanically generated. Demos that show the data unfiltered look
worse than ones that show real data. The right level of cleaning
isn't "make it match Synthea's raw output" but "make it match what a
clinician's chart would actually contain." That gap is non-trivial.

---

## 4. Shared dev infrastructure surprises

**The challenge.** The droplet runs OpenEMR via the upstream
`development-easy` docker-compose stack, which ships a number of
services we don't use. They're "free" — except when they aren't.

**Examples.**

- **Selenium burning a CPU core for weeks.** The
  `development-easy-selenium-1` container (Chromium for E2E
  testing) was caught at 109% CPU on a 1-vCPU droplet with load
  average 44.60. AgentForge doesn't use selenium. The sidecar's
  CPU was reading 44% — not because the sidecar was busy, but
  because it was queue-waiting behind selenium. After stopping
  selenium + couchdb + openldap + mailpit (also unused), sidecar
  CPU dropped from 44% → 0.2%.
- **A duplicate-named Redis container** (`pagentforge-redis`,
  leftover from a typo'd earlier deploy) ran for 17 hours
  consuming negligible resources but cluttering the operational
  picture. Easy to overlook; hard to know whether it was load-
  bearing without inspecting it.

**What we did.** Documented the canonical container set in
`docs/DEPLOYMENT.md` and saved it as a project memory
(`project_droplet_containers.md`). The deploy doc now includes a
"DO NOT restart casually" callout because `docker compose up` on
the host stack would bring the parasitic services back.

**What we learned.** "Tight" hosts (1 vCPU, 4 GB RAM) make every
parasitic process matter. Before recommending a resize, check
`docker ps`. Sometimes the answer is "stop selenium," not "buy a
bigger droplet."

---

## 5. Latency budgets calibrated for fixtures, not real data

**The challenge.** Timeouts that work for hand-authored test
fixtures break on Synthea-realistic volumes. The integration tests
pass; the live demo 503's.

**Examples.**

- **Proxy idle timeout (8s) vs 55-row problem list synthesis.** The
  PHP proxy's HTTP client was configured with `timeout: 8.0` (idle)
  and `max_duration: 30.0`. A comment said "total agent deadline
  is 7s; leave a small margin." But `TimeoutPolicy.total_turn=7s`
  isn't yet enforced (carryforward from Task 41). On pid=8 Eula
  Crist's 55-row problem list, Sonnet took ≥10s to first byte
  during synthesis — the idle timer fired and surfaced as
  `TransportException` → 503 "Agent sidecar unreachable" to the
  user.
- **Per-tool timeout vs per-encounter response volume.** Same shape
  at the tool level: `TimeoutPolicy.per_tool=2s` is fine for small
  responses but tight for tools that legitimately return larger
  result sets.

**What we did.** Bumped the proxy idle timeout to 30s as an
immediate fix; flagged that the proper fix is wiring in the
SynthesisInputTruncator (Task 45) so the synthesis input is capped
and the timeout can come back down.

**What we learned.** Latency budgets need to be set against real
data volumes, not fixture-sized data. The smoke test against the
seeded cohort surfaced two timeout-class issues that the fixture-
backed test suite never would have. Real-data smoke testing earlier
in development would have surfaced these earlier.

---

## 6. Hidden test failures and the cost of `--exclude-filter`

**The challenge.** When a test failure exists for a "known unrelated"
reason, the easy path is to exclude it from the run. That works
until a different bug hides behind the same exclude flag.

**Example.**

- The Vitals isolated test had a long-standing pre-existing failure
  on a height fixture (`'height' => 70.0` vs the actual `70`,
  suspected JSON int round-trip). The team had been running with
  `--exclude-filter Vitals` to keep the suite green. A separate bug
  — the JWT umbrella exception fix omitting `InvalidTokenStructure`
  — landed a test failure on `InternalVitalsControllerTest::
  rejectsMalformedBearer` that was masked by the same exclude
  filter for an entire session. The bug was only discovered during
  a full sweep without the exclude.

**What we did.** Backported the umbrella `JwtException` catch to all
five older controllers in a single commit (`a40b440fe`). Began
running fuller sweeps without exclude flags.

**What we learned.** `--exclude-filter` is sometimes necessary in
the short term (broken fixtures, environmental issues) but every
exclude is a place where future bugs can hide. The cost of fixing
the underlying flake isn't always small, but the cost of leaving an
exclude flag in place is hidden. Periodic full sweeps without any
excludes are the cheapest defense.

---

## 7. MVP-isms that won't scale (and that's okay, until it isn't)

**The challenge.** Several MVP shortcuts work fine for single-
process, single-replica, single-droplet deployments — but encode
assumptions that won't survive the first scale-out.

**Examples.**

- **Breakglass dedup is in-memory.**
  `BreakglassAuditTool._logged_sessions: set[tuple[int,int,str]]`
  is a per-process set. Sidecar restart wipes it; multi-replica
  would write one audit row per replica per session. Currently
  fine because we run one sidecar.
- **Sensitivity policy YAML loads from a path that resolves
  differently inside the Docker image.** Mitigated by setting
  `SENSITIVITY_POLICY_REQUIRED=false`, but `policy_loaded: false`
  on the droplet's `/health` is a real gap.
- **The dev seed loader uses an SSH tunnel for the droplet.**
  Production seeding shouldn't require SSH; this works only because
  the droplet is dev-shaped (mariadb on localhost:8320, accessible
  via `ssh -L`).

**What we did.** Logged each in `docs/DEVIATIONS.md` with the
"correct" fix path (Redis SETNX, importlib.resources, OpenEMR's
FHIR POST endpoint with OAuth2). They stay deferred for the MVP
demo.

**What we learned.** MVP-correctness and production-correctness are
different bars. Logging the gap with a path-to-fix is the cheap
discipline that makes the deferral safe. The deferral itself is
fine — what's not fine is forgetting that the deferral exists.

---

## 8. Tool catalog gaps surface live, not in tests

**The challenge.** The fixture suite tests every tool the agent has.
It can't test tools that don't exist yet. Gaps in the catalog only
surface when a real user asks a real question.

**Example.**

- The first user query to test the seeded cohort: *"Has this patient
  been immunized?"* The `immunizations` table has 348 rows on the
  droplet. There's no `get_immunizations` tool. The model didn't
  just say "I can't retrieve that" — it invented a capability
  statement: *"I don't have access to immunization records in this
  version of the co-pilot."* Speculation about future versions of
  itself.

**What we did.** Tracked as Task 51 — adds `get_immunizations` as
the 10th tool, audits other gaps (likely `get_procedures`),
refines output for similar capability-statement issues, and adds an
out-of-scope guardrail to the system prompt so the model says
plainly "I don't have a tool to retrieve X" instead of
extrapolating.

**What we learned.** A complete-looking tool catalog isn't the same
as one a clinician would call complete. The fixture-level testing
can't surface this gap because fixtures only exercise tools that
already exist. The first pass of "real questions" against the
seeded cohort surfaces the missing surface area in 5 minutes —
which is much faster than auditing the catalog up front.

---

## 9. The decoupling discipline (eval framework vs live data)

**The challenge.** When the live data shape changes (Task 50 added
25 new patients), the temptation is to update every reference —
tests, fixtures, regression locks — so they "match." Doing so
couples test fidelity to data state and bites you on the next
seed change.

**Example.**

- The eval fixture file (`sidecar/tests/fixtures/agent_eval.json`)
  uses pid=100 (Susan Underwood, "complex chronic") and pid=200
  (Alex Newman, "sparse"). The seeded DB has pid=8 Eula Crist
  (complex chronic) and pid=4 Alena Marquardt (sparse). Renaming
  the fixture pids and patient names to match the live DB would
  have churned the regression-lock canonical strings without
  improving test fidelity.

**What we did.** Updated the fixture's `_about` field to
explicitly document the decoupling: the eval tests phenotype-level
agent behavior, not patient-specific behavior; the fictional pids
are deliberate. Logged the rationale in `docs/test-data.md` and
this CHALLENGES.md.

**What we learned.** The point of decoupling is that the test
suite stays valid when the data changes. When the data changes,
update the docs that explain WHY they're decoupled — don't update
the tests to "match." It's the test that's stable; it's the data
that's expected to drift.

---

## How to add to this doc

When a new class of friction surfaces (not a one-off bug — a
*pattern* that you'd describe to someone reviewing the project),
add an `## N. <category title>` section with:

- **The challenge.** One paragraph naming the pattern.
- **Example(s).** One or two concrete instances, with file paths or
  commit hashes if useful.
- **What we did.** What landed, where (commit / file).
- **What we learned.** The transferable insight — what would
  make next time easier.

Keep examples concrete; keep takeaways general.
