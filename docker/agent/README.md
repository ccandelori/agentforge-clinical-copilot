# AgentForge Sidecar — Docker Stack

Containerized boot for the Python sidecar that runs the AgentForge
Clinical Co-Pilot. Sits alongside `docker/development-easy` and
reuses its redis + langfuse instances rather than double-running
them.

## What this stack contains

One service: `agent-sidecar` (FastAPI / uvicorn, container name
`agentforge-sidecar`). Built from `sidecar/Dockerfile`, exposed at
`http://127.0.0.1:8400` on the host.

It deliberately does **not** include redis or langfuse — both live
in `docker/development-easy/docker-compose.yml` and are reached via
the shared bridge network. Duplicating them would create two stores
that disagree about session memory and tool cache.

## What you need before booting

1. **The development-easy stack is up.** This is the host for redis,
   langfuse, mysql, openemr.
   ```bash
   cd docker/development-easy
   docker compose up --detach --wait
   ```
   Confirm with `docker network ls | grep development-easy_default`.

2. **Module-side config wired.** OpenEMR's PHP module reads its own
   .env at request time. Edit
   `interface/modules/custom_modules/oe-module-agentforge/.env` so:
   - `AGENTFORGE_JWT_SECRET` matches the value you're about to put
     in this stack's `JWT_SECRET` (same value, two names — sidecar
     uses `JWT_SECRET`, PHP module uses `AGENTFORGE_JWT_SECRET`).
   - `AGENTFORGE_SIDECAR_URL=http://agentforge-sidecar:8000` (the
     container name on the shared network — not host.docker.internal,
     which is for the host-mode `./sidecar/scripts/sidecar.sh` workflow)

3. **Sidecar config.** Copy + fill the env template:
   ```bash
   cd docker/agent
   cp .env.example .env
   # then edit .env to set AGENTFORGE_JWT_SECRET, HMAC_KEY, ANTHROPIC_API_KEY
   ```
   Generate keys with `openssl rand -base64 32`.

## Boot

From `docker/agent`:
```bash
docker compose up --build
```

First boot builds the multi-stage image (~60s on a warm cache, longer
on cold). Subsequent boots reuse layers and reach `Application
startup complete` in a few seconds.

Check liveness:
```bash
wget -qO- http://127.0.0.1:8400/health
# {"status":"healthy","policy_loaded":false}
```

`policy_loaded: false` is expected when the sensitivity policy YAML
isn't bundled at the resolved image path (NEXT-SESSION carryforward
#1) and `SENSITIVITY_POLICY_REQUIRED=false` mitigates. Production
deployments bake the YAML at a fixed Dockerfile path and flip the
flag to true.

## Two ways to run the sidecar locally

Both produce a sidecar reachable to OpenEMR; pick based on iteration
speed vs container parity:

| Mode | How | Pros | Use when |
|------|-----|------|----------|
| Host script | `./sidecar/scripts/sidecar.sh start` | Live `--reload`, PID file, instant restart | Active orchestrator development |
| Docker stack (this) | `docker compose up --build` from `docker/agent` | Production-shape image, reproducible across machines | CI parity, onboarding, pre-deploy smoke |

The two modes are mutually exclusive — both bind port 8000 internally
(host port 8400 in the docker stack vs 8000 in the script) and both
register `agentforge-sidecar` as a hostname on the dev-easy network.
Stop one before starting the other.

The OpenEMR PHP module's `AGENTFORGE_SIDECAR_URL` controls which one
is wired:
- Host script: `http://host.docker.internal:8000`
- Docker stack: `http://agentforge-sidecar:8000`

## Networking

The agent stack joins `development-easy_default` (an external
network managed by the dev-easy stack). If you renamed the dev-easy
compose project (`-p` flag or `COMPOSE_PROJECT_NAME`), set
`AGENT_DEV_NETWORK` in `.env` to match the actual network name.

Service-name hostnames available from inside the sidecar container:
- `openemr` — OpenEMR Apache container, port 80
- `mysql` — MariaDB, port 3306
- `redis` — Redis, port 6379
- `langfuse` — Langfuse UI + API, port 3000

## Troubleshooting

**"Network development-easy_default not found"**
The dev-easy stack isn't running, or it's running under a different
project name. `cd docker/development-easy && docker compose up
--detach --wait`, then `docker network ls`.

**Sidecar boots but `/health` returns 500**
Most likely `AGENTFORGE_JWT_SECRET`, `HMAC_KEY`, or `REDIS_URL` is
missing in `.env`. The sidecar fails fast on missing required
config — `docker compose logs agent-sidecar` shows the field.

**OpenEMR can't reach the sidecar**
Check that `AGENTFORGE_SIDECAR_URL` in the module .env is
`http://agentforge-sidecar:8000` (not `host.docker.internal`). The
`agentforge-sidecar` hostname only resolves from containers on the
shared network, not from your host.

**Slow `/turn` responses**
Verify the sidecar reaches the Anthropic API: `docker compose exec
agent-sidecar python -c "import os; print(bool(os.getenv('ANTHROPIC_API_KEY')))"`.

## Production note

This compose is dev-shape. Production droplet uses
`scripts/deploy-droplet.sh` which builds and runs the sidecar
container directly (no compose), against an externally-managed
redis (`agentforge-redis`). See `docs/DEPLOYMENT.md` for the
canonical 5-container layout.
