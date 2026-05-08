# Where we left off — 2026-05-08 (doc-upload thread shipped, ~36 hours to deadline)

Read me first when picking the project back up. Update or delete me
when the state captured here goes stale.

## Headline

The W2 document-upload thread is **shipped end-to-end on the live
droplet**. A clinician can attach an intake-form PDF in the AgentForge
drawer, see the chat reply summarise it, see an `<ExtractionPanel>`
render the structured fields below the bubble, click "View source (N)",
and get a modal with the PDF rendered + transparent rectangles
overlaid where each extracted field came from. Live URL:
**[https://143.244.157.90:9300/dashboard/](https://143.244.157.90:9300/dashboard/)**.

W2 deadline: Sun 2026-05-10 noon. ~36 hours left.

**Open priorities** (no specific order): defense slides refresh,
end-to-end live-demo dry run, T38.13 (PATIENT_DASHBOARD_MIGRATION
defense doc).

## What shipped this session (Thursday 2026-05-07 → Friday 2026-05-08)

`feat/t38.11-12-document-flow` merged via MR !40 on 2026-05-08 (~22
commits). The commit graph in chronological order:

```
T38.15 — Document upload pipeline:
  feat(vue-ui): add useDocumentUpload composable
  feat(vue-ui): pass document_id through useAgentTurn
  feat(vue-ui): wire file-attach in AgentChatPane composer
  feat(agentforge-php): JWT-authed internal upload_document.php endpoint
  feat(sidecar): DocumentUploadWriter — JWT-authed PHP bridge
  feat(sidecar): POST /api/agent/upload BFF route
  feat(sidecar): AgentTurnRequest.document_id + orchestrator pdf_pages wiring
  fix(vue-ui): send doc_type on upload — BFF requires it
  fix(vue-ui): bump agent-turn client timeout 30s → 120s for doc extraction

T38.11 — DocumentViewer component:
  feat(vue-ui): add DocumentViewer with bbox overlay (pdfjs-dist + injectable PdfLoader)

T38.12 — Extraction surfacing:
  feat(sidecar): surface per-turn extraction snapshot on AgentTurnResponse
  feat(vue-ui): plumb intake extraction through useAgentTurn + store
  feat(vue-ui): render <ExtractionPanel> below assistant bubble

T38.16 — Citation overlay end-to-end:
  feat(sidecar): GET /api/agent/document/{id} BFF route
  feat(sidecar): wire document-fetch router into app factory
  feat(vue-ui): View source modal on ExtractionPanel — bbox overlay
  fix(vue-ui): always show View source button when documentId known
  fix(vue-ui): await nextTick before painting PDF canvases
  fix(vue-ui): use sidecar Citation field names — quote_or_value/field_or_chunk_id

Performance:
  perf(sidecar): make Claude model configurable via CLAUDE_MODEL env
  (droplet env now: CLAUDE_MODEL + ANTHROPIC_VISION_MODEL = claude-haiku-4-5-20251001)

Security hygiene:
  security: rotate OAuth client + admin password; scrub leaked OAuth client_id
  chore(deploy): require DROPLET_HOST env, no hardcoded IP
  chore(deploy): source scripts/.env.local for personal config (gitignored)
  chore(docs): scrub workstation paths + droplet IP from supporting docs

Demo seeding:
  chore(demo): seed 4 personas matching W2 example intake forms
  chore(demo): vendor W2 example documents into repo
```

## Demo runbook (production droplet)

1. Open [https://143.244.157.90:9300/dashboard/](https://143.244.157.90:9300/dashboard/) — accept self-signed cert.
2. Login (credentials in repo `README.md`).
3. Pick **Margaret Chen** (`MRN-2026-04481`) — typed PDF, cleanest extraction.
4. Open AgentForge drawer (right edge).
5. Click paperclip → attach `week2/example-documents/intake-forms/p01-chen-intake-typed.pdf`.
6. Send "Extract this intake form."
7. Wait ~12-15s (Haiku vision; cold first call). Chat reply lists extracted fields; `<ExtractionPanel>` renders below bubble.
8. Click **"View source (18)"** — modal opens, PDF renders, blue rectangles overlay extracted-field regions.

Three other personas seeded for additional test runs:

| Persona | pid | UUID | Intake form |
|---|---|---|---|
| Margaret Chen | 29 | `a1b9f2f6-d2eb-49e1-adce-35ca6c1f8ac0` | `p01-chen-intake-typed.pdf` |
| James Whitaker | 30 | `a1b9f2f6-ed02-4156-9dcb-95d77123f009` | `p02-whitaker-intake.pdf` |
| Sofia Reyes | 31 | `a1b9f2f6-faae-4a57-a54a-9a4c6af7611e` | `p03-reyes-intake.png` |
| Robert Kowalski | 32 | `a1b9f2f7-0edb-4526-9656-a14c16c90823` | `p04-kowalski-intake.png` |

Re-run `scripts/seed-demo-patients.php` (idempotent on `pubpid`) if any persona disappears.

## Production state — for the demo + the cutover knobs

### Live URL
[https://143.244.157.90:9300/dashboard/](https://143.244.157.90:9300/dashboard/) — vue-ui SPA. Self-signed cert.

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
scp docker/openemr-proxy/agentforge-proxy.conf root@<droplet>:/opt/agentforge/agentforge-proxy.conf
ssh root@<droplet> \
  'docker cp /opt/agentforge/agentforge-proxy.conf development-easy-openemr-1:/etc/apache2/conf.d/zz-agentforge-proxy.conf && \
   docker exec development-easy-openemr-1 httpd -k graceful'
```

### Production OAuth client

Registered in OpenEMR `oauth_clients`. The original client_id leaked
on GitHub during the public-mirror push and has been rotated; old
client is `is_enabled=0, revoke_date=NOW()`.

- `client_id` / `client_secret`: live values are in
  `/opt/agentforge/sidecar/.env` on the droplet — never check them
  into the repo.
- `redirect_uris`: `["https://143.244.157.90:9300/auth/callback"]`
- `post_logout_redirect_uris`: `["https://143.244.157.90:9300/dashboard/"]`
- `is_enabled = 1`

Sidecar env in `/opt/agentforge/sidecar/.env` on droplet (sensitive
values redacted here):
```
DASHBOARD_APP_URL=https://143.244.157.90:9300
DASHBOARD_OAUTH_AUTHORITY=https://143.244.157.90:9300/oauth2/default
DASHBOARD_OAUTH_CLIENT_ID=<live; rotated 2026-05-08>
DASHBOARD_OAUTH_CLIENT_SECRET=<live; rotated 2026-05-08>
DASHBOARD_OAUTH_REDIRECT_URI=https://143.244.157.90:9300/auth/callback
DASHBOARD_OAUTH_POST_LOGOUT_REDIRECT_URI=https://143.244.157.90:9300/dashboard/
DASHBOARD_SESSION_COOKIE_SECURE=true
DASHBOARD_FHIR_BASE_URL=http://openemr/apis/default/fhir
CLAUDE_MODEL=claude-haiku-4-5-20251001
ANTHROPIC_VISION_MODEL=claude-haiku-4-5-20251001
```

**`docker restart` does NOT reload env-file vars** — to push env-file
changes you have to `docker rm -f` and `docker run` again with
`--env-file`. The deploy script's `deploy_sidecar` does this correctly;
only manual `docker restart` invocations are dangerous.

### OpenEMR admin password

Rotated from default `admin/pass` to a 24-char generated value (live
README has it). Don't roll back to default — random brute-force
scanners hit the old creds constantly.

### Local dev pattern for personal config

`scripts/.env.local` (gitignored) holds your `DROPLET_HOST` and any
other personal overrides. The deploy script auto-sources it. See
`scripts/.env.local.example` for the template. Required because
`./scripts/deploy-droplet.sh` no longer carries a hardcoded default.

### Per-droplet Synthea data backfill (vitals)

Each droplet's MySQL is a separate database. The dev-easy fix for
Synthea-imported vitals (forms-row backfill + uuid_mapping backfill)
has to be repeated per environment:

```bash
# 1. Link orphan form_vitals to encounters
ssh root@<droplet> 'docker exec development-easy-mysql-1 mariadb -uopenemr -popenemr openemr -e "
INSERT INTO forms (date, encounter, form_name, form_id, pid, user, deleted, formdir, issue_id, provider_id)
SELECT v.date,
  (SELECT fe.encounter FROM form_encounter fe WHERE fe.pid = v.pid ORDER BY ABS(TIMESTAMPDIFF(SECOND, fe.date, v.date)) LIMIT 1),
  \"Vitals\", v.id, v.pid, \"ExternalProvider\", 0, \"vitals\", 0, 0
FROM form_vitals v
WHERE NOT EXISTS (SELECT 1 FROM forms f WHERE f.form_id = v.id AND f.formdir=\"vitals\");"'

# 2. Backfill uuid_mappings (idempotent; order matters — forms first)
ssh root@<droplet> 'docker exec development-easy-openemr-1 php -r "
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
mid-flow. Set per-droplet (`<droplet>` → your IP/hostname).

## How to redeploy

```bash
# Code changes in vue-ui/* or sidecar/*?
./scripts/deploy-droplet.sh sidecar    # rebuilds image, recreates container with --env-file
./scripts/deploy-droplet.sh dashboard  # rebuilds vue-ui SPA, rsyncs dist (no container restart)
./scripts/deploy-droplet.sh module     # rsyncs PHP module, docker cp into openemr container

# Sanity check
./scripts/deploy-droplet.sh check
```

If `DROPLET_HOST` is unset, the script exits 2 with a hint pointing
at `scripts/.env.local`.

## Known gaps surfaced this session

W2-deadline-relevant (worth deciding before Sunday noon):

1. **DocumentViewer is PDF-only.** PNG intake forms (Reyes, Kowalski
   — `*.png` files in `week2/example-documents/intake-forms/`) won't
   render in the modal — PDF.js can't parse `image/png` bytes. The
   BFF returns the bytes correctly, the renderer fails. Half-day fix
   is a sibling `<ImageViewer>` for raster sources, switched on at
   the modal layer based on Content-Type. **Demo-shippable workaround:
   stick to typed PDFs (Chen, Whitaker) for the bbox-overlay story.**
2. **Bbox placement is approximate.** Haiku-vision produces bboxes
   that land in the right region but are offset by a row/cell or so.
   Acceptable as a "where on the page" trust artifact; not pixel-tight.
3. **Demographics labels are raw snake_case.** `legal_name`,
   `date_of_birth`, `sex_assigned_at_birth`, etc. — surfaced from the
   extractor's field keys verbatim. Quarter-day cleanup: a small
   `humanizeFieldName()` helper in `<ExtractionPanel>`.
4. **Chat reply duplicates panel content.** When the model decides to
   describe the extracted fields in prose, the same content shows up
   in the chat bubble AND the structured panel below. Either tighten
   the synthesizer prompt to defer to the panel, or accept it.
5. **Planner Haiku tool-call fallback warning.** Logs show
   `planner LLM returned no submit_plan tool call; falling back`
   regularly — Haiku is less compliant than Sonnet on tool-only
   output. Orchestrator falls back to `default_plan_for(use_case)`,
   so requests still complete; multi-step queries get a less-tailored
   tool selection. Cleanest fix: add a separate `PLANNER_MODEL` env
   knob and pin planner to Sonnet (synthesizer + vision stay on
   Haiku). ~half-day.

Post-deadline / future:

6. **No vue-ui tests at the SFC integration level.** Unit tests for
   composables and pure helpers (parser, mapBBoxToPixels,
   useDocumentUpload, useAgentTurn) are in. Component-level
   integration tests for the drawer flow are not.
7. **Sign & Finalize / Edit demographics are preview-only.** Same
   gap as W1 — needs `POST /api/fhir/Encounter` and `PATCH /Patient`.
   Tracked in DEVIATIONS.md (FHIR write scopes constraint).
8. **Token refresh not implemented.** OAuth access_token expires
   ~1 hr; FHIR returns 401; SPA bounces to /login.
9. **CalendarView / SettingsView are mocked** — not wired to real FHIR.
10. **Apache proxy conf is not persisted** across openemr container
    recreation — recipe above to re-inject.

## Things to focus on next session

In priority order with ~36 hours left:

1. **Defense slides** (`docs/w2-defense-slides.html`). Was dirty pre-
   session, still hasn't gotten its W2-architecture-aware refresh
   pass. The doc-upload pipeline + bbox overlay are the W2
   trust-artifact story — slides should land that.
2. **Live-demo dry run.** End-to-end on Chen + Whitaker, time it,
   note any flakes. The chat-reply-duplicates-panel and snake_case
   demographics labels are the visible papercuts; decide on the spot
   whether to fix or explain away.
3. **T38.13 — `PATIENT_DASHBOARD_MIGRATION.md` defense doc.** Graded
   artifact for the W2 surprise challenge (Vue 3 vs alternatives).
   Some of this content is already in `docs/PATIENT_DASHBOARD_MIGRATION.md`;
   needs a refresh against the now-shipped state.
4. **Decision pass on the gaps above** (1–5). Commit-to-chart is
   already in DEVIATIONS as "deferred"; decide whether the snake_case
   demographics labels and the planner-fallback warning are worth
   fixing or noting.

## Branch state at this commit

- Branch: `main` (clean, fully merged)
- Origin: GitLab (`labs.gauntletai.com/cameroncandelori/openemr`)
- Mirror: GitHub (`github.com/ccandelori/agentforge-clinical-copilot`)
- Both remotes synced to the same SHA. MR !40 closed.

Pre-existing dirty files at session end (NOT in scope, NOT committed):
- `docs/w2-defense-slides.html` (presession edits, deserves a fresh pass — see priority 1 above)
- `docs/architecture-overview-slides.html` (untracked, presession addition)

Per stash:
- `stash@{0} presession-slides-WIP` — the pre-session edits to the slides;
  pop before the slides refresh in priority 1.
