# Where we left off — 2026-05-06 (W2 pivoted: dashboard port to Vue 3)

Read me first when picking the project back up. Update or delete me
when the state captured here goes stale.

## Headline

**W2 scope expanded mid-week.** A Surprise Challenge brief landed on
2026-05-06 requiring the OpenEMR patient dashboard to be ported to a
modern frontend framework. **Deadline: 2026-05-10 noon** — effectively
Saturday night / Sunday wee hours. Brief: `~/Downloads/AgentForge —
Clinical Co-Pilot W2 — Surprise Challenge_ Modernize the Patient
Dashboard.pdf`.

| MR | Branch | Status |
|---|---|---|
| !35 | `feat/w2-mr7-cutover-wiring` | merged 2026-05-06 |
| !36 | `feat/w2-task-24-citation-overlay` | merged 2026-05-06 |
| (open) | `feat/dashboard-port` | 1 commit (T38.1 scaffold), no MR yet |

`task-master next` — work the 14 subtasks under **Task 38** in
dependency order; T38.2 (OAuth2/OIDC) is the next-up after the scaffold.

## What just shipped this session

* **MR !35 — MR 7 production cutover wiring.** Upload → extract →
  synthesize end-to-end through OpenEMR UI. Merged.
* **MR !36 — Task 24 citation overlay + integration.** pdf.js 5.7.284
  vendored via npm/gulp at `public/assets/pdfjs-dist/`; vanilla-JS
  citation overlay component with 1-indexed page contract preserved;
  citation chips in the receipts panel wire clicks to
  `window.AgentforgeCitationOverlay.mount()`. 31 new JS tests, 400 total
  passing. Merged.
* **`.gitignore` rule** for `sites/default/documents/[0-9]*/` (encrypted
  runtime patient uploads).
* **Panel-placement design grilling** (2026-05-06) — five decisions
  resolved before the W2 surprise landed (see "Decision spine" below).
* **W2 Surprise Challenge plan** — Task 38 created with 14 subtasks;
  framework defense seeded; `feat/dashboard-port` branch off main with
  T38.1 (scaffold) committed and pushed.

## Branch state

`feat/dashboard-port` (off main, 1 commit, pushed). Working tree has
local-only doc edits the user has called out as not-important
(`docs/NEXT-SESSION.md` ← the file you're reading,
`docs/w2-defense-slides.html`, `docs/architecture-overview-slides.html`).

## The W2 dashboard port plan (Task 38 + 14 subtasks)

```
38   Port the OpenEMR patient dashboard to Vue 3
├── 38.1   Scaffold Vue 3 project at dashboard/         ✓ done
├── 38.2   OAuth2/OIDC login flow against OpenEMR       ← next
├── 38.3   Patient header (FHIR Patient)                deps: 38.2
├── 38.4   Allergies card (FHIR AllergyIntolerance)     deps: 38.3
├── 38.5   Problem List card (FHIR Condition)           deps: 38.3
├── 38.6   Medications card (MedicationStatement)       deps: 38.3
├── 38.7   Prescriptions card (MedicationRequest)       deps: 38.3
├── 38.8   Care Team card (FHIR CareTeam)               deps: 38.3
├── 38.9   Lab Results card (FHIR Observation, bonus)   deps: 38.3
├── 38.10  AgentForge drawer (right-edge slide-out)     deps: 38.2, 38.3
├── 38.11  Citation overlay re-port to Vue + FHIR Bin   deps: 38.10
├── 38.12  Intake review form + FHIR write commit       deps: 38.10, 38.11
├── 38.13  PATIENT_DASHBOARD_MIGRATION.md defense doc   ← parallel
└── 38.14  Deploy new dashboard to droplet              deps: cards + drawer
```

**Critical path** (sequential): 38.1 → 38.2 → 38.3 → 38.10 → 38.11 →
38.12 → 38.14. Cards 38.4–38.9 and the defense doc 38.13 are
parallel-friendly with the critical path.

**Card pattern leverage:** build 38.4 (allergies) carefully — establish
a `<ClinicalCard>` wrapper component (collapse/expand chrome,
loading/empty/error states, FHIR fetch composable) — then 38.5–38.9 each
become 30–45 min copy-paste-modify operations. That bank pays for the
drawer/overlay polish on Saturday.

## Decision spine

Things that are settled and shouldn't be re-litigated:

### Framework

**Vue 3 + Vite + TypeScript + Vue Router + Pinia + BootstrapVueNext +
Bootstrap 5/Icons + oidc-client-ts + @types/fhir + pdfjs-dist@5.7.284 +
Vitest.** No JSX. No Playwright (revisit Saturday morning if there's
slack). Repo layout: `dashboard/` at repo root, sibling to `sidecar/`,
independent `package.json`/`node_modules`.

The defense (graded artifact at `PATIENT_DASHBOARD_MIGRATION.md`):
three legs — gain over PHP-rendered, Vue specifically vs alternatives
(React / Angular / Svelte / Qwik / HTMX), tradeoffs accepted. Qwik is
called out as the road-not-taken with concrete reasons (UI ecosystem
maturity for clinical cards, OAuth lib maturity, learning-curve risk on
a 3.5-day timer). Narrative arc: **predictability is a clinical-software
value, not a hedge.**

### AgentForge drawer (settled in panel-placement grilling 2026-05-06)

* **Q1 — Shape:** right-edge slide-out drawer (~480–560px wide). Not
  bottom-up, not floating, not left-edge.
* **Q2 — Persistence:** drawer stays open across tab/page navigation.
* **Q3 — Patient-context safety:** when the active patient changes
  while a Chart-mode conversation is in progress, render a **hard-
  interrupt OVERLAY on top of the chat** (NOT a banner) with three
  resolution buttons: *Switch to <new>'s conversation* / *Stay on
  <previous>* / *Start fresh with <new>* (the third only when a stale
  conversation exists for new). Send/input disabled until choice made.
  Conversation must be **scoped per-patient** with absolutely no risk
  of cross-patient pollution. Sidecar-side hard refusal if asserted
  patient_id ≠ session pid is the safety belt; the overlay is the
  belt-and-suspenders affordance.
* **Q4 — Three flows:** explicit mode tabs in the drawer header —
  **Chart / Intake / Research**. Chart-mode keyed by patient_id;
  Intake-mode keyed by upload session/document_id; Research-mode is
  one global scope. **Explicit > implicit.** No silent flow inference.
* **Q5 — Save-to-chart UI (Intake mode only):** structured review form
  with single Commit button. Sections mirror IntakeFormExtraction
  (chief concern, demographics, medications, allergies, family
  history). Each row: editable value + include checkbox + citation
  chip (provenance affordance, mounts overlay). Bottom: *Commit
  selected to chart*. One audit gesture, edit-in-place handles the
  "LLM read it slightly wrong" case, include checkboxes handle partial
  commits.

### Commit path (Q6 — INVALIDATED then RE-DECIDED)

Original recommendation pre-W2-pivot: browser → new session-authed
PHP endpoint. **Invalidated** by the W2 brief — no new backend
allowed. **Updated decision:** browser → FHIR API write directly
(POST/PUT to FHIR `Condition`, `MedicationStatement`,
`AllergyIntolerance`, `FamilyMemberHistory`, plus `QuestionnaireResponse`
referencing the canonical intake Questionnaire as umbrella).
Validate session pid matches form patient_id before writing.
Optimistic UI with rollback on FHIR write failure.

### Bonus section choice

**Lab Results** (FHIR `Observation`, laboratory category) — chosen
over encounter history / vitals / immunizations / appointments /
patient notes because W2 already has lab-extraction infrastructure and
a labs card with normal-range coloring + sparkline trends shows off
something more concrete than a static list.

## Next session pick-up — T38.2 (OAuth2/OIDC login flow)

The substantive auth work. Scope:

1. **OpenEMR OAuth2 client registration.** OpenEMR exposes
   `/oauth2/<site>/{authorize,token,userinfo,jwks}`. Register the
   dashboard as an OAuth2 client (Authorization Code + PKCE) via
   OpenEMR admin → Client Registrations. Capture the client_id +
   redirect_uri setup steps in PATIENT_DASHBOARD_MIGRATION.md.
2. **`oidc-client-ts` config.** authority = OpenEMR base + `/oauth2/<site>`,
   client_id from .env, redirect_uri = `<dashboard>/auth/callback`,
   scope = `openid fhirUser patient/*.read patient/*.write`,
   response_type = `code`, PKCE enabled.
3. **Pinia auth store.** id_token + access_token + expiry; refresh
   logic; sign-out tears down session and revokes. Token storage:
   `sessionStorage` (cleared on tab close), never `localStorage` in
   plaintext.
4. **Vue Router auth guard.** Unauthenticated routes redirect to
   `/login`. `/auth/callback` view handles the authorization-code
   exchange.
5. **LoginView + OAuthCallbackView.** Minimal Bootstrap 5 layouts;
   the actual visual polish lands with the patient header in 38.3.
6. **Tests.** Vitest covers the Pinia store transitions (signed-out →
   signing-in → signed-in → signed-out, refresh path) with mocked
   `oidc-client-ts`. The full OAuth handshake is browser-only —
   verified manually against dev-easy after the wiring lands.

Estimated: 2–3 hr of careful work.

## Time budget reality check (~28 hrs total)

| Day | Hours | Tasks |
|---|---|---|
| Wed evening (done) | 4 | T38.1 scaffold (✓) |
| Thu | 8 | T38.2 auth, T38.3 header, T38.4 allergies (exemplar card), T38.5 problem list |
| Fri | 8 | T38.6 meds, T38.7 prescriptions, T38.8 care team, T38.9 labs, T38.10 drawer (start) |
| Sat | 8 | T38.10 drawer (finish), T38.11 overlay re-port, T38.12 commit form, T38.13 defense doc, T38.14 deploy |

Saturday is dense; the card-pattern leverage move is what makes it
fit. Defense doc drafted in parallel from Wed night (already seeded);
refine bullets daily so it isn't a Saturday-night writing exercise.

## What's preserved from the pre-pivot W2 work

* **Task 24 (citation overlay)** — merged. The vanilla `citation_overlay.js`
  IIFE logic (1-indexed page contract, mount/unmount, click dismiss
  with × button + onClose callback) ports as Vue component logic in
  T38.11. Tests port to Vitest.
* **`pdfjs-dist@5.7.284` npm dep** — already in OpenEMR's root
  `package.json`. Dashboard installs its own copy at
  `dashboard/package.json`.
* **MR 7 backend (W2 graph + sidecar /turn + intake/evidence flows)** —
  unchanged. The new dashboard talks to the sidecar via the same
  /turn endpoint (different auth — JWT minted from the OAuth2
  access_token instead of the OpenEMR session cookie).
* **5-container droplet layout** at `143.244.157.90:9300/`. The
  existing OpenEMR + sidecar + redis + mysql + phpmyadmin keep
  running. T38.14 adds the dashboard SPA either via a new container,
  an nginx volume, or a static mount on the existing sidecar
  FastAPI — pick the lowest-friction option Saturday.

## Caveats / things to watch

* **OpenEMR FHIR API capabilities.** The brief says "consume
  OpenEMR's existing REST + FHIR API as your data layer" — verify
  early which FHIR resources are actually exposed and writable on
  the droplet's OpenEMR. Do this Wed/Thu night, not Saturday.
* **CORS.** OpenEMR REST/FHIR endpoints likely need CORS configuration
  to accept the dashboard origin. Surface in 38.2 (auth) when the
  first OAuth call surfaces a CORS error.
* **OAuth2 redirect URI mismatch** is the most common first-time
  failure. Verify the exact URI registered in OpenEMR matches what
  `oidc-client-ts` sends.
* **Anthropic API key was leaked in conversation** during MR 7's
  build session. Rotate before any non-demo use:
  Anthropic console → revoke + reissue → update
  `/opt/agentforge/sidecar/.env` → restart sidecar.
* **HF cache is not mounted as a volume on the droplet** — every
  redeploy re-downloads ~190 MB. One-line fix in
  `scripts/deploy-droplet.sh` (mentioned but never landed; do
  alongside T38.14 if there's time).
* **Disk pressure on the droplet** crossed 79% during MR 7 deploys.
  Run `docker system prune -f` before redeploys.

## Memory pointers (via `MEMORY.md`)

* `project_w2_dashboard_port.md` — the surprise pivot, deadline,
  framework lean, AgentForge-in-dashboard decision.
* `project_panel_placement.md` — top-level drawer decision (resolved
  one of the three pre-pivot open design decisions). Most of the
  grilling output now lives in the Decision spine above.
* `project_droplet.md` — droplet IP + SSH access.
* `project_droplet_containers.md` — canonical 5-container layout.
* `feedback_git_workflow.md` — branch per task, one commit per
  subtask, `Assisted-by: Claude Code` trailer.
* `feedback_use_taskmaster_cli.md` — `task-master list/show/next`,
  not raw tasks.json.
* `feedback_no_rm_rf.md` — `rm` is permission-blocked at the agent
  layer; surface paths to the user instead.

## Quick-start checklist for next session

1. `git status` — confirm clean (or known-not-important diffs).
2. `git log --oneline -5` — verify latest is the T38.1 scaffold commit
   on `feat/dashboard-port`.
3. `task-master show 38` — confirm 38.1 done, 38.2 next.
4. `cd dashboard && npm run dev` — confirm scaffold still serves at
   `http://localhost:5173`.
5. Begin T38.2: register the OAuth2 client in dev-easy OpenEMR's
   admin → Client Registrations panel; copy client_id + redirect_uri
   to `dashboard/.env`; wire `oidc-client-ts`.
6. Manual verification after each piece lands: full OAuth handshake
   in a real browser against dev-easy.

## What's deployed where

* **`http://143.244.157.90:9300/`** — production droplet running the
  MR-7 image. Upload → extract → synthesize end-to-end through the
  OpenEMR UI; W2 evidence retriever loaded; citation chips clickable
  in the receipts panel (MR !36). The new Vue dashboard is **not yet
  deployed** — that lands with T38.14 Saturday.
* **`http://localhost:5173/`** (or whatever Vite picks) — local dev
  server for the Vue dashboard, after `npm run dev` in `dashboard/`.
