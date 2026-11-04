#!/usr/bin/env bash
# Runs ON the server. Switches "current" to the release before the live one and restarts.
# Usage: rollback.sh [--base /opt/crm]
set -euo pipefail
BASE=/opt/crm
[ "${1:-}" = "--base" ] && BASE="$2"
RESTART_CMD="${RESTART_CMD:-sudo systemctl restart crm}"
CUR="$BASE/current"
LIVE="$(readlink -f "$CUR")"
PREV="$(ls -1dt "$BASE"/releases/* | grep -vx "$LIVE" | head -n 1 || true)"
[ -n "$PREV" ] || { echo "no previous release to roll back to" >&2; exit 1; }
ln -sfn "$PREV" "$CUR.new"; mv -Tf "$CUR.new" "$CUR"
if [ "$RESTART_CMD" != "none" ] && [ -n "$RESTART_CMD" ]; then bash -c "$RESTART_CMD"; fi
echo "rolled back from $LIVE to $PREV"
