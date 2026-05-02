# Where we left off — 2026-05-02 (late session)

Read me first when picking the project back up. Update or delete me when
the state captured here goes stale.

## What shipped this session (long run — 3 Taskmaster tasks + 3 live bug fixes + droplet ops)

TaskMaster: **39/49 → 42/51 (82%)** — added two new tasks (50 + 51),
finished three parent tasks (27 + 45 + 50).

```
Synthetic test data + seed pipeline (Task 50, 6 subtasks):
  50.1  Synthea v4.0.0 install + spike (CCDA + FHIR R4 output)
  50.2  OpenEMR CCDA importer probe — 8/11 tables populate cleanly,
        notes need separate path
  50.3  Production seed: 25 patients via openemr:ccda-newpatient-import
        + scripts/seed/load_synthea_notes.py (FHIR DocumentReference
        loader, 880 notes via SSH-tunneled mariadb on droplet)
  50.4  scripts/seed/agentforge_demo_overlay.sql — hand-crafted notes
        + vitals trend for pid=8 Eula (complex chronic) and pid=4
        Alena (sparse). Demoes substance_abuse_cfr42 sensitivity
        gating via SUD: title prefix.
  50.5  scripts/seed/validate_seed_data.sql + validate_seed.sh —
        14-check post-import audit, all green local + droplet
  50.6  Eval-fixture decoupling docs (intentionally separate pids
        from live DB; phenotype-level testing only)

Synthesis input truncator (Task 45, 3 subtasks):
  45.1  tiktoken-backed token counting (count_tokens, count_tool_results)
  45.2  Priority-based whole-tool drop with PRIORITY tuple +
        eviction order (unknown tools sort lowest)
  45.3  Within-tool oldest-first shrink via Pydantic model_copy on
        list-shaped payloads. Empty-list cleanup edge case fixed.
  Note: utility is COMPLETE but NOT WIRED into Orchestrator.turn() —
  same pattern as Task 41 (timeouts).

Planner agent (Task 27, 3 subtasks):
  27.1  UseCase StrEnum (4 cases) + PlannedToolCall + Plan model
        with bijection invariant on tool_calls <-> parallel_batches
  27.2  TOOL_SELECTION_BY_USE_CASE static rules + default_plan_for()
        fallback (DEFAULT_BATCH_SIZE=4)
  27.3  Planner class with submit_plan tool-use forced JSON output,
        graceful fallback to default_plan_for(ADMIT_SYNTHESIS) on
        validation failure or no-tool-call response
  Note: also COMPLETE but NOT WIRED — Orchestrator.turn() doesn't
  call Planner yet. DEVIATIONS.md captures the LangGraph-deferred
  rationale.

Live bug fixes during smoke testing the seeded cohort:
  *  fix(deploy): poll sidecar /health to absorb container startup
     race (commit 1a7947831) — wget retry loop replaces single-shot
     check; verified live on the next deploy at +6s.
  *  fix(agentforge): cast DATETIME date columns to DATE in 4
     repositories (commit 85b142f8a) — pid=8's "what are the
     patient's medical problems?" returned 503 because Pydantic
     rejected MariaDB DATETIME strings on Synthea-imported lists
     rows. SQL-layer DATE() cast is now the wire-format contract.
  *  fix(agentforge): bump proxy idle timeout 8s -> 30s (commit
     af9443cb4) — bulky synthesis on 55-row problem list exceeded
     the prior 8s; can come back down once the truncator wires in.
  *  fix(agentforge): clinically relevant problem list — drop
     SNOMED (situation) admin codes, dedup by SNOMED code (commit
     ee4a67c24). pid=8 Eula: 55 raw -> 25 distinct conditions.

Droplet operational cleanup:
  *  Seed pipeline pushed to droplet (matches local cohort exactly).
  *  Stopped 4 parasitic upstream containers (selenium, couchdb,
     openldap, mailpit). Sidecar CPU dropped 44% -> 0.2% (it had
     been queue-waiting behind selenium for weeks).
  *  Removed duplicate `pagentforge-redis` (leftover from earlier
     deploy).
  *  Droplet now runs exactly 5 containers — see docs/DEPLOYMENT.md
     "DO NOT restart casually" section.

Memories saved this session:
  *  project_synthea_location.md — `~/Desktop/Gauntlet/synthea/`
  *  feedback_no_rm_rf.md — surface deletion candidates instead
  *  project_droplet_containers.md — exactly 5 containers should run
```

Sidecar test suite: **460/460 passing** (was 417 at session start —
+43 tests across truncator + planner). mypy clean. ruff clean.
PHP isolated AgentForge tests: **214/214 passing** (was 208 — +6 from
the new ProblemsRepositoryTest). Single failure remains: pre-existing
Vitals height-fixture carryforward.

## Architecture state right now

Tool catalog: **9 tools** (demographics, problems, medications,
allergies, labs, vitals, notes, search_notes, encounters). Task 51
will add `get_immunizations` as the 10th — see "What's ready to pick
up next" below.

Reliability primitives — three are now BUILT BUT NOT WIRED into the
orchestrator. They're tested and ready for an integration pass:

  - **`SynthesisInputTruncator`** (Task 45) — 12k-token cap; priority
    drop + within-tool oldest-first shrink; tiktoken cl100k_base
  - **`Planner`** (Task 27) — UseCase classification + structured
    Plan via tool-use forced JSON output; cheaper Haiku model fits
    well here once a second LLMClient is wired
  - **TimeoutPolicy phase + total_turn budgets** (Task 41
    carryforward) — `per_tool=2s` is enforced, the rest are config
    fields with no consumer. Right shape comes alongside Task 27
    wiring (the planner is what knows about phases).

Already wired and active:

  - Tool-result cache (Redis, 60s TTL)
  - StreamingVerifier with 5-constraint DomainConstraints, OFF by
    default; `VERIFIER_ENABLED=true` on droplet
  - Langfuse traces (Null when not configured; `LANGFUSE_HOST`
    unset on droplet)
  - Session memory (opt-in via `session_id`, SOFT_CAP=6 / HARD_CAP=8
    turns)
  - Sensitivity policy + record visibility check (3 tools consume,
    plus encounter-category gating on encounters)
  - Breakglass audit (in-memory dedup; single-replica)
  - Per-tool timeout + retry (`retry_with_policy` wraps every fetcher;
    transient 5xx/timeout/network with exponential backoff up to 3
    attempts)

## What's deployed and where

`https://143.244.157.90:9300/` — production demo, same droplet.

**The droplet is current end-of-session.** Code matches main as of
the last `./scripts/deploy-droplet.sh module` run. Data side: full
Task 50 seed pipeline applied; 25 patients + 3 baseline; 882
encounters; 3,341 lab results; 886 notes (73 in last 365d); demo
overlay live for pid=8 / pid=4. All 14 `validate_seed.sh` checks
pass on droplet.

Live smoke-test results today (after the bug fixes):

  - ✅ get_active_problems — 25 distinct conditions, real CKD
       progression, no admin noise, IPV/SDOH preserved
  - ✅ get_active_medications — 6 active + 4 ended cited cleanly
  - ✅ get_active_allergies — 11 allergens with severity
  - ❌ "Has this patient been immunized?" — agent invents a
       capability statement ("in this version of the co-pilot")
       because there's no get_immunizations tool. Tracked as
       Task 51.

Droplet env still: `VERIFIER_ENABLED=true`, `agentforge-redis`
container running, `LANGFUSE_HOST` unset (NullLangfuseClient),
`policy_loaded: false` (sensitivity policy YAML path issue,
mitigated by `SENSITIVITY_POLICY_REQUIRED=false`).

Container layout pinned: see `docs/DEPLOYMENT.md` and the project
memory `project_droplet_containers.md`. **Five containers** —
openemr, mysql, phpmyadmin, agentforge-sidecar, agentforge-redis.
The four parasitic upstream containers should stay stopped.

## Live carryforwards (still deferred)

These survived this session — items intentionally not yet shipped,
logged in `docs/DEVIATIONS.md` (search 2026-05-01 / 2026-05-02
entries) and worth eyes on the next pass:

1. **Sensitivity policy YAML path doesn't resolve inside the Docker
   image.** Mitigated by `SENSITIVITY_POLICY_REQUIRED=false`. Two
   fixes to consider: bake YAML at a fixed path in the Dockerfile +
   override `SENSITIVITY_POLICY_PATH`, or use `importlib.resources`.

2. **Verifier coverage gap on notes / search_notes.**
   `verifier/cache.py` `_KNOWN_TOOLS` registers encounters now but
   still doesn't cover notes or search_notes. `[note #61]` won't
   ground via the production verifier. Trivial fix: register
   `"get_recent_notes": ("note", "notes", "id")` and
   `"search_notes": ("note", "results", "id")`.

3. **Frontend doesn't mint or send `session_id`.** Multi-turn
   memory is fully wired server-side, but `chat-panel.js` still
   posts only `{message: ...}`. Until the JS mints a session id,
   every turn is independent.

4. **In-memory breakglass dedup → multi-replica gap.** Replace with
   Redis SETNX when going multi-replica.

5. **Three orchestrator utilities built but not wired** —
   SynthesisInputTruncator (Task 45), Planner (Task 27), and the
   phase/total_turn budgets in TimeoutPolicy (Task 41). The
   integration pass is its own beat.

6. **`per_attempt_timeout` not wired through httpx.** RetryPolicy
   has the field; each fetcher still uses httpx's default 5s.

7. **Eval framework caveats:** fixtures hand-authored (not captured
   from a real demo DB SHA), no LLM-as-judge, regression-locks pin
   canonical agent-style strings (not model behavior end-to-end).

8. **Search results gate on title-prefix only** because
   `notes_search` PHP response doesn't surface `note_type` or
   `attending_only`.

## Known issues / quality gaps surfaced today

- **Vitals height fixture mismatch.** Pre-existing carryforward.
  `'height' => 70.0` vs actual `70`. JSON int round-trip suspected.
  Only remaining AgentForge isolated-test failure.

- **No `get_immunizations` tool.** 348 rows in immunizations table,
  zero tools. Tracked as Task 51 (parent + 4 subtasks).

- **MedicationsRepository may have similar duplication issues to
  what we just fixed in ProblemsRepository.** Eula's response
  showed multiple discontinued contraceptive entries — Synthea
  generates per-fill rows. Less clear-cut than problems (real meds
  legitimately have multiple courses) but worth a closer look. Not
  yet tracked.

- **Demographic citation grammar drifts.** `[demographic #8]` reads
  stilted; the patient's identity isn't a fact that needs citing.
  Tracked under Task 51.3.

- **Section labels in synthesis responses are inconsistent.** Same
  patient, two queries: "Major chronic conditions" vs "Primary
  medical conditions." Worth deciding whether to pin in the system
  prompt or accept variability. Tracked under Task 51.3.

- **Old controller bootstraps use `DriverManager::getConnection(...)`
  directly.** CLAUDE.md prefers `DatabaseConnectionFactory`.
  Module-wide tech debt, not blocking.

## What's ready to pick up next

Pending tasks remaining: 9.

- **51 — Tool catalog gaps + agent output quality + out-of-scope
  handling.** Newly added; 4 subtasks (immunizations, audit/add
  procedures, output refinement, out-of-scope guardrail). High
  priority — addresses the most visible demo gaps.
- **27/45/41 integration pass** — wire the three deferred
  utilities into `Orchestrator.turn()`. No Taskmaster ID yet; would
  need to be created. Probably the next task to add and the highest-
  leverage pure-code work.
- **35, 36, 47** — Docker Compose + reverse proxy + e2e (deploy
  infra triplet, do as a unit when there's appetite for ops work).
- **42** — Identity Ambiguity (newly unblocked by 27, but waits on
  the integration pass to be useful).
- **43** — Prompt Library (depends on 27, 28).
- **46** — Stale Data + Conflict detection.
- **48** — Deployment checklist doc (small).
- **49** — Re-evaluate `idx_procedure_*` index (low priority).

Quick wins still available:

- **Fix verifier `_KNOWN_TOOLS` for notes + search_notes** (~10 min,
  closes carryforward #2).
- **Fix Vitals height-fixture** (~5 min if it's a fixture issue).
- **Mint `session_id` in chat-panel.js** (~30 min, lights up
  multi-turn memory).
- **Bake sensitivity YAML at fixed Dockerfile path** (~30 min,
  lands `policy_loaded: true` on the droplet).
- **Bring proxy timeout back down** once the truncator is wired
  (one-line change after the integration pass).

## Quick-start checklist for next time

1. `git status` — confirm no uncommitted changes you forgot about.
2. `task-master list` and `task-master next` — see what's on deck.
3. `cd sidecar && uv run pytest` — confirm 460/460 still green.
4. If demo'ing again or testing locally:
   - `cd sidecar && ./scripts/sidecar.sh start` (sidecar on :8000)
   - `docker compose -f docker/development-easy/docker-compose.yml ps`
   - Open `http://localhost:8300/` (admin / pass)
5. If deploying to droplet:
   - `./scripts/deploy-droplet.sh check` — confirm droplet healthy
     (currently end-of-session-2026-05-02 state).
   - Code-side and data-side are BOTH current relative to main.
6. Tail Langfuse traces (when configured) for live trace
   inspection; `LANGFUSE_HOST` is unset on the droplet, so
   `NullLangfuseClient` is in use.

## Files worth opening in the first 60 seconds

- `docs/DEPLOYMENT.md` — droplet state including the new "DO NOT
  restart casually" container section.
- `docs/test-data.md` — full seed pipeline reference (six-step
  recipe for re-running from scratch, plus the audits and the
  decoupling rationale on eval fixtures).
- `docs/DEVIATIONS.md` — design decisions vs original plan. New
  this session: the LangGraph-deferred entry for Task 27.
- `docs/CHALLENGES.md` — retrospective on the project's hard parts.
  Updated each session with newly-surfaced classes of friction.
- `ARCHITECTURE.md` — original plan, still authoritative for
  unbuilt sections.
- `.taskmaster/tasks/tasks.json` — the roadmap.
- `sidecar/src/agentforge/orchestrator/planner.py` — Task 27.
  Standalone class waiting to be wired in.
- `sidecar/src/agentforge/orchestrator/truncation.py` — Task 45.
  Same — built, tested, not wired in yet.
- `sidecar/tests/eval/regression_locks.py` — the canonical examples
  that lock the eval framework's primitives. Add cases (and bump
  the size pin) when you have new product intent worth freezing.
- `scripts/seed/` — Task 50's pipeline (load_synthea_notes.py,
  agentforge_demo_overlay.sql, validate_seed_data.sql,
  validate_seed.sh). Every script's preamble describes what it
  does and how to invoke it.
