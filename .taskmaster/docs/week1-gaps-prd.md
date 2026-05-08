<context>
# Overview

This is a remediation PRD for the AgentForge Clinical Co-Pilot — an
OpenEMR fork with a Python sidecar that adds a chart-aware clinical
agent. The core system is built and demoable. This document captures
the gap between what shipped (51 closed tasks, 582 sidecar tests,
deployed droplet) and what the Week 1 rubric actually requires.

The gap was discovered after a third-party review compared the deployed
state against the architecture specification and project rubric. The
review found that "task-master 51/51 done" was treated as a success
signal, but the task list was incomplete relative to the rubric — and
several tasks were marked closed at "built and merged" rather than at
"wired into a user-visible turn."

This PRD enumerates the gaps as shippable units. The current branch is
main; the deployed droplet is at <droplet>:9300. All work below
should be branch-per-task off main, merged after review.

# Core Features

The features below are NOT new agent capabilities — they are the
delivery gaps that prevent existing capabilities from being visible,
enforceable, or measurable. Each one closes a specific rubric item.

## Multi-turn memory wired through the browser
- What: chat-panel.js mints a session_id per conversation and includes
  it in every POST to /agentforge/turn. AgentProxyController forwards
  it to the sidecar (already accepted by sidecar — see
  Orchestrator.turn signature). User-visible behavior: a follow-up
  question references prior turns in the same conversation.
- Why: server-side session memory has been wired since Task 38 but
  the frontend posts only `{message: ...}`, so every turn is
  independent in practice. The rubric expects multi-turn.
- How: generate UUID on chat-panel mount, store in component state,
  send with each turn. Reset on "new conversation" UI affordance
  (which may need to be added).

## True streaming end-to-end
- What: sidecar streams tokens from Anthropic (anthropic SDK has
  `messages.stream`), controller forwards chunked or SSE to the
  browser, chat-panel.js renders incrementally. Verifier runs over
  the streamed text — either in a finalize step after stream close,
  or via incremental constraint checks if feasible.
- Why: ARCHITECTURE.md and the rubric both expect token-by-token
  delivery to the browser. Today: sidecar calls `complete()` (one
  blocking call), controller reads the full body, browser receives
  one chunk. The system is "non-streaming" despite calling itself
  a streaming agent.
- How: replace the `LLMClient.complete` boundary with a streaming
  interface (`stream() -> AsyncIterator[str]`). Sidecar `/turn`
  becomes a streaming response (StreamingResponse with
  text/event-stream or chunked). turn.php proxies through. JS
  consumes via fetch + ReadableStream + TextDecoder.

## Integration pass: Planner + utilities wired into Orchestrator
- What: orchestrator/__init__.py routes every turn through:
  Planner (classify UseCase, build Plan with parallel tool list)
  -> parallel tool dispatch (asyncio.gather, not sequential loop)
  -> SynthesisInputTruncator (token-cap before LLM call)
  -> DataQualityChecker (flag stale labs / conflicting sources)
  -> StreamingVerifier
  -> IdentityGuard (post-synthesis check for cross-patient leakage).
- Why: all four utilities are merged with green tests but inert.
  Tasks 27, 41, 42, 45, 46 each closed at "built" rather than at
  "wired into a turn." The latency win (parallel dispatch) and the
  safety wins (truncation, identity guard, data quality) only
  exist when these components run on every turn.
- How: one focused branch on orchestrator/__init__.py. Plan +
  truncate + dispatch parallel + synth + verify + identity-guard,
  in that order. Replace the sequential loop. Add tests that
  mock each stage and assert the orchestrator calls them.

## Latency budget enforced by default
- What: default UC-1 p95 budget drops from 30000ms to 7000ms once
  the integration pass lands (parallel dispatch should take ~10-15s
  off complex charts). Controller proxy timeout in turn.php drops
  from 30s/90s back to 10s/15s. AGENTFORGE_INT_UC1_P95_BUDGET_MS
  env override remains for laptops that legitimately can't hit it.
- Why: today the test passes on samples that take 12-25s because
  the default budget is lenient. The rubric wants 7s p95 actually
  met, not "won't fail the test."
- How: ordering — finish integration pass FIRST so parallel
  dispatch is in place, THEN measure, THEN tighten budgets.
  This task is "after #3 measure and tighten."

## Verifier on by default in deployed config
- What: VERIFIER_ENABLED=true in the droplet's sidecar .env. Local
  development can leave it off if iterating. Smoke test confirms
  the verifier actually gates output on the droplet.
- Why: rubric expects grounding enforcement to be on in production.
  Today VERIFIER_ENABLED=false on the droplet because we wanted
  fast demos.
- How: edit droplet .env via deploy-droplet.sh, redeploy, smoke
  test by asking a question that should be ungrounded and confirm
  the verifier rewrites or refuses.

## Real evaluation suite that runs the agent
- What: replace tests/eval/regression_locks.py + harness.py with
  an eval runner that:
  (a) seeds a known-state demo DB (or pins a SHA of the existing one)
  (b) invokes the real Orchestrator.turn for each eval case
  (c) scores against expected behavior with two graders:
      - deterministic: citation grounding, presence of required
        clinical terms, absence of forbidden ones (cross-patient
        leakage, hallucinated record IDs)
      - LLM-as-judge: a separate Anthropic call grading clinical
        correctness, completeness, hedge appropriateness
  (d) covers FIVE failure modes per rubric:
      1. happy path (UC-1..UC-4 over Eula and a healthy patient)
      2. missing data (patient has no labs, no allergies)
      3. ambiguous query ("what about her heart?" with no cardiac
         tools available)
      4. unauthorized access (pid mismatch, breakglass denied)
      5. clinical hallucination probe (drug not in chart, fake
         lab value, fabricated note)
  (e) emits a markdown report with pass/fail + scores per case,
      committed to docs/eval-report-YYYY-MM-DD.md
- Why: today's "eval" is fixture-asserts that don't invoke the
  agent. The rubric expects real evaluation with reportable
  results.
- How: new sidecar/tests/eval/runner.py + cases/ directory.
  Reuse pytest-asyncio for ergonomics but mark explicitly as
  `eval` (separate from `slow`/`latency`).

## Cost accounting in traces
- What: every LLM call records (input_tokens, output_tokens,
  model, dollar_cost). Per-turn aggregate (sum across all calls
  in a turn) surfaces in Langfuse trace metadata AND in a
  /agentforge/turn response header (X-Agent-Cost-USD) so the
  PHP module can log it. CLI tool: `uv run python -m
  agentforge.observability.cost_report` reads recent traces
  and prints daily/weekly cost summaries.
- Why: ARCHITECTURE.md lists $/turn as a required observability
  signal. Today we track tokens but not dollars; "cost per turn"
  is not derivable from the trace.
- How: pricing table in agentforge.observability.pricing
  (claude-3-5-sonnet, claude-3-5-haiku, etc.). LLMClient wraps
  every call to record metadata. Aggregate at end-of-turn in
  the orchestrator. Langfuse generation events get the cost
  field. Response header set in main.py.

## redis_client wired from REDIS_URL in production
- What: create_app() in main.py reads REDIS_URL from settings,
  constructs an AgentRedisClient, passes it to the lifespan
  context. policy_loaded=true on droplet startup.
- Why: today the sensitivity policy YAML is baked into the image
  but the loader gates on `redis_client is not None`, and the
  production app factory doesn't construct one. The result:
  policy_loaded=false despite the YAML being present, which
  means record visibility checks fall through to fail-closed
  defaults — safer than silent disable, but not the configured
  policy.
- How: ~30-60 minutes. Touches the AgentRedisClient vs
  _AppRedisProto split. Tests already exist for the wiring
  contract; just make production match.

## Project README at repo root
- What: replace the upstream OpenEMR README at /README.md with
  a project submission README:
  - one-paragraph what-is-this
  - deployed demo link (<droplet>:9300)
  - 60-second quickstart (clone, docker compose up, login)
  - architecture summary (5-line)
  - links to AUDIT.md, USERS.md, ARCHITECTURE.md, DEPLOY.md
  - upstream OpenEMR origin acknowledgment
- Why: a reviewer landing on the repo today sees upstream
  OpenEMR's README and can't tell what this fork does, where
  it's deployed, or how to evaluate it.
- How: archive the original README to README.upstream.md, write
  a new README.md scoped to this project. Don't delete history.

# User Experience

## Reviewer journey
- Lands on github/repo URL.
- Reads new README.md, sees deployed-demo link.
- Visits <droplet>:9300, logs in as admin/pass.
- Opens any patient (Eula, pid=8, is the recommended demo).
- AgentForge sidebar is present and functional.
- Asks "Give me a chart overview" — sees streaming response with
  citations.
- Asks a follow-up — agent uses prior turn's context.
- Reads AUDIT.md, USERS.md, ARCHITECTURE.md, DEPLOY.md for depth.
- Reads the most recent docs/eval-report-*.md for "does it work
  on hard cases."

## Operator journey (us)
- Local dev unchanged: `cd docker/development-easy && docker
  compose up`, `./sidecar/scripts/sidecar.sh start`, code, test.
- New: `uv run python -m agentforge.observability.cost_report`
  shows yesterday's $ spend.
- New: `uv run pytest -m eval` runs the full eval suite against
  the local stack and writes a report.

</context>
<PRD>
# Technical Architecture

## Affected components
- sidecar/src/agentforge/orchestrator/__init__.py — integration
  pass (Planner, parallel dispatch, truncator, identity guard
  wiring)
- sidecar/src/agentforge/llm/client.py — streaming interface
- sidecar/src/agentforge/main.py — redis_client wiring,
  X-Agent-Cost-USD response header
- sidecar/src/agentforge/observability/cost.py (NEW) — pricing
  table + per-turn aggregator
- sidecar/src/agentforge/observability/cost_report.py (NEW) —
  CLI tool
- sidecar/src/agentforge/observability/langfuse_client.py — emit
  cost field on generation events
- sidecar/tests/eval/runner.py (NEW) — agent-running eval runner
- sidecar/tests/eval/cases/ (NEW) — per-failure-mode case files
- interface/modules/.../public/turn.php — chunked/SSE proxy
  passthrough; lower timeout
- interface/modules/.../public/js/agent_panel.js — session_id
  mint, ReadableStream consumption
- README.md (REPLACE) — project README
- README.upstream.md (NEW) — archive original

## Data flow

Today (sequential):
  user -> turn.php -> sidecar -> [Tool0; Tool1; Tool2; ...]
                                  one at a time
                              -> LLM.complete (blocking)
                              -> verifier
                              -> response
                              -> browser sees full text

Target (parallel + streaming):
  user -> turn.php -> sidecar -> Planner.classify
                              -> asyncio.gather([Tool0, Tool1, ...])
                              -> Truncator.fit
                              -> LLM.stream
                                  (yields chunks)
                                  - DataQualityChecker on tool out
                                  - IdentityGuard on completed text
                                  - Verifier on completed text
                              -> chunks proxied via SSE/chunked
                                  through turn.php
                              -> browser renders progressively
  cost: aggregated across LLM.stream calls per turn,
        emitted on response header + Langfuse trace metadata.

## Eval architecture

```
sidecar/tests/eval/
  runner.py              # invoke Orchestrator.turn, score, report
  cases/
    happy_path.yaml      # UC-1..UC-4 over Eula + healthy patient
    missing_data.yaml    # no labs, no allergies, no notes
    ambiguous.yaml       # "what about her heart?"
    unauthorized.yaml    # pid mismatch, breakglass denied
    hallucination.yaml   # probe for fake records
  graders/
    deterministic.py     # citation grounding, term presence
    llm_judge.py         # Anthropic call -> 1-5 score + rationale
docs/
  eval-report-YYYY-MM-DD.md   # committed report per run
```

## Configuration changes

- droplet sidecar/.env: VERIFIER_ENABLED=true (was false)
- droplet sidecar/.env: REDIS_URL=redis://agentforge-redis:6379/0
  (already set, but we'll confirm wiring)
- module .env: AGENTFORGE_PROXY_TIMEOUT_S=10 (was 30; lower
  after integration pass)

# Development Roadmap

## Phase 1 — visible deliverables (block reviewer "what is this")
1. Project README at repo root.
2. redis_client wired from REDIS_URL (closes policy_loaded=false).
3. session_id minted in agent_panel.js (lights up multi-turn).

## Phase 2 — integration pass (block "agent is sequential")
4. Planner wired into Orchestrator.turn (classification + plan).
5. Parallel tool dispatch via asyncio.gather replacing sequential
   loop.
6. SynthesisInputTruncator wired before LLM call.
7. DataQualityChecker wired on tool outputs.
8. IdentityGuard wired on synthesis output.
9. Drop UC-1 default p95 budget to 7000ms; lower controller proxy
   timeout to 10/15s.

## Phase 3 — true streaming (block "claims streaming, isn't")
10. LLMClient.stream() interface; replace complete() in
    orchestrator.
11. Sidecar /turn returns StreamingResponse.
12. turn.php proxies chunked/SSE through to client.
13. agent_panel.js consumes ReadableStream and renders
    incrementally.
14. Verifier runs in finalize step (or as incremental constraints
    if feasible).

## Phase 4 — observability (block "no cost, no real eval")
15. Pricing table + per-call cost recording in LLMClient.
16. Per-turn cost aggregator; X-Agent-Cost-USD response header.
17. Langfuse generation events include cost metadata.
18. cost_report CLI.
19. Eval runner that invokes Orchestrator.turn end-to-end.
20. Five failure-mode case files (happy, missing, ambiguous,
    unauthorized, hallucination).
21. Deterministic grader (citation grounding + term presence).
22. LLM-as-judge grader.
23. Eval report writer; commit first report under
    docs/eval-report-2026-05-XX.md.

## Phase 5 — production hardening
24. Verifier on by default in droplet .env; smoke-test refusal of
    ungrounded answers.
25. Per-attempt timeout wired through httpx in retry policy
    (existing carryforward).

# Logical Dependency Chain

- README and redis_client wiring are unblockers for everything
  else and have no dependencies. Do them first.
- session_id is independent of the integration pass; it can ship
  in parallel with anything else.
- Integration pass (Phase 2) must land before latency budget can
  be tightened (Phase 2 task #9). Without parallel dispatch, the
  7s budget can't be met.
- Streaming (Phase 3) is independent of the integration pass but
  touches the same orchestrator file — sequence them so two
  branches don't conflict. Recommended order: integration pass
  first, then streaming, since streaming changes the
  Orchestrator.turn return shape.
- Cost accounting (Phase 4 #15-18) depends on streaming because
  cost recording must aggregate across stream chunks. Do
  streaming first.
- Eval runner (Phase 4 #19-23) depends on the integration pass
  having stabilized — running eval against an unstable
  orchestrator wastes signal. Do it after Phase 2.
- Verifier-on-by-default (Phase 5 #24) only makes sense after
  the eval runner has confirmed the verifier doesn't refuse too
  aggressively. Do it last.

# Risks and Mitigations

## Risk: integration pass regresses live behavior
- Mitigation: smoke-test against the droplet seeded cohort
  (Eula, pid=8) after each utility is wired. Keep the change
  branch-per-utility within the integration-pass branch so we
  can bisect if a regression shows up.

## Risk: streaming pipe drops the verifier
- Mitigation: explicit finalize step on stream close. Verifier
  runs over the full assembled text. If verifier rejects, send
  a final "rewriting for accuracy" SSE event and stream the
  rewritten response. Document the latency cost honestly in
  ARCHITECTURE.md.

## Risk: LLM-as-judge graders introduce nondeterminism
- Mitigation: pin model + temperature=0; capture rationale; allow
  3-of-3 consensus across re-runs before scoring a case as
  failing.

## Risk: latency budget too tight after parallel dispatch but
  before context caching
- Mitigation: keep the env-override knob. Production target is
  7s p95; if dev-laptop is 10s and we want CI to be loose,
  AGENTFORGE_INT_UC1_P95_BUDGET_MS=15000 in CI is acceptable.
  The droplet must hit 7s.

## Risk: README rewrite drops upstream attribution
- Mitigation: README.upstream.md preserves original; new README
  has explicit "this is a fork of OpenEMR" sentence with link.

# Appendix

## Origin of this PRD

The gap analysis underlying this PRD came from a third-party
review (GPT-4) that compared the deployed AgentForge state
against ARCHITECTURE.md and the project rubric. The review
identified:
- multi-turn UX claimed but not active
- streaming claimed but not implemented
- planner/parallel dispatch designed but not wired
- 7s p95 latency target not enforced by default
- evaluation harness exists but doesn't run the agent
- cost in observability missing despite spec listing it
- README still upstream

These were all real findings. This PRD treats each as a
shippable unit and re-grounds the work in the rubric rather
than in the original task-master roadmap (which closed at 51/51
without covering several rubric items).

## Out of scope

The following carryforwards from the prior session are NOT in
this PRD:
- Multi-replica breakglass dedup (Redis SETNX) — single-replica
  today; not a Week 1 blocker.
- per_attempt_timeout through httpx — keep open as a follow-up;
  not visible in any rubric item.
- Apache reverse-proxy auto-install — operational nicety; the
  manual install step is documented.
- MedicationsRepository deduplication — same shape as the
  procedures fix, low priority.
- Search results gating on title-prefix only — upstream OpenEMR
  surface limitation; documented.

These remain in docs/NEXT-SESSION.md as backlog. They should not
be parsed into Week 1 remediation tasks.
</PRD>
