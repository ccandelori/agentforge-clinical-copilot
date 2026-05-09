# AgentForge Deployment Notes

Source of truth for the production demo deployment of AgentForge Clinical
Co-Pilot. Update this file whenever you change something on the droplet —
"what's running there" should be discoverable without SSH'ing in.

## The droplet

| Field | Value |
|---|---|
| Provider | DigitalOcean |
| IP | `<droplet>` |
| Hostname | `ubuntu-s-1vcpu-2gb-nyc1` (the name lies — actually 3.8 GiB / 1 vCPU) |
| OS | Ubuntu 24.04 |
| SSH | `ssh root@<droplet>` (key-based) |

## What's running

OpenEMR is running via the **upstream `development-easy` docker-compose
stack** (same shape as local dev), not a production-grade compose. It was
set up before AgentForge work began. We deploy AgentForge **on top of it**.

```
Compose file: /opt/openemr-source/docker/development-easy/docker-compose.yml
Compose project: development-easy
```

Containers (post-AgentForge deploy):

| Container | Image | Internal port | Purpose |
|---|---|---|---|
| `development-easy-openemr-1` | `openemr/openemr:flex` | 80, 443 | OpenEMR PHP/Apache |
| `development-easy-mysql-1` | `mariadb:11.8.6` | 3306 | DB |
| `development-easy-phpmyadmin-1` | `phpmyadmin:latest` | 80 | DB admin UI |
| **`agentforge-sidecar`** | `agentforge-sidecar:latest` (built on droplet) | 8000 | **Python sidecar — also serves the Vue dashboard SPA from `/app/dashboard/dist` (T38.14)** |
| **`agentforge-redis`** | `redis:7-alpine` | 6379 | **Tool-result cache (added by us)** |

The sidecar is on the **`development-easy_default`** docker network so the
OpenEMR container can resolve it as `agentforge-sidecar` via internal DNS.

**No new container for the dashboard.** Per the canonical 5-container
layout, the Vue dashboard SPA is served from inside `agentforge-sidecar`
via FastAPI's `StaticFiles` mount at `/`. The build output lives on the
host at `/opt/agentforge/dashboard/dist/` (rsync target) and is
bind-mounted read-only into the container at `/app/dashboard/dist`.
Mounting (rather than baking into the image) means a dashboard-only
redeploy is `rsync` + no rebuild — see "Dashboard deploy" below.

### Stopped 2026-05-02 — DO NOT restart casually

The upstream `development-easy` compose stack also ships `couchdb`,
`selenium`, `openldap`, and `mailpit` containers — none of which AgentForge
uses. They were running for the first weeks of this droplet but were
stopped on 2026-05-02 after `selenium` was caught burning a full CPU core
and pushing the load average above 40. After stopping all four, sidecar
CPU dropped from 44% → 0.2% (it had been queue-waiting behind selenium).

If `docker compose up` is ever re-run on the droplet (e.g. for a stack
upgrade), these will come back; stop them again immediately:

```bash
docker stop development-easy-selenium-1 \
            development-easy-couchdb-1 \
            development-easy-openldap-1 \
            development-easy-mailpit-1
```

A duplicate-named `pagentforge-redis` container was also removed in the
same cleanup pass — leftover from an earlier deploy attempt that never
got wired in. The actual cache the sidecar uses is `agentforge-redis`.

## Public URLs

| URL | Purpose |
|---|---|
| `https://<droplet>:9300/` | OpenEMR (HTTPS, **self-signed cert** — browser will warn) |
| `http://<droplet>:8300/` | OpenEMR (plain HTTP) |
| `http://<droplet>:8310/` | phpMyAdmin |

The sidecar is **not** publicly exposed — only reachable from the OpenEMR
container via the docker network.

## Where the AgentForge code lives on the droplet

Three staging directories on the host filesystem (rsync targets):

```
/opt/agentforge/module/          # OpenEMR PHP module — rsync from local
/opt/agentforge/sidecar/         # Python sidecar — rsync from local + Dockerfile
/opt/agentforge/dashboard/dist/  # Vue dashboard build output — rsync of `dashboard/dist/`
                                 #   bind-mounted at /app/dashboard/dist in agentforge-sidecar (T38.14)
```

The OpenEMR container's source tree is **inside the `openemr/openemr:flex`
image** (only `sites/`, `node_modules/`, `themes/`, `assets/` are persistent
docker volumes). So the module is injected via `docker cp`:

```
container path:
  /var/www/localhost/htdocs/openemr/interface/modules/custom_modules/oe-module-agentforge/
```

**This is volatile.** If the openemr container is recreated (`docker compose
down && up`), the AgentForge module disappears and must be re-`docker cp`'d.
For real persistence, add a bind-mount to compose or build a custom OpenEMR
image — both deferred for tonight.

## Secrets

Two `.env` files, never committed to git, dropped via SSH:

| Path | Owner of secrets |
|---|---|
| `/opt/agentforge/module/.env` (also `docker cp`'d into container at the module path) | `AGENTFORGE_JWT_SECRET`, `AGENTFORGE_SIDECAR_URL` |
| `/opt/agentforge/sidecar/.env` (read by container via `docker run --env-file`) | `JWT_SECRET` (must match), `HMAC_KEY`, `ANTHROPIC_API_KEY`, `OPENEMR_BASE_URL`, `VERIFIER_ENABLED=true`, `EVIDENCE_RETRIEVER_ENABLED=true` (W2 retriever; see note below) |

The sidecar's `JWT_SECRET` and the module's `AGENTFORGE_JWT_SECRET` **must be
byte-identical** — that's how the sidecar verifies tokens minted by the PHP
module.

To rotate: generate a new value with `openssl rand -base64 32`, update both
files, restart the sidecar container.

### `EVIDENCE_RETRIEVER_ENABLED` (W2 opt-in, MR 7)

Default-off in the sidecar `Settings`. When set to `true` in the droplet's
`/opt/agentforge/sidecar/.env`, ``create_app`` builds the full W2 RAG
pipeline (BM25 + SentenceTransformer dense + RRF + cross-encoder rerank)
at startup. The dense + cross-encoder models load ~190 MB of ML weights
on construction; on a fresh container without a Hugging Face cache this
adds ~30-60 s to the first startup as the weights download. Subsequent
container starts re-download unless the HF cache is mounted as a docker
volume (planned follow-up). The deploy script's health-check polls
`/health` for 30 s — if a clean redeploy fails the health check, that's
the model download still finishing; check `docker logs agentforge-sidecar`
for the FastAPI "Application startup complete" line and re-run
`./scripts/deploy-droplet.sh check` once it appears.

See `docs/DEVIATIONS.md` 2026-05-05 for the rationale behind the
default-off choice.

## How to deploy a new version of the code

Use the helper script. It does the rsync + `docker cp` + image rebuild +
container restart + health check, and is safe to re-run after every change:

```bash
# from the repo root:
./scripts/deploy-droplet.sh             # module + sidecar + dashboard (default)
./scripts/deploy-droplet.sh module      # only PHP module changed
./scripts/deploy-droplet.sh sidecar     # only Python sidecar changed
./scripts/deploy-droplet.sh dashboard   # only Vue dashboard changed (npm build → rsync → no image rebuild)
./scripts/deploy-droplet.sh dashboard --skip-build  # rsync existing dashboard/dist/ as-is
./scripts/deploy-droplet.sh check       # health-check, no deploy
./scripts/deploy-droplet.sh logs        # tail the live sidecar log
./scripts/deploy-droplet.sh help        # show all subcommands
```

### Dashboard deploy (T38.14)

The Vue dashboard build output is served from inside the sidecar
container via FastAPI's `StaticFiles` mount. The deploy flow:

1. `npm run build` runs locally in `dashboard/` to produce `dashboard/dist/`.
2. `rsync -az --delete dashboard/dist/ → /opt/agentforge/dashboard/dist/`.
3. The sidecar's bind mount (`/opt/agentforge/dashboard/dist:/app/dashboard/dist:ro`)
   makes the new files visible inside the container immediately —
   FastAPI's `StaticFiles` reads from disk on every request, so no
   container restart is needed for content updates.
4. The script auto-detects whether the running sidecar already has the
   bind mount (deployments before T38.14 don't); if not, it falls back
   to `deploy_sidecar` to restart the container with the mount + the
   `DASHBOARD_DIST_DIR=/app/dashboard/dist` env var.

If `dashboard/dist/` is missing or the `npm run build` fails locally,
pass `--skip-build` to use whatever's already on disk.

The script is parameterized via env vars (`DROPLET_HOST`, `OPENEMR_CONTAINER`,
`OPENEMR_NETWORK`, `SIDECAR_NAME`) so a new droplet only needs:

```bash
DROPLET_HOST=root@1.2.3.4 ./scripts/deploy-droplet.sh
```

It will warn but not fail if either `.env` is missing on the droplet, since
those are part of the one-time setup below.

## How to check it's healthy

```bash
./scripts/deploy-droplet.sh check
```

(Or, if you'd rather see the raw shape: SSH in and run the equivalent commands
the script's `check_health` function emits — they're documented inline in
`scripts/deploy-droplet.sh`.)

Open the Vue dashboard at `https://<droplet>:9300/dashboard/`, sign in
(admin / pass), and either attach a test document or send a question
like *"summarize this patient including problems and meds."*

## Known gotchas (also documented in `docs/DEVIATIONS.md`)

- **`sqlconf.php $config = 0` reset.** OpenEMR's install script resets the
  install marker if anything thinks the container is fresh. If you hit the
  setup wizard instead of the login screen, edit
  `/var/www/localhost/htdocs/openemr/sites/default/sqlconf.php` and set
  `$config = 1` (the file is in a docker volume so the change persists).

- **`npm install` + `npm run build` may need to be re-run inside the
  container** if the assets / themes volumes get cleared. Symptom: the login
  page renders unstyled with the giant logo.

- **Apache strips `Authorization` header from `$_SERVER`** by default. Our
  internal endpoints rehydrate it via `apache_request_headers()` — see the
  `Authorization` block in `public/internal/demographics.php`.

- **First `/turn` after a fresh sidecar start may 503.** Cold-start of the
  Anthropic SDK's HTTP client pool. Retry once; subsequent requests are fine.

## Production cutover gotchas (T38.14 dashboard deploy)

The dashboard's BFF auth flow is currently pinned to `localhost:5173` for
local development (see `PATIENT_DASHBOARD_MIGRATION.md` lines 184-200).
When the dashboard goes live on the droplet, three things have to move
in lockstep — miss any one and the OAuth dance fails closed:

### 1. Re-register the OpenEMR OAuth client at the production origin

The dev-easy registration shipped with `redirect_uris=["http://localhost:5173/auth/callback"]`.
That value is what OpenEMR's `/oauth2/<site>/authorize` checks against
the `redirect_uri` query param the sidecar sends; a mismatch is rejected
before the user ever sees a consent screen. Re-register against the
production origin (or update the existing client's `redirect_uris` and
`post_logout_redirect_uris`):

```bash
ssh root@<droplet> \
  'docker exec -T development-easy-openemr-1 curl -sS -X POST \
    http://localhost/oauth2/default/registration \
    -H "Content-Type: application/json" \
    --data "{
      \"application_type\": \"private\",
      \"client_name\": \"AgentForge Dashboard BFF (sidecar, prod)\",
      \"redirect_uris\": [\"https://<droplet>:9300/auth/callback\"],
      \"post_logout_redirect_uris\": [\"https://<droplet>:9300/\"],
      \"token_endpoint_auth_method\": \"client_secret_post\",
      \"grant_types\": [\"authorization_code\", \"refresh_token\"],
      \"response_types\": [\"code\"],
      \"scope\": \"openid offline_access fhirUser user/Patient.read user/AllergyIntolerance.read user/Condition.read user/MedicationRequest.read user/CareTeam.read user/Observation.read user/Encounter.read user/Practitioner.read user/Organization.read\"
    }"'
```

After registration, **enable the client in Admin → System → API Clients
→ Enable** (one-time per droplet; the OpenEMR `oauth_clients` table
persists across deploys). Without this step, every `/authorize` call
returns "client disabled".

The sidecar will need to learn the new `client_id` / `client_secret` —
both come back in the registration response and need to be written into
`/opt/agentforge/sidecar/.env` as `DASHBOARD_OAUTH_CLIENT_ID` and
`DASHBOARD_OAUTH_CLIENT_SECRET`, then `./scripts/deploy-droplet.sh sidecar`
to restart with the new env.

### 2. Update sidecar env vars to the production origin

In `/opt/agentforge/sidecar/.env` on the droplet:

```bash
# Where the SPA lives — the post-auth landing page is built as
# DASHBOARD_APP_URL.rstrip('/') + next_path. Same-origin in production.
DASHBOARD_APP_URL=https://<droplet>:9300/

# Must match the OAuth client's registered redirect_uris (above).
DASHBOARD_OAUTH_REDIRECT_URI=https://<droplet>:9300/auth/callback
DASHBOARD_OAUTH_POST_LOGOUT_REDIRECT_URI=https://<droplet>:9300/

# Discovery URL of the authorization server (HTTPS port; OAuth2
# endpoints reject HTTP — see PATIENT_DASHBOARD_MIGRATION.md).
DASHBOARD_OAUTH_AUTHORITY=https://<droplet>:9300/oauth2/default

# Required: aud query param on /authorize. Binds the issued token
# to the FHIR resource server.
DASHBOARD_OAUTH_AUDIENCE=https://<droplet>:9300/apis/default/fhir

# FHIR proxy target. The sidecar forwards /api/fhir/* here with the
# session's bearer token. Note the HTTPS port — the FHIR API rejects HTTP.
DASHBOARD_FHIR_BASE_URL=https://<droplet>:9300/apis/default/fhir

# Production must serve cookies over HTTPS only.
DASHBOARD_SESSION_COOKIE_SECURE=true

# Bind mount target inside the container — set automatically by the
# Dockerfile and the deploy script's `docker run -e`. Listed here for
# completeness; override only if you change the mount path.
DASHBOARD_DIST_DIR=/app/dashboard/dist
```

After changes: `./scripts/deploy-droplet.sh sidecar` restarts the
container with the new env. (Env vars are read at process start; running
containers don't pick up `.env` edits.)

### 3. `/auth/login?next=...` must accept production-side paths

The sidecar's `_is_safe_next()` (in `dashboard_auth/routes.py`) only
accepts **relative paths** (single-leading-slash, no double-slash).
Origin doesn't enter the check — the sidecar treats every `next=` as
a path that gets glued onto `DASHBOARD_APP_URL` after auth completes.
That means the post-auth landing URL is always
`DASHBOARD_APP_URL.rstrip('/') + next_path`, so once
`DASHBOARD_APP_URL` is set to the production origin (step 2), the same
`/auth/login?next=/patient/<uuid>` request that worked locally will
land at the production origin without any code change.

If a future feature needs to whitelist absolute origins (e.g. a
multi-tenant dashboard split across hosts), `_is_safe_next` is the
choke point.

### Cert posture

The droplet currently terminates HTTPS with a **self-signed cert** on
port 9300 (browsers will warn on first visit; users have to click
through). The sidecar's two BFF httpx clients pass `verify=False` to
work against this — see `agentforge.main` for the comment thread. When
production migrates to a real cert (Let's Encrypt; tracked under
ARCHITECTURE.md §10), flip `verify=True` and remove the comment.

## Optional: tighten REST `api_log` body logging

OpenEMR's `ApiResponseLoggerListener` logs request/response bodies into
the `api_log` table when the `api_log_option` global is `2` (default).
AgentForge's sidecar→PHP internal endpoints don't go through that
listener (they use bare Symfony Requests, not `HttpRestRequest`), so
this isn't a present-day leak — but as defense-in-depth for any
future calls that DO route through the REST stack, ship the global
at `1` ("minimal logging — body skipped"):

```bash
# From inside the openemr container (or any host with the codebase mounted):
php scripts/configure_api_logging.php --check    # report current value
php scripts/configure_api_logging.php            # set to 1; idempotent
```

Reload Apache after the change so the globals bag re-reads the row.

## First-time deploy (one-time setup before the script works)

The deploy script assumes the droplet is provisioned with OpenEMR already
running, and that the two `.env` files exist at their canonical paths. To get
there from a fresh droplet:

1. **OpenEMR up.** Stand up OpenEMR via the upstream `development-easy`
   compose stack — out of scope of this repo; follow OpenEMR's docs.
2. **Make staging dirs:**
   ```bash
   ssh root@<droplet> 'mkdir -p /opt/agentforge/module /opt/agentforge/sidecar'
   ```
3. **Generate secrets:**
   ```bash
   echo "JWT_SECRET=$(openssl rand -base64 32)"
   echo "HMAC_KEY=$(openssl rand -base64 32)"
   ```
   Save both — you'll paste them in step 4 + 5.
4. **Drop module `.env`** on the droplet:
   ```bash
   ssh root@<droplet> 'cat > /opt/agentforge/module/.env' <<EOF
   AGENTFORGE_JWT_SECRET=<the JWT_SECRET from step 3>
   AGENTFORGE_SIDECAR_URL=http://agentforge-sidecar:8000
   EOF
   ```
5. **Drop sidecar `.env`** on the droplet (use `read -rsp` for the API key
   so it doesn't enter shell history):
   ```bash
   read -rsp 'Anthropic API key: ' KEY
   ssh root@<droplet> "cat > /opt/agentforge/sidecar/.env" <<EOF
   JWT_SECRET=<same value as AGENTFORGE_JWT_SECRET above — must match byte-for-byte>
   HMAC_KEY=<the HMAC_KEY from step 3>
   ANTHROPIC_API_KEY=$KEY
   OPENEMR_BASE_URL=http://openemr:80
   VERIFIER_ENABLED=true
   EOF
   ssh root@<droplet> 'chmod 600 /opt/agentforge/sidecar/.env'
   unset KEY
   ```
6. **Run the deploy script** to push code + build sidecar + start containers:
   ```bash
   ./scripts/deploy-droplet.sh
   ```
7. **Enable module in OpenEMR UI:** open the OpenEMR site → Admin → Modules
   → Manage Modules → find "AgentForge Clinical Co-Pilot" → Install → Enable.
   (One-time per droplet; the modules table persists across deploys.)
8. **Smoke test:** open the Vue dashboard at `https://<droplet>:9300/dashboard/`,
   sign in, and either attach a test document or send a question. First
   `/turn` after a fresh sidecar start may 503 (cold-start). Retry once.
