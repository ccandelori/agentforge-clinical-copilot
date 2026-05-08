# vue-ui ↔ dashboard-port migration recon

> Phase 0, read-only. Scope: would swapping `dashboard/` → `vue-ui/` ship by
> the W2 defense (Sun 2026-05-10 noon)?
>
> Inputs: `feat/dashboard-port` working tree (current branch) +
> `feat/vue-ui-rewrite` via `git show` (vue-ui/ source not present on this
> branch, only `vue-ui/dist/` and `vue-ui/node_modules/`).
>
> Author: Phase 0 recon agent, 2026-05-07.

---

## TL;DR

**Swap is not feasible by Sun noon.** Better play: **lift visual design from
vue-ui into dashboard-port**, treat vue-ui as a v2 reference branch.
Rationale below.

---

## 1. Screen parity table

`dashboard/src/router/index.ts` defines 3 routes; `vue-ui` defines 8.
The two builds optimize for different goals: dashboard = real-data
spike on the W2 brief; vue-ui = full-app aesthetic / Wave-2 surface area.

| Concern | dashboard-port (`dashboard/`) | vue-ui (`vue-ui/`) | Notes |
|---|---|---|---|
| Login | `views/LoginView.vue` — single "Sign in with OpenEMR" button, hands off to sidecar BFF `/auth/login` | `views/auth/LoginView.vue` — username/password form against in-memory whitelist | **Different security model.** vue-ui's flow is incompatible with the BFF/OAuth2 trust boundary. |
| Patient picker / list | `views/PatientPickerView.vue` — minimal list-group, hits `/api/fhir/Patient?_count=20` | `views/patients/PatientList.vue` — search, filters, sort, pagination, density toggle (mock data) | vue-ui list view is much richer but works only against `api/mock.ts`. |
| Patient dashboard | `views/PatientDashboardView.vue` — header + 6 cards (Allergies, Problems, Meds, Rx, Labs, CareTeam) hitting real FHIR | `views/patients/PatientDashboard.vue` — header band + vitals strip (sparkline cards) + Allergies/Problems/Meds/Encounters/Labs cards (mock data) | vue-ui has Encounters + sticky vitals strip; dashboard-port has CareTeam + Prescriptions split + working FHIR. |
| Calendar | — (not in scope) | `views/calendar/CalendarView.vue` (Day/Week/Month + appointment modal) | vue-ui-only. Bonus surface, not a W2 requirement. |
| Encounter editor | — | `views/encounters/EncounterEditor.vue` — full SOAP layout, BMI calc, ICD typeahead, sign+finalize gate | vue-ui-only. Drafts persist to localStorage (PHI risk). |
| Settings | — | `views/settings/SettingsView.vue` (theme/accent/font/density/keybindings/about) | vue-ui-only. |
| Dashboard "home" | — | `views/_placeholders/DashboardHome.vue` (placeholder only) | Neither side has a real landing page. |
| AgentForge drawer | `components/AgentDrawer.vue` mounted at App.vue, three tabs Chart/Intake/Research, real `/api/agent/turn` wiring | `components/agentforge/AgentDrawer.vue` — three tabs Chat/Citations/History, canned replies + setTimeout typewriter + `localStorage` persistence | Different tab models. **vue-ui's split (Chat/Citations/History) is the right product** per memory; dashboard-port's split (Chart/Intake/Research) is the right *agent-mode* model. They're orthogonal — both could live in v2. |
| 404 / catch-all | — | `views/_placeholders/NotFound.vue` | vue-ui-only. |

**Feature-parity gap (vue-ui has, dashboard-port doesn't):** Calendar,
Encounter editor, Settings, Dashboard home, NotFound, vitals strip with
sparklines, citations pane, conversation history pane, design-token
system.

**Required-by-brief regression (dashboard-port has, vue-ui doesn't):**
working OAuth2/FHIR/agent wiring; CareTeam card; Prescriptions card
distinct from active Meds; Labs with proper FHIR `Observation`
sparkline + interpretation-code flagging; Patient picker against real
`Patient/?_count=20`.

---

## 2. Wiring code inventory (dashboard-port — what vue-ui would need to crib)

### Auth (BFF flow, not classic PKCE)

The dashboard never sees an OAuth2 token. The sidecar holds the
client_secret and exchanges code → tokens server-side; the browser
holds only an HttpOnly session cookie.

- `dashboard/src/stores/auth.ts` (133 lines) — `hydrate()` calls
  `/auth/whoami`, derives `status: 'unknown' | 'signed-in' | 'signed-out'`,
  surfaces `user: { sub, name, fhir_user, email }`. 5s timeout when the
  sidecar is unreachable. `signIn()` is a top-level `window.location`
  navigation to `/auth/login?next=...`; `signOut()` POSTs `/auth/logout`.
- `dashboard/src/router/index.ts` lines 36–48 — router guard awaits
  `auth.hydrate()` once, then redirects unauthenticated users to `/login`.
- `dashboard/src/services/navigation.ts` — `navigateTo()` indirection so
  Vitest can stub `window.location.assign`.
- **Sidecar half** (Python, untouched by the swap):
  `sidecar/src/agentforge/dashboard_auth/{routes.py, oauth.py, sessions.py}`.
- **No request interceptor.** Auth rides on `credentials: 'same-origin'`
  in every `fetch` call — there is no Axios/Ky middleware to copy.
- **vue-ui's `stores/auth.ts` is incompatible.** It checks against a
  hardcoded `CREDENTIALS` whitelist and writes the user to
  `sessionStorage` under `openemr-vue-session`. Replace wholesale.

### FHIR client

There isn't one — there's a single 45-line composable.

- `dashboard/src/composables/useFhirResource.ts` — auto-firing fetch
  primitive returning `{ status, data, error, refetch }`.
  ```ts
  const res = await fetch(path, {
    credentials: 'same-origin',
    headers: { Accept: 'application/fhir+json' },
  })
  ```
  No 401-retry, no token refresh, no interceptor stack — by design,
  because the BFF cookie carries auth and the sidecar handles refresh
  server-side.
- **Type strategy**: `@types/fhir` (`fhir4.Patient`, `fhir4.Bundle`,
  `fhir4.AllergyIntolerance`, `fhir4.Observation`). Each card writes its
  own `is{Resource}` type guard inline (see
  `dashboard/src/components/AllergiesCard.vue:29-36` for the pattern) and
  projects FHIR shape → row view-model in a `computed`. **No adapter
  layer** — view components touch FHIR directly.
- **vue-ui's `api/mock.ts` is a parallel-universe shape.** Its `Patient`
  has `firstName/lastName/dob/sex/phone/address` flat fields; FHIR's
  `Patient.name` is an array of `HumanName` objects with `use` discriminators.
  The seven vue-ui screens consume the mock shape directly — there is no
  adapter, so swapping data sources requires either rewriting every
  consuming screen or building a FHIR→mock adapter (logged as Tier 1 in
  `vue-ui/NEXT-SESSION.md`).

### AgentForge sidecar bridge

- `dashboard/src/composables/useAgentTurn.ts` (80 lines) — `POST
  /api/agent/turn` with `{message, patient_uuid, session_id}`. **Buffered
  JSON response, not SSE** (the legacy `/turn` route still has SSE behind
  a flag — see `docs/NEXT-SESSION.md` "Bridge things deferred"). No
  streaming reducer in the dashboard yet.
- `dashboard/src/stores/agentDrawer.ts` (197 lines) — `Pinia` store with
  reactive `Record<scope, AgentMessage[]>`, three modes (chart/intake/research),
  patient-context conflict policy (Switch/Stay/Fresh resolution).
  `currentScopeId` is `chart:<pid>` / `intake:<docId>` / `research:global`.
- `dashboard/src/components/AgentDrawer.vue` — drawer shell that owns
  layout + send button. Sits at App.vue root (top-level, not per-chart),
  matches the panel-placement decision in memory.
- `dashboard/src/components/PatientContextConflictOverlay.vue` — the
  hard-interrupt "you're switching patients mid-conversation" overlay.
- **Citations are not modeled** in the dashboard yet. The agent's reply
  is a plain `string`, set into `addAssistantTurn(reply)` — no citation
  pills, no overlay, no tab-switch. **Task 38.11 is the missing piece.**
- vue-ui has a richer agent surface — `Citation`, `ChatMessage`,
  `Conversation`, `CitationPill`, `CitationsPane`, `HistoryPane` — but
  fully canned (no real LLM call). The data model is plausible to
  port back to dashboard-port.

### Patient UUID → pid resolver (commit `b3696d421`)

- **Sidecar:** `sidecar/src/agentforge/dashboard_auth/openemr_patient_pid.py`
  — async fetcher to OpenEMR `/internal/patient_pid.php`; per-session
  cache.
- **PHP module:** `interface/modules/custom_modules/oe-module-agentforge/src/.../PatientPidRepository.php`
  + `public/internal/patient_pid.php` entry point. Lookup-purpose JWT
  authed (signature + issuer + exp only).
- **Dashboard side:** `useAgentTurn.send()` passes `patient_uuid` (the
  hyphenated FHIR resource id from `/patient/:pid`); the BFF resolves it
  to integer `patient_data.pid` server-side before minting the agent JWT.
- **Affects vue-ui port?** No — the resolver is server-side. The vue-ui
  port only needs to send `patient_uuid` in the same request shape.

### UNHEX SQL boundary fix (commit `04e6c2611`)

- Backend-only. `users.uuid` and `patient_data.uuid` are `BINARY(16)`,
  but FHIR ids and OIDC `fhirUser` claims are hyphenated strings. Fix
  is `WHERE uuid = UNHEX(REPLACE(?, "-", ""))` in two PHP repositories.
- **Affects vue-ui port?** No. Pure PHP. The frontend swap doesn't trip
  on this.

### Other reusables

- `dashboard/src/components/ClinicalCard.vue` — collapsible card shell
  with loading/empty/error slots. Every card composes against it.
- `dashboard/src/utils/formatDate.ts` — FHIR-date formatting helpers.
- `dashboard/src/components/PatientHeader.vue` — patient banner from a
  `fhir4.Patient`.

---

## 3. OpenEMR module / asset story

**This is the big unknown the brief flagged — and it is genuinely
unresolved on both branches.** Findings:

- **There is no production deployment of `dashboard/dist/` yet.**
  Taskmaster `T38.14` ("Deploy new dashboard to droplet") is **pending**
  with details:
  > Build Vue app (npm run build → dashboard/dist/). Serve as static SPA
  > from droplet — options: (a) extend agentforge-sidecar FastAPI with
  > a StaticFiles mount on /, (b) add an nginx container, (c) add an
  > Apache site to the existing openemr container.
- The current droplet (`docs/DEPLOYMENT.md`) runs **5 containers**:
  openemr, mysql, phpmyadmin, agentforge-sidecar, agentforge-redis.
  None of them serve the new Vue dashboard. Today's demo URL
  (`https://<droplet>:9300/`) lands on classic OpenEMR; the
  AgentForge integration is the **legacy in-chart panel** wired by
  `interface/modules/custom_modules/oe-module-agentforge/public/js/agent_panel.js`
  (661 lines) + `citation_overlay.js` (230 lines). Those are the
  files Task 24 (citation overlay) shipped in.
- `sidecar/src/agentforge/main.py` does **not** mount `StaticFiles`.
  The dashboard is dev-only today: `npm run dev` on port 5173 with a
  Vite proxy onto the sidecar at `localhost:8000`
  (`dashboard/vite.config.ts:36-45`).
- OAuth client registration in OpenEMR pins
  `redirect_uris=["http://localhost:5173/auth/callback"]`. Production
  cutover requires re-registering the client with the production
  origin and updating sidecar env vars
  (`DASHBOARD_OAUTH_REDIRECT_URI`, `DASHBOARD_APP_URL`).
- **No iframe story.** No `interface/modules/custom_modules/`
  registration of the Vue dashboard. The dashboard is a **separate
  SPA** that talks to OpenEMR through the sidecar BFF — it does
  **not** live inside OpenEMR's UI shell. The OpenEMR module is
  AgentForge-only.

**To swap `dashboard/dist/` → `vue-ui/dist/`:** the swap itself is just
a directory copy (or a Vite output-dir rename). What it requires:

1. Pick (a)/(b)/(c) above and implement it (T38.14 work). Single config
   line if (a) — `app.mount("/", StaticFiles(directory="static", html=True))`
   in `sidecar/src/agentforge/main.py`.
2. Then the swap is one line: `rsync -a vue-ui/dist/ /opt/.../static/`
   instead of `dashboard/dist/`.

The asset-serving story is the same blocker for both frontends. **It is
not a vue-ui-specific risk.** But it does mean shipping vue-ui requires
solving T38.14 *and* re-wiring vue-ui's auth/data layer in the same
window — a much bigger surface than shipping the dashboard-port build.

---

## 4. Build & dev workflow on dashboard-port

- `dashboard/vite.config.ts:36-45` — Vite proxies `/auth/*` and `/api/*`
  to `VITE_SIDECAR_BASE` (default `http://localhost:8000`). That's how
  the SPA is "same-origin" with the sidecar in dev, so the HttpOnly
  session cookie rides correctly on FHIR + agent calls.
- Env vars: `VITE_SIDECAR_BASE` is the only one. `.env.example` is
  committed; `.env.development.local` is the local override (denied by
  read permissions, so I haven't inspected its contents — the
  variable name is the only contract that matters).
- **CORS is not a problem in dev** because the proxy makes everything
  same-origin; in production (T38.14) the dashboard is served *from*
  the sidecar host, so still same-origin.
- vue-ui's `vite.config.ts` has no proxy, no env vars, no auth
  surface — pure SPA serving on `127.0.0.1:5174`. Adding the dashboard's
  proxy block is one block of copy-paste.

---

## 5. AgentForge specifics

### Drawer placement
- Dashboard-port: **top-level**, mounted at `App.vue`, persists across
  route changes. Matches the `project_panel_placement` memory decision
  (per-chart embed = "dangerously wrong").
- vue-ui: **top-level**, mounted via `<Teleport to="#drawer-root">` in
  `AppShell.vue`. Same placement.
- The legacy in-chart panel (`agent_panel.js` in the OpenEMR module) is
  what's deployed on the droplet. Both Vue surfaces are correct; the
  legacy one is what users see today.

### Citations / Task 24
- **Backend wiring landed** in `feat/w2-task-24-citation-overlay` (merged
  per `docs/NEXT-SESSION.md` !36). The PHP module exposes citation
  data via the agent response.
- **Dashboard frontend has NOT shipped the overlay** (T38.11 is
  pending). The dashboard's `addAssistantTurn(reply)` accepts a string,
  not a structured `{text, citations[]}`.
- **Legacy** `interface/modules/.../public/js/citation_overlay.js` is
  the shipped renderer — it's what would have to be ported, in shape,
  to either Vue surface.
- **vue-ui** has a `Citation` model + `CitationPill` + `CitationsPane`
  ready (canned data), so the component shape is closer in vue-ui than
  in dashboard-port.

---

## 6. PHI / localStorage / sessionStorage

`grep -l 'localStorage|sessionStorage|IndexedDB'` on `dashboard/src/`
returns **zero hits.** Every test reference is a stub in
`__tests__/`. Auth runs on HttpOnly cookies; agent state is in-memory
Pinia.

`vue-ui/src/`:
- `composables/useEncounterDraft.ts` — writes a full SOAP draft
  (chief complaint, HPI, exam, assessment) to `localStorage` keyed
  `encounter-draft.<id>`. **PHI in plaintext, browser-side.** Flagged
  in `vue-ui/NEXT-SESSION.md` as "PHI risk before prod".
- `stores/auth.ts` — persists `User` (id, username, fullName, role)
  to `sessionStorage` under `openemr-vue-session`.
- `stores/agentforge.ts` — persists conversations + canned reply text
  to `localStorage` under `agentforge-conversations`.
- `stores/preferences.ts` — UI preferences only.
- `stores/ui.ts` / `views/patients/PatientList.vue` — density/UI
  prefs only.

The PHI exposure (encounter drafts + agent conversations) is a
**hard remediation gate** before vue-ui can touch real data, even
beyond the auth/data port itself.

---

## 7. Deploy artifacts

- `DEPLOY.md` — pre-deploy gates, smoke tests, rollback. Mentions only
  the AgentForge sidecar + PHP module (no dashboard).
- `docs/DEPLOYMENT.md` — droplet inventory, secrets layout. Mentions
  only the 5 containers above.
- `scripts/deploy-droplet.sh` — `module` / `sidecar` / `check` / `logs`
  subcommands. **No `dashboard` subcommand.**
- **No CI for the dashboard.** `.gitlab-ci.yml` (root) does not exercise
  `dashboard/` or `vue-ui/`. Both have local `npm run type-check` /
  `npm run build` only.

The cutover story for swapping frontends — once T38.14 lands — is
"point the static-file root at a different `dist/`". Trivially
reversible if T38.14 picks the FastAPI StaticFiles option and the path
is a single config var.

---

## 8. Risk-adjusted migration plan

Three plans, ranked by feasibility against the Sun-noon defense.

### Plan A — ship dashboard-port (recommended)
**Estimate: 1–2 days. High confidence.**

1. Finish T38.11 (citation overlay re-port from `citation_overlay.js`
   into a Vue component reading citations from
   `useAgentTurn` response). Touch:
   `dashboard/src/composables/useAgentTurn.ts` (extend response shape),
   `dashboard/src/components/AgentDrawer.vue`, new
   `dashboard/src/components/CitationOverlay.vue`. ~6h with parallel
   agents.
2. Finish T38.14 (asset story). Pick option (a) — FastAPI
   `StaticFiles`. Touch: `sidecar/src/agentforge/main.py`,
   `scripts/deploy-droplet.sh` (add `dashboard` subcommand or fold
   into `all`). Re-register OAuth client at production origin. ~3h.
3. (Optional) Lift vue-ui's design tokens (`tailwind.config.ts`,
   `assets/main.css`) into dashboard-port to close the visual gap.
   Pure additive CSS work. ~3h.

**Wall clock with one human + 2 parallel agents: ~1 day.**

### Plan B — swap to vue-ui
**Estimate: ~2 weeks. Will not fit.**

The Tier 1 list in `vue-ui/NEXT-SESSION.md` is honest: replace `auth.ts`
with the BFF flow, build a FHIR adapter for `api/mock.ts` (the mock
shape is incompatible with the FHIR shape), replace canned agent
streaming with real `/api/agent/turn`, remediate PHI in localStorage,
re-wire dev proxy + cookies, and solve T38.14. Each item independently
is 1–3 days. Cumulative: not under the Sun deadline.

### Plan C — hybrid: ship dashboard-port, port vue-ui's design forward
**Estimate: 1.5 days. Recommended posture if Plan A finishes early.**

After Plan A is green, lift specific vue-ui pieces into dashboard-port
in dependency order:

1. `tailwind.config.ts` + `assets/main.css` design tokens (additive;
   doesn't break Bootstrap classes already in use).
2. `components/agentforge/CitationPill.vue` shape — reuse as the
   dashboard's citation rendering (after T38.11).
3. `components/patients/dashboard/VitalsStrip.vue` + `Sparkline.vue` —
   add a vitals row to `PatientDashboardView.vue`. Requires sourcing
   vitals from `Observation?category=vital-signs`; the component math
   is the reusable part.
4. `components/patients/list/PatientFilterBar.vue` — slot into the
   existing patient picker; the filter state lives in a local `ref`,
   the FHIR query stays the same.

Items 1, 2, 3, 4 are independent — full agent parallelism. ~6h total.

---

## 9. Open questions / unknowns

1. **`.env.development.local`** is denied by my read permissions; I
   haven't seen its actual values. Only `VITE_SIDECAR_BASE` matters
   per `vite.config.ts`, but if there are extra vars I missed, they
   matter for any port.
2. **T38.14 picking criterion.** I haven't seen a decision between
   options (a) FastAPI StaticFiles, (b) nginx, (c) Apache site. The
   memory `project_droplet_containers` says "5 containers, no more" —
   pushes toward (a). Recommend confirming before Plan A step 2.
3. **OAuth client registration** for production origin. The dev-easy
   registration in `PATIENT_DASHBOARD_MIGRATION.md:184-200` pins
   `localhost:5173` — does the droplet have a separate registered
   client already? Without checking the OpenEMR `oauth_clients` table
   I can't tell.
4. **Whether vue-ui's mock "Encounters" / vitals strip data shape can
   be sourced from FHIR** at parity. `Observation?category=vital-signs`
   exists; `Encounter` exists; but per `project_dashboard_data_gaps`
   memory, several Synthea-imported fields are sparse in dev-easy.
5. **Token refresh.** Both frontends defer it. The dashboard's auth
   store does not refresh; users re-sign-in when the access_token
   expires. Demo-acceptable; not production.
6. **Whether vue-ui's `BaseTable.vue` generic-export fix (Wave 3) is
   the only file that escaped the "shared, do not modify" lock.** If
   other shared files moved, the lift in Plan C step 1 may need
   spot-checks beyond what I've inventoried.

---

## 10. Citations (file paths)

Most-cited reusables in dashboard-port (the things vue-ui-or-future-v2
should crib):

- `<repo>/dashboard/src/stores/auth.ts`
- `<repo>/dashboard/src/stores/agentDrawer.ts`
- `<repo>/dashboard/src/composables/useFhirResource.ts`
- `<repo>/dashboard/src/composables/useAgentTurn.ts`
- `<repo>/dashboard/src/components/AgentDrawer.vue`
- `<repo>/dashboard/src/components/PatientContextConflictOverlay.vue`
- `<repo>/dashboard/src/router/index.ts`
- `<repo>/dashboard/vite.config.ts`
- `<repo>/sidecar/src/agentforge/dashboard_auth/routes.py`
- `<repo>/sidecar/src/agentforge/dashboard_auth/turn_route.py`
- `<repo>/docs/adr/0001-dashboard-auth-bridging.md`
- `<repo>/PATIENT_DASHBOARD_MIGRATION.md`

Most-reusable visual / UX in vue-ui (the things to lift forward):

- `vue-ui/tailwind.config.ts` (design tokens — clinical teal, surface/ink/line)
- `vue-ui/src/assets/main.css` (CSS vars, light/dark)
- `vue-ui/src/components/agentforge/CitationPill.vue` + `CitationsPane.vue`
- `vue-ui/src/components/patients/dashboard/VitalsStrip.vue`,
  `VitalCard.vue`, `Sparkline.vue`
- `vue-ui/src/components/patients/list/PatientFilterBar.vue`,
  `PatientDensityToggle.vue`
- `vue-ui/AGENT-CONTRACT.md` (ownership-map pattern is reusable for any
  future parallel-agent build-out)
