# Wave 3 — Integration & Smoke Build Notes

Running log of integration findings during the Wave 3 smoke build.

## Build status (Wave 3)

- `npm install`: clean (185 packages, 5 moderate audit warnings — none blocking).
- `npm run type-check`: passes (vue-tsc --build --force, exit 0).
- `npm run build`: passes (vite 6.4.2, 165 modules, ~1s, ~141 kB main chunk).
- `npm run dev`: starts on `http://127.0.0.1:5174/` and serves the
  expected HTML shell. Smoke-tested via node fetch.

## Fixes applied during Wave 3

### `vite.config.ts` — `test` block belonged in vitest config

`vite`'s `defineConfig` does not accept a `test` field. Wave 1 had the
runner config inline. Wave 3 split it into a separate `vitest.config.ts`
so vue-tsc accepts both files cleanly.

### `vitest.config.ts` — runner-only, no plugins

Vitest 2.x bundles its own copy of vite 5, which produces incompatible
`Plugin<Api>` types when imported alongside the project's vite 6 + the
project's `@vitejs/plugin-vue`. To keep type-check clean without
upgrading vitest in scaffold (locked file), `vitest.config.ts` configures
only the test runner (jsdom, globals, include patterns). When the project
upgrades vitest to 3.x (which supports vite 6), reintroduce
`@vitejs/plugin-vue` and the `@` alias in the vitest config.

### `src/components/ui/BaseTable.vue` — exported generic Props

vue-tsc reported `TS4082: Default export uses private name 'Props'`
because the component is generic (`generic="TRow extends ..."`) and the
inferred default-export type leaked the private `Props` interface. Fixed
by renaming the interface to `BaseTableProps<TRowType>` and exporting it.
This is a minimal change and does not break the component's call sites
(props are positional via `defineProps`).

### `src/components/patients/dashboard/EncountersCard.vue` — unused import

Removed unused `relativeTime` import (TS6133).

## Environmental notes

### macOS + Node 24 + Vite 6 fsevents

On the first dev run, vite crashed with `TypeError: fsevents.watch is not
a function`. `fsevents` is an optional dep on macOS and the initial
install did not pick up its native binding. A subsequent `npm install`
(no flags) resolved `fsevents@2.3.3` and the dev server starts cleanly.

If a fresh clone hits the same issue, run `npm install` once more — do
**not** `rm -rf node_modules`; let the optional-dep resolution settle.

## Open follow-ups

- Vitest is configured but no tests have been integrated through the
  pipeline yet. When vitest is upgraded to ^3 (compatible with vite 6),
  re-add the vue plugin and `@` alias in `vitest.config.ts`.
- `npm audit` reports 5 moderate vulnerabilities (all transitive through
  build tooling). Triage in a future wave; none affect runtime.
- The `_placeholders/DashboardHome.vue` is still wired for the `/dashboard`
  route — there is no Wave-2 owner for the global landing page yet.
