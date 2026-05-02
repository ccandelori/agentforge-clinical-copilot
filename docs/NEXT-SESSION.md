# Where we left off — 2026-05-02

Read me first when picking the project back up. Update or delete me when
the state captured here goes stale.

## What shipped this session (huge run — 11 Taskmaster tasks + 1 carryforward fix)

TaskMaster: **28/49 → 39/49 (80%)**.

```
Notes vertical (PHP + Python, parallel sub-agent on PHP):
  23  PHP recent_notes endpoint (UNION pnotes + form_clinical_notes)
  22  Python get_recent_notes tool adapter (per-record sensitivity gating)
  25  PHP notes_search endpoint (FULLTEXT MATCH AGAINST)
  24  Python search_notes tool adapter (gating + empty-query short-circuit)

Reliability:
  41  TimeoutPolicy + RetryPolicy + retry_with_policy + orchestrator wiring
      (transient 503/504/timeout/network retry; degradation notice)
  34  Breakglass audit (Python BreakglassAuditTool + PHP log_breakglass.php
      + DEVIATIONS for in-memory dedup vs Redis SETNX)

Eval framework (sequential, solo):
  37  EvalHarness (programmatic grounding + behavior callable)
  38  MockToolLayer + agent_eval.json fixtures (8 tools + later extended to 9)
  39  Regression-lock suite (6 canonical (response, case) triples, locked
      against the eval primitives)

Ops + tooling:
  44  scripts/configure_api_logging.php — REFRAMED (spec wanted a
      users.api_log_option column that doesn't exist; shipped a global-
      setter instead)
  21  Encounters tool — Python + PHP, parallel sub-agent. Spec wanted
      FHIR Encounter endpoint; shipped custom internal endpoint
      (matches every other AgentForge tool). MockToolLayer now covers 9/9.

Carryforward fix (from prior session's open issues list):
  *   Umbrella JwtException catch backported to 5 older controllers
      (Allergies, Demographics, Medications, Problems, Vitals). Was
      catching only RequiredConstraintsViolated | RuntimeException —
      missed InvalidTokenStructure. Vitals' malformed-bearer test was
      actually failing all session, hidden by --exclude-filter Vitals.
      Same commit fixed pre-existing phpcs file-header docblock nits
      on those 5 files. Commit a40b440fe.
```

Sidecar test suite: **417/417 passing** on main. mypy clean on src/. ruff clean.
PHP isolated AgentForge tests: **208/208 passing** (only the
pre-existing Vitals height-fixture failure is excluded — see
"Older known issues" below).

## Architecture state right now

The agent's tool catalog: **9 tools** (demographics, problems,
medications, allergies, labs, vitals, notes, search_notes, encounters).
Five of those gate per-record via `AuthGateway.check_record_visibility`
(notes, search_notes, encounters, plus the older two implicitly via
the gateway's existing rules).

Reliability primitives wired into the orchestrator:

  - **Tool-result cache** (Redis, 60s TTL) on every dispatch
  - **StreamingVerifier** with the 5-constraint DomainConstraints —
    OFF by default; flip via `VERIFIER_ENABLED=true`. Verifier's
    `_KNOWN_TOOLS` covers 7 of 9 (notes / search_notes citations
    don't ground; see the verifier-coverage gap below)
  - **Langfuse traces** — Null impl when not configured, real one
    when `LANGFUSE_HOST` + keys are set
  - **Session memory** — opt-in via `session_id` on the request body;
    SOFT_CAP=6 / HARD_CAP=8 turns
  - **Sensitivity policy** + record visibility check — loaded on
    startup; gateway's `check_record_visibility` consumed by 3 tools
  - **Breakglass audit** — fires once per session via in-memory
    dedup, never raises (AUDIT_FAILED is an enum value, not an
    exception)
  - **Timeout/Retry** — `retry_with_policy` wraps every fetcher call;
    `TimeoutPolicy.per_tool=2s` is the active budget; transient
    5xx/timeout/network retries with exponential backoff up to 3
    attempts. Persistent timeouts surface a degradation notice.

Eval framework primitives (CI-deterministic, no real LLM required):

  - `tests/mocks/tools.py` — `MockToolLayer` with hand-authored fixtures
    for 9 tools × 2 patient phenotypes (Susan Underwood complex chronic,
    Alex Newman sparse).
  - `tests/eval/harness.py` — `EvalHarness.evaluate(response, case,
    tool_results)` returns an `EvalResult` with grounded /
    grounding_failures / behavior_pass / citations_found.
  - `tests/eval/regression_locks.py` — 6 locked (response, case, fixture)
    triples, parametrized over a single test. Set size pinned at 6 by
    a separate test so adding/removing locks requires intent.

The system prompt now declares all 9 tools and mandates `[record_type
#id]` citations on every factual sentence.

## What's deployed and where

`https://143.244.157.90:9300/` — production demo, same droplet.

**The droplet is current as of 2026-05-02 end-of-session.** All 11
tasks + the JWT backport were redeployed via
`./scripts/deploy-droplet.sh`. Sidecar comes up clean with no import
errors from the new modules (notes, search_notes, encounters,
breakglass, timeouts). Live smoke test confirmed:

  - ✅ `get_recent_notes` fires and returns a clean empty result
       (Susan Underwood) — no hallucinated notes
  - ✅ `get_recent_encounters` ditto — clean empty result
  - ✅ Deploy-script race fix verified — `/health` polled at +6s
       (would have been a false-positive failure under the old
       single-shot wget)
  - ⚠️ Empty results above mean **the demo DB doesn't have notes /
       encounters for Susan** — see Test data carryforward in
       "Live carryforwards" below
  - ⚠️ `policy_loaded: false` still present — sensitivity policy
       path issue, mitigated by `SENSITIVITY_POLICY_REQUIRED=false`
       (carryforward #1)
  - ⚪ Verifier redaction path still not tested live (no
       speculative-question test run yet)

Droplet env still: `VERIFIER_ENABLED=true`, `agentforge-redis`
container running, `LANGFUSE_HOST` unset (NullLangfuseClient).

If you redeploy, the deploy script is at `scripts/deploy-droplet.sh`
and retains all prior conventions.

## Live carryforwards (deferred items from this session's work)

These are decisions or follow-ups that were intentional, logged in
`docs/DEVIATIONS.md` (scroll to the 2026-05-01 / 2026-05-02 entries),
but want eyes:

1. **Sensitivity policy YAML path doesn't resolve inside the Docker
   image.** Same as last session — `Settings.sensitivity_policy_path`
   resolves relative to the package layout, which differs in the
   container. Two fixes worth considering: bake YAML at a fixed path
   in the Dockerfile and override `SENSITIVITY_POLICY_PATH` in
   container env; or use `importlib.resources`. Currently mitigated
   by `SENSITIVITY_POLICY_REQUIRED=false` on the droplet.

2. **Verifier coverage gap on notes / search_notes / encounters.**
   `verifier/cache.py` `_KNOWN_TOOLS` registers encounters this
   session but still doesn't cover notes or search_notes. So a
   citation like `[note #61]` won't ground via the production
   verifier (it WILL ground via the eval harness because eval uses
   the same `build_citation_index`, but the eval was checked against
   `_KNOWN_TOOLS`). When the model starts citing notes in real
   responses, this becomes a redaction problem. Trivial fix:
   register `"get_recent_notes": ("note", "notes", "id")` and
   `"search_notes": ("note", "results", "id")` in the dispatch table.

3. **Frontend still doesn't mint or send `session_id`.** Multi-turn
   memory is fully wired server-side, but
   `interface/modules/custom_modules/oe-module-agentforge/public/js/chat-panel.js`
   still posts only `{message: ...}`. Until the JS mints a session
   id (e.g. `crypto.randomUUID()` in localStorage on conversation
   start), every turn is independent.

4. **In-memory breakglass dedup → multi-replica gap.**
   `BreakglassAuditTool._logged_sessions` is a per-process set.
   Sidecar restart wipes it; multi-replica deployments would write
   one audit row per replica per session. Replace with Redis SETNX
   when going multi-replica. Currently single-replica on the
   droplet — non-issue today.

5. **TimeoutPolicy phase + total-turn budgets deferred.** Only
   `per_tool` is enforced. `tool_phase`, `total_turn`, `max_steps`,
   `synthesis_input_cap` are config fields with no consumer yet.
   Right shape comes alongside Task 27 (Planner restructure) — the
   Planner is the layer that knows about phases.

6. **`per_attempt_timeout` not wired through httpx.** RetryPolicy
   has the field; each fetcher still uses httpx's default 5s.
   Wiring per-attempt timeout through every fetcher constructor
   was out of scope for Task 41.

7. **Eval framework caveats:**
   - Fixtures hand-authored (not captured from a real demo DB SHA)
   - No LLM-as-judge — grounding + behavior callable only
   - Regression locks pin canonical agent-style response strings, not
     model behavior. End-to-end model regression is a real-LLM eval
     run, which is an open follow-up.

8. **Search results gate on title-prefix only** because the PHP
   notes_search response doesn't surface `note_type` or
   `attending_only`. Title-prefix policy rules fire correctly;
   note-type / attending-only rules are best-effort allow until the
   search response is extended. Worth tracking if those rule
   classes ever fire in prod.

9. **Demo DB lacks clinical notes + encounters for our test
   patients.** Live smoke test of `get_recent_notes` and
   `get_recent_encounters` against Susan Underwood (pid=2) on the
   droplet returned clean empty results — tools fire correctly but
   there's nothing to retrieve. We can't meaningfully demo the
   notes / search_notes / encounters tools, the sensitivity gating,
   or the verifier's citation grounding without populated data.
   Open Taskmaster task tracks options (Synthea, hand-crafted
   fixtures, hybrid) and the chosen seed pipeline.

## Older known issues (still open from earlier sessions)

- **Vitals height fixture mismatch.** `InternalVitalsControllerTest::
  returnsVitalsArrayOnHappyPath` expects `'height' => 70.0`, gets
  `'height' => 70`. Looks like a JSON-decoding round-trip issue
  where 70.0 deserializes as int. Pre-existing failure all session;
  the only remaining AgentForge isolated-test failure. Likely a
  small fixture-side fix.

- **`docs/demo-slides.html` still uncommitted.** Decide whether to
  commit or delete.

- **Old controller bootstraps use `DriverManager::getConnection(...)`
  directly.** CLAUDE.md prefers `DatabaseConnectionFactory`. Module-
  wide tech debt across all `public/internal/*.php` files. Sub-agent
  flagged this on Task 34; not Task-21 / 34 scope.

## What's ready to pick up next

Pending tasks remaining: 10. From `task-master list`:

- **27** — Planner Agent. The heaviest remaining sidecar work and the
  one that unlocks the deferred phase/turn budgets from Task 41 plus
  the synthesis cap from Task 45.
- **35, 36, 47** — Docker Compose + reverse proxy + end-to-end integration
  test. The deploy / infra triplet. Probably best done together when
  there's appetite for ops work.
- **42** — Identity Ambiguity (depends on 27 — Planner first).
- **43** — Prompt Library (depends on 27, 28).
- **45** — Synthesis Input Cap (depends on 26 — done).
- **46** — Stale Data + Conflict detection (depends on 28, 29).
- **48** — Deployment checklist doc (depends on 40, 44 — done; small).
- **49** — Re-evaluate `idx_procedure_*` index (low priority).

Quick wins that would close known issues without picking up a fresh
Taskmaster task:

- Fix the Vitals height-fixture (~5 min if it's a fixture issue).
- Backport `_KNOWN_TOOLS` to cover notes + search_notes (~10 min).
- Mint `session_id` in chat-panel.js so multi-turn memory actually
  fires (~30 min).
- Bake sensitivity YAML at a fixed path in the Dockerfile + override
  env (~30 min). Would land `policy_loaded: true` on the droplet.

## Quick-start checklist for next time

1. `git status` — confirm no uncommitted changes you forgot about.
2. `task-master list` and `task-master next` — see what's on deck.
3. `cd sidecar && uv run pytest` — confirm 417/417 still green.
4. If demo'ing again or testing locally:
   - `cd sidecar && ./scripts/sidecar.sh start` (sidecar on :8000)
   - `docker compose -f docker/development-easy/docker-compose.yml ps`
   - Open `http://localhost:8300/` (admin / pass)
5. If deploying to droplet:
   - `./scripts/deploy-droplet.sh check` — confirm droplet still
     healthy (currently running the prior-session's code)
   - **Heads up:** this session's 11 tasks are NOT yet on the droplet.
     A push will deploy the full 9-tool catalog, retry/timeout
     machinery, breakglass audit, eval framework (test-only), and
     the JWT backport.
6. Tail Langfuse traces (when configured) for live trace inspection;
   `LANGFUSE_HOST` is still unset on the droplet, so
   `NullLangfuseClient` is in use.

## Files worth opening in the first 60 seconds

- `docs/DEPLOYMENT.md` — droplet state of play (gained an
  "Optional: tighten REST api_log body logging" section this session).
- `docs/DEVIATIONS.md` — design decisions vs the original plan. New
  this session: 6 entries on 2026-05-01 / 2026-05-02 covering
  Task 41 (timeouts), Task 34 (breakglass dedup + auth-gateway
  placement), Tasks 37/38/39 (eval scope), Task 44 (api_log_option
  reframe), and Task 21 (FHIR vs custom endpoint).
- `ARCHITECTURE.md` — original plan, still authoritative for unbuilt
  sections (§3 Planner = Task 27, §6 Verification = mostly done,
  §7 Observability = mostly done, §8 Eval = framework done, real-
  LLM eval still open).
- `.taskmaster/tasks/tasks.json` — the roadmap.
- `sidecar/src/agentforge/timeouts.py` — new this session; worth a
  read before adding anything else to the orchestrator's failure
  handling.
- `sidecar/tests/eval/regression_locks.py` — the canonical examples
  that lock the eval framework's primitives. Add cases here
  (and bump the size-pin) when you have new product intent worth
  freezing.
