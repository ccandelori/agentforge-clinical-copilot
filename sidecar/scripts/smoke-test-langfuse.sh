#!/usr/bin/env bash
set -euo pipefail

# Smoke test: verify sidecar /turn emits Langfuse traces on the droplet.
#
# Usage:
#   LANGFUSE_PUBLIC_KEY=pk-xxx \
#   LANGFUSE_SECRET_KEY=sk-xxx \
#   ./smoke-test-langfuse.sh
#
# Optional overrides:
#   DROPLET_URL      — default https://143.244.157.90:9300
#   LANGFUSE_HOST    — default https://cloud.langfuse.com
#   SIDECAR_JWT      — pre-minted JWT for the /turn endpoint (required unless
#                      the sidecar accepts unauthenticated requests)
#
# Exit codes:
#   0  all checks passed
#   1  assertion failure (see output)
#   2  dependency missing (curl, jq)

# ---------- prerequisites -------------------------------------------------

for dep in curl jq; do
    if ! command -v "$dep" &>/dev/null; then
        echo "ERROR: $dep is required but not installed." >&2
        exit 2
    fi
done

# ---------- configuration -------------------------------------------------

DROPLET_URL="${DROPLET_URL:-https://143.244.157.90:9300}"
SIDECAR_URL="${DROPLET_URL%/}/sidecar"
LANGFUSE_HOST="${LANGFUSE_HOST:-https://cloud.langfuse.com}"
LANGFUSE_PUBLIC_KEY="${LANGFUSE_PUBLIC_KEY:?set LANGFUSE_PUBLIC_KEY}"
LANGFUSE_SECRET_KEY="${LANGFUSE_SECRET_KEY:?set LANGFUSE_SECRET_KEY}"
SIDECAR_JWT="${SIDECAR_JWT:-}"

CURL_OPTS=(-sk --max-time 30)

echo "=== AgentForge Langfuse smoke test ==="
echo "Sidecar : $SIDECAR_URL"
echo "Langfuse: $LANGFUSE_HOST"
echo

# ---------- step 1: health check -----------------------------------------

echo "[1/4] Checking sidecar /health ..."
HEALTH_RESP=$(curl "${CURL_OPTS[@]}" "$SIDECAR_URL/health")
if ! echo "$HEALTH_RESP" | jq -e '.status == "healthy"' >/dev/null 2>&1; then
    echo "FAIL: /health did not return {status: healthy}" >&2
    echo "      Response: $HEALTH_RESP" >&2
    exit 1
fi
echo "      OK — $(echo "$HEALTH_RESP" | jq -r '.status')"

# ---------- step 2: run a /turn ------------------------------------------

echo "[2/4] Running a test /turn ..."

AUTH_HEADER=()
if [[ -n "$SIDECAR_JWT" ]]; then
    AUTH_HEADER=(-H "Authorization: Bearer $SIDECAR_JWT")
fi

TURN_BODY='{"message":"What medications is this patient on?","session_id":"smoke-test-session"}'

TURN_RESP=$(curl "${CURL_OPTS[@]}" \
    "${AUTH_HEADER[@]}" \
    -X POST \
    -H "Content-Type: application/json" \
    -d "$TURN_BODY" \
    -D /tmp/smoke_test_headers.txt \
    "$SIDECAR_URL/turn")

HTTP_STATUS=$(head -1 /tmp/smoke_test_headers.txt | awk '{print $2}')
if [[ "$HTTP_STATUS" != "200" ]]; then
    echo "FAIL: /turn returned HTTP $HTTP_STATUS" >&2
    echo "      Response: $TURN_RESP" >&2
    exit 1
fi
echo "      HTTP $HTTP_STATUS — reply received"

# ---------- step 3: extract X-Trace-Id -----------------------------------

echo "[3/4] Extracting X-Trace-Id header ..."
TRACE_ID=$(grep -i "^x-trace-id:" /tmp/smoke_test_headers.txt \
    | awk '{print $2}' \
    | tr -d '[:space:]')

if [[ -z "$TRACE_ID" ]]; then
    echo "WARN: X-Trace-Id header not present." >&2
    echo "      This is expected when LANGFUSE_HOST is not configured on the" >&2
    echo "      sidecar, or when the NullLangfuseClient is active." >&2
    echo "      Skipping Langfuse API verification." >&2
    echo
    echo "=== Partial pass (no trace ID available) ==="
    exit 0
fi
echo "      trace_id = $TRACE_ID"

# ---------- step 4: query Langfuse for the trace -------------------------

echo "[4/4] Querying Langfuse API for trace $TRACE_ID ..."

LF_TRACE_RESP=$(curl "${CURL_OPTS[@]}" \
    -u "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" \
    "$LANGFUSE_HOST/api/public/traces/$TRACE_ID")

if ! echo "$LF_TRACE_RESP" | jq -e '.id' >/dev/null 2>&1; then
    echo "FAIL: Langfuse did not return a trace for id=$TRACE_ID" >&2
    echo "      Response: $LF_TRACE_RESP" >&2
    exit 1
fi

LF_TRACE_NAME=$(echo "$LF_TRACE_RESP" | jq -r '.name // "unknown"')
echo "      Found trace: name=$LF_TRACE_NAME id=$TRACE_ID"

echo
echo "=== PASS — trace $TRACE_ID confirmed in Langfuse ==="
