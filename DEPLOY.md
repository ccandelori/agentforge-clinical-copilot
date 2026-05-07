# AgentForge Deployment Checklist

The act of deploying: what to verify before flipping traffic, the commands
that actually run, and how to back out if something breaks. For *what is
currently deployed* (the snapshot), see `docs/DEPLOYMENT.md`.

Companion docs:
- `docs/DEPLOYMENT.md` — current droplet state, container layout, secrets layout
- `docs/DEVIATIONS.md` — design decisions vs original plan
- `docs/NEXT-SESSION.md` — live carryforward list
- `docker/agent/README.md` — local dev compose stack walkthrough
- `interface/modules/custom_modules/oe-module-agentforge/README.md` — pre-deploy index gate
- `ARCHITECTURE.md` — §2 auth gateway, §3 latency budgets, §10 deployment

## Pre-deploy hard gates

These must all be true before traffic goes to a new build. Each item is
verifiable on the running deployment; nothing is "trust me."

### Database — composite + FULLTEXT indexes (AUDIT.md P2)

The agent's lab and note-search tools depend on five indexes across five
tables. Without them, `get_recent_labs` and `search_notes` blow the per-tool
2 s budget by an order of magnitude.

- [ ] `idx_form_vitals_pid_date` on `form_vitals(pid, date)` — defined inline at `sql/database.sql:2455`
- [ ] `idx_procedure_order_patient_date` on `procedure_order(patient_id, date_ordered)` — `sql/database.sql:10423`
- [ ] `idx_procedure_report_date` on `procedure_report(procedure_report_id, date_report)` — `sql/database.sql:10495`
- [ ] `ft_pnotes_body` FULLTEXT on `pnotes(body)` — `sql/database.sql:8696`
- [ ] FULLTEXT on `form_clinical_notes(note)` — applied via `db/Migrations/Version20260430000002.php`

**Verify on the target environment** (regression test asserts all seven
indexes with the right columns + INDEX_TYPE):

```bash
docker compose exec openemr bash -c \
    'cd /var/www/localhost/htdocs/openemr \
     && vendor/bin/phpunit --filter AgentForgeIndexesTest'
```

Expected: `Tests: 7, Assertions: 21, OK`. Any failure = gate unmet.

**Apply if missing.** Fresh installs get them via `setup.php` (included in
`sql/database.sql`). Existing installs need the Doctrine migrations
manually — auto-apply on upgrade is pending upstream:

```bash
ENVIRONMENT=development ./cli migrations:migrate --no-interaction
```

See `interface/modules/custom_modules/oe-module-agentforge/README.md` for
troubleshooting (`Duplicate key name`, environment var unset, schema drift).

### Sidecar infrastructure

- [ ] **Redis reachable from sidecar** at `REDIS_URL`. Sidecar refuses to
      start without it (`sidecar/src/agentforge/config.py:40`). On the
      droplet this is `agentforge-redis` on `development-easy_default`.
- [ ] **Sensitivity policy YAML reachable** at `SENSITIVITY_POLICY_PATH`
      (default: `sidecar/config/sensitivity_policy.yaml`).
      **Production must set `SENSITIVITY_POLICY_REQUIRED=true`** so a
      missing file fails sidecar startup instead of silently disabling the
      visibility gateway. Today's droplet runs `false` — see
      `docs/NEXT-SESSION.md` carryforward #1.
- [ ] **`ANTHROPIC_API_KEY` set** when `LLM_PROVIDER=claude`. Sidecar
      starts without it but `/turn` 503s on first request.

### JWT and HMAC secrets

The PHP module mints; the sidecar verifies. Mismatch = every `/turn` 401s.

- [ ] `AGENTFORGE_JWT_SECRET` (module `.env`) and `JWT_SECRET` (sidecar
      `.env`) are **byte-identical**. Same value, two names — convention
      difference between the PHP-Dotenv layer and pydantic-settings.
- [ ] Both at least 32 bytes (`openssl rand -base64 32`). Rotate at this
      gate.
- [ ] `HMAC_KEY` set on sidecar (required at startup; non-optional in
      config). ARCHITECTURE.md §7.2: quarterly rotation.
- [ ] No `.env` files committed. Deploy-script rsync excludes them;
      staging dirs on droplet hold them at `/opt/agentforge/{module,sidecar}/.env`.

### OpenEMR-side cleanup (AUDIT.md S3, S4, C5)

- [ ] **Default credentials rotated** away from `admin/pass`. Hard
      pre-deploy gate per ARCHITECTURE.md §10. (Acceptable for a demo URL
      with the caveat documented; **not** acceptable for any non-demo
      environment.)
- [ ] **Composer token rotated.** The token at
      `docker/development-easy/docker-compose.yml:62`
      (`GITHUB_COMPOSER_TOKEN`, plus `_ENCODED` and `_ENCODED_ALTERNATE` on
      lines 63–64) was checked into git on the dev compose; revoke and
      re-issue before any production cut.
- [ ] **`api_log_option=1`** (suppress request/response bodies) so
      `api_log` does not become a parallel PHI store. Run the helper:
      ```bash
      php scripts/configure_api_logging.php --check    # report current value
      php scripts/configure_api_logging.php            # set to 1; idempotent
      ```
      Note: this is defense-in-depth only — AgentForge's internal endpoints
      use bare Symfony Requests and do not currently route through
      `ApiResponseLoggerListener`.

### Network surface

- [ ] **Sidecar not publicly exposed.** Reachable only from OpenEMR via
      the shared docker network (hostname `agentforge-sidecar`, port
      8000). Dev-only host mapping at `docker/agent/docker-compose.yml`
      binds `127.0.0.1:8400` — do not ship to production.
- [ ] **`/agentforge/internal/*` restricted.** Endpoints under
      `interface/modules/custom_modules/oe-module-agentforge/public/internal/`
      are JWT-gated; on non-localhost deployments also restrict by source
      IP / network policy.
- [ ] **TLS terminates upstream of OpenEMR.** Demo URL uses self-signed on
      port 9300; production migrates to Let's Encrypt (ARCHITECTURE.md §10).

### Dashboard cutover (T38.14)

The Vue dashboard ships through the same `agentforge-sidecar` container
(no new container — see the 5-container layout note at the bottom of
this file). Three things must move in lockstep before the SPA can load:

- [ ] **OAuth client re-registered at production origin.** Dev-easy
      registration pins `redirect_uris=["http://localhost:5173/auth/callback"]`
      (see `PATIENT_DASHBOARD_MIGRATION.md:184-200`). Re-register or update
      the client to match the production origin and **enable** it in
      Admin → System → API Clients. Exact `curl` command in
      `docs/DEPLOYMENT.md` "Production cutover gotchas".
- [ ] **Sidecar env vars updated** in `/opt/agentforge/sidecar/.env`:
      `DASHBOARD_APP_URL`, `DASHBOARD_OAUTH_REDIRECT_URI`,
      `DASHBOARD_OAUTH_POST_LOGOUT_REDIRECT_URI`, `DASHBOARD_OAUTH_AUTHORITY`,
      `DASHBOARD_OAUTH_AUDIENCE`, `DASHBOARD_FHIR_BASE_URL`, and
      `DASHBOARD_SESSION_COOKIE_SECURE=true`. Restart the sidecar with
      `./scripts/deploy-droplet.sh sidecar` to pick them up.
- [ ] **`dashboard/dist/` rsync'd to droplet** at
      `/opt/agentforge/dashboard/dist/` (host) and bind-mounted at
      `/app/dashboard/dist` (container) — handled by
      `./scripts/deploy-droplet.sh dashboard`. The sidecar logs
      `Mounting dashboard SPA from /app/dashboard/dist at /` on startup
      when the mount is in place; if the dir is empty or missing, it
      logs a warning and skips the mount, leaving the API surface
      reachable but the SPA un-served.

## Configuration checklist — env vars by stack

### Sidecar (`sidecar/.env` locally; `/opt/agentforge/sidecar/.env` on droplet)

Template: `sidecar/.env.example`. Required fields are the ones without
defaults in `sidecar/src/agentforge/config.py`.

- [ ] `JWT_SECRET` — must match `AGENTFORGE_JWT_SECRET` in the PHP module
- [ ] `JWT_ALGORITHM=HS256`
- [ ] `REDIS_URL` — `redis://agentforge-redis:6379/0` on droplet,
      `redis://localhost:6379/0` for host-mode dev
- [ ] `SESSION_TTL_SECONDS=4500` (75 min, ARCHITECTURE.md §3)
- [ ] `TOOL_CACHE_TTL_SECONDS=60`
- [ ] `LLM_PROVIDER=claude`
- [ ] `ANTHROPIC_API_KEY` — non-empty when `LLM_PROVIDER=claude`
- [ ] `OPENEMR_BASE_URL` — `http://openemr:80` on droplet,
      `http://localhost:80` for host-mode dev
- [ ] `OPENEMR_FHIR_ENDPOINT=/apis/fhir/r4`
- [ ] `OPENEMR_INTERNAL_ENDPOINT=/agentforge/internal`
- [ ] `HMAC_KEY` — required, 32+ bytes
- [ ] `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` — all
      three or none. Missing any one wires `NullLangfuseClient`.
- [ ] `VERIFIER_ENABLED=true` for production. Default is `false`.
- [ ] `SENSITIVITY_POLICY_REQUIRED=true` for production. Default is `true`
      in code; `docker/agent/.env.example` ships `false` for dev.

### PHP module (`interface/modules/custom_modules/oe-module-agentforge/.env`)

Template: `interface/modules/custom_modules/oe-module-agentforge/.env.example`.

- [ ] `AGENTFORGE_JWT_SECRET` — same value as sidecar `JWT_SECRET`
- [ ] `AGENTFORGE_SIDECAR_URL`
  - Production droplet: `http://agentforge-sidecar:8000`
  - Local docker stack: `http://agentforge-sidecar:8000`
  - Host-mode `./sidecar/scripts/sidecar.sh start`: `http://host.docker.internal:8000`

### Local dev compose stack (`docker/agent/.env`)

Template: `docker/agent/.env.example`. Combined sidecar + dev-stack
overlay; reuses dev-easy redis + langfuse via the shared external network.

- [ ] All required sidecar fields above
- [ ] `AGENT_DEV_NETWORK=development-easy_default` (override only if you
      renamed the dev-easy compose project)
- [ ] `AGENT_SIDECAR_PORT=8400` (host-side smoke port; container port stays 8000)

### Droplet shell — deploy script overrides

`scripts/deploy-droplet.sh` reads these from the environment at run time
(all have sensible defaults; override for a different droplet):

- [ ] `DROPLET_HOST=root@143.244.157.90`
- [ ] `OPENEMR_CONTAINER=development-easy-openemr-1`
- [ ] `OPENEMR_NETWORK=development-easy_default`
- [ ] `SIDECAR_NAME=agentforge-sidecar`

## Pre-deploy smoke test script

Run from the **target environment** (your laptop for dev, the droplet for
prod). Each step is independently meaningful — partial passes mean a
specific layer is broken.

```bash
# 1. Sidecar liveness — direct, from inside the sidecar container
docker exec agentforge-sidecar python -c "import urllib.request; \
    print(urllib.request.urlopen('http://localhost:8000/health',timeout=3).read().decode())"
# Expected: {"status":"healthy","policy_loaded":true}
#                                 (false in dev today — see carryforward #1)

# 2. Sidecar reachable from OpenEMR (verifies the shared network)
docker exec development-easy-openemr-1 sh -c \
    'wget -qO- --timeout=5 http://agentforge-sidecar:8000/health'
# Expected: same JSON. If empty, the docker network is the problem.

# 3. Redis reachable from sidecar
docker exec agentforge-sidecar python -c \
    "import os, redis; r = redis.from_url(os.environ['REDIS_URL']); print(r.ping())"
# Expected: True

# 4. Module files present in container (deploy script also checks this)
docker exec development-easy-openemr-1 test -f \
    /var/www/localhost/htdocs/openemr/interface/modules/custom_modules/oe-module-agentforge/openemr.bootstrap.php \
    && echo OK
# Expected: OK

# 5. End-to-end /turn through the OpenEMR proxy (requires a real session
#    cookie — log in via the browser first and grab the cookie). The
#    proxy is at /agentforge/turn (POST), wired by public/turn.php.
#    For a fully unauthenticated check, do step 2 instead.

# 6. Index gate (re-run on the live target DB)
docker compose exec openemr bash -c \
    'cd /var/www/localhost/htdocs/openemr \
     && vendor/bin/phpunit --filter AgentForgeIndexesTest'
# Expected: Tests: 7, Assertions: 21, OK
```

## Roll-forward — what `./scripts/deploy-droplet.sh` does

Idempotent. Safe to re-run after every code change.

```bash
# Default: deploy all three halves (module + sidecar + dashboard)
./scripts/deploy-droplet.sh             # = preflight + module + sidecar + dashboard + check_health

# Targeted
./scripts/deploy-droplet.sh module      # PHP-only changes
./scripts/deploy-droplet.sh sidecar     # Python-only changes
./scripts/deploy-droplet.sh dashboard   # Vue dashboard only (npm build → rsync; no image rebuild)
./scripts/deploy-droplet.sh dashboard --skip-build  # rsync existing dashboard/dist/ as-is
./scripts/deploy-droplet.sh check       # health-check only, no deploy
./scripts/deploy-droplet.sh logs        # tail sidecar logs
```

The sequence executed by the `all` path (defined in
`scripts/deploy-droplet.sh`):

1. **`preflight`** — assert local module + sidecar + dashboard dirs exist,
   SSH works, warn (don't fail) if either remote `.env` is missing.
2. **`deploy_module`** —
   - `rsync -az --delete` from `interface/modules/custom_modules/oe-module-agentforge/`
     to `/opt/agentforge/module/` on the droplet, excluding `.git`,
     `vendor`, `*.tmp`, `.env`.
   - `docker cp /opt/agentforge/module/. <openemr>:/var/www/localhost/htdocs/openemr/interface/modules/custom_modules/oe-module-agentforge/`
   - `docker cp /opt/agentforge/module/.env <openemr>:<module-path>/.env`
     if it exists.
3. **`deploy_sidecar`** —
   - `rsync -az --delete` `sidecar/` to `/opt/agentforge/sidecar/`,
     excluding `.venv`, `__pycache__`, `.pytest_cache`, `.mypy_cache`,
     `.ruff_cache`, `var`, `.env`.
   - `docker build -t agentforge-sidecar:latest .` on the droplet.
   - `docker rm -f agentforge-sidecar` (force-remove old container).
   - `docker run -d --name agentforge-sidecar --restart unless-stopped
     --network development-easy_default --env-file
     /opt/agentforge/sidecar/.env -e DASHBOARD_DIST_DIR=/app/dashboard/dist
     -v /opt/agentforge/dashboard/dist:/app/dashboard/dist:ro
     agentforge-sidecar:latest`
4. **`deploy_dashboard`** (T38.14) —
   - Local `npm run build` in `dashboard/` (skip with `--skip-build`).
   - `rsync -az --delete dashboard/dist/` → `/opt/agentforge/dashboard/dist/`.
   - Detects whether the running sidecar already has the bind mount
     (`docker inspect`); if not, falls back to `deploy_sidecar` to pick it up.
     With the mount in place, content updates are visible without a restart.
5. **`check_health`** — poll `http://agentforge-sidecar:8000/health` from
   inside the OpenEMR container until it answers (up to 30 s, every 2 s),
   verify module files present, dump last 5 `/turn` log lines.

Things the script **does not do** (deliberately):

- It does not enable the OpenEMR module — first-time deploy only, do it
  through Admin → Modules → Manage Modules → "AgentForge Clinical
  Co-Pilot" → Install → Enable. The `modules` table persists across
  redeploys.
- It does not run database migrations — the index gate is checked
  separately (see Pre-deploy hard gates above).
- It does not seed demo data — that's `scripts/seed/` (see Post-deploy
  validation below).

**Warning.** The OpenEMR source tree lives inside the
`openemr/openemr:flex` image. `docker cp` is volatile: if compose
recreates the container (`docker compose down && up`), the module
disappears and must be re-pushed via `./scripts/deploy-droplet.sh
module`. Persisted volumes: `sites/`, `node_modules/`, `themes/`, `assets/`.

## Rollback

The deploy script is forward-only. Rolling back = re-deploying the
previous version.

### Code rollback (sidecar or module)

1. Find the previous good commit: `git log --oneline -10`
2. Stand up a worktree at that SHA:
   ```bash
   git worktree add ../openemr-rollback <previous-good-sha>
   cd ../openemr-rollback
   ```
3. Re-deploy from the rolled-back tree:
   - Sidecar: `./scripts/deploy-droplet.sh sidecar` (rebuilds image, restarts container)
   - Module: `./scripts/deploy-droplet.sh module` (just `docker cp`, fast)
4. `git worktree remove ../openemr-rollback` when done.

### `.env` rollback

`.env` files are not in git — they live on the droplet at
`/opt/agentforge/{module,sidecar}/.env`. Before changes, snapshot:

```bash
ssh root@143.244.157.90 'cp /opt/agentforge/sidecar/.env /opt/agentforge/sidecar/.env.bak.$(date +%s)'
ssh root@143.244.157.90 'cp /opt/agentforge/module/.env /opt/agentforge/module/.env.bak.$(date +%s)'
```

Restore by reversing the copy + `./scripts/deploy-droplet.sh sidecar`
(env is read at container start; restart required).

### Database rollback

Deploy script does not touch the DB. Schema changes go through Doctrine
migrations (`db/Migrations/`); rollback via
`./cli migrations:migrate <previous-version>`. Index migrations:
`db/Migrations/Version20260430000001.php`, `Version20260430000002.php`.

## Post-deploy validation

Run after every deploy. All of these have green-field reference results
for the current droplet — divergence means a regression.

### Code health

- [ ] Sidecar container `Up` and healthy:
      ```bash
      ssh root@143.244.157.90 'docker ps --filter name=agentforge-sidecar --format "{{.Status}}"'
      ```
- [ ] OpenEMR can reach the sidecar:
      ```bash
      ./scripts/deploy-droplet.sh check
      ```

### Data health — seed validation

The droplet runs the same seed pipeline as local. After any data work
(or to confirm no drift after a deploy):

- [ ] All 14 checks in `scripts/seed/validate_seed.sh` pass.
      ```bash
      MYSQL_CONTAINER=development-easy-mysql-1 ./scripts/seed/validate_seed.sh
      ```
      Expected: every row in `validate_seed_data.sql`'s output reports
      `violations = 0`. Wrapper exits non-zero on any violation.

### Behavioral smoke — chart overview + out-of-scope

These two queries together exercise the full pipeline end-to-end (auth,
proxy, sidecar, tools, synthesis, verifier).

- [ ] Open `https://143.244.157.90:9300/`, log in, open Susan Underwood
      (`pid=2` historically; `pid=100` per the regression-lock fixture in
      `sidecar/tests/eval/regression_locks.py`).
- [ ] Expand the **Clinical Co-Pilot** panel.
- [ ] Send: *"Give me the chart overview."* Expected response shape
      (canonical headers, weave demographics into prose, single opening
      `[demographic #N]` citation): see `_UC1_CANONICAL_STYLE` in
      `sidecar/tests/eval/regression_locks.py:156`.
- [ ] Send: *"What is the patient's billing history?"* Expected: a single
      sentence redirect — *"I don't have a tool to retrieve billing.
      Check the chart's billing section directly."* — with no
      "in this version of the co-pilot" hedging. See the
      out-of-scope guardrail lock at
      `sidecar/tests/eval/regression_locks.py:189` onward (Task 51.4).
- [ ] **Cold-start tolerance.** First `/turn` after a fresh sidecar start
      may 503 (Anthropic SDK HTTP-pool warmup). Retry once. Documented in
      `docs/DEPLOYMENT.md` "Known gotchas".

### Privacy spot-check

- [ ] Tail recent Langfuse traces (or `docker logs agentforge-sidecar`)
      and confirm: no raw `patient_id`, no patient names, no tool result
      bodies. Pseudonyms are HMAC-SHA256 keyed; visual inspection should
      see only opaque hashes (ARCHITECTURE.md §7.2).

## Known issues — live carryforwards

The full list lives in `docs/NEXT-SESSION.md` "Live carryforwards" —
eight items as of 2026-05-02, with rationale in `docs/DEVIATIONS.md`
(search 2026-05-01 / 2026-05-02 entries). The deploy-relevant subset:

1. **Sensitivity policy YAML doesn't resolve inside the docker image.**
   Mitigated by `SENSITIVITY_POLICY_REQUIRED=false`. Production must bake
   the YAML at a fixed Dockerfile path (or use `importlib.resources`),
   then flip the flag back to `true`. Until fixed, the visibility
   gateway fails open on missing rules.
2. **Verifier coverage gap on `notes` / `search_notes`.** `_KNOWN_TOOLS`
   in `sidecar/src/agentforge/verifier/cache.py` doesn't register the
   notes tools yet — `[note #N]` citations don't ground via the production
   verifier. Trivial fix.
3. **In-memory breakglass dedup.** Single-replica only. Multi-replica
   requires Redis SETNX (DEVIATIONS.md 2026-05-01).
4. **Three orchestrator utilities built but not wired** —
   `SynthesisInputTruncator` (Task 45), `Planner` (Task 27),
   phase/total-turn budgets in `TimeoutPolicy` (Task 41). Tested,
   shipped-as-dead-code, no behavior change.
5. **Frontend doesn't mint `session_id`.** Multi-turn memory is wired
   server-side but `chat-panel.js` posts only `{message: ...}`. Every
   turn is independent until the JS is updated.
6. **Default credentials and self-signed TLS** still ship on the demo
   droplet (ARCHITECTURE.md §10 calls both MVP-only). Hard gates for
   any non-demo deployment.

The droplet's container layout is pinned to **exactly five containers**:
`development-easy-openemr-1`, `development-easy-mysql-1`,
`development-easy-phpmyadmin-1`, `agentforge-sidecar`,
`agentforge-redis`. The four parasitic upstream services (`selenium`,
`couchdb`, `openldap`, `mailpit`) were stopped 2026-05-02 after
selenium burned a CPU core. If a `docker compose up` re-creates them,
stop them again — exact command in `docs/DEPLOYMENT.md` "Stopped
2026-05-02 — DO NOT restart casually".
