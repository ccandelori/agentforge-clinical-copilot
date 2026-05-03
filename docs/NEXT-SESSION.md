# Where we left off — 2026-05-02 (mid-session, week1-gaps 7/22 — Planner + parallel dispatch shipped)

Read me first when picking the project back up. Update or delete me
when the state captured here goes stale.

## Headline

**Major scope reset this session.** Closed all 51 task-master items
the previous session, then reviewed against the actual Week 1 rubric
and discovered gaps that the original roadmap didn't cover. Wrote a
new PRD (`.taskmaster/docs/week1-gaps-prd.md`), parsed it into 22
remediation tasks under the `week1-gaps` tag, expanded the heavy
ones into 44 subtasks, and started shipping.

**Current state: week1-gaps 7/22 done + 1 hotfix.** Phase 1 (visible
deliverables) complete. Phase 2 integration pass started: Planner
(#4) and parallel dispatch (#5) BOTH shipped this session and are
exercising on every /turn locally. Eval baseline holds at 6 pass +
1 xfail across both refactors; the ADV-CROSS-PATIENT XFAIL is the
deliberate signal that #7 (IdentityGuard) hasn't shipped yet.

**Eval runtime moved from 98s (pre-Planner) → 139s (Planner default-on)
→ 122s (parallel dispatch).** The +41s from Planner is the cost-gap
carryforward — every turn now does +1 LLM call for classification.
The -17s from parallel dispatch is the headline: UC-1 / UC-3 chart
overviews where the LLM emits 5-8 tool_calls in one shot now run
max(per-tool-latency) instead of sum.

Integration pass continues: #6 (truncator) → #7 (data-quality +
identity guard) → #8 (latency budget). Streaming refactor
(#9-#13) still untouched — touches the same orchestrator file
heavily so sequence after the integration pass.

**The eval suite earned its keep on first live run.** It surfaced
two things 51/51 master + 598 unit tests didn't: a pre-existing
auth-gateway 503 that hit every /turn the moment redis_client got
wired (the policy sentinel was looking for a key the loader never
wrote), and the agent's IdentityGuard regression — when asked
about a different patient, the agent attributes the bound chart's
records to the wrong name. The first is fixed; the second is
encoded as a strict-xfail until Task #7 ships.

## Why we're on a new tag

The previous session closed 51/51 master tasks but a third-party
review (GPT-4 reading against ARCHITECTURE.md and the rubric)
flagged real gaps:

  * Multi-turn UX claimed but session_id not minted in browser.
  * Streaming claimed but verifier runs on completed text blob.
  * Planner / parallel dispatch / truncator / data-quality / identity
    guard all built but not wired into Orchestrator.turn.
  * 7s p95 not enforced (default test budget 30s).
  * Eval framework existed but didn't invoke the agent.
  * Cost in observability missing despite spec.
  * README still upstream OpenEMR.

Mistake to learn from: closing the task-master roadmap is not the
same as meeting the rubric. Task-master scope ≠ rubric scope. Don't
treat "all green" as "all done" without comparing against the spec
the work is graded against.

The week1-gaps tag is the remediation roadmap. Switch via
``task-master tags use week1-gaps``.

## What shipped this session

```
week1-gaps tasks closed (in merge order, all on main):
  1   Project README replacement              (visible)
  2   Wire redis_client from REDIS_URL        (closes policy_loaded=false)
  3   Multi-turn session_id in chat panel     (closes carryforward #2)
  21  Baseline eval runner                    (safety net for #4-#13)
  14  Cost accounting in LLM calls + traces   (X-Agent-Cost-USD header)
  4   Planner wired into Orchestrator.turn    (5 subtasks)
  5   Parallel tool dispatch via gather       (4 subtasks)

7 / 22 done.

Hotfixes (no week1-gaps task ID — pre-existing bug surfaced by #21):
  * fix-w1-policy-sentinel-key-mismatch — auth_gateway used
    POLICY_SENTINEL_KEY="agentforge:policy:version" while the loader
    wrote POLICY_LOADED_KEY="agentforge:policy:loaded". Each side's
    tests mocked their own key; mismatch was invisible until #2
    wired both halves to the same Redis. Aliased to a single key.
```

Sidecar test suite: **598 → 617** default-tier (+19 from Planner
wiring + parallel dispatch + observability spans). Plus **6 passed
+ 1 xfailed** on ``-m eval`` against the live local stack — the
xfail is the ADV-CROSS-PATIENT IdentityGuard probe, strict-xfailed
until #7 wires the guard so a flip to passing reports as XPASS.

PHP isolated AgentForge: still **251/251** (no PHP work this push).

## Where the integration pass stands

**TWO utilities still BUILT BUT NOT WIRED:**

  * SynthesisInputTruncator (Task 45) — token cap + drop/shrink → #6
  * DataQualityChecker (Task 46) + IdentityGuard (Task 42) → #7

(Planner #27 wired in #4; parallel dispatch wired in #5.)

Remaining sequence:

  6 truncator → 7 DataQuality + IdentityGuard → 8 latency budget
  enforcement.

Task #7 has a documented data-sourcing decision (option A/B/C —
recommended B: fetch demographics first, then run IdentityGuard).
See `task-master show 7` for details.

The baseline eval (#21) is the safety net during this refactor —
``uv run pytest -m eval`` produced identical 6 pass + 1 xfail
across BOTH #4 (Planner) AND #5 (parallel dispatch). Continue
running it between subtasks. **Watch for ADV-CROSS-PATIENT
flipping XFAIL → XPASS when #7 wires IdentityGuard — that's the
deliberate signal that the guard is working end-to-end.**

## Where streaming stands

Streaming refactor is independent of the integration pass; both
touch orchestrator/__init__.py heavily, so sequence them rather
than parallelize. Recommended: integration pass first because
it changes Orchestrator.turn shape less than streaming does.

Sequence: 9 LLM stream interface → 10 sidecar StreamingResponse →
11 controller+turn.php SSE proxy → 12 JS reader → 13 verify-BEFORE-
emit.

**Important design decision (already documented in #13):** stream
through the existing sentence-buffered StreamingVerifier and emit
ONLY verified sentences. Do NOT stream unverified clinical text and
"rewrite" it after — briefly exposing unsafe content is a clinical
safety violation regardless of how fast the rewrite arrives.

## What's deployed and where

`https://143.244.157.90:9300/` — production demo. Droplet was current
at end of previous session (commit `b96690e68`). NOT redeployed this
session — none of #1, #2, #3, #14, or #21 strictly needs a redeploy
to be visible:

  * #1 README  — repo-only, not on deployed instance.
  * #2 redis_client — sidecar code change; needs redeploy to take
    effect on droplet (would close `policy_loaded=false` there).
  * #3 session_id — module JS change; needs redeploy to take effect
    on droplet.
  * #14 cost accounting — sidecar; needs redeploy.
  * #21 baseline eval — test code only, no production change.
  * **Policy sentinel hotfix — sidecar; MUST be in any redeploy
    or every droplet /turn 503's. This is now coupled with the #2
    fix: redeploying #2 without the hotfix takes the droplet down.**

**Recommended:** redeploy at next session start so the droplet
reflects #2, #3, #14, AND the hotfix together. Run baseline eval
(#21) against droplet first to confirm pre-redeploy behavior, then
redeploy, then run again to confirm cost header appears AND the
IdentityGuard xfail still fires on the droplet (proves #7 is still
the open question, not infrastructure drift).

## Live carryforwards (still deferred — no taskmaster ID)

These are NOT in week1-gaps — flagged as out-of-scope in the PRD
and remain backlog. Don't accidentally pull these in mid-session.

1. **In-memory breakglass dedup → multi-replica gap.** Replace with
   Redis SETNX when going multi-replica.

2. **`per_attempt_timeout` not wired through httpx.** RetryPolicy
   has the field; each fetcher still uses httpx's default 5s.

3. **Apache reverse-proxy include shipped but not auto-installed.**
   Operators must `docker cp` it into Apache's conf.d/ manually.

4. **Latency-test --latency-report flag not implemented** (Task
   47.5 stretch).

5. **MedicationsRepository may have similar duplication issues** to
   what we fixed in problems / procedures (carryforward from prior
   session — Eula's response showed multiple discontinued
   contraceptive entries).

6. **`sidecar/check_loader.py` left in subagent worktree** from
   prior session. Throwaway diagnostic; safe to delete by hand.

7. **Search results gate on title-prefix only** because
   `notes_search` PHP response doesn't surface `note_type` or
   `attending_only`.

8. **Planner LLM cost not routed through `_record_llm_call`.** The
   Planner uses its own LLMClient and doesn't surface token counts,
   so the per-turn ``_TURN_COST_VAR`` ContextVar undercounts every
   turn by the planner's contribution (~$0.005 with claude-sonnet-4-5).
   The X-Agent-Cost-USD header therefore misses planner cost. Address
   before #20 enables the verifier by default — the cost dashboard
   needs to be trustworthy when verifier is on. Cleanest fix:
   extend ``Planner.plan()`` to return a `(Plan, LLMResponse)` tuple
   and route the response through the existing `_record_llm_call`
   path in Orchestrator. Touches planner.py + orchestrator.

9. **Eval is flaky-ish on cold sidecar reload.** Watching `--reload`
   wired sidecars while eval runs leaves ~3-7s windows where /turn
   returns 422/503. Skips are handled (the suite skips 503s) but
   422s look like failures. Run eval AFTER sidecar settles, or use
   the Docker-stack mode for eval-on-CI.

10. **`.env` is fragile.** This session ran into a state where the
    sidecar.sh restart failed with "redis_url Field required" —
    pid claimed alive but app never bound port 8000. Bypassed by
    starting with `REDIS_URL=redis://localhost:6379/0
    ./scripts/sidecar.sh start`. Suggests something stripped or
    corrupted .env entries; verify `.env` has REDIS_URL,
    JWT_SECRET, HMAC_KEY, ANTHROPIC_API_KEY, LANGFUSE_* before
    a clean restart. Original sidecar pid that worked at session
    start was launched with parent-shell env vars set, not from
    .env exclusively.

## Quick wins still available (within week1-gaps)

  * **Continue integration pass at #6 (truncator)** — same shape
    as #4: wire an existing utility (SynthesisInputTruncator) into
    Orchestrator.turn after tool results are collected, before the
    final synthesis call. Should be ~3-5 commits like #4 was.

  * **Start streaming refactor at #9 (LLM stream interface)** —
    pure sidecar change, ~2-3 hours. Independent of integration
    pass; sequence after #6/#7/#8 land because both branches
    touch orchestrator/__init__.py heavily.

  * **#15 (cost_report CLI tool)** — INDEPENDENT of integration
    pass. Reads cost spans from Langfuse and prints a per-turn
    breakdown. Could be parallelized via subagent if you push
    main to origin first; otherwise run foreground.

## Quick-start checklist for next time

1. ``git status`` — confirm no uncommitted changes you forgot about.
2. ``task-master tags use week1-gaps && task-master list`` — confirm
   7/22 still reflects reality.
3. ``cd sidecar && uv run pytest`` — confirm 617/617 still green.
4. **Verify sidecar can boot.** Before running the eval, confirm
   the sidecar is alive on :8000 — `tail -3 sidecar/var/sidecar.log`
   should end with "Application startup complete." If it shows a
   pydantic validation error about `redis_url`, restart with an
   explicit env var: `REDIS_URL=redis://localhost:6379/0
   ./sidecar/scripts/sidecar.sh restart` (or fix `.env` if it's
   missing keys). See carryforward #10.
5. ``cd sidecar && uv run pytest -m eval`` — confirm baseline at
   **6 passed + 1 xfailed** (the xfail is ADV-CROSS-PATIENT until
   #7 ships).
6. ``task-master next`` should propose **#6 (truncator
   integration)** as the recommended next task.
7. If iterating locally:
   - **Host-script mode:** ``./sidecar/scripts/sidecar.sh start``
     (sidecar on :8000, ``--reload``)
   - **Docker-stack mode:** ``cd docker/agent && docker compose up
     --build`` (port 8400 with companion agentforge-redis)
   - ``docker compose -f docker/development-easy/docker-compose.yml ps``
   - Open ``http://localhost:8300/`` (admin / pass)
8. If running the slow / latency / eval suites:
   - ``cd sidecar && uv run pytest -m slow tests/integration/``
     — full LLM UC flows (~100s)
   - ``cd sidecar && uv run pytest -m latency tests/integration/``
     — latency-budget tests
   - ``cd sidecar && uv run pytest -m eval`` — baseline eval
     (7 cases, ~2 min with Planner default-on, requires Anthropic
     key)
   - All three deselected from default ``uv run pytest``.
9. If deploying:
   - ``./scripts/deploy-droplet.sh check`` — confirm droplet healthy
   - ``./scripts/deploy-droplet.sh all`` — full module + sidecar
     deploy. Droplet has NOT been redeployed since 2026-05-02
     prior-session end (commit `b96690e68`); is now significantly
     behind. Recommend a single coherent redeploy after the
     integration pass lands (probably after #8) so the droplet
     reflects everything-or-nothing rather than a partial state.

## Files worth opening in the first 60 seconds

  * `.taskmaster/docs/week1-gaps-prd.md` — the remediation PRD
  * `task-master tags use week1-gaps` then `task-master list` —
    full task graph
  * `task-master show 4` — Planner integration, 5 subtasks
  * `task-master show 7` — DataQuality + IdentityGuard
    (option A/B/C decision in there)
  * `task-master show 13` — verify-BEFORE-emit design (corrected
    from finalize-after-stream)
  * `sidecar/tests/eval/baseline/` — the new baseline eval suite
  * `DEPLOY.md`, `docs/DEPLOYMENT.md` — deployment plan
  * `ARCHITECTURE.md` — six load-bearing decisions

## Subagent worktree gotcha (learned this session)

When using `Agent` with `isolation: worktree`, the worktree base may
not be local main — in this session the worktree branched from a
~140-commit-old `origin/main`. If launching subagents, either push
local main to origin first or pass an explicit base. Otherwise the
subagent reports "files don't exist" because the project's
foundational scaffolding is on commits the worktree never sees.

## How this session is going (mid-session checkpoint)

```
7 / 22 week1-gaps tasks closed + 1 hotfix (no new hotfixes this slice)
617 default sidecar tests + 4 slow + 2 latency + 7 eval (6 pass / 1 xfail) — all opt-in
251 PHP isolated AgentForge tests (untouched this slice)
~24 commits across this slice, 4 merges to main
9 commits between #4 (Planner) start and #5 (parallel dispatch) merge
Eval runtime: 98s → 139s (Planner default-on) → 122s (parallel
  dispatch) — net +24s for richer functionality with parallel
  reclaim
```

Continuing the integration pass: #6 truncator → #7 DataQuality +
IdentityGuard → #8 latency budget. **The single most informative
signal the next session can produce is ADV-CROSS-PATIENT flipping
XFAIL → XPASS — that's the moment IdentityGuard is wired
end-to-end and the eval suite earns its keep a third time.**

## Lessons surfaced this session

* **Closing all the tasks ≠ meeting the rubric.** Master closed at
  51/51, but eight rubric items weren't in the task list.
  Task-master scope is what we tracked, not what we were graded on.

* **An eval that runs the agent end-to-end pays for itself on
  first run.** The ContextVar wiring + tuned cases caught both a
  pre-existing infrastructure bug AND a real clinical safety
  regression. Unit tests had been green throughout.

* **"Untracked" is not a value judgment.** Don't recommend
  deleting `.claude/`, `.learning/`, `.clj-kondo/`, or other
  tool-state dirs even at session-end cleanup; only flag files I
  created and know to be throwaway. Captured in the user-memory
  store as `feedback_untracked_is_not_junk.md`.

* **Branch off main early.** The `task-w1-4-planner-integration`
  branch was created mid-prior-session BEFORE the policy hotfix
  landed; checking it out put the workspace at the pre-hotfix
  state. The eval caught it (wall-to-wall 503s once #4 wired
  Redis + Planner). Fix was a clean merge of main into the
  branch. Lesson: if a branch is more than a session old, merge
  main before the first commit on it.

* **`uvicorn --reload` + heavy file edits = sidecar reload churn.**
  Multiple WatchFiles restarts during eval runs created 422/503
  windows that look like real failures but aren't. If the eval
  shows mixed skips + failures, restart sidecar cleanly first
  and re-run before chasing the symptom.

* **`.env` integrity is a real failure mode.** The session ran
  into a state where the disk `.env` was either missing fields
  or pydantic-settings couldn't parse it; surfaced as
  "redis_url Field required" on every restart. Bypassed via
  `REDIS_URL=... ./scripts/sidecar.sh restart`. Carry forward
  #10 covers the fix.
