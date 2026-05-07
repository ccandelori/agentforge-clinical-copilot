# AgentForge Patient Dashboard (Vue 3)

Modern frontend port of the OpenEMR patient dashboard for the W2 Surprise
Challenge. Backend untouched — this app consumes OpenEMR's REST + FHIR R4
API as the data layer, authenticates via OAuth2 / OpenID Connect, and
hosts the AgentForge Co-Pilot drawer as a first-class component.

The full defense lives at [`PATIENT_DASHBOARD_MIGRATION.md`](../PATIENT_DASHBOARD_MIGRATION.md)
at the repo root; this README is the developer-side entry point.

## Stack

- Vue 3 + Vite + TypeScript
- Vue Router (auth-gated routes)
- Pinia (auth, current-patient, conversation stores)
- BootstrapVueNext + Bootstrap 5 + Bootstrap Icons (visual parity with the May 2025 OpenEMR redesign)
- `oidc-client-ts` (OAuth2 / OIDC client)
- `@types/fhir` (typed FHIR R4 resources)
- `pdfjs-dist` 5.7.284 (citation overlay PDF rendering — re-port of Task 24)
- Vitest + jsdom (unit tests)

## Setup

```sh
cp .env.example .env   # fill in VITE_OPENEMR_BASE_URL + OAuth client config
npm install
npm run dev            # http://localhost:5173
```

## Scripts

| Command | Purpose |
|---|---|
| `npm run dev` | Vite dev server with HMR |
| `npm run build` | type-check + production build → `dist/` |
| `npm run preview` | serve the production build locally |
| `npm run type-check` | `vue-tsc --build` (no emit) |
| `npm run test:unit` | Vitest in jsdom |
| `npm run lint` | oxlint + ESLint with autofix |
| `npm run format` | Prettier |

## Layout

```
dashboard/
├── src/
│   ├── App.vue                  # Router shell (intentionally thin)
│   ├── main.ts                  # Pinia + Router + BootstrapVueNext + CSS
│   ├── router/                  # Auth-gated routes
│   ├── views/                   # Top-level routes (Login, PatientDashboard, …)
│   ├── components/
│   │   ├── cards/               # Clinical cards (Allergies, Problem List, …)
│   │   └── drawer/              # AgentForge Co-Pilot drawer
│   ├── composables/             # useFhirResource, useAuth, …
│   ├── services/fhir/           # Typed FHIR client per resource
│   ├── stores/                  # Pinia stores
│   └── assets/                  # base.css, main.css
├── .env.example                 # OpenEMR base URL + OAuth client config
├── vite.config.ts
├── vitest.config.ts
└── eslint.config.ts
```

## Task tracking

Implementation is sliced into Taskmaster Task 38 (subtasks 38.1–38.14).
Run `task-master show 38` from the repo root to see the active subtask
list with statuses and dependencies.
