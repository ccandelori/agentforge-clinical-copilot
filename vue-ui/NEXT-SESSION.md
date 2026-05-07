# `vue-ui/` — Next-Session Resume Doc

> Branch: `feat/vue-ui-rewrite` (off `origin/main`). Not pushed.
> Status: complete demo, fully mocked, type-checks + builds + serves clean.
> Date paused: 2026-05-07.

## What this is

A greenfield Vue 3 rewrite of the OpenEMR clinical UI, built in one session by
seven parallel Wave-2 agents on top of a Wave-1 scaffold. Lives entirely under
`/vue-ui/` so it does **not** clobber the W2 patient-dashboard port on
`feat/dashboard-port`.

This is a side bet, not the W2 deliverable. The W2 deliverable is
`feat/dashboard-port`, which already has real OAuth2, FHIR, and the
`/api/agent/turn` sidecar bridge wired up.

## Why we paused

The W2 deadline is 2026-05-10 noon. Wiring this branch to real backends
(OAuth2 PKCE, FHIR adapter, sidecar streaming, OpenEMR module integration) is
~1–2 weeks of careful work — not feasible inside the deadline window. Better
to lift the **visual design** from this branch into `feat/dashboard-port` and
keep this as a v2 reference.

## What's actually built

- **Stack**: Vue 3.5 + Vite 6 + TS strict + Pinia + Vue Router 4 + Tailwind 3
- **Port**: 5174 (so it doesn't collide with the existing dashboard on 5173)
- **Bundle**: ~141 kB main / 53 kB gzip, lazy-loaded route chunks
- **Tests**: zero (vitest is wired but no tests written)
- **Auth**: hardcoded whitelist (`admin`/`pass`, `dr_smith`/`pass`, `nurse_jane`/`pass`), sessionStorage
- **Data**: 100% mock, deterministic, 12 Synthea-style patients in `src/api/mock.ts`
- **AgentForge**: canned responses + setTimeout typewriter — no real LLM call

### Screens

| Wave | Owner | Screen | Notes |
|---|---|---|---|
| 1 | scaffold | App shell, design tokens, Base* kit, mock API, router | locked from Wave 2 |
| 2a | auth | `LoginView`, real Pinia auth store | redirect-aware guard |
| 2b | patients-list | `PatientList` w/ search, filters, sort, pagination, density toggle | hand-rolled table (BaseTable lacks header-click) |
| 2c | patients-dashboard | `PatientDashboard` w/ sticky header, sparkline vitals strip, 5 cards | usePatient composable parallelizes 7 fetches |
| 2d | calendar | `CalendarView` w/ Day/Week/Month, new-appointment modal | no date library; pure-Date helpers in `src/lib/dates.ts` |
| 2e | encounters | `EncounterEditor` SOAP layout, BMI calc, ICD typeahead, sign+finalize gate | draft auto-saves to localStorage (PHI risk before prod) |
| 2f | agentforge | top-level `AgentDrawer`, three tabs (Chat / Citations / History), typewriter | canned replies; mounted from `App.vue` via Teleport to `#drawer-root` |
| 2g | settings | `SettingsView` w/ theme, accent, font scale, density, keybindings, about | preferences persist to localStorage |

### Key shared files (DO NOT modify lightly)

- `src/api/mock.ts` — type contracts the seven screens depend on
- `src/components/ui/Base*.vue` — the component kit
- `src/layouts/AppShell.vue` — sidebar + topbar + teleport target
- `tailwind.config.ts` — design tokens (clinical teal, surface/ink/line, dark via class)
- `src/assets/main.css` — CSS variables for both light and dark
- `vite.config.ts`, `tsconfig*.json`
- `AGENT-CONTRACT.md` — full ownership map

## How to run

```bash
cd vue-ui
npm install   # ~1 min, fsevents may skip on first install — second install fixes it
npm run dev   # http://127.0.0.1:5174 — login admin/pass
```

Other scripts: `npm run type-check`, `npm run build`, `npm run preview`.

## Open items (from `INTEGRATION-NOTES.md`)

1. Vitest pinned at `^2.1.6` to avoid the vite 6 / vitest 3 plugin mismatch — upgrade post-deadline.
2. 5 transitive moderate `npm audit` warnings in build tooling, none runtime.
3. fsevents skip on macOS+Node 24 first install — second install resolves it.
4. `_placeholders/DashboardHome.vue` is still wired for `/dashboard` — no Wave-2 owner for the global landing page.
5. `BaseTable.vue` was updated in Wave 3 to fix a TS4082 generic-export issue (renamed `interface Props` → `export interface BaseTableProps<TRowType>`). Single concession to the shared-files lock; documented in INTEGRATION-NOTES.

## What it would take to make this real

(Not in scope for this branch right now. Notes for future-self.)

### Tier 1 — make it talk to the actual backend (~2 weeks, 1 dev)

- **`src/stores/auth.ts`** → replace the hardcoded whitelist with the OAuth2
  PKCE flow used by `feat/dashboard-port`. Token refresh, session expiry,
  redirect handling, state param, the works.
- **`src/api/mock.ts`** → split into a FHIR client (`fhirClient.ts` over
  `/apis/default/fhir/R4/`) and an adapter layer that converts FHIR
  resources to the simple types the seven screens expect. The mock's
  `Patient` type does NOT match `fhir.Patient` — adapter is non-trivial.
  Cross-reference dev-easy data gaps logged in
  `~/.claude/projects/-Users-sheep-Desktop-Gauntlet-openemr/memory/project_dashboard_data_gaps.md`.
- **`src/stores/agentforge.ts`** → replace canned replies + setTimeout
  typewriter with calls to `/api/agent/turn` (SSE or chunked streaming).
  Citations come from the response, no longer from the mock.
- **PHI in localStorage** → `useEncounterDraft` writes drafts to localStorage
  keyed by encounter ID. That's PHI sitting unencrypted in the browser.
  Either move drafts server-side or encrypt + scope to session.
- **Dev proxy + cookies** → `vite.config.ts` needs a `/api` and
  `/apis/default/fhir/` proxy to the OpenEMR + sidecar containers, with
  cookies passed through. CORS / OAuth redirect handling.
- **Build pipeline** → integrate into the OpenEMR module asset story so a
  built `dist/` lands where OpenEMR can serve it.

### Tier 2 — make it usable by a clinical user (a quarter)

Tests, a11y audit (WCAG 2.1 AA), real catalogs (SNOMED/RxNorm/LOINC over
the inline 30-ICD / 20-lab stubs), RBAC, server-side pagination, print views,
i18n, real audit logging hooks, error tracking, idle-timeout handling.

### Tier 3 — feature parity with OpenEMR

Years. Hundreds of screens (billing, claims, RCM, eRx, faxing, immunization
registries, reporting, scheduler templates, …). Out of scope.

## Pointers

- `vue-ui/README.md` — quick overview + run instructions
- `vue-ui/AGENT-CONTRACT.md` — full Wave-2 ownership map
- `vue-ui/INTEGRATION-NOTES.md` — Wave 3 fixes + open issues
- Wave-2 agent transcripts (if you want to know exactly what each agent did):
  `/private/tmp/claude-501/-Users-sheep-Desktop-Gauntlet-openemr/928ae274-a50e-4308-8600-cf5abc2ca4e2/tasks/`

## How to come back

```bash
git checkout feat/vue-ui-rewrite
cd vue-ui && npm install && npm run dev
```

If lifting design pieces over to `feat/dashboard-port`, work in that direction
(this → that), not the reverse. Source-of-truth visual tokens for the lift
are in `vue-ui/src/assets/main.css` + `vue-ui/tailwind.config.ts`.
