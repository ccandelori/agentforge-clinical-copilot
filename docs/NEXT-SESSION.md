# Where we left off — 2026-05-07 afternoon (vue-ui live on droplet)

Read me first when picking the project back up. Update or delete me
when the state captured here goes stale.

## Headline

**The vue-ui rewrite is live on the droplet at
[https://143.244.157.90:9300/dashboard/](https://143.244.157.90:9300/dashboard/)**.
Same-origin BFF, real OAuth, real FHIR, real `/api/agent/turn` with
inline-citation surfacing. The dashboard-port frontend in `dashboard/`
is now dead code — kept on the branch as a fallback but not deployed.

W2 deadline (Sun 2026-05-10 noon) is in striking distance: the live
demo URL works, OAuth + Synthea data + the AgentForge drawer all
function. What's left is **defense slides + run-through + slide review**.

Branch `feat/dashboard-port` is ~30 commits ahead of origin, **no MR**.
Local tests pre-vue-ui swap: 216 dashboard vitest, 1083 sidecar pytest,
352 PHP isolated. **vue-ui has zero tests of its own** — that's a known
gap, fine for the demo, real for post-deadline.

## What shipped this session (Thursday afternoon 2026-05-07)

This was the day vue-ui went from a polished mock to the production
deliverable. The full sequence on `feat/dashboard-port`:

```
3a5fcc6be  docs(migration): phase 0 recon — vue-ui ↔ dashboard-port migration plan
2b31e7a70  feat(dashboard): lift vue-ui design tokens + add dark mode toggle
60e8861be  feat(sidecar): T38.14 serve dashboard SPA via FastAPI StaticFiles
71badfcc5  feat(agent-drawer): T38.11 citation pills + history pane + suggestion chips
a6184a220  feat(dashboard): vitals strip with sparklines + dark-mode regression fixes
29a5b21fe  chore(vue-ui): import vue-ui source from feat/vue-ui-rewrite
6765734f9  feat(sidecar): repoint T38.14 from dashboard/dist → vue-ui/dist
89c8b813c  feat(vue-ui): swap mock auth for BFF flow + Vite proxy
574a18aa2  feat(vue-ui): replace mock data layer with FHIR-backed implementation
567c2b2d8  feat(vue-ui): wire AgentForge drawer to real /api/agent/turn
768bdb2b1  fix(vue-ui): move encounter drafts off localStorage (PHI risk)
becfaabff  fix(vue-ui): patient chart UX — open encounters, problem filter, header actions
7e611efbd  feat(agentforge): real citations + defensive re-auth + reactivity fix
25931593d  chore(vue-ui): dev port 5174 → 5173, env example for VITE_SIDECAR_BASE
```

The commits not yet made (working-tree dirty after the deploy):

* `vue-ui/vite.config.ts` — `base: '/dashboard/'` for production builds
* `vue-ui/src/router/index.ts` — `createWebHistory(import.meta.env.BASE_URL)`
* `vue-ui/index.html` — favicon path made relative
* `docker/openemr-proxy/agentforge-proxy.conf` (new) — Apache reverse proxy
* `docs/NEXT-SESSION.md` — this file

Commit those next session. They're the production cutover (T38.14
proper) and deserve their own commit.

## How the live deploy is wired (Path B — Apache reverse proxy)

The droplet's OpenEMR container hosts both PHP and the Vue SPA at the
same origin so the HttpOnly session cookie rides the OAuth flow:

```
Browser → https://143.244.157.90:9300/...
            │
            ├─ /dashboard/*  ──► Apache → agentforge-sidecar:8000/*  (StaticFiles, prefix-stripped)
            ├─ /auth/*       ──► Apache → agentforge-sidecar:8000/auth/*
            ├─ /api/*        ──► Apache → agentforge-sidecar:8000/api/*
            └─ /apis/, /interface/, /portal/, /oauth2/*  ──► OpenEMR PHP (unchanged)
```

The proxy config is in `docker/openemr-proxy/agentforge-proxy.conf`,
deployed to `/etc/apache2/conf.d/zz-agentforge-proxy.conf` inside the
openemr container. **It's not persistent across container recreation
yet** — if `development-easy-openemr-1` ever restarts from scratch
(image refresh, etc.), the conf has to be re-injected:

```bash
scp docker/openemr-proxy/agentforge-proxy.conf root@143.244.157.90:/opt/agentforge/agentforge-proxy.conf
ssh root@143.244.157.90 \
  'docker cp /opt/agentforge/agentforge-proxy.conf development-easy-openemr-1:/etc/apache2/conf.d/zz-agentforge-proxy.conf && \
   docker exec development-easy-openemr-1 httpd -k graceful'
```

Folding this into `deploy-droplet.sh` is a post-deadline TODO.

## Production OAuth state

* Client registered in OpenEMR `oauth_clients` table:
  - `client_id`: `bdWGHR1OMeJbxMmvSRskPghCa_tIzUSz0qmDs5-uEIs`
  - `client_name`: "AgentForge Dashboard BFF (sidecar, prod)"
  - `redirect_uris`: `["https://143.244.157.90:9300/auth/callback"]`
  - `post_logout_redirect_uris`: `["https://143.244.157.90:9300/dashboard/"]`
  - `is_enabled = 1`
* Sidecar env in `/opt/agentforge/sidecar/.env` on droplet:
  ```
  DASHBOARD_APP_URL=https://143.244.157.90:9300
  DASHBOARD_OAUTH_AUTHORITY=https://143.244.157.90:9300/oauth2/default
  DASHBOARD_OAUTH_CLIENT_ID=bdWGHR1OMeJbxMmvSRskPghCa_tIzUSz0qmDs5-uEIs
  DASHBOARD_OAUTH_CLIENT_SECRET=<set, in droplet .env only — not committed>
  DASHBOARD_OAUTH_REDIRECT_URI=https://143.244.157.90:9300/auth/callback
  DASHBOARD_OAUTH_POST_LOGOUT_REDIRECT_URI=https://143.244.157.90:9300/dashboard/
  DASHBOARD_SESSION_COOKIE_SECURE=true
  ```
* Container started with `--env-file /opt/agentforge/sidecar/.env`
  so the env loads on `docker run`. **`docker restart` does NOT reload
  env vars** — to push env-file changes you have to `docker rm -f` and
  `docker run` again. (Hit this hard during cutover.)

## What works in the live demo

* Sign in with OpenEMR (admin/pass) → bounces to OAuth → returns
  authenticated → SPA loads.
* Patient list (real Synthea data, paginated, search/filter).
* Patient dashboard with vitals strip, problems (active/inactive
  toggle), meds, allergies, encounters, labs.
* Encounter editor opens by clicking from EncountersCard;
  + New encounter creates a sessionStorage draft.
* AgentForge drawer:
  - Real `/api/agent/turn` round-trip
  - Inline `[type #id]` citations from the model surface as clickable
    pills under each assistant bubble
  - Citations tab shows the actual record text (not just the token);
    "View source" expands to full content
  - Defensive 401 handler bounces to /login on token expiry
* "Edit demographics" modal (preview-only — no FHIR PATCH yet).

## Known gaps (post-deadline)

1. **No vue-ui tests.** Vitest is wired but no specs. Ship some
   smoke tests after the demo.
2. **Sign & Finalize is a no-op.** Real `POST /api/fhir/Encounter` is
   the right move; today it just stamps signedAt locally.
3. **Edit demographics is preview-only.** Same — needs `PATCH /Patient`.
4. **Token refresh not implemented.** When the OAuth access_token
   expires (~1 hr default), FHIR returns 401, the SPA's
   `auth:unauthorized` handler force-bounces to /login. Acceptable
   for a defense demo. For real usage, sidecar should refresh via
   `refresh_token`.
5. **`docs/NEXT-SESSION.md` mentioned a TODO that's now closed
   (T38.11 citation overlay):** the surface is alive — the model emits
   inline `[note #N]`, the sidecar parses + resolves, the SPA renders
   pills. What it doesn't do: track UUIDs back to a *clickable
   citation overlay* on the chart itself (jumping from a citation to
   the underlying note's location in the patient view). That's a v2.
6. **vue-ui's CalendarView, SettingsView, EncounterEditor (Sign path)
   are mocked** — not wired to real FHIR. Not part of the W2 demo
   path, but they're shown in the sidebar.
7. **Apache proxy conf is not persisted** — recreate-on-fresh-container
   workflow above.
8. **Synthea data quirks:** vitals required `UuidMapping::createAllMissingResourceUuids()`
   to be runnable from FHIR — see
   `~/.claude/projects/.../memory/project_synthea_uuid_mapping_gap.md`
   for the recipe. Re-run after any Synthea reimport.
9. **dashboard-port `dashboard/` directory is dead code** — kept on the
   branch as fallback. Drop in a follow-up cleanup commit post-defense.

## How to redeploy

```bash
# Code changes in vue-ui/* or sidecar/*?
./scripts/deploy-droplet.sh sidecar   # rebuilds image, recreates container with --env-file
./scripts/deploy-droplet.sh dashboard # rebuilds vue-ui SPA, rsyncs dist (no container restart)

# Apache conf changed?
scp docker/openemr-proxy/agentforge-proxy.conf root@143.244.157.90:/opt/agentforge/
ssh root@143.244.157.90 \
  'docker cp /opt/agentforge/agentforge-proxy.conf development-easy-openemr-1:/etc/apache2/conf.d/zz-agentforge-proxy.conf && \
   docker exec development-easy-openemr-1 httpd -t && \
   docker exec development-easy-openemr-1 httpd -k graceful'

# Sanity check
./scripts/deploy-droplet.sh check
python3 -c "import ssl,urllib.request; ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE; print(urllib.request.urlopen('https://143.244.157.90:9300/auth/whoami', context=ctx, timeout=5).read())"
```

## Things to focus on next session

In order:

1. **Commit the production cutover** (vite base, router base, favicon
   relpath, Apache conf, this NEXT-SESSION.md). One commit.
2. **Defense slides.** `docs/w2-defense-slides.html` is dirty from
   pre-session edits and the architecture changed substantially.
   Walk through with the slide deck open and the live URL beside it.
3. **Run the full demo flow** end-to-end on the live URL once or
   twice before Sunday so you've got muscle memory.
4. **(Optional)** Drop the dashboard-port frontend if it bothers you
   to have dead code on the branch.

The hard work is done. The demo is real and reachable.
