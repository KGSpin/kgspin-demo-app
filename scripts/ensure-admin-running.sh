#!/usr/bin/env bash
# Ensure kgspin-admin is responding at $KGSPIN_ADMIN_URL. If not,
# background-start it from $KGSPIN_ADMIN_PATH and wait up to 30s for
# it to come up. On success, echoes the PID of the started admin (or
# empty string if admin was already running and we did not start it).
#
# Reads:
#   KGSPIN_ADMIN_URL   — default http://127.0.0.1:8750
#   KGSPIN_ADMIN_PATH  — default ../kgspin-admin (sibling of this repo)
#   KGSPIN_ADMIN_LOG   — default /tmp/kgspin-admin.log
#
# Usage:
#   source scripts/ensure-admin-running.sh  # sets $ADMIN_STARTED_PID
#   or
#   admin_pid=$(scripts/ensure-admin-running.sh --print-pid)

set -euo pipefail

ADMIN_URL="${KGSPIN_ADMIN_URL:-http://127.0.0.1:8750}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ADMIN_PATH="${KGSPIN_ADMIN_PATH:-$(cd "$APP_ROOT/../kgspin-admin" 2>/dev/null && pwd || true)}"
ADMIN_LOG="${KGSPIN_ADMIN_LOG:-/tmp/kgspin-admin.log}"

# Parse host + port out of the URL (http://host:port form).
hostport="${ADMIN_URL#*://}"
hostport="${hostport%/}"
HOST="${hostport%:*}"
PORT="${hostport##*:}"
if [[ "$PORT" == "$hostport" ]]; then
    PORT=80
fi

ping_admin() {
    curl -fsS -m 2 "${ADMIN_URL}/resources?kind=fetcher" >/dev/null 2>&1
}

ADMIN_STARTED_PID=""

if ping_admin; then
    echo "[ensure-admin] Admin already running at $ADMIN_URL — reusing."
else
    if [[ -z "$ADMIN_PATH" || ! -d "$ADMIN_PATH" ]]; then
        echo "[ensure-admin] ERROR: admin not responding at $ADMIN_URL" >&2
        echo "                and admin repo not found." >&2
        echo "                Set KGSPIN_ADMIN_PATH to the kgspin-admin repo root," >&2
        echo "                or clone it as a sibling of kgspin-demo-app." >&2
        exit 1
    fi
    echo "[ensure-admin] Admin not responding; starting from $ADMIN_PATH"
    echo "[ensure-admin] Admin logs → $ADMIN_LOG"
    (
        cd "$ADMIN_PATH" && \
        uv run uvicorn kgspin_admin.http.bootstrap:app \
            --host "$HOST" --port "$PORT" >"$ADMIN_LOG" 2>&1
    ) &
    ADMIN_STARTED_PID=$!

    echo -n "[ensure-admin] Waiting for admin"
    for _ in $(seq 1 30); do
        if ping_admin; then
            echo " — up."
            break
        fi
        if ! kill -0 "$ADMIN_STARTED_PID" 2>/dev/null; then
            echo
            echo "[ensure-admin] ERROR: admin process exited. Last 20 lines of $ADMIN_LOG:" >&2
            tail -n 20 "$ADMIN_LOG" >&2 || true
            exit 1
        fi
        echo -n "."
        sleep 1
    done
    if ! ping_admin; then
        echo
        echo "[ensure-admin] ERROR: admin did not come up within 30s" >&2
        echo "[ensure-admin] Last 20 lines of $ADMIN_LOG:" >&2
        tail -n 20 "$ADMIN_LOG" >&2 || true
        exit 1
    fi
fi

# When run standalone (not sourced), print the started PID so the caller
# can capture it and manage cleanup. When sourced, the caller reads
# $ADMIN_STARTED_PID directly.
if [[ "${1:-}" == "--print-pid" ]]; then
    echo "$ADMIN_STARTED_PID"
fi
