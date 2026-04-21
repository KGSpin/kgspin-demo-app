#!/usr/bin/env bash
# Launch the compare demo.
#
# Cross-repo contract (ADR-003 §7):
#   Layer 2 config repo is resolved via $KGSPIN_DEMO_CONFIG_PATH
#   (default: sibling directory ../kgspin-demo-config). If
#   $KGSPIN_DEMO_CONFIG_PATH/admin/config.yaml exists, we export
#   KGSPIN_DEMO_CONFIG pointing at it so the app loads the Layer 2
#   config instead of CWD-relative config.yaml.
#
# Reads:
#   KGSPIN_ADMIN_URL          — default http://127.0.0.1:8750
#   KGSPIN_ADMIN_PATH         — default ../kgspin-admin (sibling repo)
#   KGSPIN_DEMO_CONFIG_PATH   — default ../kgspin-demo-config (sibling repo)
#   KGSPIN_DEMO_CONFIG        — explicit config.yaml path (overrides the above)
#
# Ctrl-C stops both admin (if we started it) and the demo.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# --- Layer 2 config resolution ---------------------------------------------

CONFIG_PATH="${KGSPIN_DEMO_CONFIG_PATH:-}"
if [[ -z "$CONFIG_PATH" ]]; then
    CONFIG_PATH="$(cd "$APP_ROOT/../kgspin-demo-config" 2>/dev/null && pwd || true)"
fi

if [[ -n "$CONFIG_PATH" && -d "$CONFIG_PATH" ]]; then
    export KGSPIN_DEMO_CONFIG_PATH="$CONFIG_PATH"
    echo "[start-demo] KGSPIN_DEMO_CONFIG_PATH resolved → $CONFIG_PATH"

    # Only export KGSPIN_DEMO_CONFIG if the operator has not already set
    # it explicitly, and only if the Layer 2 admin/config.yaml exists.
    if [[ -z "${KGSPIN_DEMO_CONFIG:-}" && -f "$CONFIG_PATH/admin/config.yaml" ]]; then
        export KGSPIN_DEMO_CONFIG="$CONFIG_PATH/admin/config.yaml"
        echo "[start-demo] KGSPIN_DEMO_CONFIG → $KGSPIN_DEMO_CONFIG"
    fi
else
    echo "[start-demo] WARN: KGSPIN_DEMO_CONFIG_PATH not resolvable." >&2
    echo "              Looked at: ${KGSPIN_DEMO_CONFIG_PATH:-<unset>} and $APP_ROOT/../kgspin-demo-config" >&2
    echo "              Proceeding with app-local config.template.yaml bootstrap." >&2
fi

# --- Admin bootstrap -------------------------------------------------------

# shellcheck source=./ensure-admin-running.sh
source "$SCRIPT_DIR/ensure-admin-running.sh"

admin_pid="${ADMIN_STARTED_PID:-}"
cleanup() {
    if [[ -n "$admin_pid" ]] && kill -0 "$admin_pid" 2>/dev/null; then
        echo "[start-demo] Stopping admin (pid $admin_pid)..." >&2
        kill "$admin_pid" 2>/dev/null || true
        wait "$admin_pid" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# --- Demo launch -----------------------------------------------------------

echo "[start-demo] Starting compare demo (Ctrl-C stops admin + demo)..."
cd "$APP_ROOT"
uv run python demos/extraction/demo_compare.py
