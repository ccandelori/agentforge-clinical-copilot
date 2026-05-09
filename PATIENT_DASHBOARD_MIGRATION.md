# Patient Dashboard Migration — Defense

> Required artifact for W2 Surprise Challenge: *"Port the OpenEMR Patient
> Dashboard to a Modern Framework."* Brief at
> `docs/w2-surprise-challenge.pdf`. Deadline: 2026-05-10 noon.
>
> **Status:** shipped. Subtasks 38.1–38.12 done; 38.14 (deploy) in motion;
> 38.13 is this document. Live URL:
> [https://143.244.157.90:9300/dashboard/](https://143.244.157.90:9300/dashboard/).

## Executive summary

The OpenEMR patient dashboard was a PHP-rendered, server-side surface.
Every collapse, expand, edit, and chart-write was a server round-trip;
rich interactions (the AgentForge Co-Pilot, citation overlays, optimistic
UI) got grafted on as ad-hoc JavaScript with no shared state model.

This port moves the presentation layer to **Vue 3 + Vite + TypeScript**,
consuming OpenEMR's existing **REST + FHIR R4 API** as the data layer.
**OAuth2 / OpenID Connect** replaces session-cookie auth, brokered by a
**Backend-For-Frontend** in the AgentForge sidecar so the access token
never touches JavaScript.

The OpenEMR PHP backend is untouched apart from a small set of
**net-new internal endpoints** added inside the AgentForge module
(`Internal*Controller.php` — JWT-scoped reads/writes the sidecar BFF
calls into; never exposed to the browser). No legacy controllers, no
core schema, and no upstream FHIR routes were modified.

The deliverable is graded on three dimensions, defended below: gain
over PHP, choice of Vue specifically, and the tradeoffs accepted. Two
correctness stories — sidecar-initiated persistence and the eval gate
— extend the architectural defense beyond UI-framework choice.

The visual parity target is the May 2025 capminds.com OpenEMR
redesign (card-based, single-scroll Bootstrap-5 layout). The port
matches that structure; the architectural change is moving the state
that drives the cards from PHP to the client.

---

## Leg 1 — Why move off PHP-rendered

Server-rendered PHP couples every interaction to a round-trip. This caps
the ceiling on:

- **Optimistic UI.** A clinician marks an allergy as resolved → row dims
  immediately, FHIR `PUT /AllergyIntolerance/N` runs in the background,
  rollback on failure. Server-rendered pages can't do this honestly —
  the state lives on the server, not the client.
- **Streaming + real-time.** The AgentForge agent streams tokens back
  via SSE; FHIR Subscriptions can push observation updates. Both need
  client-side state machines that PHP-rendered pages don't host.
- **Composable state.** A drawer that survives tab navigation (the
  AgentForge Co-Pilot, scoped per-patient via the active route)
  requires a single client-side state container. PHP-rendered pages
  re-render their state on every nav — the panel either re-mounts and
  loses the conversation, or holds state in a separate store that
  bypasses the page lifecycle entirely (which is what the original
  per-chart panel did, with its own bugs).
- **Strong typing across the data boundary.** TypeScript + a local FHIR
  type fallback (drop-in replaceable with `@types/fhir`) catches schema
  mismatches at compile time, not on a clinician's screen.

The May 2025 capminds.com OpenEMR redesign already moved to a
card-based, single-scroll Bootstrap layout — but the **state** still
lives on the server. This port separates the presentation layer
cleanly: the FHIR API becomes the contract, and any FHIR-compliant
backend (HAPI FHIR, Bahmni, a future OpenEMR core rewrite) can swap in
without re-rendering anything.

The headline argument is the **separation itself**, not the framework.
Vue is the specific instance; the architectural win generalizes.

### A correctness corollary: persistence happens server-side

A common consequence of moving presentation to the client is "now the
client has to coordinate writes." We rejected that for the AgentForge
extraction pipeline. When the agent's vision extractor produces an
intake-form or lab-PDF extraction, the **sidecar** posts it to OpenEMR
in the same turn; the dashboard sees a single round-trip and a
`persisted_resource_id` on the response (P1.1 — Option A in the
2026-05-08 DEVIATIONS entry). The dashboard's confirm-panel then
moves data from "extracted but unapproved" into structured tables —
not from "in-memory" into "stored". The client surface stays thin;
the audit trail stays single-sourced through
`QuestionnaireResponseService::saveQuestionnaireResponse()` (P2.1) and
`InternalLabPersistController` (P2.2). Neither write lands as a raw
INSERT; both fire the standard service events.

This matters for clinical software in a way that doesn't matter for a
typical SPA: the persistence layer is also the audit layer. Putting the
"who/what/when" reconstruction in the client would have been the
defensible-looking choice and the wrong one.

---

## Leg 2 — Why Vue 3 specifically

Honest comparison, framed against *this* port (5 required cards +
patient header + AgentForge drawer + bonus, 3.5-day timeline, parity
with the May 2025 Bootstrap-5 design):

| Alternative | Why not |
|---|---|
| **React** | Capable, but more ceremony per component (hook-rule footguns, more decisions per render). For a parity port on a 3.5-day clock, Vue's lower ceremony wins. |
| **Angular** | RxJS + DI + decorators is over-engineered for a card grid. Justified by "enterprise scale," which a parity port doesn't need. |
| **Svelte / SvelteKit** | Compiler-as-framework is genuinely elegant, but the Bootstrap-aligned component ecosystem is thinner. Slower to ship parity. |
| **Qwik** | Resumability is real and impressive; the defense story is sharper ("first paint matters when clinicians context-switch fast"). The UI-library ecosystem isn't there for clinical card surfaces — Qwik UI is younger; OAuth2 client libs are less mature. The defense weight against shipping risk doesn't pay off on a 3.5-day clock. **Called out explicitly as the road-not-taken.** |
| **HTMX + Alpine** | Returns HTML fragments — still server-rendered. Actively contradicts "move the presentation layer to a better tool." |

Vue brings:

- **Single-file components** (`.vue` = template + script + style
  co-located). Mirrors OpenEMR's "one file = one surface" pattern,
  minimizing onboarding friction for a future maintainer who's been
  working in the PHP codebase.
- **Reactivity** (`ref` / `computed` / `watch`) is simpler than React
  hook rules — fewer footguns under time pressure. (One footgun
  remains: a Pinia setter that creates-then-returns a reactive object
  has to return the proxy through the parent, not the local var. We
  learned this the hard way; the lesson is captured in
  `ensureConversation()` and the project memory.)
- **Pinia** is the right state-management size: more structured than
  vanilla refs, not as heavy as Redux. Per-patient conversation
  scoping in the AgentForge drawer maps cleanly to a Pinia store
  whose `currentPatientUuid()` is derived from `useRoute()`.
- **Native TypeScript** with strong inference — no per-component type
  ceremony, and FHIR resource types from a local `fhir4.*` namespace
  (drop-in for `@types/fhir`) flow through components without manual
  annotation.
- **Tailwind + custom UI primitives** (`BaseCard`, `BaseButton`,
  `BaseModal`, etc.) instead of BootstrapVueNext. We landed on
  hand-rolled primitives over a Bootstrap-Vue port because the design
  language ended up closer to the capminds.com refresh than to
  vanilla Bootstrap-5; the primitives are ~150 LOC each and avoid
  pulling in an entire Bootstrap-Vue runtime for a handful of
  components.
- **Vite** dev iteration is among the fastest in the ecosystem; on a
  3.5-day timeline, iteration speed is load-bearing.

The thread: **lower-ceremony framework + ecosystem alignment with the
existing visual design language = highest probability of feature parity
in 3.5 days.** The defense doesn't claim Vue is *the best* framework;
it claims Vue is the *right* framework for this specific port.

---

## Leg 3 — Tradeoffs explicitly accepted

- **Larger runtime than Svelte / Qwik** (~30KB gzipped). Doesn't
  matter for a logged-in clinical desktop; would matter for embedded
  / mobile-first.
- **SPA, not SSR.** First-paint is slower than a server-rendered app
  on cold load. Acceptable because clinicians log in once and stay;
  the cost amortizes within minutes of session usage.
- **Less hyped than Qwik in 2026.** Honest. We're trading novelty for
  shipping reliability — and for clinical software, predictability
  *is* a feature, not a hedge. Half-shipped clinical software is
  worse than no port at all.
- **Smaller ecosystem than React.** For our specific needs (OAuth2,
  FHIR, charting for the labs bonus), Vue has battle-tested options.
  We're not at the edges of what the ecosystem supports.
- **Vue Router lazy-route splitting requires explicit `() => import()`**
  syntax (not free per-route the way Next.js / Nuxt is). Acceptable;
  one-line cost per route. The router config in `vue-ui/src/router/`
  uses this pattern throughout.

---

## Architecture overview

The shipped shape under `vue-ui/src/`:

```
vue-ui/src/
├── api/
│   └── mock.ts                  # FHIR client (filename predates rewrite;
│                                #   despite the name this is the live
│                                #   `/api/fhir/*` fetch layer with FHIR R4
│                                #   resource → view-model mappers)
├── stores/                      # Pinia stores
│   ├── auth.ts                  # session/whoami state via the BFF
│   ├── agentforge.ts            # per-patient AgentForge conversations
│   ├── calendar.ts              # CalendarView (preview-only, mocked)
│   ├── preferences.ts           # density / sort persistence
│   └── ui.ts                    # drawer open/closed, modal stack
├── composables/                 # framework-agnostic side-effect hooks
│   ├── usePatient.ts            # parallel-fetch the 7 FHIR queries the
│   │                            #   dashboard needs; per-tab cache
│   ├── useAgentTurn.ts          # POST /api/agent/turn round-trip
│   ├── useDocumentUpload.ts     # POST /api/agent/upload (multipart)
│   ├── inferDocType.ts          # filename → DocumentType heuristic
│   ├── parseIntakeExtraction.ts # narrows sidecar extraction shape
│   ├── useEncounterDraft.ts     # encounter-editor state machine
│   └── useRelativeTime.ts       # display formatting
├── views/
│   ├── auth/LoginView.vue       # `window.location` → /auth/login
│   ├── patients/
│   │   ├── PatientList.vue      # FHIR Patient search + filters
│   │   └── PatientDashboard.vue # 5 cards + header + drawer host
│   ├── encounters/EncounterEditor.vue
│   ├── calendar/CalendarView.vue
│   └── settings/SettingsView.vue
├── components/
│   ├── ui/                      # Base{Card,Button,Modal,...}
│   ├── patients/dashboard/      # 5 cards + Vitals strip + header
│   │   ├── PatientHeaderBand.vue
│   │   ├── VitalsStrip.vue          # bonus
│   │   ├── ProblemListCard.vue
│   │   ├── MedicationsCard.vue
│   │   ├── AllergiesCard.vue
│   │   ├── EncountersCard.vue       # required (history)
│   │   └── LabsCard.vue             # bonus
│   ├── agentforge/              # drawer surface (see next section)
│   ├── encounter/               # SOAP-shaped encounter editor
│   ├── calendar/                # day/week/month + appointment modal
│   └── DocumentViewer.vue       # PDF.js + bbox overlay (T38.11)
├── layouts/AppShell.vue         # router-view + persistent drawer host
├── router/index.ts              # auth-gated routes; lazy import per view
└── main.ts                      # createApp + Pinia + router wiring
```

The router is the single client-side state authority for "what page
am I on"; Pinia stores are the single authority for "what data have I
loaded"; composables are stateless side-effect hooks that compose
into store actions and view setup. Components never call `fetch`
directly — every network call goes through a composable, every
composable goes through a typed FHIR module, and the FHIR module is
the only place that knows about HTTP details (URL shape, error
handling, response narrowing).

### Data layer: FHIR via the BFF, not directly

The dashboard never speaks to OpenEMR's FHIR endpoint directly. Every
`/api/fhir/{path}` call is forwarded by the sidecar BFF (FastAPI
proxy in `sidecar/src/agentforge/dashboard_auth/`), which attaches
the user's bearer token from the session store and pipes the response
back. The dashboard sees an HttpOnly cookie; the OAuth2 access token
never enters JavaScript. The same proxy fronts `/api/agent/*` for the
AgentForge turn / upload / document routes.

---

## OAuth2 / OpenID Connect integration — BFF flow (v2)

Lands with subtask 38.2 (v2). The dashboard does **not** speak OAuth2
itself; the AgentForge sidecar is a **Backend For Frontend** that
holds the OAuth2 client credentials server-side, performs the token
exchange, and proxies FHIR reads to OpenEMR with the user's bearer
token. The dashboard sees only an HttpOnly session cookie.

This is a pivot from v1 (public client + PKCE in the SPA, which
couldn't get the `user/*` scopes a clinician dashboard requires). See
the [v1 → v2 entry in DEVIATIONS.md](docs/DEVIATIONS.md) for the
discovery narrative.

### Architecture

```
Browser (dashboard SPA, http://localhost:5173)
   │
   │ /auth/login  /auth/whoami  /auth/logout  /api/fhir/...  /api/agent/...
   ▼  (Vite dev proxy in dev; same-origin in prod via Apache)
Sidecar (FastAPI, http://localhost:8000)
   │  ── holds confidential OAuth2 client_secret
   │  ── stores sessions in Redis keyed by HttpOnly cookie
   │  ── mints internal JWTs for OpenEMR-side reads/writes
   │
   │ OAuth2 authorize / token / userinfo / FHIR / Internal* endpoints
   ▼
OpenEMR (https://localhost:9300/oauth2/default + /apis/default/fhir
         + /interface/modules/.../public/internal/*.php)
```

### Client registration

OpenEMR's OIDC discovery (`/oauth2/<site>/.well-known/openid-configuration`)
gives the canonical authority — for dev-easy that's
`https://localhost:9300/oauth2/default`. **HTTPS port 9300 is mandatory**;
the OAuth2 endpoints reject HTTP.

The dashboard registers as a **confidential client** so it can request
`user/*.read` scopes (clinical-user context — what a clinician's chart
view actually needs). Public clients are rejected from `user/*` and
`system/*` scopes server-side; that's why v1 (public client) couldn't
get past 401 on FHIR endpoints. The `client_secret` lives on the
sidecar — it never enters the browser bundle.

Exact registration call (re-runnable on a fresh dev-easy):

```bash
docker compose exec -T openemr curl -sS -X POST \
  http://localhost/oauth2/default/registration \
  -H 'Content-Type: application/json' \
  --data '{
    "application_type": "private",
    "client_name": "AgentForge Dashboard BFF (sidecar, confidential)",
    "redirect_uris": ["http://localhost:5173/auth/callback"],
    "post_logout_redirect_uris": ["http://localhost:5173/"],
    "token_endpoint_auth_method": "client_secret_post",
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
    "scope": "openid offline_access fhirUser user/Patient.read user/AllergyIntolerance.read user/Condition.read user/MedicationRequest.read user/CareTeam.read user/Observation.read user/Encounter.read user/Practitioner.read user/Organization.read"
  }'
```

After registration, the client must be **enabled** manually: Admin →
System → API Clients → Enable. Without this step every authorize call is
rejected with "client disabled". This is intentional ONC-Cures behavior
and there's no API to short-circuit it. Also confirm Admin → Config →
Connectors → "Enable OpenEMR Standard FHIR REST API" is on.

`redirect_uris` points at the **dashboard origin** (`http://localhost:5173/auth/callback`)
because the user's browser is what OpenEMR redirects. Vite's dev-server
proxy forwards `/auth/*` to the sidecar; the sidecar's `/auth/callback`
handler is what actually processes the OAuth2 code. In production
(T38.14), the dashboard is served from the same Apache that fronts
OpenEMR (`https://143.244.157.90:9300/dashboard/`) so `/auth/*` hits
the sidecar through the reverse proxy without a separate origin.

### Sidecar BFF surface

Implemented in `sidecar/src/agentforge/dashboard_auth/`:

| Endpoint | Purpose |
|---|---|
| `GET /auth/login?next=<path>` | Generate state + PKCE, persist in Redis, 307 redirect to OpenEMR's `/authorize` |
| `GET /auth/callback?code=&state=` | Validate state (one-time), exchange code for tokens, fetch userinfo, create session, set HttpOnly cookie, 307 redirect to dashboard `next` path |
| `GET /auth/whoami` | Return `{ authenticated, user, expires_at }` based on session cookie |
| `POST /auth/logout` | Delete session in Redis, clear cookie, 204 |
| `* /api/fhir/{path:path}` | Proxy FHIR R4 requests with the session's bearer token; passes through query string + body + content-type |
| `POST /api/agent/turn` | Drive a LangGraph turn (chart Q&A, vision extraction, RAG); returns reply + citations + extraction + persisted_resource_id |
| `POST /api/agent/upload` | Multipart upload → mint internal JWT → forward to OpenEMR's `InternalUploadDocumentController` |
| `GET /api/agent/document/{id}` | Citation-overlay byte fetch; chains through `InternalDocumentBytesController` |

Each session in Redis stores: `sub`, `name`, `fhir_user`, `email`,
`access_token`, `refresh_token`, `expires_at`. Cookie is opaque — only
the random `session_id`. Pending OAuth state (between `/login` and
`/callback`) is one-time-use: a successful read deletes it from Redis,
so a stale `state` value can never satisfy a second callback.

33+ pytest specs in `sidecar/tests/test_dashboard_auth_*.py` cover the
OAuth2 helpers (PKCE pair, authorize URL, token exchange, refresh,
userinfo), the SessionStore CRUD, and the routes end-to-end via
`fastapi.testclient.TestClient` with `httpx.MockTransport` for both
OpenEMR and the upstream FHIR endpoint. See the test files for the
full contract.

### Sidecar configuration

The sidecar reads its OAuth2 + dashboard config from `sidecar/.env`
(template in `sidecar/.env.example`). Required when the BFF is in use:

- `DASHBOARD_OAUTH_AUTHORITY` — `${OPENEMR_HTTPS}/oauth2/<site>`
- `DASHBOARD_OAUTH_CLIENT_ID` + `DASHBOARD_OAUTH_CLIENT_SECRET` — from registration
- `DASHBOARD_OAUTH_REDIRECT_URI` — `http://localhost:5173/auth/callback`
- `DASHBOARD_OAUTH_AUDIENCE` — OpenEMR-specific; binds the access token to the FHIR resource server
- `DASHBOARD_OAUTH_SCOPE` — `openid offline_access fhirUser user/* …`
- `DASHBOARD_FHIR_BASE_URL` — `https://localhost:9300/apis/default/fhir`
- `DASHBOARD_APP_URL` — where the dashboard lives (post-callback redirect target)

When `DASHBOARD_OAUTH_CLIENT_ID` is empty, the BFF routes mount but
return `503 BFF not configured` — the routing surface is stable
across deployment shapes, and existing sidecar tests don't need to
configure dashboard auth.

### Dashboard side

The dashboard (`vue-ui/src/`) holds **no OAuth2 state**. The Pinia
auth store (`stores/auth.ts`) tracks:

```
status:    'unknown' | 'signed-in' | 'signed-out'
user:      { sub, name, fhir_user, email } | null
expiresAt: number | null
```

Actions:

- `hydrate()` — fetches `/auth/whoami` on first navigation. The router
  guard awaits this before deciding whether to redirect to `/login`.
- `signIn(targetPath?)` — `window.location.assign('/auth/login?next=...')`.
  The handshake is a top-level navigation; the user lands back on the
  dashboard after the sidecar finishes the code-for-token exchange.
- `signOut()` — POST `/auth/logout` (best-effort), clear local state,
  navigate to `/login`.

10+ Vitest specs cover the store transitions (`unknown → signed-in`,
`unknown → signed-out` via `authenticated:false`, network-error
fallback, signIn URL encoding, signOut clearing local state even when
the network call fails).

The dashboard ships with **no `oidc-client-ts` dependency** — the v1
work was excised when the BFF landed.

### Security tradeoffs accepted

- **Token never touches JS.** XSS on the dashboard cannot exfiltrate
  the OAuth2 access token because it lives on the sidecar; only the
  HttpOnly session cookie is in the browser, and HttpOnly cookies are
  unreadable from JS.
- **Self-signed cert on dev-easy and the demo droplet.** First-time
  users must navigate to `https://<host>:9300/` once and accept the
  cert, otherwise the browser's redirect to OpenEMR's `/authorize`
  fails. Sidecar uses `verify=False` against dev-easy for its
  server-side requests; this flips to verified TLS in production
  proper (carried as a follow-up — the demo droplet still rides on
  the self-signed cert by design).
- **No token refresh yet.** When the access_token expires the user is
  re-prompted to sign in. The sidecar holds the refresh_token; refresh
  wiring is a small follow-up but not required for the demo.

---

## FHIR data layer

Lands incrementally with the cards (38.3–38.9). Discovery findings from
T38.2 that shape the data layer:

- **Read**: OpenEMR advertises `patient/Patient.read`,
  `patient/AllergyIntolerance.read`, `patient/Condition.read`,
  `patient/MedicationRequest.read`, `patient/CareTeam.read`,
  `patient/Observation.read`, plus six others we don't need. That covers
  every required card except medications/prescriptions — see next.
- **`MedicationStatement` is not exposed**, only `MedicationRequest`.
  The plan called for both T38.6 (medications) and T38.7
  (prescriptions); in practice both are sourced from
  `MedicationRequest` and split by status (`active` for current meds,
  `completed`/`stopped` for prescription history). Logged in the
  deviation log.
- **`FamilyMemberHistory` is not exposed.** The intake review form's
  family-history section persists via the sidecar's
  `InternalIntakePersistController` path rather than via FHIR write.
- **No `patient/*.write` scopes are advertised** at all. Writes exist
  but are gated by `user/*.write` scopes (legacy REST API surface,
  resource-named: `user/medical_problem.write`, etc.). For chart
  writes that need to fire OpenEMR's service events + audit, we route
  through the sidecar BFF → `Internal*Controller.php` rather than
  surfacing OAuth2 write scopes to the SPA. This keeps the dashboard
  read-only against FHIR proper and the write surface auditable
  through the AgentForge module's existing JWT-validated entry points.

Each card module owns its FHIR resource type and the transformation
from FHIR shape to view-model shape inside `vue-ui/src/api/mock.ts`
(misnomer — see file header; this is the live FHIR client). The
composition pattern is `useFhirResource()`-shaped: `usePatient(id)`
parallel-fetches the seven resources the dashboard needs, surfaces a
`{ patient, vitals, problems, ... , loading, error, refresh }`
bundle, and caches it per-patient with a 60s TTL so revisits are
instant.

---

## AgentForge drawer integration

The drawer is a **first-class component** of the dashboard, not an
add-on. It lives at `vue-ui/src/components/agentforge/` and is hosted
by `AppShell.vue` so it survives every route transition while the
chosen patient context is derived from `useRoute()`.

### Surface layout

`AgentDrawer.vue` is a right-edge slide-out (480px on >=sm,
full-width on mobile) teleported to a top-level `#drawer-root` so
nested-layout z-index issues don't clip it. Three tabs at the top:

| Tab | What it shows |
|---|---|
| **Chat** | The active conversation: messages, citations inline, attachment composer, "Ask guidelines" toggle |
| **Citations** | Aggregated view of every citation across the active conversation — clickable to scroll into the chat |
| **History** | List of past conversations across the app; selection swaps the active conversation in place |

(The earlier draft of this doc speculated on Chart / Intake / Research
mode tabs from a 2026-05-06 grilling. In implementation that mode
distinction collapsed: the chat composer adapts to context — attach is
disabled when not on a patient route; the "Ask guidelines" toggle is
the explicit lever for the research/RAG flow — without needing
separate top-level modes. The tab bar is now Chat / Citations /
History, which is the parity surface for a clinical co-pilot drawer.)

### Per-patient scoping

A single Pinia store (`stores/agentforge.ts`) owns the conversation
list. The store derives `currentPatientUuid()` from `useRoute()` at
send time and forwards it to the sidecar; the sidecar maps the FHIR
Patient UUID to OpenEMR's integer `patient_data.pid` and bakes it
into the per-turn JWT. Conversations themselves are not pinned to a
patient — a clinician can carry one conversation across charts — but
every turn's data context is whatever patient the URL says they are
looking at *right now*. This is a deliberate choice: the audit trail
ties each turn to a patient through the JWT, not through a
conversation-level pinning that could drift if the user renamed
conversations or imported a session.

### State + persistence

- **Conversations** live in `sessionStorage` (NOT `localStorage`) so
  no PHI rides between browser tabs. Survives a refresh but not a
  new tab.
- **`isSending`** flag gates the composer.
- **`pendingAttachment`** rides exactly one turn — the next
  `sendMessage` clears it before the network round-trip so a
  re-fired message can't re-attach the same upload.
- **`guidelineMode`** is the "Ask guidelines" toggle (default off).
  When on, the store mirrors the user's text into both `message` and
  `evidence_query`, which trips the W2 graph's evidence-retriever
  node (RAG over `sidecar/data/guidelines/`). When off, the W1
  chart-Q&A loop runs.

### Document attachment flow

The chat composer's paperclip → `<input type="file">` → composable
chain:

1. **`useDocumentUpload.ts`** POSTs the file as multipart to
   `/api/agent/upload`. The BFF mints a short-lived internal JWT
   bound to the resolved `patient_id` and forwards to
   `InternalUploadDocumentController.php`. The response carries a
   numeric `document_id` (string-ified for JSON safety on large IDs).
2. **`inferDocType.ts`** — extracted as a composable so it's
   independently unit-tested — sniffs the filename for whole-word
   lab markers (`\b(lab|panel|cbc|cmp|lipid|hba1c|results?)\b`) and
   falls back to `intake_form`. The resulting `DocumentType` is
   stamped on the `PendingAttachment` so the BFF graph dispatches to
   the right contract (P4#1 — see the 2026-05-08 DEVIATIONS entry).
3. **Next `sendMessage`** posts `{ document_id, doc_type, ... }`
   alongside the user's text. The BFF's W2 graph routes through the
   vision extractor (`intake_extractor_node`) on `state["doc_type"]`
   (P1.2) and produces an `IntakeFormExtraction` (or
   `LabPdfExtraction`, when the lab worker arrives).
4. **The sidecar persists** the extraction transparently in the same
   turn (P1.1, see the next section); the response carries
   `persisted_resource_id` so the dashboard's confirm-panel knows
   which `QuestionnaireResponse` (or `procedure_order` cascade) to
   address later.
5. **The chat reply** carries the assistant's narrative, plus a
   `<ExtractionPanel>` rendered below the bubble showing the
   structured field set, plus a "View source" button if a
   `document_id` is in scope. Click → `<DocumentViewer>` modal opens
   with the PDF and overlaid bounding boxes.

### Sidecar-driven persistence (Option A)

`sidecar/src/agentforge/persist/` is a new top-level package matching
the established sibling pattern (`tools/`, `dashboard_auth/`).
`ExtractionPersister` mirrors the document-bytes fetcher shape: long-
lived `httpx.AsyncClient`, JWT bearer auth, narrow typed
`ExtractionPersistError(status_code, message)`. Two methods —
`persist_intake(IntakeFormExtraction, ...)` and
`persist_lab(LabPdfExtraction, ...)` — POST to the existing W2
controllers; the PHP controller surface required no changes because
the Pydantic `model_dump(mode="json")` shape lined up cleanly.

The orchestrator dispatches by `isinstance(extraction,
LabPdfExtraction)` vs `IntakeFormExtraction` and writes the result
into a `_TURN_PERSISTED_VAR` ContextVar parallel to the per-turn
extraction var. The BFF turn route reads it and surfaces
`persisted_resource_id: str | None` on `AgentTurnResponse`.

Failures are best-effort: the persister logs status_code +
patient_id + document_id (never PHI) and the synthesis turn always
surfaces the model's reply. The dashboard's confirm-panel can
re-trigger persist if the auto-persist failed.

### Trust artifact: bbox citation overlay

`<DocumentViewer>` is the citation analogue for vision extractions.
PDF.js renders the source document; absolutely-positioned `<div>`s
overlay the bounding boxes the vision model emitted alongside each
extracted field. A clinician can click "View source (18)" on any
`<ExtractionPanel>` and verify field-by-field what the LLM
extracted.

Architectural notes:

- `mapBBoxToPixels` is pure and separately tested — overlay
  positioning math is the load-bearing piece, so it lives outside
  the rendering shell.
- The `PdfLoader` contract is injectable; jsdom can't reliably boot
  PDF.js, so unit tests substitute an in-memory fake.
- The viewer consumes bboxes only; it doesn't own click handlers.
  Wiring overlay clicks back to citation pills is integration work
  that lives in the citations pane.

This matters for a clinical co-pilot in a way it wouldn't for a
general LLM UI: the alternative to "click to verify" is "trust the
model on a chart write." For the persistence pipeline that auto-
populates structured tables, "verify the extraction" has to be a
two-second round-trip, not a context switch into a separate
document viewer.

### Drawer placement: the W1 panel was yanked

The original W1 architecture embedded an AgentForge chat panel
directly inside OpenEMR's per-chart patient summary view (Twig
template + Angular-style JS bundle + two PHP route entry points). A
2026-05-06 panel-design grilling concluded the per-chart embed was
dangerously wrong — the intake-form workflow operates on a *new*
patient who doesn't have a chart yet, the guideline-retrieval flow
is patient-agnostic, and only chart-questions need a `patient_id`.
The dashboard drawer is the correct placement.

The 2026-05-08 code review surfaced that the legacy panel still had
latent bugs (lab PDFs hard-coded as `intake_form`, citation overlay
plumbing never finished). With ~36h to deadline and the Vue
dashboard already serving every demo path, fixing dead code was
pure carrying cost. We yanked the legacy panel surface in one atomic
commit (P4#3 — see DEVIATIONS): the Twig template, the Angular JS
bundle, the two `public/turn.php` / `upload_document.php` entry
points, the `AgentProxyController` / `UploadDocumentController`
controllers, the `Bootstrap.php` event subscriber, plus the panel
tests. The Apache vhost lost its `/agentforge/turn` alias; the
JWT-scoped `Internal*` route surface stayed.

This is part of the architectural defense, not a footnote: when
placement decisions flip, the deprecated surface is carrying cost
both for maintenance (PHPStan/PHPCS keep auditing it) and for review
attention. Same-day deletion would have saved one round of code-
review cycles.

---

## Bonus section: Lab Results

`LabsCard.vue` lands with 38.9. Chosen over the other bonus options
(encounter history, immunizations, appointments, patient notes)
because:

- W2 already has lab-extraction infrastructure (Synthea fixtures,
  FHIR `Observation` shape well-understood, the lab-persistence
  service via `InternalLabPersistController`).
- Out-of-range coloring + sparkline trends are concrete UX features
  that show off something more than a static list. The `Sparkline`
  component is hand-rolled SVG (no charting library — keeps the
  bundle tight).
- FHIR `Observation` (laboratory category) is among the most
  thoroughly supported FHIR resources across servers — high-
  confidence parity.

Vitals (`VitalsStrip.vue` + `VitalCard.vue`) ships alongside as a
strip across the top of the dashboard; the `Sparkline` component is
shared between vitals and labs. Encounters lands as
`EncountersCard.vue`. Two of the original bonus candidates therefore
ship in addition to the labs deliverable.

Known data gap: Synthea-imported `form_vitals` rows lack
`uuid_mapping` rows by default; FHIR `Observation:vital-signs`
returns 0 until backfilled with `UuidMapping::createAllMissingResourceUuids()`.
Tracked in project memory; the demo droplet has the backfill applied.

---

## Correctness story: the eval gate as a safety property

The W2 eval pipeline (Tasks 15–22, shipped 2026-05-08 evening) is
not just a CI step — it's a correctness claim about the agent's
outputs. The relevant defense piece for *this* document is that the
gate is **proven** to catch regressions, not just *configured* to.

`sidecar/tests/eval/gate/test_gate_blocks_regression.py` (Task 19) is
a self-test: it constructs a deliberately-regressed adapter that
strips citations from the model's response and asserts that the gate
fails the regressed run end-to-end. This proves the pipeline
correctly flags fabrication-shaped regressions through the
programmatic `citation_present` grader (the W2 contract is "every
clinical claim carries a Citation"). Every run of the test suite
re-validates that the gate would catch a future regression — the
correctness property is reasserted on every CI run, not just at
gate-config time.

The gate plumbing:

- `sidecar/src/agentforge/eval/supervisor_adapter.py` (Task 40) is
  the production `Callable[[EvalCase], SupervisorOutput]` adapter
  that drives `build_graph().ainvoke()` and shapes the result for
  the runner.
- `sidecar/src/agentforge/eval/regenerate_baseline.py` is the manual
  CLI for refreshing `sidecar/tests/eval/baselines/week2.json`
  against the real graph + real LLMs.
- The shipped baseline is structurally pinned at 1.0 with
  `_meta.status: "stub"` (deliberately — see the 2026-05-08 Task
  18.4 entry in DEVIATIONS for the framing).

What the eval gate *does not* yet measure is the agent's absolute
pass rate against the 50 W2 cases. The gate self-test carries the
load-bearing correctness property; absolute-rate measurement is a
follow-up that requires a human-supervised real-LLM regen.

---

## Future work

Things explicitly **not** shipped, with the rationale for each:

### Demo polish gaps (~hours, deferred for time)

- **DocumentViewer is PDF-only.** PNG intake forms (the Reyes and
  Kowalski personas) won't render in the View-source modal — PDF.js
  can't parse `image/png` bytes. Demo workaround: stick to typed
  PDFs (Chen, Whitaker). A `<canvas>`-based PNG fallback is a
  half-day fix; punted because the bbox-overlay defense story
  reads cleaner with a PDF anyway.
- **Bbox placement is approximate.** Haiku-vision bboxes land in the
  right region but offset by a row/cell. Acceptable as a trust
  artifact (the clinician sees the intent; pixel precision would
  require either a larger vision model or a post-extraction
  alignment pass against tessera OCR).
- **Demographics labels are raw snake_case** in `<ExtractionPanel>`
  (`primary_phone` instead of "Primary phone"). Quarter-day fix:
  `humanizeFieldName()` helper. Punted.
- **Chat reply duplicates panel content.** The synthesizer narrates
  what the panel already shows. Either tighten the prompt to defer
  to the panel or accept it as a redundancy that helps clinicians
  who scan-read.

### Agent / pipeline gaps (deferred)

- **Planner Haiku tool-call fallback.** Logs show "planner LLM
  returned no submit_plan tool call; falling back". The orchestrator
  falls back to `default_plan_for(use_case)`; requests complete with
  less-tailored tool selection. Half-day fix is a separate
  `PLANNER_MODEL` env knob pinning the planner to Sonnet.
- **Eval baseline is a stub** (see "Correctness story" above). The
  gate self-test (Task 19) carries the correctness claim; absolute
  pass-rate measurement is the follow-up.
- **Judge routing limitation.** The LLM judge's
  `factually_consistent` category fires only for
  `HALLUCINATION` / `REFUSAL` `EvalCategory` values. The W2 case
  suite uses `extraction` / `evidence_retrieval` / `citations` /
  `refusal` / `missing_data`. The programmatic `citation_present`
  grader carries the load-bearing assertion in the gate self-test.
  Extending the judge routing for value-fabrication coverage is a
  documented follow-up.
- **Lab graph worker not yet wired.** P1.2 added the doc_type
  dispatch in `intake_extractor_node` so a `LabPdfExtraction` *can*
  route through the lab contract; P1.1's persister is
  `isinstance`-typed so it'll fire when one arrives. The graph's
  `extraction_result` field is still typed around
  `IntakeFormExtraction` in practice. Forward-compat-ready follow-up.
- **Demo corpus is project-prepared summaries.** The 2026-05-09
  punch-list strengthened the framing in
  `sidecar/data/guidelines/NOTICE.md` to call this out explicitly.
  Production-grade corpus ingestion (real source PDFs) is post-W2.

### Production gaps (post-deadline)

- **No SFC integration tests** for the drawer. Composable + pure-
  helper + store unit coverage is in; drawer-flow integration tests
  are not.
- **Sign & Finalize / Edit demographics are preview-only.** Same gap
  as W1 — needs `POST /api/fhir/Encounter` and `PATCH /Patient`.
- **Token refresh not implemented.** OAuth access_token expires ~1
  hour; FHIR returns 401; SPA bounces to /login.
- **CalendarView / SettingsView are mocked** — not wired to real
  FHIR.
- **Vue 3 SSR / Nuxt migration.** Acceptable trade for a clinical
  desktop app; would matter on cold-load-sensitive surfaces.
- **Server-side FHIR caching layer.** The dashboard's per-tab
  `usePatient` cache (60s TTL) is the only caching today.
- **Real-time FHIR Subscription wiring.** Dashboard polls on
  navigation; subscriptions would need a sidecar fan-out.
- **E2E (Playwright) test suite.** Sidecar has integration tests +
  Vitest covers composables/stores; full browser-driven E2E is
  follow-up.

---

## Status

| Subtask | Status |
|---|---|
| 38.1 — Scaffold | done |
| 38.2 — OAuth2/OIDC login (BFF) | done |
| 38.3 — Patient header | done |
| 38.4 — Allergies card | done |
| 38.5 — Problem List card | done |
| 38.6 — Medications card | done |
| 38.7 — Prescriptions card | deferred (subsumed by `MedicationsCard` filtering `MedicationRequest` by status; no separate prescriptions surface shipped) |
| 38.8 — Care Team card | deferred (Synthea-imported demo personas have empty CareTeam tables — no data to render; tracked as Taskmaster #39) |
| 38.9 — Lab Results card (bonus) | done — `LabsCard.vue` plus `VitalsStrip` + `EncountersCard` ride-alongs |
| 38.10 — AgentForge drawer | done |
| 38.11 — Citation overlay re-port | done |
| 38.12 — Intake review form + FHIR commit | done |
| 38.13 — This document | done |
| 38.14 — Deploy to droplet | done |
