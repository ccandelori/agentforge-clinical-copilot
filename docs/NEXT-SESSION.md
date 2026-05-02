# Where we left off — 2026-05-02 (very late session)

Read me first when picking the project back up. Update or delete me when
the state captured here goes stale.

## What shipped this session (very long run — 8 tasks closed across 4 hours)

TaskMaster: **42/51 (82%) -> 49/51 (96%)** at the time this doc was
written (49 if both subagents in flight succeed; conservatively 47/51
if not). Eight parent tasks closed today, three of them via parallel
worktree subagents:

```
Task 51 — Tool catalog gaps + agent output quality + out-of-scope handling
  (4 subtasks, all done)
  51.1  get_immunizations tool end-to-end (10th tool)
  51.2  get_procedures tool end-to-end (11th tool, NOT EXISTS
        procedure_result discriminator + ROW_NUMBER dedup)
  51.3  Output refinement — canonical section headers + citation type
        table + demographic-weaving prompt rules. Closed the
        notes/search_notes verifier `_KNOWN_TOOLS` carryforward gap
        as part of the citation-types fix.
  51.4  Out-of-scope guardrail regression-locks (positive + adversarial)

Task 35 — docker/agent compose stack (sidecar + agentforge-redis,
  joins development-easy_default external network). Self-contained
  alternative to the host-script dev workflow. Live-validated.

Task 36 — Apache reverse-proxy include (Alias for /agentforge/turn,
  RFC-1918 access control on /internal/*). Validated both allow path
  (192.168/16 -> 401 from PHP) and deny path (TEST-NET-2 swap -> 403
  from Apache before PHP). Skipped 36.3 (Nginx alt) intentionally —
  project's actual deployment shape is Apache.

Task 46 — DataQualityChecker (parallel subagent worktree). Stale-lab
  flagging (>30d) + problem/note conflict detection (sentence-chunk
  scoped negation cues). 13 tests, mypy + ruff clean. Standalone
  class — integration deferred to the integration pass.

Task 48 — DEPLOY.md deployment checklist (parallel subagent worktree).
  401 lines, seven sections, every reference cites a real artifact
  in this repo (sql/database.sql line numbers, migration paths,
  regression_locks.py refs). Companion to docs/DEPLOYMENT.md (the
  snapshot of what's deployed; DEPLOY.md is the act of deploying).

Live bug fixes during smoke-testing earlier in the session
(see f4b2e7616 / 2b12f5857 / 1b0e3d8a3 / 51ce87e2c):
  *  Citation types canonicalised in the system prompt — LLM was
     emitting [lab #N] instead of [lab_result #N], would have been
     silently rejected by the verifier on production.
  *  Section headers pinned to the tool's clinical surface — same
     query type now produces the same shape across runs.
  *  Demographics woven into clinical sentences instead of standalone
     "[demographic #8]" recitations.
  *  Out-of-scope queries now return "I don't have a tool to retrieve
     X" with no "in this version of the co-pilot" hedge. Pinned by
     OOS-BILLING (positive) and ADV-OOS-HEDGE (adversarial)
     regression locks.

Memories saved this session:
  *  feedback_no_rm_rf.md — surface deletion candidates, don't rm
  *  project_droplet_containers.md — exactly 5 containers should run
  *  project_synthea_location.md — `~/Desktop/Gauntlet/synthea/`
```

Sidecar test suite: **460/460 -> 499/499** (+39 across the day:
+13 immunizations, +13 procedures, +13 data_quality, plus regression
locks growing from 6 -> 9). mypy + ruff clean (one pre-existing
N812 in labs.py noted but unrelated).

PHP isolated AgentForge tests: **214/214 -> 251/251** (+37). One
pre-existing failure remains: the Vitals height-fixture
`'height' => 70.0` vs `70` carryforward. Not blocking.

## Architecture state right now

Tool catalog: **11 tools.** demographics, problems, medications,
allergies, labs, vitals, notes, search_notes, encounters, immunizations,
procedures.

Reliability primitives — three are still BUILT BUT NOT WIRED into the
orchestrator (the integration pass remains the single most leveraged
pure-code beat available):

  - **`SynthesisInputTruncator`** (Task 45) — 12k-token cap; priority
    drop + within-tool oldest-first shrink; tiktoken cl100k_base.
    PRIORITY tuple now includes immunizations + procedures.
  - **`Planner`** (Task 27) — UseCase classification + structured
    Plan via tool-use forced JSON output; falls back to
    default_plan_for(ADMIT_SYNTHESIS) on validation failure.
    TOOL_SELECTION_BY_USE_CASE now includes immunizations + procedures
    in ADMIT_SYNTHESIS, procedures in DELTA_COMPUTATION.
  - **`DataQualityChecker`** (Task 46, NEW today) — stale-lab
    flagger + problem/note conflict detector. Same standalone-class
    pattern.
  - **TimeoutPolicy phase + total_turn budgets** (Task 41
    carryforward) — still only `per_tool=2s` is enforced.

Already wired and active:

  - Tool-result cache (Redis, 60s TTL)
  - StreamingVerifier with 5-constraint DomainConstraints, OFF by
    default; `VERIFIER_ENABLED=true` on droplet
  - Verifier `_KNOWN_TOOLS` covers all 11 tools (notes/search_notes
    gap closed as part of 51.3)
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

**The droplet is BEHIND main.** End-of-session-2026-05-01 the droplet
was current; today's eight new merges (Task 51.1, 51.2, 51.3, 51.4,
35, 36, 46, 48) are local-only. Run `./scripts/deploy-droplet.sh
all` to bring the droplet forward. Demo will gain immunizations +
procedures tools live on the next deploy.

Local dev now has TWO ways to run the sidecar (introduced by Task 35):

  1. **Host script:** `./sidecar/scripts/sidecar.sh start` —
     `--reload` enabled, instant iteration, bind on host port 8000.
     Module .env: `AGENTFORGE_SIDECAR_URL=http://host.docker.internal:8000`
  2. **Docker stack:** `cd docker/agent && docker compose up --build`
     — production-shape image, `agentforge-redis` companion service
     to match the droplet, host port 8400. Module .env:
     `AGENTFORGE_SIDECAR_URL=http://agentforge-sidecar:8000`

Mutually exclusive — both register `agentforge-sidecar` as a
network hostname. `docker/agent/README.md` documents which to pick
when.

The Apache reverse-proxy include (Task 36) is shipped at
`interface/modules/custom_modules/oe-module-agentforge/deploy/`
but NOT auto-installed. Operators copy it into Apache's conf.d/
manually; `deploy/README.md` walks dev-easy / Debian / droplet
installs separately.

Live smoke-test results today (after the 51.x work):

  - ✅ get_active_problems — 25 distinct conditions, real CKD
       progression, no admin noise, IPV/SDOH preserved
  - ✅ get_active_medications — 6 active + 4 ended cited cleanly
  - ✅ get_active_allergies — 11 allergens with severity
  - ✅ get_immunizations — 12 rows for pid=8, vaccine_name resolved
       cleanly via codes table (CVX 140 -> "Influenza, seasonal,
       injectable, preservative free", etc.)
  - ✅ get_procedures — 18 deduped procedures (raw 328), grouped
       clinically into behavioral health screenings, dental care,
       preventive procedures
  - ✅ Out-of-scope queries (billing, family history) get clean
       "I don't have a tool to retrieve X" responses
  - ✅ Citation grammar — section headers canonical, demographic
       woven into clinical sentences

Droplet env still: `VERIFIER_ENABLED=true`, `agentforge-redis`
container running, `LANGFUSE_HOST` unset (NullLangfuseClient),
`policy_loaded: false` (sensitivity policy YAML path issue).

Container layout pinned: see `docs/DEPLOYMENT.md` and the project
memory `project_droplet_containers.md`. **Five containers** —
openemr, mysql, phpmyadmin, agentforge-sidecar, agentforge-redis.
The four parasitic upstream containers should stay stopped.

## Live carryforwards (still deferred)

These survived this session — items intentionally not yet shipped,
logged in `docs/DEVIATIONS.md` (search 2026-05-01 / 2026-05-02
entries) and worth eyes on the next pass:

1. **Sensitivity policy YAML path doesn't resolve inside the Docker
   image.** Mitigated by `SENSITIVITY_POLICY_REQUIRED=false`.
   Two fixes to consider: bake YAML at a fixed path in the
   Dockerfile + override `SENSITIVITY_POLICY_PATH`, or use
   `importlib.resources`.

2. **Frontend doesn't mint or send `session_id`.** Multi-turn
   memory is fully wired server-side, but `chat-panel.js` still
   posts only `{message: ...}`. Until the JS mints a session id,
   every turn is independent.

3. **In-memory breakglass dedup -> multi-replica gap.** Replace
   with Redis SETNX when going multi-replica.

4. **Four orchestrator utilities built but not wired** —
   SynthesisInputTruncator (Task 45), Planner (Task 27),
   DataQualityChecker (Task 46), and the phase/total_turn budgets
   in TimeoutPolicy (Task 41). The integration pass is its own
   beat — no Taskmaster ID yet but it's the highest-leverage
   pure-code work remaining.

5. **`per_attempt_timeout` not wired through httpx.** RetryPolicy
   has the field; each fetcher still uses httpx's default 5s.

6. **Eval framework caveats:** fixtures hand-authored (not captured
   from a real demo DB SHA), no LLM-as-judge, regression-locks pin
   canonical agent-style strings (not model behavior end-to-end).

7. **Search results gate on title-prefix only** because
   `notes_search` PHP response doesn't surface `note_type` or
   `attending_only`.

8. **Citation parser silently swallows multi-id forms.**
   `[problem #293, #294]` parses as a citation to #293 only; #294
   ends up in `extra` and never gets verified. Tightening the
   parser is a separate beat from 51.3.

9. **Apache reverse-proxy include is shipped but not auto-installed.**
   Operators must `docker cp` it into Apache's conf.d/ manually.
   Future revision of `scripts/deploy-droplet.sh` could push it
   automatically.

## Known issues / quality gaps surfaced today

- **Vitals height fixture mismatch** (carryforward unchanged).
  `'height' => 70.0` vs actual `70`. JSON int round-trip suspected.
  Only remaining AgentForge isolated-test failure.

- **MedicationsRepository may have similar duplication issues to
  what we fixed in ProblemsRepository / ProceduresRepository.**
  Eula's response showed multiple discontinued contraceptive
  entries — Synthea generates per-fill rows. Less clear-cut than
  problems / procedures (real meds legitimately have multiple
  courses) but worth a closer look. Not yet tracked.

- **Old controller bootstraps use `DriverManager::getConnection(...)`
  directly.** CLAUDE.md prefers `DatabaseConnectionFactory`.
  Module-wide tech debt, not blocking.

## What's ready to pick up next

Pending tasks remaining: 4 (or 2 once today's two in-flight
subagents land — Tasks 49 + 42).

- **42 — Identity Ambiguity** (in flight: parallel subagent today;
  may be done by the time you read this). Detects cross-patient
  references mid-conversation.
- **43 — Prompt Library** (depends on 27, 28, both done). Externalize
  system / planner / synthesizer prompts into versioned templates.
  Substantial refactor — touches every place SYSTEM_PROMPT is read.
- **47 — E2E Integration Test Suite** (deps 33, 35, 36 all done now).
  Build full PHP-module-through-sidecar integration tests. The
  unblocked-but-not-yet-shipped triplet from earlier is now closed
  on its dependencies — 47 is the last piece.
- **49 — idx_procedure_* re-eval** (in flight: parallel subagent
  today; expects to drop the index as redundant).
- **Integration pass** (no Taskmaster ID) — wire the FOUR deferred
  utilities (truncator, planner, timeout budgets, data-quality
  checker) into `Orchestrator.turn()`. Probably the next task to
  add and the highest-leverage pure-code work after this batch.

Quick wins still available:

- **Bake sensitivity YAML at fixed Dockerfile path** (~30 min,
  lands `policy_loaded: true` on the droplet).
- **Mint `session_id` in chat-panel.js** (~30 min, lights up
  multi-turn memory).
- **Bring proxy timeout back down** once the truncator is wired
  (one-line change after the integration pass).
- **Tighten citation parser** to handle multi-id forms (~30 min).

## Quick-start checklist for next time

1. `git status` — confirm no uncommitted changes you forgot about.
2. `task-master list` and `task-master next` — see what's on deck.
3. `cd sidecar && uv run pytest` — confirm 499/499 still green.
4. If demo'ing again or testing locally:
   - **Host-script mode:** `./sidecar/scripts/sidecar.sh start`
     (sidecar on :8000, requires REDIS_URL set)
   - **Docker-stack mode:** `cd docker/agent && docker compose up
     --build` (sidecar on host :8400, container :8000, with
     companion agentforge-redis)
   - `docker compose -f docker/development-easy/docker-compose.yml ps`
   - Open `http://localhost:8300/` (admin / pass)
5. If deploying to droplet:
   - `./scripts/deploy-droplet.sh check` — confirm droplet healthy
   - **Code-side is BEHIND main** — `./scripts/deploy-droplet.sh all`
     to push today's eight new merges (immunizations + procedures
     tools, output refinement, data-quality checker, deploy infra).
6. If testing the apache reverse-proxy include locally:
   - `docker cp interface/modules/custom_modules/oe-module-agentforge/deploy/apache-agentforge.conf development-easy-openemr-1:/etc/apache2/conf.d/agentforge.conf`
   - `docker exec development-easy-openemr-1 httpd -k graceful`
   - `wget -qO- http://localhost:8300/agentforge/turn` (should be 400 = no session)

## Files worth opening in the first 60 seconds

- `DEPLOY.md` (NEW) — deployment checklist; act-of-deploying companion
  to docs/DEPLOYMENT.md.
- `docs/DEPLOYMENT.md` — droplet snapshot including the 5-container
  layout pin.
- `docs/DEVIATIONS.md` — design decisions vs original plan.
- `docs/CHALLENGES.md` — retrospective on the project's hard parts.
- `docker/agent/README.md` (NEW) — local docker stack walkthrough.
- `interface/modules/custom_modules/oe-module-agentforge/deploy/README.md`
  (NEW) — apache include install instructions.
- `ARCHITECTURE.md` — original plan, still authoritative for unbuilt
  sections.
- `.taskmaster/tasks/tasks.json` — the roadmap.
- `sidecar/src/agentforge/orchestrator/planner.py` — Task 27.
  Standalone class waiting to be wired in.
- `sidecar/src/agentforge/orchestrator/truncation.py` — Task 45.
  Same — built, tested, not wired in yet.
- `sidecar/src/agentforge/verifier/data_quality.py` (NEW) — Task 46.
  Same — built, tested, not wired in yet.
- `sidecar/tests/eval/regression_locks.py` — 9 canonical examples
  pinning the eval framework's primitives.
- `scripts/seed/` — Task 50's pipeline (load_synthea_notes.py,
  agentforge_demo_overlay.sql, validate_seed_data.sql,
  validate_seed.sh).
