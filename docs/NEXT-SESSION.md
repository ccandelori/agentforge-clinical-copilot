# Where we left off — 2026-05-07 evening (vue-ui live, intake next)

Read me first when picking the project back up. Update or delete me
when the state captured here goes stale.

## Headline

The vue-ui is **live on the droplet at
[https://143.244.157.90:9300/dashboard/](https://143.244.157.90:9300/dashboard/)**
with real OAuth, real FHIR, real `/api/agent/turn` (with structured
citations and rich kind-aware excerpts). The legacy `dashboard/`
frontend is dead code on the branch, kept as a fallback but not the
deploy target. From this session forward we work only on `vue-ui/`
and the sidecar — no more legacy work.

**Next thread: bring document uploads into the vue-ui AgentForge drawer.**
The sidecar already accepts intake documents — see "Document uploads
— starting context" below for the picking-up plan.

W2 deadline: Sun 2026-05-10 noon. Two days left.

## What shipped this session (Thursday afternoon + evening 2026-05-07)

The day vue-ui went from a polished mock to the production deliverable:

```
0137b0f06  fix(agentforge): kind-aware citation excerpts for non-narrative records
16810ef5f  perf(vue-ui): in-memory patient bundle cache (60s TTL)
1e73e2b4e  fix(vue-ui): partial-load patient dashboard + 20s FHIR timeout
6470906f8  docs(next-session): note DASHBOARD_FHIR_BASE_URL must be set on droplet
7621995ea  docs(next-session): note site_addr_oath global pinned localhost
e6f798dd9  feat(deploy): T38.14 production cutover — vue-ui live at /dashboard/
25931593d  chore(vue-ui): dev port 5174 → 5173, env example for VITE_SIDECAR_BASE
7e611efbd  feat(agentforge): real citations + defensive re-auth + reactivity fix
becfaabff  fix(vue-ui): patient chart UX — open encounters, problem filter, header actions
768bdb2b1  fix(vue-ui): move encounter drafts off localStorage (PHI risk)
567c2b2d8  feat(vue-ui): wire AgentForge drawer to real /api/agent/turn
574a18aa2  feat(vue-ui): replace mock data layer with FHIR-backed implementation
89c8b813c  feat(vue-ui): swap mock auth for BFF flow + Vite proxy
6765734f9  feat(sidecar): repoint T38.14 from dashboard/dist → vue-ui/dist
29a5b21fe  chore(vue-ui): import vue-ui source from feat/vue-ui-rewrite
a6184a220  feat(dashboard): vitals strip with sparklines + dark-mode regression fixes
71badfcc5  feat(agent-drawer): T38.11 citation pills + history pane + suggestion chips
60e8861be  feat(sidecar): T38.14 serve dashboard SPA via FastAPI StaticFiles
2b31e7a70  feat(dashboard): lift vue-ui design tokens + add dark mode toggle
3a5fcc6be  docs(migration): phase 0 recon — vue-ui ↔ dashboard-port migration plan
```

## Document uploads — starting context for next session

This is the next scope: **port the intake document upload UX from the
legacy `agent_panel.js` flow into vue-ui's AgentForge drawer**. Sidecar
work is already done; the gap is purely client-side.

### What already works (don't redo)

- **Sidecar `/api/agent/turn`** accepts an optional `pdf_pages: list[RenderedPage]`
  parameter (see `sidecar/src/agentforge/orchestrator/__init__.py` →
  `Orchestrator.turn()`). When pages are supplied, the W2 graph
  routes through the supervisor + extractor + verifier instead of
  the W1 chart-question loop.
- **`agentforge.tools.attach_and_extract`** is the tool that takes a
  document, runs OCR/VLM extraction, returns structured fields with
  per-field bbox + bbox_confidence per the W2 Citation contract
  (`sidecar/src/agentforge/schemas/citation.py`). The verifier rejects
  anything below the bbox-confidence floor.
- **OpenEMR-side document storage** + the PHP module's
  `public/internal/get_document_bytes.php` endpoint exposes uploaded
  documents back to the sidecar by id. Already deployed on droplet
  (verified: 17 internal endpoints in the module).
- **Browser upload (T6)** — done in dashboard-port era. PR
  `feat/w2-task6-document-upload` (or similar) landed pre-vue-ui.
  Mechanism is OpenEMR's existing document upload flow + a
  `document_id` returned to the agent.
- **Citation pills + Citations pane** in vue-ui's drawer already
  render structured citations. Intake-extracted fields with bboxes
  would render through the same pipeline (with kind = `note` or a
  new `intake` kind we can add to the closed enum if we want a
  distinct badge).

### What needs to be done in vue-ui

1. **File-attach button in `AgentChatPane`'s composer.** Currently a
   no-op (`attach button (no-op for now)`). Wire it to a hidden
   `<input type="file" accept=".pdf,image/*">` and emit the file via
   the agent turn flow.
2. **Upload mechanism**: post the file to OpenEMR's document upload
   endpoint (re-use what dashboard-port did — see the
   `feat/w2-task6-document-upload` work for the API shape — or hit
   the sidecar BFF if there's an /api/upload path that proxies it).
   Returns a `document_id`.
3. **Pass `document_id` (and/or rendered `pdf_pages`) into the agent
   turn request body.** The `useAgentTurn.send()` shape in
   `vue-ui/src/composables/useAgentTurn.ts` currently sends
   `{ message, patient_uuid?, session_id? }`. Extend to include
   `document_id` and let the sidecar decide whether to render PDF
   pages on its side.
4. **Render the extraction result panel** below the assistant bubble
   when the response includes structured extraction. Per
   `_TURN_EXTRACTION_VAR` in the orchestrator, the sidecar already
   stashes a per-turn extraction snapshot — surface it via the
   `AgentTurnResponse` and render in the chat. Confirmable
   field-edit panel is the goal (see W2_ARCHITECTURE.md §2.2 for
   the contract).
5. **Maybe add an `Intake` mode to the drawer.** vue-ui's drawer is
   currently chat-only (no scope tabs). Dashboard-port had
   Chart/Intake/Research mode pills. For document upload UX,
   either: (a) keep chat as the only surface and trigger intake
   automatically when a file is attached, or (b) lift the
   Chart/Intake/Research mode strip from dashboard-port. Decision
   for next session.

### Key references

- `interface/modules/custom_modules/oe-module-agentforge/public/internal/get_document_bytes.php`
- `sidecar/src/agentforge/tools/attach_and_extract.py`
- `sidecar/src/agentforge/schemas/citation.py` (Citation/PageBBox)
- `sidecar/src/agentforge/orchestrator/__init__.py` (`turn(... pdf_pages=...)`)
- `dashboard/src/components/AgentDrawer.vue` (legacy intake mode UI to crib)
- `dashboard/src/composables/useAgentTurn.ts` (legacy upload-aware composable
  if it exists; otherwise the agent_panel.js path is the reference)
- `W2_ARCHITECTURE.md` §2.2 (intake contract), §2.4 (verifier floor)
- `docs/adr/0001-dashboard-auth-bridging.md` (BFF / JWT model that any
  upload path also rides)

## Production state — for the demo + the cutover knobs

### Live URL
https://143.244.157.90:9300/dashboard/ — the vue-ui SPA. Self-signed cert.

### Apache reverse proxy

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
openemr container. **It's not persistent across container recreation**
— if `development-easy-openemr-1` ever restarts from scratch, re-inject:

```bash
scp docker/openemr-proxy/agentforge-proxy.conf root@143.244.157.90:/opt/agentforge/agentforge-proxy.conf
ssh root@143.244.157.90 \
  'docker cp /opt/agentforge/agentforge-proxy.conf development-easy-openemr-1:/etc/apache2/conf.d/zz-agentforge-proxy.conf && \
   docker exec development-easy-openemr-1 httpd -k graceful'
```

### Production OAuth client

Registered in OpenEMR `oauth_clients`:
- `client_id`: `bdWGHR1OMeJbxMmvSRskPghCa_tIzUSz0qmDs5-uEIs`
- `redirect_uris`: `["https://143.244.157.90:9300/auth/callback"]`
- `post_logout_redirect_uris`: `["https://143.244.157.90:9300/dashboard/"]`
- `is_enabled = 1`

Sidecar env in `/opt/agentforge/sidecar/.env` on droplet:
```
DASHBOARD_APP_URL=https://143.244.157.90:9300
DASHBOARD_OAUTH_AUTHORITY=https://143.244.157.90:9300/oauth2/default
DASHBOARD_OAUTH_CLIENT_ID=bdWGHR1OMeJbxMmvSRskPghCa_tIzUSz0qmDs5-uEIs
DASHBOARD_OAUTH_CLIENT_SECRET=<set>
DASHBOARD_OAUTH_REDIRECT_URI=https://143.244.157.90:9300/auth/callback
DASHBOARD_OAUTH_POST_LOGOUT_REDIRECT_URI=https://143.244.157.90:9300/dashboard/
DASHBOARD_SESSION_COOKIE_SECURE=true
DASHBOARD_FHIR_BASE_URL=http://openemr/apis/default/fhir
```

**`docker restart` does NOT reload env-file vars** — to push env-file
changes you have to `docker rm -f` and `docker run` again with
`--env-file`. The deploy script's `deploy_sidecar` does this correctly;
only manual `docker restart` invocations are dangerous.

### Per-droplet Synthea data backfill (vitals)

Each droplet's MySQL is a separate database. The dev-easy fix for
Synthea-imported vitals (forms-row backfill + uuid_mapping backfill)
has to be repeated per environment:

```bash
# 1. Link orphan form_vitals to encounters
ssh root@143.244.157.90 'docker exec development-easy-mysql-1 mariadb -uopenemr -popenemr openemr -e "
INSERT INTO forms (date, encounter, form_name, form_id, pid, user, deleted, formdir, issue_id, provider_id)
SELECT v.date,
  (SELECT fe.encounter FROM form_encounter fe WHERE fe.pid = v.pid ORDER BY ABS(TIMESTAMPDIFF(SECOND, fe.date, v.date)) LIMIT 1),
  \"Vitals\", v.id, v.pid, \"ExternalProvider\", 0, \"vitals\", 0, 0
FROM form_vitals v
WHERE NOT EXISTS (SELECT 1 FROM forms f WHERE f.form_id = v.id AND f.formdir=\"vitals\");"'

# 2. Backfill uuid_mappings (idempotent; order matters — forms first)
ssh root@143.244.157.90 'docker exec development-easy-openemr-1 php -r "
\$_GET[\"site\"] = \"default\";
\$ignoreAuth = true;
require \"/var/www/localhost/htdocs/openemr/interface/globals.php\";
echo \OpenEMR\Common\Uuid\UuidMapping::createAllMissingResourceUuids();"'
```

### `DASHBOARD_FHIR_BASE_URL` must be set in sidecar env

Default is empty → all FHIR requests fail with `502 Bad Gateway`
("FHIR upstream unreachable"). Production value:
`http://openemr/apis/default/fhir` (docker network alias).

### OpenEMR's `site_addr_oath` global must point at the droplet

Dev-easy ships with `globals.site_addr_oath = https://localhost:9300`.
OpenEMR uses this to build redirect URLs during the OAuth login bounce;
left at localhost, the user gets redirected to their own machine
mid-flow. Set per-droplet:

```bash
ssh root@143.244.157.90 \
  'docker exec development-easy-mysql-1 mariadb -uopenemr -popenemr openemr -e \
    "UPDATE globals SET gl_value = \"https://143.244.157.90:9300\" WHERE gl_name = \"site_addr_oath\";"
   docker exec development-easy-openemr-1 httpd -k graceful'
```

## How to redeploy

```bash
# Code changes in vue-ui/* or sidecar/*?
./scripts/deploy-droplet.sh sidecar    # rebuilds image, recreates container with --env-file
./scripts/deploy-droplet.sh dashboard  # rebuilds vue-ui SPA, rsyncs dist (no container restart)
./scripts/deploy-droplet.sh module     # rsyncs PHP module, docker cp into openemr container

# Apache conf changed?
scp docker/openemr-proxy/agentforge-proxy.conf root@143.244.157.90:/opt/agentforge/
ssh root@143.244.157.90 \
  'docker cp /opt/agentforge/agentforge-proxy.conf development-easy-openemr-1:/etc/apache2/conf.d/zz-agentforge-proxy.conf && \
   docker exec development-easy-openemr-1 httpd -k graceful'

# Sanity check
./scripts/deploy-droplet.sh check
python3 -c "import ssl,urllib.request; ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE; print(urllib.request.urlopen('https://143.244.157.90:9300/auth/whoami', context=ctx, timeout=5).read())"
```

## Known gaps (post-deadline / future)

1. **No vue-ui tests.** Vitest is wired but no specs.
2. **Sign & Finalize is a no-op.** Real `POST /api/fhir/Encounter` is
   the right move; today it just stamps signedAt locally.
3. **Edit demographics is preview-only.** Same — needs `PATCH /Patient`.
4. **Token refresh not implemented.** When the OAuth access_token
   expires (~1 hr default), FHIR returns 401, the SPA's
   `auth:unauthorized` handler force-bounces to /login.
5. **CalendarView / SettingsView are mocked** — not wired to real FHIR.
6. **Apache proxy conf is not persisted** across openemr container
   recreation — recipe above to re-inject.
7. **`dashboard/` directory is dead code** — kept on the branch as
   fallback. Drop in a follow-up cleanup commit post-defense.
8. **Citations show all kinds even if not in the index** — encounter/
   problem/etc citations now produce useful excerpts via the kind-aware
   metadata schema, but unindexed citations still surface as raw tokens.
   Filter in turn_route._build_citations if we want only verified ones.

## Things to focus on next session

In order of urgency for the W2 deadline:

1. **Document uploads in vue-ui's drawer** (this is what got pinned —
   see "Document uploads — starting context" above).
2. **Defense slides** (`docs/w2-defense-slides.html` is dirty from
   pre-session edits, needs a refresh against the new architecture).
3. **Run the live demo end-to-end** once or twice before Sunday.
4. **Drop the `dashboard/` directory** if it bothers you to have dead
   code on the merged branch.

## Branch state at this commit

- Branch: `feat/dashboard-port`
- Origin: GitLab (`labs.gauntletai.com/cameroncandelori/openemr`)
- ~32 commits ahead of `origin/main`. **(Push + open MR before next session.)**

Pre-existing dirty files at session end (NOT in scope, NOT committed):
- `.taskmaster/tasks/tasks.json` (tracking deltas from earlier work)
- `docs/w2-defense-slides.html` (presession edits, deserves a fresh pass)
- `docs/architecture-overview-slides.html` (untracked, presession addition)
