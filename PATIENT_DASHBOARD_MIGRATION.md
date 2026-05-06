# Patient Dashboard Migration — Defense

> Required artifact for W2 Surprise Challenge: *"Port the OpenEMR Patient
> Dashboard to a Modern Framework."* Brief at
> `docs/w2-surprise-challenge.pdf`. Deadline: 2026-05-10 noon.
>
> **Status:** in progress. The migration is being implemented incrementally
> against Taskmaster Task 38 (subtasks 38.1–38.14). This document is updated
> as decisions land in code; sections marked **TBD** are placeholders pending
> the implementation work that justifies them.

## Executive summary

The OpenEMR patient dashboard is a PHP-rendered, server-side surface. Every
collapse, expand, edit, and chart-write today is a server round-trip; rich
interactions (the AgentForge Co-Pilot, citation overlays, optimistic UI) get
grafted on as ad-hoc JavaScript with no shared state model.

This port moves the presentation layer to **Vue 3 + Vite + TypeScript**,
consuming OpenEMR's existing **REST + FHIR R4 API** as the data layer.
**OAuth2 / OpenID Connect** replaces session-cookie auth. **Backend untouched.**

The deliverable is graded on three dimensions, defended below: gain over PHP,
choice of Vue specifically, and the tradeoffs accepted.

---

## Leg 1 — Why move off PHP-rendered

Server-rendered PHP couples every interaction to a round-trip. This caps
the ceiling on:

- **Optimistic UI.** A clinician marks an allergy as resolved → row dims
  immediately, FHIR `PUT /AllergyIntolerance/N` runs in the background,
  rollback on failure. Server-rendered pages can't do this honestly — the
  state lives on the server, not the client.
- **Streaming + real-time.** The AgentForge agent streams tokens back via
  SSE; FHIR Subscriptions can push observation updates. Both need
  client-side state machines that PHP-rendered pages don't host.
- **Composable state.** A drawer that survives tab navigation (the
  AgentForge Co-Pilot, scoped per-patient) requires a single client-side
  state container. PHP-rendered pages re-render their state on every nav.
- **Strong typing across the data boundary.** TypeScript + `@types/fhir`
  catches schema mismatches at compile time, not on a clinician's screen.

The May 2025 OpenEMR redesign already moved to a card-based,
single-scroll Bootstrap 4/5 layout — but the **state** still lives on the
server. This port separates the presentation layer cleanly: the FHIR API
becomes the contract, and any FHIR-compliant backend (HAPI FHIR, Bahmni,
a future OpenEMR core rewrite) can swap in without re-rendering anything.

The headline argument is the **separation itself**, not the framework.
Vue is the specific instance; the architectural win generalizes.

---

## Leg 2 — Why Vue 3 specifically

Honest comparison, framed against *this* port (5 required cards + patient
header + AgentForge drawer + bonus, 3.5-day timeline, parity with the May
2025 Bootstrap 5 design):

| Alternative | Why not |
|---|---|
| **React** | Capable, but more ceremony per component (hook-rule footguns, more decisions per render). For a parity port on a 3.5-day clock, Vue's lower ceremony wins. |
| **Angular** | RxJS + DI + decorators is over-engineered for a card grid. Justified by "enterprise scale," which a parity port doesn't need. |
| **Svelte / SvelteKit** | Compiler-as-framework is genuinely elegant, but the Bootstrap-5-aligned component ecosystem is thinner. Slower to ship parity. |
| **Qwik** | Resumability is real and impressive, and the defense story sharper ("first paint matters when clinicians context-switch fast"). The UI library ecosystem isn't there for clinical card surfaces — Qwik UI is younger; OAuth2 client libs are less mature. Defense weight against shipping risk doesn't pay off on a 3.5-day clock. **Called out explicitly as the road-not-taken.** |
| **HTMX + Alpine** | Returns HTML fragments — still server-rendered. Actively contradicts "move the presentation layer to a better tool." |

Vue brings:

- **Single-file components** (`.vue` = template + script + style co-located).
  Mirrors OpenEMR's "one file = one surface" pattern, minimizing onboarding
  friction for a future maintainer who's been working in the PHP codebase.
- **Reactivity** (`ref` / `computed` / `watch`) is simpler than React hook
  rules — fewer footguns under time pressure.
- **Pinia** is the right state-management size: more structured than vanilla
  refs, not as heavy as Redux. Per-patient conversation scoping in the
  AgentForge drawer maps cleanly to a Pinia store keyed by `patient_id`.
- **Native TypeScript** with strong inference — no per-component type
  ceremony, and FHIR resource types from `@types/fhir` flow through
  components without manual annotation.
- **BootstrapVueNext** gives Bootstrap 5 components in idiomatic Vue —
  direct parity with the May 2025 visual language, no custom card CSS to
  reinvent.
- **Vite** dev iteration is among the fastest in the ecosystem; on a
  3.5-day timeline, iteration speed is load-bearing.

The thread: **lower-ceremony framework + ecosystem alignment with the
existing visual design language = highest probability of feature parity
in 3.5 days.** The defense doesn't claim Vue is *the best* framework; it
claims Vue is the *right* framework for this specific port.

---

## Leg 3 — Tradeoffs explicitly accepted

- **Larger runtime than Svelte / Qwik** (~30KB gzipped). Doesn't matter
  for a logged-in clinical desktop; would matter for embedded / mobile-first.
- **SPA, not SSR.** First-paint is slower than a server-rendered app on
  cold load. Acceptable because clinicians log in once and stay; the cost
  amortizes within minutes of session usage.
- **Less hyped than Qwik in 2026.** Honest. We're trading novelty for
  shipping reliability — and for clinical software, predictability *is* a
  feature, not a hedge. Half-shipped clinical software is worse than no
  port at all.
- **Smaller ecosystem than React.** For our specific needs (OAuth2, FHIR,
  Bootstrap, optionally a charting lib for the lab-results bonus card),
  Vue has battle-tested options. We're not at the edges of what the
  ecosystem supports.
- **Vue Router lazy-route splitting requires explicit `() => import()`**
  syntax (not free per-route the way Next.js / Nuxt is). Acceptable;
  one-line cost per route.

---

## Architecture overview

**TBD** — fills in as 38.1 (scaffold) → 38.14 (deploy) land.

Planned shape:

- `dashboard/src/services/fhir/` — typed FHIR client, one module per
  resource (`patient.ts`, `allergyIntolerance.ts`, etc.). Returns
  strongly-typed `@types/fhir` resources.
- `dashboard/src/stores/` — Pinia stores: `auth.ts` (OAuth2 token state),
  `patient.ts` (current patient context), `conversation.ts`
  (per-patient agent conversations).
- `dashboard/src/views/` — top-level routes (`PatientDashboardView`,
  `LoginView`, `OAuthCallbackView`).
- `dashboard/src/components/cards/` — one component per clinical card,
  composed inside `PatientDashboardView` via a shared `<ClinicalCard>`
  wrapper.
- `dashboard/src/components/drawer/` — AgentForge Co-Pilot drawer
  (right-edge slide-out, three mode tabs, per-patient conversation
  scoping per the panel-design grilling 2026-05-06).

---

## OAuth2 / OpenID Connect integration

**TBD** — lands with subtask 38.2.

OpenEMR exposes the OAuth2 endpoints under `/oauth2/<site>/` with
authorization-code-plus-PKCE flow. Client (`oidc-client-ts`) configuration:

- `authority`: OpenEMR base URL + `/oauth2/<site>`
- `client_id`: registered via OpenEMR's *Client Registrations* admin
- `redirect_uri`: dashboard's `/auth/callback` route
- `scope`: `openid fhirUser patient/*.read patient/*.write`
- `response_type`: `code`
- PKCE: enabled (recommended for SPAs)

Token storage: `sessionStorage` (cleared on tab close), never
`localStorage` in plaintext.

---

## FHIR data layer

**TBD** — lands incrementally with the cards (38.3–38.9).

Each card module owns its FHIR resource type and the transformation from
FHIR shape to view-model shape. Composition pattern: `useFhirResource()`
composable handling fetch, loading, error, and refresh state per
endpoint.

---

## AgentForge drawer integration

**TBD** — lands with 38.10–38.12.

Inherits the panel-design decisions from the 2026-05-06 grilling
(captured in `project_panel_placement` and
`project_w2_dashboard_port` memory):

- Right-edge slide-out drawer (~480–560px), Bootstrap 5 offcanvas pattern.
- Three explicit mode tabs in the drawer header: **Chart / Intake /
  Research** — one conversation per scope key (chart = patient_id;
  intake = upload session; research = global).
- Conversation persists across tab navigation while the drawer is open.
  When the active patient changes, an **overlay-on-top-of-chat hard
  interrupt** (not a banner) appears with three resolution buttons:
  *Switch to <new>'s conversation* / *Stay on <previous>* / *Start
  fresh with <new>* (the third only when a stale conversation exists).
- Send/input disabled until the user resolves the scope question.

Defense for the drawer placement vs an embedded-per-chart panel: the
intake-form workflow (one of three flows) operates on a *new* patient
who doesn't have a chart yet; the guideline-retrieval flow is
patient-agnostic; only chart-questions need a `patient_id`. Embedding
in the per-chart view misled clinicians into thinking all three flows
operated on the open chart.

---

## Bonus section: Lab Results

**TBD** — lands with 38.9.

Chosen over the other bonus options (encounter history, vitals,
immunizations, appointments, patient notes) because:

- W2 already has lab-extraction infrastructure (Synthea fixtures, FHIR
  `Observation` shape well-understood, the lab-persistence service).
- Out-of-range coloring + sparkline trends are concrete UX features
  that show off something more than a static list.
- FHIR `Observation` (laboratory category) is among the most thoroughly
  supported FHIR resources across servers — high-confidence parity.

---

## Future work

**TBD** — lands at completion.

Things we explicitly **didn't** ship and the rationale for each:

- Server-side FHIR caching layer.
- Real-time FHIR Subscription wiring.
- E2E (Playwright) test suite.
- Vue 3 SSR / Nuxt migration.
- Code-splitting per-route at the router-config layer.

---

## Status

| Subtask | Status |
|---|---|
| 38.1 — Scaffold | in progress |
| 38.2 — OAuth2/OIDC login | pending |
| 38.3 — Patient header | pending |
| 38.4 — Allergies card | pending |
| 38.5 — Problem List card | pending |
| 38.6 — Medications card | pending |
| 38.7 — Prescriptions card | pending |
| 38.8 — Care Team card | pending |
| 38.9 — Lab Results card (bonus) | pending |
| 38.10 — AgentForge drawer | pending |
| 38.11 — Citation overlay re-port | pending |
| 38.12 — Intake review form + FHIR commit | pending |
| 38.13 — This document | in progress |
| 38.14 — Deploy to droplet | pending |
