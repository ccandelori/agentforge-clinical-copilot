# Wave 2 Agent Contract

This file defines who owns what in the `vue-ui/` tree so that several agents
can build screens in parallel without colliding.

If you are a Wave 2 agent, **only modify files inside your assigned
directories**. Anything in the "shared (do not modify)" list is locked by
Wave 1 — file an issue if it needs to change.

## Ownership map

| Agent | Owned paths | Notes |
|---|---|---|
| **2a — Auth** | `src/views/auth/` (new), `src/stores/auth.ts` (replace), `src/router/index.ts` (only the `/login` route + `beforeEach` real-auth wiring) | Replace placeholder `src/views/_placeholders/LoginView.vue` import in `router/index.ts` with `src/views/auth/LoginView.vue`. |
| **2b — Patient List** | `src/views/patients/PatientList.vue`, `src/components/patients/list/` | Replace placeholder `src/views/_placeholders/PatientList.vue` import. |
| **2c — Patient Dashboard** | `src/views/patients/PatientDashboard.vue`, `src/components/patients/dashboard/` | Replace placeholder `src/views/_placeholders/PatientDashboard.vue` import. |
| **2d — Calendar** | `src/views/calendar/`, `src/components/calendar/` | Replace placeholder `CalendarView.vue` import. |
| **2e — Encounter** | `src/views/encounters/`, `src/components/encounter/` | Replace placeholder `EncounterEditor.vue` import. |
| **2f — AgentForge Drawer** | `src/components/agentforge/`, `src/stores/agentforge.ts` (new) | Mount drawer via `<Teleport to="#drawer-root">`. Read open state from `useUiStore().agentDrawerOpen`. |
| **2g — Settings** | `src/views/settings/`, `src/stores/preferences.ts` (new) | Replace placeholder `SettingsView.vue` import. |

## Replacing placeholders

Each placeholder under `src/views/_placeholders/` is referenced by name from
`src/router/index.ts`. To swap in your real screen:

1. Create your view file in your owned `src/views/<area>/` directory.
2. Update **only your route's** `component:` import in `src/router/index.ts`
   to point to your new file.
3. Leave the placeholder file in place if other routes still use it; otherwise
   delete it.

## Shared (DO NOT modify after Wave 1 scaffold)

- `tailwind.config.ts`
- `vite.config.ts`
- `package.json`
- `src/api/mock.ts`
- `src/components/ui/*` (the `Base*` component kit)
- `src/layouts/AppShell.vue`
- `index.html`
- `src/main.ts`

If you need a new shared component, **add a new file** in `src/components/ui/`
with a clearly distinct name. Do not edit the existing `Base*` components.

## Conventions for all Wave 2 agents

- TypeScript strict, no `any`.
- `<script setup lang="ts">` only — no Options API.
- Tailwind utility classes for styling. No SCSS or CSS modules.
- Use the design tokens (`bg-surface`, `text-ink`, `text-ink-muted`,
  `border-line`, `bg-primary-*`, `text-success-*`, etc.). Do not hard-code
  colors.
- Pull data from `src/api/mock.ts` only. Wave 3 will swap this for the real
  FHIR client.
- Use `useUiStore()` for sidebar/theme/drawer state — do not invent your own.
- Use `useAuthStore()` for the current user; do not read `localStorage`
  directly.
- Add unit tests as `*.spec.ts` next to the component when behaviour is
  non-trivial.
