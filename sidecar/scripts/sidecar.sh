#!/usr/bin/env bash
# AgentForge sidecar control script.
#
# Usage:
#   ./scripts/sidecar.sh start    # daemonize uvicorn on :8000
#   ./scripts/sidecar.sh stop     # SIGTERM the running process
#   ./scripts/sidecar.sh restart  # stop + start
#   ./scripts/sidecar.sh status   # is it running, on what port, since when
#   ./scripts/sidecar.sh logs     # tail -f the log file
#   ./scripts/sidecar.sh logs -n 200   # show the last 200 lines and exit
#
# Run from the sidecar/ directory:  cd sidecar && ./scripts/sidecar.sh start
#
# Reads JWT_SECRET / ANTHROPIC_API_KEY / etc. from sidecar/.env via
# pydantic-settings. Logs go to sidecar/var/sidecar.log; the PID lives
# at sidecar/var/sidecar.pid. Both files are gitignored.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIDECAR_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VAR_DIR="$SIDECAR_DIR/var"
PID_FILE="$VAR_DIR/sidecar.pid"
LOG_FILE="$VAR_DIR/sidecar.log"
PORT="${AGENTFORGE_PORT:-8000}"

mkdir -p "$VAR_DIR"

is_running() {
    [[ -f "$PID_FILE" ]] || return 1
    local pid
    pid="$(cat "$PID_FILE")"
    kill -0 "$pid" 2>/dev/null
}

start() {
    if is_running; then
        echo "sidecar already running (pid $(cat "$PID_FILE"))"
        return 0
    fi
    cd "$SIDECAR_DIR"
    echo "starting sidecar on :$PORT (logs: $LOG_FILE)"
    nohup uv run uvicorn agentforge.main:create_app --factory \
        --host 0.0.0.0 --port "$PORT" --reload \
        >>"$LOG_FILE" 2>&1 &
    echo $! >"$PID_FILE"
    sleep 1
    if is_running; then
        echo "started (pid $(cat "$PID_FILE"))"
    else
        echo "failed to start; tail $LOG_FILE for details" >&2
        rm -f "$PID_FILE"
        return 1
    fi
}

stop() {
    if ! is_running; then
        echo "sidecar not running"
        rm -f "$PID_FILE"
        return 0
    fi
    local pid
    pid="$(cat "$PID_FILE")"
    echo "stopping sidecar (pid $pid)"
    kill "$pid" 2>/dev/null || true
    for _ in {1..10}; do
        is_running || break
        sleep 0.3
    done
    if is_running; then
        echo "process did not exit on SIGTERM; sending SIGKILL"
        kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
    echo "stopped"
}

status() {
    if is_running; then
        local pid
        pid="$(cat "$PID_FILE")"
        local started_at
        # ps -o lstart returns the process start time portably across macOS/Linux
        started_at="$(ps -o lstart= -p "$pid" 2>/dev/null | awk '{$1=$1};1')"
        echo "running (pid $pid) on :$PORT, started $started_at"
    else
        echo "not running"
        return 1
    fi
}

logs() {
    if [[ ! -f "$LOG_FILE" ]]; then
        echo "no log file at $LOG_FILE (sidecar never started?)"
        return 1
    fi
    if [[ "${1:-}" == "-n" && -n "${2:-}" ]]; then
        tail -n "$2" "$LOG_FILE"
    else
        tail -f "$LOG_FILE"
    fi
}

case "${1:-}" in
    start) start ;;
    stop) stop ;;
    restart) stop; start ;;
    status) status ;;
    logs) shift; logs "$@" ;;
    *)
        echo "usage: $0 {start|stop|restart|status|logs [-n N]}" >&2
        exit 2
        ;;
esac
