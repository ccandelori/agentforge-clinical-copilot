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

Use the helper script. It does the rsync + `docker cp` + image rebuild +
container restart + health check, and is safe to re-run after every change:

```bash
# from the repo root:
./scripts/deploy-droplet.sh           # both module + sidecar (default)
./scripts/deploy-droplet.sh module    # only PHP module changed
./scripts/deploy-droplet.sh sidecar   # only Python sidecar changed
./scripts/deploy-droplet.sh check     # health-check, no deploy
./scripts/deploy-droplet.sh logs      # tail the live sidecar log
./scripts/deploy-droplet.sh help      # show all subcommands
```

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

## First-time deploy (one-time setup before the script works)

The deploy script assumes the droplet is provisioned with OpenEMR already
running, and that the two `.env` files exist at their canonical paths. To get
there from a fresh droplet:

1. **OpenEMR up.** Stand up OpenEMR via the upstream `development-easy`
   compose stack — out of scope of this repo; follow OpenEMR's docs.
2. **Make staging dirs:**
   ```bash
   ssh root@143.244.157.90 'mkdir -p /opt/agentforge/module /opt/agentforge/sidecar'
   ```
3. **Generate secrets:**
   ```bash
   echo "JWT_SECRET=$(openssl rand -base64 32)"
   echo "HMAC_KEY=$(openssl rand -base64 32)"
   ```
   Save both — you'll paste them in step 4 + 5.
4. **Drop module `.env`** on the droplet:
   ```bash
   ssh root@143.244.157.90 'cat > /opt/agentforge/module/.env' <<EOF
   AGENTFORGE_JWT_SECRET=<the JWT_SECRET from step 3>
   AGENTFORGE_SIDECAR_URL=http://agentforge-sidecar:8000
   EOF
   ```
5. **Drop sidecar `.env`** on the droplet (use `read -rsp` for the API key
   so it doesn't enter shell history):
   ```bash
   read -rsp 'Anthropic API key: ' KEY
   ssh root@143.244.157.90 "cat > /opt/agentforge/sidecar/.env" <<EOF
   JWT_SECRET=<same value as AGENTFORGE_JWT_SECRET above — must match byte-for-byte>
   HMAC_KEY=<the HMAC_KEY from step 3>
   ANTHROPIC_API_KEY=$KEY
   OPENEMR_BASE_URL=http://openemr:80
   EOF
   ssh root@143.244.157.90 'chmod 600 /opt/agentforge/sidecar/.env'
   unset KEY
   ```
6. **Run the deploy script** to push code + build sidecar + start containers:
   ```bash
   ./scripts/deploy-droplet.sh
   ```
7. **Enable module in OpenEMR UI:** open the OpenEMR site → Admin → Modules
   → Manage Modules → find "AgentForge Clinical Co-Pilot" → Install → Enable.
   (One-time per droplet; the modules table persists across deploys.)
8. **Smoke test:** open a patient chart, expand the Clinical Co-Pilot panel,
   send a question. First `/turn` after a fresh sidecar start may 503
   (cold-start). Retry once.
