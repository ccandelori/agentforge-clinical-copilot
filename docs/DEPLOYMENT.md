# AgentForge Deployment Notes

Source of truth for the production demo deployment of AgentForge Clinical
Co-Pilot. Update this file whenever you change something on the droplet —
"what's running there" should be discoverable without SSH'ing in.

## The droplet

| Field | Value |
|---|---|
| Provider | DigitalOcean |
| IP | `143.244.157.90` |
| Hostname | `ubuntu-s-1vcpu-2gb-nyc1` (the name lies — actually 3.8 GiB / 1 vCPU) |
| OS | Ubuntu 24.04 |
| SSH | `ssh root@143.244.157.90` (key-based) |

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
| `development-easy-couchdb-1` | `couchdb:3.5.1` | 5984 | (unused by AgentForge — left running) |
| `development-easy-selenium-1` | `selenium/standalone-chromium` | 4444 | (unused — was for e2e, can stop to save RAM) |
| `development-easy-openldap-1` | `openemr/dev-ldap:easy` | 389 | (unused) |
| `development-easy-mailpit-1` | `axllent/mailpit:v1.29.7` | 1025 | (unused) |
| **`agentforge-sidecar`** | `agentforge-sidecar:latest` (built on droplet) | 8000 | **Python sidecar (added by us)** |

The sidecar is on the **`development-easy_default`** docker network so the
OpenEMR container can resolve it as `agentforge-sidecar` via internal DNS.

## Public URLs

| URL | Purpose |
|---|---|
| `https://143.244.157.90:9300/` | OpenEMR (HTTPS, **self-signed cert** — browser will warn) |
| `http://143.244.157.90:8300/` | OpenEMR (plain HTTP) |
| `http://143.244.157.90:8310/` | phpMyAdmin |

The sidecar is **not** publicly exposed — only reachable from the OpenEMR
container via the docker network.

## Where the AgentForge code lives on the droplet

Two staging directories on the host filesystem (rsync targets):

```
/opt/agentforge/module/          # OpenEMR PHP module — rsync from local
/opt/agentforge/sidecar/         # Python sidecar — rsync from local + Dockerfile
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
| `/opt/agentforge/sidecar/.env` (read by container via `docker run --env-file`) | `JWT_SECRET` (must match), `HMAC_KEY`, `ANTHROPIC_API_KEY`, `OPENEMR_BASE_URL` |

The sidecar's `JWT_SECRET` and the module's `AGENTFORGE_JWT_SECRET` **must be
byte-identical** — that's how the sidecar verifies tokens minted by the PHP
module.

To rotate: generate a new value with `openssl rand -base64 32`, update both
files, restart the sidecar container.

## How to deploy a new version of the code

From your local checkout (one-time setup is documented in this file's
"First-time deploy" section below):

```bash
# 1. Push code to droplet
rsync -avz --delete --exclude='.git' --exclude='vendor' --exclude='*.tmp' \
    interface/modules/custom_modules/oe-module-agentforge/ \
    root@143.244.157.90:/opt/agentforge/module/

rsync -avz --delete --exclude='.venv' --exclude='__pycache__' \
    --exclude='.pytest_cache' --exclude='.mypy_cache' --exclude='.ruff_cache' \
    --exclude='var' --exclude='.env' \
    sidecar/ \
    root@143.244.157.90:/opt/agentforge/sidecar/

# 2. Re-inject the module into the running openemr container
ssh root@143.244.157.90 \
    'docker cp /opt/agentforge/module/. development-easy-openemr-1:/var/www/localhost/htdocs/openemr/interface/modules/custom_modules/oe-module-agentforge/ && \
     docker cp /opt/agentforge/module/.env development-easy-openemr-1:/var/www/localhost/htdocs/openemr/interface/modules/custom_modules/oe-module-agentforge/.env'

# 3. Rebuild + restart the sidecar (only if sidecar code changed)
ssh root@143.244.157.90 \
    'cd /opt/agentforge/sidecar && docker build -t agentforge-sidecar:latest . && \
     docker rm -f agentforge-sidecar && \
     docker run -d --name agentforge-sidecar --restart unless-stopped \
         --network development-easy_default \
         --env-file /opt/agentforge/sidecar/.env \
         agentforge-sidecar:latest'
```

PHP module changes only need step 1 + 2 (no sidecar rebuild). Python sidecar
changes only need steps 1 + 3.

## How to check it's healthy

```bash
ssh root@143.244.157.90 '
  echo "=== sidecar status ===";
  docker ps --filter name=agentforge-sidecar --format "{{.Status}}";
  echo "=== sidecar reachable from openemr ===";
  docker exec development-easy-openemr-1 sh -c "wget -qO- http://agentforge-sidecar:8000/health";
  echo "=== last 5 turns ===";
  docker logs --tail 30 agentforge-sidecar 2>&1 | grep "POST /turn" | tail -5;
'
```

Open `https://143.244.157.90:9300/`, log in (admin / pass), open Susan
Underwood's chart (pid=2), expand the **Clinical Co-Pilot** panel, and ask
*"summarize this patient including problems and meds."*

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

## First-time deploy steps (already done; for reference)

These are the steps that got us from "fresh droplet with OpenEMR running" to
"AgentForge fully wired up." Re-run if rebuilding from scratch.

1. **Stage code:** `rsync` of the module dir → `/opt/agentforge/module/`
   and the sidecar dir → `/opt/agentforge/sidecar/`.
2. **Inject module into openemr container:**
   `docker cp /opt/agentforge/module/. development-easy-openemr-1:/var/www/localhost/htdocs/openemr/interface/modules/custom_modules/oe-module-agentforge/`.
3. **Generate secrets:** `openssl rand -base64 32` twice (one for JWT, one for HMAC).
4. **Drop module `.env`** at `/opt/agentforge/module/.env` and `docker cp`
   it into the container at the module path.
5. **Drop sidecar `.env`** at `/opt/agentforge/sidecar/.env` (use `read -rsp`
   for the API key so it doesn't enter shell history).
6. **Build sidecar image:**
   `cd /opt/agentforge/sidecar && docker build -t agentforge-sidecar:latest .`
7. **Start sidecar container** on the openemr docker network (see deploy
   commands above).
8. **Enable module in OpenEMR UI:** Admin → Modules → Manage Modules → find
   "AgentForge Clinical Co-Pilot" → Install → Enable.
9. **Smoke test:** open a patient chart, expand the Clinical Co-Pilot panel,
   send a question.
