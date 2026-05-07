# ADR-0001 — Bridging the dashboard's session-cookie auth into the agent's JWT trust boundary

**Status:** Accepted — 2026-05-06
**Authors:** Cameron Candelori (with Claude Code)
**Context tags:** auth, sidecar, dashboard, trust-boundary

---

## TL;DR

The new Vue dashboard talks to the sidecar over an HttpOnly session
cookie (BFF pattern). The agent's tool layer talks to OpenEMR's PHP
endpoints over an HS256 JWT signed with `AGENTFORGE_JWT_SECRET`. Those
are two different identity formats that meet at the same trust
boundary — `RequestContext`, mandated by `ARCHITECTURE.md §2`.

We chose to **mint an internal JWT from the dashboard session** and
funnel everything through the existing `AuthGateway`, rather than
build a parallel BFF→Claude pipeline that would skip the orchestrator,
verifier, and sensitivity policy. The bridge requires a tiny new
`/me` endpoint in the OpenEMR module to resolve OIDC identity to the
integer `user_id` the JWT contract expects.

---

## 1. The problem

The original AgentForge module is a per-chart embed inside legacy
OpenEMR. That panel ships its identity to the sidecar like this:

```
Browser ──(rendered inside OpenEMR PHP page)──► OpenEMR PHP module
              │                                  │
              │                                  └─► AgentJwtService::mint()
              │                                          │ HS256(AGENTFORGE_JWT_SECRET)
              │                                          │ claims: user_id, patient_id,
              │                                          │ username, role, breakglass_*
              ▼
         /turn   ◄──── Bearer <internal JWT> ──── (sidecar receives;
                                                  AuthGateway validates;
                                                  RequestContext is the
                                                  trust boundary)
```

Every tool the agent calls (demographics, labs, meds, allergies, …)
posts back to the OpenEMR module's `/internal/*` PHP endpoints with
the **same JWT**. Those endpoints validate the JWT against
`AGENTFORGE_JWT_SECRET` and run the database query. The trust pipeline
is symmetrical: the same internal JWT fronts every hop.

The W2 dashboard pivot (Task 38) replaces the per-chart embed with a
top-level Vue SPA that talks to the sidecar through a Backend-for-
Frontend pattern:

```
Browser ──(Vue SPA)──► Sidecar
              │           │
              ▼           ├── Holds OAuth2 access_token in Redis session
   Set-Cookie: agentforge_session=<id>; HttpOnly
                          │
                          └── /api/fhir/* proxy: forwards FHIR reads
                              to OpenEMR using session.access_token
```

Nothing the browser sees is a token; OAuth2 credentials live entirely
server-side. That's the whole point of the BFF pattern.

The dashboard now needs to talk to the **agent**. The session
identity is OIDC: `sub`, `fhirUser` (a URI like
`https://…/fhir/Practitioner/<uuid>`), `name`, `email`. None of those
are the integer `user_id` the agent's JWT contract expects, and the
two pieces of code that produce identity (the OpenEMR PHP module and
the OAuth callback) live on opposite sides of a network boundary.

---

## 2. The constraint that rules out the easy path

`ARCHITECTURE.md §2` is explicit:

> Every agent turn enters through `AuthGateway`. The gateway is the
> single chokepoint for authorization decisions. Tools accept
> `RequestContext` as a typed parameter so they can't construct one
> themselves.

That's not advisory — every downstream defence mechanism (sensitivity
policy enforcement, breakglass auditing, the streaming verifier's
grounding cache) is keyed off the immutable `RequestContext` the
gateway produces. Skip the gateway and you skip the lot.

So the easy-looking option — "have the dashboard's BFF route call
Claude directly with the user's question" — is not a shortcut. It
is a second trust path that re-implements every safety control we
already have. Each tool we'd want from the chat (chart context, lab
lookup, …) becomes a fresh re-implementation against a different auth
surface; each safety feature we add later (sensitivity, breakglass,
verifier) becomes two edits.

A second trust path is not a feature with a deferral cost. It's an
architectural mistake with a *re-amortizing* cost.

---

## 3. The options

### Option A — Cookie produces a `RequestContext` (preserves the boundary)

The session is a credential. It just isn't *the* credential the
gateway speaks. Two flavours:

| | Mechanism | Trade |
|---|---|---|
| **A1** | Mint an internal JWT from session identity, hand it to the existing `AuthGateway` unchanged. The cookie-auth dependency *becomes* a JWT producer. | Surgical. No gateway changes. One new lazy "session bootstrap" step. The legacy module's JWT path is untouched and can keep working in parallel. |
| **A2** | Refactor `AuthGateway` to accept either a JWT or a session as input; make `RequestContext` construction generic. | Conceptually cleaner long-term. But every test that mocks the gateway changes; the auth surface becomes a tagged union; it's a much larger blast radius for a deadline week. |

### Option B — Parallel BFF pipeline (rejected)

Add `/api/agent/turn` that authenticates via cookie, runs Claude
directly, and bypasses `AuthGateway`. Defer the orchestrator, tools,
and verifier "until later".

We rejected this. Re-stating why explicitly because it's the option
that *looks* fastest: B doesn't defer work, it duplicates it. Every
feature added to it later either re-implements its A-path equivalent
or merges into A retroactively (the merge being more work than just
doing A from the start).

---

## 4. The decision

**A1.** Mint an internal JWT from the dashboard session and run it
through the existing `AuthGateway`. The dashboard's `POST /api/agent/turn`
route is a thin shim: cookie → session → internal JWT → `AuthGateway` →
`RequestContext` → existing `Orchestrator`.

```
Dashboard ──HttpOnly cookie──► Sidecar /api/agent/turn
                                    │
                                    ▼
                          SessionToInternalJWT
                          (lazy mint, cached on session)
                                    │
                                    ▼ Bearer <internal JWT>
                          AuthGateway.validate_request()  ◄── unchanged
                                    │
                                    ▼
                          RequestContext  ◄── the trust boundary
                                    │
                                    ▼
                          Orchestrator.turn()
                                    │
                                    ▼ tools call back to OpenEMR
                          /internal/demographics, /internal/labs, …
                          (validate the same JWT, unchanged)
```

The internal JWT is *exactly* what the legacy PHP module produces
today — same secret, same issuer (`openemr-agentforge`), same claim
shape. The only difference is who minted it. The PHP module mints
one when the legacy panel boots; the sidecar mints one when the
cookie route needs one. Tools cannot tell the difference, and that's
the whole point.

---

## 5. The OIDC-to-integer-user_id puzzle

The internal JWT requires:

| Claim | Source on legacy path | Source on dashboard path |
|---|---|---|
| `sub` (= `user_id`, int) | OpenEMR session direct | **must be resolved** |
| `username` | OpenEMR session direct | **must be resolved** |
| `role` | `UserRoleLookup` (DB) | **must be resolved** |
| `patient_id` | URL param in chart | URL param in chart |
| `breakglass_*` | UI toggle | (defer; default false) |

The dashboard session has:

- `sub` — OIDC subject (a UUID-shaped identifier; we don't get to
  pick its format, OpenEMR's authorization server emits it).
- `fhirUser` — a URI like `…/Practitioner/<uuid>`. The terminal
  segment is the **user UUID** from `users.uuid`, not the integer
  `users.id` that the agent pipeline keys off.
- `email`, `name`, `access_token`.

The candidate cheap parses ("just split the URI, grab the integer")
fail because the UUID *is* a UUID — it's not a stringified `users.id`.
There's a real mapping table involved (`users.uuid → users.id`), and
the sidecar doesn't have direct database access (and shouldn't —
OpenEMR is the source of truth on identity).

We considered three resolutions:

1. **Use the existing `/userinfo` endpoint.** OpenEMR advertises
   `/userinfo` in its OIDC discovery doc but actually returns 404
   (already noted in `docs/NEXT-SESSION.md` as a known issue). Not
   available to us.
2. **Refactor `RequestContext` to use UUIDs throughout.** Eliminates
   the resolution problem at the cost of touching every file that
   references `user_id` — including all tool fixtures, all sensitivity
   policy keys, every audit log row's foreign key. Out of scope for
   the defence week.
3. **Add a new `/me` endpoint to the OpenEMR module.** Validates the
   OAuth2 access_token (same `BearerTokenAuthorizationStrategy` used
   by FHIR), looks up the `users` row by UUID, returns
   `{user_id, username, role}`. The sidecar calls this once when
   bootstrapping a session and caches the result.

We chose **(3)**. It's small, additive, and consistent with the
existing module's pattern of exposing internal lookup endpoints.

The cache invariant: the resolved identity is bound to the OAuth2
access_token's lifetime, not the session's. If the access_token is
rotated mid-session (it isn't, today — refresh isn't wired) the
cached identity is invalidated alongside it.

---

## 6. What gets implemented

| Component | Where | What |
|---|---|---|
| `GET /apis/default/api/agentforge/me` | OpenEMR module | OAuth2-authed endpoint returning `{user_id, username, role}` for the bearer's user. |
| `OpenEMRMeFetcher` | sidecar | Async HTTPX client that calls the above with `Bearer <session.access_token>`. |
| `SessionToInternalJWT` | sidecar | Lazy mint + cache. Reads cached `OpenEMRMeFetcher` result; produces an HS256 JWT with the legacy claim shape. Cached on the session record (Redis), keyed by access_token, TTL aligned with the JWT's 5-minute exp. |
| `get_request_context_from_cookie` | sidecar | FastAPI dependency: cookie → session → minted JWT → `gateway.validate_request("Bearer …")` → `RequestContext`. |
| `POST /api/agent/turn` | sidecar dashboard router | Mirrors `/turn`'s body shape (`message`, `session_id`, `document_id`, `evidence_query`, plus a new `mode` field). Uses the cookie dependency above. Re-uses orchestrator + verifier unchanged. |
| `useAgentTurn` | dashboard | Composable that POSTs `/api/agent/turn` with the active patient context. Updates the Pinia store on response. |
| `AgentDrawer.send()` | dashboard | Replaces the local-only `addUserTurn` echo with a real send → wait → assistant turn cycle. |

What gets **deferred** (with explicit notes in `docs/DEVIATIONS.md`):

- **Token refresh.** When the access_token expires the user
  re-authenticates. Mid-session refresh is a follow-up.
- **Breakglass UI.** The internal JWT carries `breakglass_flag=false`
  unconditionally from the dashboard for now. Adding the dashboard
  affordance is straightforward — it just isn't on the defence-week
  critical path.
- **Streaming on the new route.** The legacy `/turn` supports SSE
  when `STREAMING_ENABLED=true`. The new BFF route ships buffered
  only — streaming through a cookie-authed reverse proxy is its own
  small puzzle (proxy buffering, mid-stream auth refresh).

---

## 7. Why this is defensible (and not a corner)

The single test that matters: **what does it cost to delete the legacy
JWT path when the dashboard is the only entry point?**

Under A1: one route file (`/turn`) and one bootstrap path in the PHP
module disappear. The orchestrator, every tool, the verifier, and the
sensitivity policy are all untouched — they were always speaking to
`RequestContext`, never to a specific minter. The new BFF route stays.

Under B (rejected): we'd have two pipelines feeding two `Orchestrator`
implementations against two trust surfaces, and "deleting the legacy
path" means *also* deleting half the code we wrote for the new path.

A1 is the smallest change that preserves the boundary and the
smallest change to walk away from when the legacy module is removed.
That is the definition of defensible.

---

## 8. Cross-references

- `ARCHITECTURE.md §2` — `RequestContext` as the trust boundary.
- `docs/NEXT-SESSION.md` — `/userinfo` 404 caveat; W2 timeline.
- `docs/DEVIATIONS.md` — running log of what we've shipped vs the
  original plan; deferrals from §6 land here when the implementation
  ships.
- `interface/modules/custom_modules/oe-module-agentforge/src/Services/AgentJwtService.php`
  — canonical claim shape that `SessionToInternalJWT` must match.
- `sidecar/src/agentforge/gateway/auth_gateway.py` — `RequestContext`
  + `AuthGateway` we're funnelling through.
- `sidecar/src/agentforge/dashboard_auth/routes.py` — existing FHIR
  proxy's cookie pattern that the new route mirrors.
