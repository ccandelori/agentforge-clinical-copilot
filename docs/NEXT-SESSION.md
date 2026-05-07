# Where we left off — 2026-05-06 night (chart row complete; drawer next)

Read me first when picking the project back up. Update or delete me
when the state captured here goes stale.

## Headline

**The entire chart row landed in one Wednesday-evening session.**
T38.3 (patient header + picker + ClinicalCard wrapper) plus the six
clinical cards (T38.4 Allergies, T38.5 Problem List, T38.6 Active
Medications, T38.7 Prescription history, T38.8 Care Team, T38.9 Lab
Results) all shipped TDD-style with 160 vitest specs total. Branch
`feat/dashboard-port` is 13 commits ahead of origin, **no MR yet**.
T38.10 (AgentForge drawer) is the next big architectural move.

| MR | Branch | Status |
|---|---|---|
| !35 | `feat/w2-mr7-cutover-wiring` | merged 2026-05-06 |
| !36 | `feat/w2-task-24-citation-overlay` | merged 2026-05-06 |
| (open) | `feat/dashboard-port` | 13 commits, no MR yet |

`task-master next` — **T38.10 (AgentForge drawer)** is the next-up
critical-path task. T38.13 (defense doc) remains parallel-friendly.

## What shipped this session (Wed evening 2026-05-06)

Eight commits on `feat/dashboard-port`:

```
feb872a4c  chore(taskmaster): mark T38.3–T38.9 done; add T39
de945ce24  feat(dashboard): lab results card + sparklines (T38.9)
17f91390d  feat(dashboard): care team card (T38.8)
25ba3be8c  feat(dashboard): prescription history card (T38.7)
18099a5b7  feat(dashboard): active medications card (T38.6)
83f266e85  feat(dashboard): problem list card (T38.5)
2e801314b  feat(dashboard): allergies card + collapse on ClinicalCard (T38.4)
7112b6ed6  feat(dashboard): patient header + picker + ClinicalCard (T38.3)
```

Plus **T39** (low priority) added: seed CareTeam data in dev-easy
because `care_teams` and `care_team_member` tables are both empty
(0 rows) — the card itself works correctly, just renders empty for
every patient.

## The card pattern (stable; T38.10+ should respect it)

Every chart card follows the same shape; cloning is a 30-min move:

* `defineProps<{ pid: string }>()`
* `useFhirResource<fhir4.Bundle>('/api/fhir/Resource?patient=…')`
  auto-fires; exposes `status`, `data`, `error`, `refetch`
* Type-guard via `is<Resource>(r): r is fhir4.Resource`
* Project entries → row interface (`name`, `status`, `date`, etc.)
* Sort by clinical-importance rank then date desc (or just date)
* `cardState` computed: `loading | empty | error | ready`
* Render via `<ClinicalCard collapsible :title :count :state :error>`
  — header chevron toggle, slots for `loading | empty | error |
  default`, v-show on body so child composables don't refire on expand

Shared helpers worth knowing:

* `useFhirResource<T>(path)` at `src/composables/useFhirResource.ts`
  — cookie-authed FHIR fetch. Auto-fires on mount, `refetch()` to
  refresh. Same Accept header (`application/fhir+json`), same
  `credentials: 'same-origin'`.
* `formatFhirDate(iso)` at `src/utils/formatDate.ts` — handles
  `YYYY-MM-DD` (parses as local-date to dodge UTC-midnight TZ shift)
  and ISO datetime strings; returns `'—'` for null/undefined/empty.
* `<ClinicalCard>` at `src/components/ClinicalCard.vue` — title,
  count, header-actions slot, optional collapsible chevron.

## Discoveries this session worth carrying forward

### Data gaps (Synthea / dev-easy)

* **CareTeam:** Synthea bundles ship 1-5 CareTeams per patient with
  multiple clinicians, but OpenEMR's `care_teams` + `care_team_member`
  tables have 0 rows. Whatever ingestion path populated allergies /
  conditions / medications skipped CareTeam. **T39** captures the
  follow-up.
* **Problem List:** Synthea `Condition` resources are all
  `category=encounter-diagnosis`, never `problem-list-item`. Strict
  category filter (per T38.5 spec) returns empty for every patient.
* **Active Medications:** All Synthea `MedicationRequest` resources
  have `status=completed`, so the active-status filter is empty.
  T38.7's history card surfaces them all.
* **Lab interpretation/refRange:** Synthea `Observation` resources
  don't carry `interpretation[]` or `referenceRange[]`. Color coding
  (red high / blue low / bold red critical) is wired correctly but
  doesn't fire on dev-easy data. To colorize, we'd need hardcoded
  LOINC ranges — out of scope for the cards.

### OpenEMR FHIR mapper bugs to know

* **`{entry.value}` template placeholder:** OpenEMR's FHIR mapper
  sometimes emits an unsubstituted Smarty/template string as
  `Observation.valueString` instead of the actual value (observed
  on Cause of Death observations, LOINC 69453-9). LabResultsCard
  filters them via `/^\{[^}]+\}$/`.
* **`/userinfo` returns 404** despite OIDC discovery advertising it
  (still applies; sidecar reads identity from id_token JWT).
* **`procedure_result` UUID-backfill SQL bug** still occasionally
  hits the FHIR `/metadata` endpoint with a 500 (doesn't affect any
  of the chart cards' endpoints).

### TDD wrinkle

* `wrapper.isVisible()` from `@vue/test-utils` is flaky in JSDOM for
  `v-show` ancestor styles (relies on `getComputedStyle` /
  `offsetParent` which JSDOM doesn't fully implement). Inspect the
  inline style attribute directly — see `ClinicalCard.spec.ts`'s
  `bodyHidden(wrapper)` helper for the pattern.
* `oxlint` requires explicit type parameters on `vi.fn()` mocks;
  mocking fetch should look like `vi.fn<typeof fetch>().mockResolvedValue(...)`.

## Branch state

`feat/dashboard-port` (13 commits ahead of origin, pushed). Working
tree only has the two not-important doc HTMLs:

```
M  docs/w2-defense-slides.html
?? docs/architecture-overview-slides.html
```

Both flagged as not-important previously; ignore for commits.

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
├── 38.10  AgentForge drawer (right-edge slide-out)     ← next
├── 38.11  Citation overlay re-port to Vue + FHIR Bin   deps: 38.10
├── 38.12  Intake review form + commit (path TBD)       deps: 38.10, 38.11
├── 38.13  PATIENT_DASHBOARD_MIGRATION.md defense doc   ← parallel
└── 38.14  Deploy new dashboard to droplet              deps: drawer + overlay

39   Seed CareTeam data in dev-easy                     ○ low priority
```

**Remaining critical path**: 38.10 → 38.11 → 38.12 → 38.14. Defense
doc (38.13) and seed-CareTeam (T39) are parallel-friendly.

## Decision spine — unchanged from pre-session

### Framework — unchanged

**Vue 3 + Vite + TypeScript + Vue Router + Pinia + BootstrapVueNext +
Bootstrap 5/Icons + @types/fhir + pdfjs-dist@5.7.284 + Vitest.** No
JSX. No Playwright (revisit Saturday morning if there's slack).

### Auth — settled v2 (BFF)

Sidecar holds OAuth2 client credentials; dashboard talks to `/auth/*`
and `/api/fhir/*` over an HttpOnly session cookie. Tokens never touch
JS. See `sidecar/src/agentforge/dashboard_auth/` for the implementation
and `feat/dashboard-port`'s T38.2 commits for the dashboard side.

### AgentForge drawer — settled (carries from pre-pivot)

* **Q1 — Shape:** right-edge slide-out drawer (~480-560px wide).
* **Q2 — Persistence:** drawer stays open across tab/page navigation.
* **Q3 — Patient-context safety:** when the active patient changes
  while a Chart-mode conversation is in progress, render a hard-
  interrupt **OVERLAY on top of the chat** with three resolution
  buttons. Conversation must be **scoped per-patient**.
* **Q4 — Three flows:** explicit mode tabs in the drawer header —
  **Chart / Intake / Research**. Explicit > implicit.
* **Q5 — Save-to-chart UI (Intake mode only):** structured review
  form with single Commit button.

### Commit path (Q6) — TBD at T38.12

Three options at T38.12:

a. **BFF proxy** the legacy REST writes (sidecar's token already has
   `user/*.write` scopes; add a `/api/legacy/*` passthrough).
b. **Defer commit-to-chart** — surface the structured intake-review
   form, end with a *Copy structured summary* button. Demo-grade.
c. Keep deferred and revisit if there's slack on Saturday.

## Next session pick-up — T38.10 (AgentForge drawer)

The drawer is the next big architectural piece. Per the decision
spine (above): right-edge slide-out, ~480-560px wide, persistent
across navigation, three explicit mode tabs (Chart / Intake /
Research), per-patient conversation scoping with a hard-interrupt
overlay on patient-context change.

### Likely shape (subject to TDD discipline)

* `<AgentDrawer>` shell component — slide-out animation, three mode
  tabs, scoped per-patient.
* Pinia store `useAgentDrawer` — current mode, drawer open/closed,
  per-patient conversation registry, active conversation id.
* Composable bridging the sidecar's existing `/turn` endpoint —
  reuse the W2 graph + intake/evidence flows; auth flips from a
  custom JWT to the sidecar session cookie since sidecar now owns
  sessions.
* PatientDashboardView gets a `<AgentDrawer>` mounted at root level
  (sibling to the navbar + main, fixed-positioned right edge).
* Patient-context overlay — when `patientId` changes while a Chart
  conversation has unsaved state, render a modal-ish overlay with
  three resolution buttons (Discard / Save & Switch / Cancel).

### Estimated cost

3-4 hr — drawer shell + three modes + overlay + Pinia store + sidecar
bridging. Bigger than any single card.

## Time budget reality check

| Day | Hours | Tasks | Status |
|---|---|---|---|
| Wed 2026-05-06 | ~8 | T38.1, T38.2 v1+v2, T38.3–T38.9, T39 | done |
| Thu 2026-05-07 | 8 | T38.10 (drawer), T38.13 (defense doc start) | next |
| Fri 2026-05-08 | 8 | T38.11 (overlay re-port), T38.12 (commit form), T38.13 polish | |
| Sat 2026-05-09 | ~6 | T38.14 (deploy), T39 (if time), buffer for fixes | |

**Deadline: Sat 2026-05-10 noon.** ~6 hours of cushion gained
tonight by closing all six cards in one session instead of spread
across Thu+Fri.

## Caveats / things to watch

### Runtime requirements (must be running for dashboard work)

1. **Dev-easy stack** — `cd docker/development-easy && docker compose ps`
   should show 7 containers. If down: `docker compose up --detach --wait`.
2. **`agentforge-redis` container** — required by sidecar for sessions.
   `docker ps --filter name=agentforge-redis`. If missing:
   `docker run -d --name agentforge-redis -p 6379:6379 redis:7-alpine`.
3. **Sidecar** — `cd sidecar && ./scripts/sidecar.sh status` should
   say `running on :8000`. If not: `./scripts/sidecar.sh start`.
4. **Vite dev server** — `cd dashboard && npm run dev`. Restart on
   `vite.config.ts` edits (proxy block doesn't HMR).

### Security / production tradeoffs accepted

* **id_token signature verification deferred** — sidecar decodes the
  JWT without verifying against the JWKS endpoint. T38.14 follow-up.
* **Token refresh not wired** — when the access_token expires the
  user gets re-prompted to sign in.
* **Anthropic API key was leaked in conversation** during MR 7's
  build session. Rotate before any non-demo use.
* **HF cache is not mounted as a volume on the droplet** — every
  redeploy re-downloads ~190 MB.
* **Disk pressure on the droplet** crossed 79% during MR 7 deploys.
  Run `docker system prune -f` before redeploys.

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
   `feb872a4c chore(taskmaster): mark T38.3–T38.9 done; …`.
3. `task-master show 38` — confirm 38.1–38.9 done, 38.10 pending.
4. **Spin up the runtime stack** (in this order):
   - `docker ps --filter name=agentforge-redis` — start if missing.
   - `cd sidecar && ./scripts/sidecar.sh status` — start if not running.
   - `cd dashboard && npm run dev` — confirm dashboard serves at
     :5173, `/patient/<pid>` shows the full chart row.
5. Begin T38.10:
   - Sketch `<AgentDrawer>` shell + Pinia store from the decision
     spine (mode tabs, patient-scoped conversations, persistent
     across nav).
   - Decide on overlay-on-patient-change UX (probably a Bootstrap
     `<modal>` rendered above the drawer chat area, not the page).
   - Wire to sidecar `/turn` with the existing session cookie (no
     custom JWT — it's the same identity that owns the FHIR proxy).

## What's deployed where

* **`http://143.244.157.90:9300/`** — production droplet running the
  MR-7 image. Dashboard SPA **not yet deployed** — lands with T38.14.
* **`http://localhost:5173/`** — local dashboard (Vite). `/auth/*`
  and `/api/*` proxied to the sidecar at `localhost:8000`.
* **`http://localhost:8000/`** — local sidecar (FastAPI). `/health`,
  `/turn`, plus the dashboard BFF surface.
* **`https://localhost:9300/`** — local dev-easy OpenEMR (HTTPS only
  for OAuth2 endpoints).
