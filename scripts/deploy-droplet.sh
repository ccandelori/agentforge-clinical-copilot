#!/usr/bin/env bash
# Deploy AgentForge (PHP module + Python sidecar) to the production
# droplet. Idempotent — safe to re-run after every code change.
#
# Usage:
#   ./scripts/deploy-droplet.sh             # module + sidecar + dashboard
#   ./scripts/deploy-droplet.sh module      # just the OpenEMR PHP module
#   ./scripts/deploy-droplet.sh sidecar     # just the Python sidecar (rebuild + restart)
#   ./scripts/deploy-droplet.sh dashboard   # just the Vue dashboard (npm run build → rsync → bind-mount)
#   ./scripts/deploy-droplet.sh dashboard --skip-build  # rsync existing vue-ui/dist/ without rebuilding
#   ./scripts/deploy-droplet.sh check       # health check only, no deploy
#   ./scripts/deploy-droplet.sh logs        # tail the sidecar log
#
# Configuration (env vars; DROPLET_HOST is required):
#   DROPLET_HOST=root@<your-droplet>      # SSH target — REQUIRED
#   OPENEMR_CONTAINER=development-easy-openemr-1
#   OPENEMR_NETWORK=development-easy_default
#   SIDECAR_NAME=agentforge-sidecar
#
# Prerequisites (one-time, see docs/DEPLOYMENT.md "First-time deploy"):
#   - SSH key access to DROPLET_HOST
#   - Module .env exists at /opt/agentforge/module/.env on droplet
#   - Sidecar .env exists at /opt/agentforge/sidecar/.env on droplet
#   - Module installed + enabled in OpenEMR Admin UI

set -euo pipefail

# ---------- Config ----------

if [[ -z "${DROPLET_HOST:-}" ]]; then
    echo "DROPLET_HOST is required (e.g. DROPLET_HOST=root@1.2.3.4 $0)" >&2
    exit 2
fi

DROPLET_HOST="$DROPLET_HOST"
OPENEMR_CONTAINER="${OPENEMR_CONTAINER:-development-easy-openemr-1}"
OPENEMR_NETWORK="${OPENEMR_NETWORK:-development-easy_default}"
SIDECAR_NAME="${SIDECAR_NAME:-agentforge-sidecar}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODULE_LOCAL="$REPO_ROOT/interface/modules/custom_modules/oe-module-agentforge"
SIDECAR_LOCAL="$REPO_ROOT/sidecar"
PROMPTS_LOCAL="$REPO_ROOT/prompts"
DASHBOARD_LOCAL="$REPO_ROOT/vue-ui"

MODULE_REMOTE="/opt/agentforge/module"
SIDECAR_REMOTE="/opt/agentforge/sidecar"
PROMPTS_REMOTE="/opt/agentforge/prompts"
# Dashboard SPA bind-mount target. The sidecar container mounts this
# host path at /app/dashboard/dist (see docker run -v in deploy_sidecar
# below); the FastAPI StaticFiles mount in agentforge.main reads it via
# DASHBOARD_DIST_DIR. Bind-mounting (not COPY) means a dashboard-only
# redeploy is `rsync` + (if running) container restart — no image rebuild.
DASHBOARD_REMOTE="/opt/agentforge/dashboard/dist"
MODULE_IN_CONTAINER="/var/www/localhost/htdocs/openemr/interface/modules/custom_modules/oe-module-agentforge"

# ---------- Helpers ----------

color() { printf '\033[1;%sm%s\033[0m\n' "$1" "$2"; }
step() { color 36 "==> $*"; }
ok() { color 32 "    $*"; }
warn() { color 33 "    $*" >&2; }
die() { color 31 "✗ $*" >&2; exit 1; }

ssh_run() {
    ssh -o BatchMode=yes -o ConnectTimeout=10 "$DROPLET_HOST" "$@"
}

preflight() {
    [[ -d "$MODULE_LOCAL" ]] || die "Module dir not found at $MODULE_LOCAL — wrong repo?"
    [[ -d "$SIDECAR_LOCAL" ]] || die "Sidecar dir not found at $SIDECAR_LOCAL — wrong repo?"
    [[ -d "$DASHBOARD_LOCAL" ]] || die "Dashboard dir not found at $DASHBOARD_LOCAL — wrong repo?"
    ssh_run 'echo ok' >/dev/null 2>&1 || die "SSH to $DROPLET_HOST failed (key auth?)."
    ssh_run "test -f $MODULE_REMOTE/.env" \
        || warn "$MODULE_REMOTE/.env missing — module deploy will leave the container without a JWT secret."
    ssh_run "test -f $SIDECAR_REMOTE/.env" \
        || warn "$SIDECAR_REMOTE/.env missing — sidecar will fail to start without it."
}

# ---------- Steps ----------

deploy_module() {
    step "rsyncing module → $DROPLET_HOST:$MODULE_REMOTE/"
    rsync -az --delete \
        --exclude='.git' --exclude='vendor' --exclude='*.tmp' --exclude='.env' \
        "$MODULE_LOCAL/" "$DROPLET_HOST:$MODULE_REMOTE/"
    ok "module synced"

    step "injecting module into $OPENEMR_CONTAINER"
    ssh_run "docker cp $MODULE_REMOTE/. $OPENEMR_CONTAINER:$MODULE_IN_CONTAINER/"
    if ssh_run "test -f $MODULE_REMOTE/.env"; then
        ssh_run "docker cp $MODULE_REMOTE/.env $OPENEMR_CONTAINER:$MODULE_IN_CONTAINER/.env"
        ok "module + .env injected"
    else
        warn "no .env to copy — agent will 503 until you create $MODULE_REMOTE/.env"
    fi
}

deploy_sidecar() {
    step "rsyncing sidecar → $DROPLET_HOST:$SIDECAR_REMOTE/"
    rsync -az --delete \
        --exclude='.venv' --exclude='__pycache__' \
        --exclude='.pytest_cache' --exclude='.mypy_cache' --exclude='.ruff_cache' \
        --exclude='var' --exclude='.env' \
        "$SIDECAR_LOCAL/" "$DROPLET_HOST:$SIDECAR_REMOTE/"
    ok "sidecar synced"

    # Prompt library lives at repo root (Task 43 — versioned templates).
    # The sidecar Dockerfile pulls it via a named build context so the
    # rsync target sits next to the sidecar dir on the droplet.
    step "rsyncing prompts → $DROPLET_HOST:$PROMPTS_REMOTE/"
    rsync -az --delete "$PROMPTS_LOCAL/" "$DROPLET_HOST:$PROMPTS_REMOTE/"
    ok "prompts synced"

    step "building sidecar image"
    ssh_run "cd $SIDECAR_REMOTE && docker build \
        --build-context prompts=$PROMPTS_REMOTE \
        -t ${SIDECAR_NAME}:latest . 2>&1 | tail -3"
    ok "image built"

    step "restarting sidecar container"
    ssh_run "docker rm -f $SIDECAR_NAME 2>/dev/null || true"
    # Bind-mount dashboard/dist as read-only at /app/dashboard/dist so the
    # FastAPI StaticFiles mount picks it up (see agentforge.main + T38.14).
    # `mkdir -p` keeps the container start-up clean even when the dashboard
    # has not been deployed yet — the static mount logs a warning and skips
    # itself, and the rest of the API still serves.
    ssh_run "mkdir -p $DASHBOARD_REMOTE"
    ssh_run "docker run -d --name $SIDECAR_NAME --restart unless-stopped \
        --network $OPENEMR_NETWORK \
        --env-file $SIDECAR_REMOTE/.env \
        -e DASHBOARD_DIST_DIR=/app/dashboard/dist \
        -v $DASHBOARD_REMOTE:/app/dashboard/dist:ro \
        ${SIDECAR_NAME}:latest"
    ok "container started (dashboard mount: $DASHBOARD_REMOTE → /app/dashboard/dist:ro)"
}

deploy_dashboard() {
    local skip_build="${1:-}"

    if [[ "$skip_build" != "--skip-build" ]]; then
        step "building dashboard locally (npm run build)"
        if ! command -v npm >/dev/null 2>&1; then
            die "npm not found locally. Install Node, or pass --skip-build to use the existing vue-ui/dist/"
        fi
        ( cd "$DASHBOARD_LOCAL" && npm run build )
        ok "dashboard built → $DASHBOARD_LOCAL/dist/"
    else
        warn "--skip-build passed; using existing $DASHBOARD_LOCAL/dist/ as-is"
        [[ -d "$DASHBOARD_LOCAL/dist" ]] \
            || die "$DASHBOARD_LOCAL/dist/ does not exist — drop --skip-build or run 'npm run build' first"
    fi

    step "rsyncing vue-ui/dist → $DROPLET_HOST:$DASHBOARD_REMOTE/"
    ssh_run "mkdir -p $DASHBOARD_REMOTE"
    rsync -az --delete \
        "$DASHBOARD_LOCAL/dist/" "$DROPLET_HOST:$DASHBOARD_REMOTE/"
    ok "dashboard synced"

    step "checking sidecar container picks up the new files"
    # Two cases:
    #  (a) The sidecar already runs WITH the bind mount → new files are
    #      visible immediately; FastAPI's StaticFiles re-reads from disk
    #      on every request, so no restart needed.
    #  (b) The sidecar runs WITHOUT the bind mount (deployed before
    #      T38.14 landed) → it needs a restart to pick up the new
    #      /app/dashboard/dist mount and DASHBOARD_DIST_DIR env. Detect
    #      this by inspecting the container's mounts.
    if ssh_run "docker ps --filter name=$SIDECAR_NAME --format '{{.Names}}' | grep -q ^${SIDECAR_NAME}\$"; then
        if ssh_run "docker inspect --format '{{range .Mounts}}{{.Destination}} {{end}}' $SIDECAR_NAME 2>/dev/null | grep -q '/app/dashboard/dist'"; then
            ok "sidecar already has the dashboard mount; new files are live"
        else
            warn "sidecar is running but lacks the dashboard mount — restarting via deploy_sidecar to pick it up"
            deploy_sidecar
        fi
    else
        warn "sidecar container is not running — start it with './scripts/deploy-droplet.sh sidecar'"
    fi
}

check_health() {
    step "checking sidecar container status"
    local status
    status=$(ssh_run "docker ps --filter name=$SIDECAR_NAME --format '{{.Status}}'")
    if [[ -z "$status" ]]; then
        die "sidecar container is not running"
    fi
    ok "$status"

    step "checking sidecar reachable from openemr"
    # Sidecar binds 3-5s after `docker run` returns, so a single wget
    # at T+1s is racy. Poll until /health answers or we burn 30s.
    local body=""
    local elapsed=0
    local timeout=30
    local interval=2
    while (( elapsed < timeout )); do
        if body=$(ssh_run "docker exec $OPENEMR_CONTAINER sh -c 'wget -qO- --timeout=5 http://${SIDECAR_NAME}:8000/health'" 2>/dev/null); then
            [[ -n "$body" ]] && break
        fi
        body=""
        sleep "$interval"
        elapsed=$((elapsed + interval))
    done
    [[ -n "$body" ]] || die "openemr container cannot reach sidecar after ${timeout}s"
    if (( elapsed > 0 )); then
        ok "sidecar /health responded after ${elapsed}s: $body"
    else
        ok "sidecar /health responded: $body"
    fi

    step "checking module is in openemr container"
    if ssh_run "docker exec $OPENEMR_CONTAINER test -f $MODULE_IN_CONTAINER/openemr.bootstrap.php"; then
        ok "module files present"
    else
        die "module files missing in container — re-run with 'module' subcommand"
    fi

    step "recent /turn requests"
    ssh_run "docker logs --tail 50 $SIDECAR_NAME 2>&1 | grep '/turn' | tail -5" \
        || ok "no /turn requests yet"
}

tail_logs() {
    step "tailing sidecar log (ctrl-c to stop)"
    ssh_run "docker logs --tail 30 -f $SIDECAR_NAME"
}

# ---------- Dispatch ----------

cmd="${1:-all}"
case "$cmd" in
    all)
        preflight
        deploy_module
        deploy_sidecar
        deploy_dashboard "${2:-}"
        check_health
        ;;
    module)
        preflight
        deploy_module
        ;;
    sidecar)
        preflight
        deploy_sidecar
        check_health
        ;;
    dashboard)
        preflight
        deploy_dashboard "${2:-}"
        ;;
    check)
        check_health
        ;;
    logs)
        tail_logs
        ;;
    -h|--help|help)
        sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
        ;;
    *)
        die "unknown subcommand '$cmd' — try: all | module | sidecar | dashboard | check | logs | help"
        ;;
esac

color 32 "✓ done"
