# Where we left off — 2026-05-02 (end of session, week1-gaps 5/22 + 1 hotfix)

Read me first when picking the project back up. Update or delete me
when the state captured here goes stale.

## Headline

**Major scope reset this session.** Closed all 51 task-master items
the previous session, then reviewed against the actual Week 1 rubric
and discovered gaps that the original roadmap didn't cover. Wrote a
new PRD (`.taskmaster/docs/week1-gaps-prd.md`), parsed it into 22
remediation tasks under the `week1-gaps` tag, expanded the heavy
ones into 44 subtasks, and started shipping.

**Current state: week1-gaps 5/22 done + 1 hotfix.** Phase 1 (visible
deliverables) is complete; baseline eval is in place AND has run
live against the dev stack; cost accounting is wired; a real
production bug (auth-gateway sentinel key mismatch) was discovered
by the eval and fixed. Integration pass (#4-#7) and streaming
refactor (#9-#13) are now unblocked but neither has been started —
both are multi-file refactors that warrant fresh context.

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

5 / 22 done. 44 subtasks expanded across the 7 heavy parents
(#4, #7, #11, #13, #14, #21, #22).

Hotfixes (no week1-gaps task ID — pre-existing bug surfaced by #21):
  * fix-w1-policy-sentinel-key-mismatch — auth_gateway used
    POLICY_SENTINEL_KEY="agentforge:policy:version" while the loader
    wrote POLICY_LOADED_KEY="agentforge:policy:loaded". Each side's
    tests mocked their own key; mismatch was invisible until #2
    wired both halves to the same Redis. Aliased to a single key.
```

Sidecar test suite: **583 → 598** default-tier (+15 from cost
accounting + observability). Plus **6 passed + 1 xfailed** on
``-m eval`` against the live local stack — the xfail is the
ADV-CROSS-PATIENT IdentityGuard probe, strict-xfailed until #7
wires the guard so a flip to passing reports as XPASS.

PHP isolated AgentForge: still **251/251** (no PHP work this push).

## Where the integration pass stands

**FOUR utilities still BUILT BUT NOT WIRED:**

  * SynthesisInputTruncator (Task 45) — token cap + drop/shrink
  * Planner (Task 27) — UseCase classification + structured Plan
  * DataQualityChecker (Task 46) — stale-lab + conflict flags
  * IdentityGuard (Task 42) — cross-patient reference detection

The integration pass is the headline next move. Sequence:

  4 Planner → 5 parallel dispatch → 6 truncator → 7 DataQuality +
  IdentityGuard → 8 latency budget enforcement.

Task #7 has a documented data-sourcing decision (option A/B/C —
recommended B: fetch demographics first, then run IdentityGuard).
See `task-master show 7` for details.

The baseline eval (#21) is the safety net during this refactor —
``uv run pytest -m eval`` should produce identical pass/fail per
case before and after the integration pass lands. Run it as the
regression check between subtasks. **Watch for ADV-CROSS-PATIENT
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

8. **Pre-hotfix eval baseline file** captured at commit
   `1a7bc1a5b` shows all 7 cases failing with 502s — that was the
   policy sentinel bug, not real eval failures. The post-hotfix
   true baseline is **6 passed + 1 xfailed**. Re-record at next
   session start (run ``uv run pytest -m eval -v -s | tee
   docs/eval-baseline-post-hotfix.txt``) so the diff line is
   accurate before #4 starts.

## Quick wins still available (within week1-gaps)

  * **Start integration pass at #4 (Planner)** — ~2-4 hour focused
    branch on orchestrator/__init__.py. Has 5 subtasks expanded.
    BLOCKED by no further deps after #2 + #21 landed.

  * **Start streaming refactor at #9 (LLM stream interface)** —
    pure sidecar change, ~2-3 hours. Independent of integration
    pass; sequence after if both happen one after another.

  * **Run baseline eval against current main** to lock in the
    pre-refactor pass/fail line. Requires sidecar up + an
    Anthropic API key. ~1-3 minute run.

## Quick-start checklist for next time

1. ``git status`` — confirm no uncommitted changes you forgot about.
2. ``task-master tags use week1-gaps && task-master list`` — confirm
   5/22 still reflects reality.
3. ``cd sidecar && uv run pytest`` — confirm 598/598 still green.
4. ``cd sidecar && uv run pytest -m eval`` — confirm baseline at
   **6 passed + 1 xfailed** (sidecar must be running locally; the
   xfail is ADV-CROSS-PATIENT until #7 ships).
5. ``task-master next`` should propose **#4 Planner integration**
   as the recommended next task.
5. If iterating locally:
   - **Host-script mode:** ``./sidecar/scripts/sidecar.sh start``
     (sidecar on :8000, ``--reload``)
   - **Docker-stack mode:** ``cd docker/agent && docker compose up
     --build`` (port 8400 with companion agentforge-redis)
   - ``docker compose -f docker/development-easy/docker-compose.yml ps``
   - Open ``http://localhost:8300/`` (admin / pass)
6. If running the slow / latency / eval suites:
   - ``cd sidecar && uv run pytest -m slow tests/integration/``
     — full LLM UC flows (~100s)
   - ``cd sidecar && uv run pytest -m latency tests/integration/``
     — latency-budget tests
   - ``cd sidecar && uv run pytest -m eval`` — baseline eval
     (NEW, 7 cases, ~1-3 min, requires Anthropic key)
   - All three deselected from default ``uv run pytest``.
7. If deploying:
   - ``./scripts/deploy-droplet.sh check`` — confirm droplet healthy
   - ``./scripts/deploy-droplet.sh all`` — full module + sidecar
     deploy. End-of-session droplet was at `b96690e68`, ahead by
     several commits now (recommend redeploy at next start).

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

## How this session ended

```
5 / 22 week1-gaps tasks closed + 1 hotfix
598 default sidecar tests + 4 slow + 2 latency + 7 eval (6 pass / 1 xfail) — all opt-in
251 PHP isolated AgentForge tests (untouched this push)
~14 commits, 7 merges to main, 1 subagent attempt (failed cleanly)
1 real production safety finding (IdentityGuard regression),
  encoded as strict-xfail in the eval until #7 ships
```

Next session is aimed at the integration pass: wire Planner +
parallel dispatch + truncator + data-quality + identity-guard
into Orchestrator.turn. Use the baseline eval (#21) as the
regression check between subtasks. **The single most informative
signal the next session can produce is ADV-CROSS-PATIENT flipping
XFAIL → XPASS — that's the moment IdentityGuard is wired
end-to-end.**

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
