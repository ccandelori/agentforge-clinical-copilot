# Where we left off — 2026-05-06 late night (drawer + auth bridge live)

Read me first when picking the project back up. Update or delete me
when the state captured here goes stale.

## Headline

**T38.10 is functionally done — the AgentForge drawer talks to the
orchestrator end-to-end.** The harder half wasn't the drawer; it was
the auth bridge between the dashboard's cookie session and the
agent's JWT trust boundary. That's now landed and documented in
**`docs/adr/0001-dashboard-auth-bridging.md`** — read it before
touching the BFF or the AuthGateway.

Branch `feat/dashboard-port` is 22 commits ahead of origin, **no MR
yet**. Tests across all three stacks green: 214 vitest · 1083 sidecar
pytest · 352 AgentForge PHP isolated.

| MR | Branch | Status |
|---|---|---|
| !35 | `feat/w2-mr7-cutover-wiring` | merged 2026-05-06 |
| !36 | `feat/w2-task-24-citation-overlay` | merged 2026-05-06 |
| (open) | `feat/dashboard-port` | 22 commits, no MR yet |

`task-master next` — **T38.13 (defense doc)** is parallel-friendly;
**T38.11 (citation overlay re-port)** is the next critical-path
piece.

## What shipped this session (Wed evening + late night 2026-05-06)

Eight commits on top of the chart-row sweep:

```
04e6c2611  fix(agentforge): UNHEX hyphenated UUIDs at the SQL boundary
b3696d421  feat(agentforge): patient UUID → pid resolver completes the bridge
20ceda157  feat(dashboard): wire AgentDrawer to /api/agent/turn
9214bc6ea  feat(sidecar): dashboard auth bridge + /api/agent/turn route
2a387e0f7  feat(agentforge): /me identity-bootstrap endpoint + ADR-0001
e78924772  fix(dashboard): use v-if for drawer aside (T38.10)
13eb7f5a4  feat(dashboard): AgentForge drawer shell + patient-context overlay
138bf41e8  docs(next-session): refresh after chart-row sweep
```

Net additions:

* AgentDrawer with three mode tabs (Chart/Intake/Research),
  patient-context conflict overlay (Switch / Stay / Fresh), and
  per-scope conversation history in a Pinia store.
* OpenEMR module: two new identity-bootstrap endpoints
  (`/internal/me.php`, `/internal/patient_pid.php`) — both authed
  by a "lookup-purpose" JWT (signature + issuer + exp only).
* Sidecar: `OpenEMRMeFetcher`, `OpenEMRPatientPidFetcher`,
  `InternalJwtMinter`, and a new `POST /api/agent/turn` route. All
  routed through the existing `AuthGateway` — the trust boundary
  stays a single chokepoint.
* Dashboard: `useAgentTurn` composable + AgentDrawer wired to it.
* **ADR-0001** — the architectural decision record for the bridge.

## The auth bridge — quick mental model (full version in ADR-0001)

```
Browser ── HttpOnly cookie ──► Sidecar /api/agent/turn
                                    │
                                    ▼
                      OpenEMR /internal/me.php          (cached per access_token)
                          → {user_id, username, role}
                                    │
                                    ▼
                      OpenEMR /internal/patient_pid.php (cached per patient UUID)
                          → {pid: int}
                                    │
                                    ▼
                      InternalJwtMinter (HS256, AGENTFORGE_JWT_SECRET)
                                    │
                                    ▼
                      AuthGateway.validate_request()    ← unchanged
                                    │
                                    ▼
                      RequestContext  ◄── trust boundary (ARCHITECTURE.md §2)
                                    │
                                    ▼
                      Orchestrator.turn() — full tool access
```

### Two gotchas worth carrying forward

* **`users.uuid` and `patient_data.uuid` are stored as `BINARY(16)`.**
  The OIDC `fhirUser` claim and FHIR Patient resource id both expose
  the **hyphenated string** form. Both new repositories use
  `UNHEX(REPLACE(?, "-", ""))` at the SQL boundary; copy that pattern
  if you ever look up another OpenEMR row by UUID.
* **`Bootstrap's `.d-flex` carries `display: flex !important`** which
  silently overrides Vue's `v-show` inline style. Use `v-if` for any
  drawer/modal that has utility classes — caught here in the
  AgentDrawer aside (commit `e78924772`).

### Bridge things deferred (intentional, captured in ADR §6)

* Token refresh — when the access_token expires the user re-signs in.
* Breakglass UI — internal JWT carries `breakglass_flag=false`
  unconditionally from the dashboard.
* Streaming on `/api/agent/turn` — buffered only; the legacy `/turn`
  still has SSE behind a flag.
* Research and Intake modes are NOT wired to the agent yet — the
  drawer disables the input outside Chart mode. T38.11+ territory.

## Branch state

`feat/dashboard-port` (22 commits ahead of origin, pushed through
slice 4). Working tree only has the same two not-important doc
HTMLs:

```
M  docs/w2-defense-slides.html
?? docs/architecture-overview-slides.html
```

Both flagged as not-important previously; ignore for commits.

**Taskmaster:** T38.10 is still flagged `in-progress`. Batch the
status flip into the next `chore(taskmaster):` commit — same pattern
as the chart-row sweep.

## The W2 dashboard port plan (Task 38)

```
38   Port the OpenEMR patient dashboard to Vue 3
├── 38.1   Scaffold Vue 3 project at dashboard/         ✓ done
├── 38.2   OAuth2/OIDC login flow against OpenEMR       ✓ done (v1+v2)
├── 38.3   Patient header (FHIR Patient)                ✓ done
├── 38.4   Allergies card (FHIR AllergyIntolerance)     ✓ done
├── 38.5   Problem List card (FHIR Condition)           ✓ done
├── 38.6   Medications card (MedicationRequest, active) ✓ done
├── 38.7   Prescriptions card (MedicationRequest hist)  ✓ done
├── 38.8   Care Team card (FHIR CareTeam)               ✓ done (empty data — see T39)
├── 38.9   Lab Results card (FHIR Observation, bonus)   ✓ done
├── 38.10  AgentForge drawer + auth bridge              ✓ done (taskmaster pending)
├── 38.11  Citation overlay re-port to Vue + FHIR Bin   ← critical path
├── 38.12  Intake review form + commit (path TBD)       deps: 38.10, 38.11
├── 38.13  PATIENT_DASHBOARD_MIGRATION.md defense doc   ← parallel
└── 38.14  Deploy new dashboard to droplet              deps: drawer + overlay

39   Seed CareTeam data in dev-easy                     ○ low priority
```

**Remaining critical path**: 38.11 → 38.12 → 38.14. Defense doc
(38.13) and seed-CareTeam (T39) are parallel-friendly. ADR-0001
already does heavy lifting for 38.13.

## Decision spine — unchanged from earlier

### Framework — unchanged

**Vue 3 + Vite + TypeScript + Vue Router + Pinia + BootstrapVueNext +
Bootstrap 5/Icons + @types/fhir + pdfjs-dist@5.7.284 + Vitest.** No
JSX. No Playwright (revisit Saturday morning if there's slack).

### Auth — settled, plus the bridge

* Browser → Sidecar uses BFF cookies (T38.2 v2). Tokens never touch
  JS.
* Sidecar → OpenEMR uses two pipelines today:
  * **FHIR proxy** (`/api/fhir/*`) forwards reads with the session's
    OAuth access_token. Established by T38.2.
  * **Agent bridge** (`/api/agent/turn`) mints an internal JWT after
    bootstrapping identity through the two new endpoints. Established
    by ADR-0001.
* Both pipelines end at the same trust boundaries on the OpenEMR
  side (FHIR's BearerToken validation and the legacy
  AGENTFORGE_JWT_SECRET respectively).

### AgentForge drawer — settled (this session shipped it)

* Right-edge slide-out, ~480 px wide, persistent across navigation.
* Three explicit mode tabs (Chart / Intake / Research). Chart is the
  only one wired to the agent today. Research is the default when no
  active patient.
* Per-patient conversation scoping with a hard-interrupt overlay on
  patient-context change (Switch / Stay / Fresh).
* Conversation history lives in Pinia, lost on reload.

### Commit path (Q6) — TBD at T38.12

Three options at T38.12:

a. **BFF proxy** the legacy REST writes (sidecar's session token has
   `user/*.write` scopes; add a `/api/legacy/*` passthrough).
b. **Defer commit-to-chart** — surface the structured intake-review
   form, end with a *Copy structured summary* button. Demo-grade.
c. Keep deferred and revisit if there's slack on Saturday.

## Next session pick-up — pick one of two

### Option A — T38.11 (citation overlay re-port to Vue)

The legacy citation overlay lives in the OpenEMR module's chart
embed; we need it ported to the Vue dashboard so the agent's
"sources" affordance still works. Re-uses the BFF FHIR proxy plus
the existing pdfjs-dist setup. Estimate ~2-3 hr.

Critical path. Do this first if you only have one session.

### Option B — T38.13 (defense doc) — parallel-friendly

`PATIENT_DASHBOARD_MIGRATION.md` writeup. ADR-0001 already covers
the auth bridge in depth — fold it in as the "hardest piece" section.
The remaining sections cover: framework choice, BFF over storing
tokens in JS, the /me + /patient_pid bootstrap, the chart-row TDD
pattern, what's deferred and why. Estimate ~1.5 hr.

Doesn't block anything else — good background work or a slot for
when the dev-easy stack is misbehaving.

## Time budget reality check

| Day | Hours | Tasks | Status |
|---|---|---|---|
| Wed 2026-05-06 | ~10 | T38.1, T38.2 v1+v2, T38.3–T38.9, T39, **T38.10 + bridge** | done |
| Thu 2026-05-07 | 8 | T38.11 (overlay), start T38.13 | next |
| Fri 2026-05-08 | 8 | T38.12 (commit form), finish T38.13 | |
| Sat 2026-05-09 | ~6 | T38.14 (deploy), T39 (if time), buffer | |

**Deadline: Sat 2026-05-10 noon.** A full half-day was reclaimed
across Wed evening + late night by closing the chart row AND the
drawer + bridge in one push.

## Caveats / things to watch

### Runtime requirements (must be running for dashboard work)

1. **Dev-easy stack** — `cd docker/development-easy && docker compose ps`
   should show 7 containers. If down: `docker compose up --detach --wait`.
2. **`agentforge-redis` container** — required by sidecar for sessions.
   `docker ps --filter name=agentforge-redis`. If missing:
   `docker run -d --name agentforge-redis -p 6379:6379 redis:7-alpine`.
3. **Sidecar** — `cd sidecar && ./scripts/sidecar.sh status` should
   say `running on :8000`. If not: `./scripts/sidecar.sh start`.
   Tail logs at `sidecar/var/sidecar.log` — bridge failures now
   include `stage=` (me / patient_pid) and `upstream_status` so they
   diagnose at a glance.
4. **Vite dev server** — `cd dashboard && npm run dev`. Restart on
   `vite.config.ts` edits (proxy block doesn't HMR).

### Security / production tradeoffs accepted

* **id_token signature verification deferred** — sidecar decodes the
  JWT without verifying against the JWKS endpoint. T38.14 follow-up.
* **Token refresh not wired** — when the access_token expires the
  user gets re-prompted to sign in. The agent bridge's identity
  cache is keyed by access_token so it invalidates in lockstep when
  refresh lands.
* **Anthropic API key was leaked in conversation** during MR 7's
  build session. Rotate before any non-demo use.
* **HF cache is not mounted as a volume on the droplet** — every
  redeploy re-downloads ~190 MB.
* **Disk pressure on the droplet** crossed 79% during MR 7 deploys.
  Run `docker system prune -f` before redeploys.

### OpenEMR FHIR mapper bugs to know (carried forward)

* **`{entry.value}` template placeholder** — OpenEMR's FHIR mapper
  sometimes emits an unsubstituted Smarty/template string as
  `Observation.valueString` (observed on Cause of Death, LOINC
  69453-9). LabResultsCard filters them via `/^\{[^}]+\}$/`.
* **`/userinfo` returns 404** despite OIDC discovery advertising it
  (still applies; sidecar reads identity from id_token JWT, and the
  agent bridge uses fhirUser → /me instead).
* **`procedure_result` UUID-backfill SQL bug** still occasionally
  hits the FHIR `/metadata` endpoint with a 500.

## Memory pointers (via `MEMORY.md`)

* `project_w2_dashboard_port.md` — the surprise pivot, deadline,
  framework lean.
* `project_panel_placement.md` — top-level drawer decision.
* `project_droplet.md` — droplet IP + SSH access.
* `project_droplet_containers.md` — canonical 5-container layout.
* `project_repo_layout.md` — sidecar/ + interface/modules/.../oe-module-agentforge/.
* `feedback_git_workflow.md` — branch per task, one commit per
  Taskmaster subtask, `Assisted-by` trailer, batched
  `chore(taskmaster):` commits at session boundaries.
* `feedback_task_ordering.md` — depth-first / context continuity
  preferred when CLI proposes a context switch.
* `feedback_use_taskmaster_cli.md` — `task-master list/show/next`
  not raw JSON.
* `feedback_no_rm_rf.md` — `rm -rf` is permission-blocked; use
  `git rm` for tracked deletes.
* `feedback_tdd_primary.md` — default to TDD; vitest for dashboard.
* `user_web_background.md` — explain web fundamentals (cookies,
  sessions, OAuth) from scratch when load-bearing.

## Quick-start checklist for next session

1. `git status` — confirm clean (or known-not-important diffs only).
2. `git log --oneline -5` — verify latest commit is
   `04e6c2611 fix(agentforge): UNHEX hyphenated UUIDs …`.
3. `task-master show 38` — confirm 38.1–38.9 done; 38.10 *should* be
   marked done (batch into the next `chore(taskmaster):` commit
   if it's still `in-progress`).
4. **Spin up the runtime stack** (in this order):
   - `docker ps --filter name=agentforge-redis` — start if missing.
   - `cd sidecar && ./scripts/sidecar.sh status` — start if not running.
   - `cd dashboard && npm run dev` — confirm dashboard serves at
     :5173, `/patient/<uuid>` shows the chart row, and clicking
     the right-edge **Agent** tab → typing a chart question →
     send produces a real orchestrator reply.
5. Read **`docs/adr/0001-dashboard-auth-bridging.md`** if you're
   touching the BFF, the auth gateway, or either bootstrap endpoint.
6. Pick a path:
   - **T38.11** if you have a full session and want critical-path
     progress (citation overlay re-port).
   - **T38.13** if you have a smaller window or the dev-easy stack
     is misbehaving (defense doc; ADR-0001 covers half of it).

## What's deployed where

* **`http://143.244.157.90:9300/`** — production droplet running the
  MR-7 image. Dashboard SPA **not yet deployed** — lands with T38.14.
* **`http://localhost:5173/`** — local dashboard (Vite). `/auth/*`
  and `/api/*` proxied to the sidecar at `localhost:8000`.
* **`http://localhost:8000/`** — local sidecar (FastAPI). `/health`,
  `/turn` (legacy JWT path), `/auth/*`, `/api/fhir/*`, and the new
  `/api/agent/turn` (BFF agent path).
* **`https://localhost:9300/`** — local dev-easy OpenEMR (HTTPS).
  The two new `/internal/me.php` + `/internal/patient_pid.php`
  endpoints live here; sidecar reaches them over `:8300` HTTP.
