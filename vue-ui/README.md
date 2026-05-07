# OpenEMR — Vue Edition

A greenfield Vue 3 + TypeScript rewrite of the OpenEMR clinical UI, built
in parallel waves of independent agents. This directory is **independent**
of the existing `dashboard/` Vue port one level up — they don't share files
or build tooling. Both run side-by-side until one is chosen.

Vue UI dev runs on **port 5174** so it doesn't collide with `dashboard/`
(5173).

## Stack

- **Vue 3.5** with Composition API and `<script setup lang="ts">`
- **vue-router 4.4** (nested routes; `AppShell` wraps authenticated routes)
- **Pinia 2** for state (`useUiStore`, `useAuthStore`, `useAgentForgeStore`,
  `usePreferencesStore`)
- **TypeScript 5.6** in strict mode (no `any`, no `@ts-ignore`)
- **Tailwind CSS 3.4** with design tokens (clinical teal primary, semantic
  success/warning/danger/info, light + dark via the `class` strategy)
- **Vite 6** dev server / production bundler
- **Vitest 2** for unit tests (runner only — see Known limitations)

## Running locally

```bash
cd vue-ui
npm install
npm run dev        # http://localhost:5174 (or 127.0.0.1:5174)
```

Other scripts:

```bash
npm run build        # type-check + production build (vite build)
npm run type-check   # vue-tsc --build only
npm run preview      # serve the production build
npm run test         # vitest (config in vitest.config.ts)
```

The build pipeline is clean: `npm run build` runs vue-tsc and vite build in
parallel; both must succeed.

## Route map

All routes are defined in [`src/router/index.ts`](./src/router/index.ts).

| Path | Name | Component | Auth |
|---|---|---|---|
| `/login` | `login` | `views/auth/LoginView.vue` | public |
| `/` | (redirects) | → `dashboard` | required |
| `/dashboard` | `dashboard` | `views/_placeholders/DashboardHome.vue` | required |
| `/patients` | `patients` | `views/patients/PatientList.vue` | required |
| `/patients/:id` | `patient-dashboard` | `views/patients/PatientDashboard.vue` | required |
| `/calendar` | `calendar` | `views/calendar/CalendarView.vue` | required |
| `/encounters/:id` | `encounter` | `views/encounters/EncounterEditor.vue` | required |
| `/settings` | `settings` | `views/settings/SettingsView.vue` | required |
| `/:catchAll(.*)*` | `not-found` | `views/_placeholders/NotFound.vue` | public |

Authenticated routes are nested under a single `AppShell` parent so the
sidebar/topbar/drawer mount once and the inner `<router-view />` swaps.

## Screen ownership

Built in parallel by Wave-2 agents (compressed from
[`AGENT-CONTRACT.md`](./AGENT-CONTRACT.md)).

| Wave | Area | Owns |
|---|---|---|
| 2a | Auth | `src/views/auth/`, `src/stores/auth.ts` |
| 2b | Patient list | `src/views/patients/PatientList.vue`, `src/components/patients/list/` |
| 2c | Patient dashboard | `src/views/patients/PatientDashboard.vue`, `src/components/patients/dashboard/` |
| 2d | Calendar | `src/views/calendar/`, `src/components/calendar/` |
| 2e | Encounter / visit note | `src/views/encounters/`, `src/components/encounter/` |
| 2f | AgentForge drawer | `src/components/agentforge/`, `src/stores/agentforge.ts` (mounted via `<Teleport to="#drawer-root">`) |
| 2g | Settings & preferences | `src/views/settings/`, `src/stores/preferences.ts` |

Shared scaffold (locked after Wave 1): `tailwind.config.ts`,
`vite.config.ts`, `package.json`, `src/api/mock.ts`,
`src/components/ui/Base*`, `src/layouts/AppShell.vue`, `index.html`,
`src/main.ts`.

## Layout

```
vue-ui/
├── index.html
├── package.json
├── vite.config.ts            # vite-only (build/dev)
├── vitest.config.ts          # vitest-only (test runner)
├── tsconfig*.json
├── tailwind.config.ts
├── postcss.config.js
├── env.d.ts
├── AGENT-CONTRACT.md
├── INTEGRATION-NOTES.md      # known issues / deviations from Wave 3
└── src/
    ├── main.ts               # Vue + Pinia + router + CSS bootstrap
    ├── App.vue               # <router-view />
    ├── api/mock.ts           # typed mock API + Synthea-style seed data
    ├── assets/main.css       # Tailwind directives + CSS variables
    ├── components/
    │   ├── ui/               # Base* design-system kit (BaseButton, BaseCard,
    │   │                     # BaseInput, BaseModal, BaseTable, BaseBadge,
    │   │                     # BaseSpinner, BaseAvatar, BaseEmptyState)
    │   ├── patients/         # list & dashboard cards
    │   ├── calendar/         # day/week/month chrome
    │   ├── encounter/        # note editor blocks
    │   └── agentforge/       # Co-Pilot drawer + chat
    ├── composables/          # usePatient, useRelativeTime, etc.
    ├── layouts/AppShell.vue  # sidebar + topbar + main + drawer mount
    ├── lib/                  # framework-agnostic helpers
    ├── router/index.ts       # routes + auth guard
    ├── stores/               # Pinia stores (ui, auth, preferences,
    │                         # agentforge, …)
    └── views/
        ├── _placeholders/    # Wave-1 placeholders still used by some routes
        ├── auth/             # 2a
        ├── patients/         # 2b, 2c
        ├── calendar/         # 2d
        ├── encounters/       # 2e
        └── settings/         # 2g (sections/ subdir for tab content)
```

## Auth in the scaffold

`useAuthStore()` provides `isAuthenticated` and gates routes via the
router's `beforeEach`. Wave 2a replaced the Wave-1 stub with a real OAuth2
flow that hits the FHIR endpoint exposed by the existing `dashboard/` port.

## Mock data

`src/api/mock.ts` ships with 12 Synthea-style patients and seed data for
problems, medications, allergies, vitals, encounters, and labs. Every
function returns a Promise with a 150–300 ms artificial delay so loading
states are realistic. The surface is stable — Wave 3+ will swap the
implementation with a real FHIR client without changing the public API.

## Known limitations

See [`INTEGRATION-NOTES.md`](./INTEGRATION-NOTES.md) for the running list
of integration deviations and follow-ups discovered during the Wave-3
smoke build.
