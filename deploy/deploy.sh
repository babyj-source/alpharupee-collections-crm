#!/usr/bin/env bash
# Runs ON the server. Unpacks a release, switches the "current" link, restarts, checks health,
# and goes back to the previous release if the check fails.
#
# Usage: deploy.sh --id RELEASE_ID --archive /path/release.tgz [--base /opt/crm]
# Optional environment:
#   RESTART_CMD  command that restarts the app (default: sudo systemctl restart crm; set to "none" to skip)
#   HEALTH_URL   URL that must return success after the restart (empty skips the check)
#   KEEP         number of old releases to keep (default 5)
set -euo pipefail

BASE=/opt/crm; ID=""; ARCHIVE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --id) ID="$2"; shift 2 ;;
    --archive) ARCHIVE="$2"; shift 2 ;;
    --base) BASE="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$ID" ] && [ -f "$ARCHIVE" ] || { echo "usage: deploy.sh --id ID --archive FILE [--base DIR]" >&2; exit 2; }

RESTART_CMD="${RESTART_CMD:-sudo systemctl restart crm}"
HEALTH_URL="${HEALTH_URL:-}"
KEEP="${KEEP:-5}"
REL="$BASE/releases/$ID"
CUR="$BASE/current"

restart() {
  if [ "$RESTART_CMD" = "none" ] || [ -z "$RESTART_CMD" ]; then return 0; fi
  bash -c "$RESTART_CMD"
}
healthy() {
  [ -z "$HEALTH_URL" ] && return 0
  for _ in $(seq 1 10); do
    if curl -fsS --max-time 5 "$HEALTH_URL" >/dev/null; then return 0; fi
    sleep 3
  done
  return 1
}

mkdir -p "$BASE/releases"
PREV="$(readlink -f "$CUR" 2>/dev/null || true)"

[ -e "$REL" ] && { echo "release $ID already exists" >&2; exit 1; }
mkdir "$REL"
tar -xzf "$ARCHIVE" -C "$REL"
if [ -x "$REL/post-deploy.sh" ]; then (cd "$REL" && ./post-deploy.sh); fi

ln -sfn "$REL" "$CUR.new"
mv -Tf "$CUR.new" "$CUR"
echo "switched current -> $REL"

if restart && healthy; then
  echo "release $ID is live"
else
  echo "release $ID failed its restart or health check" >&2
  if [ -n "$PREV" ] && [ -d "$PREV" ]; then
    ln -sfn "$PREV" "$CUR.new"; mv -Tf "$CUR.new" "$CUR"
    restart || true
    echo "rolled back to $PREV" >&2
  fi
  exit 1
fi

# keep the newest $KEEP releases, never the live or previous one
ls -1dt "$BASE"/releases/* 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
  [ "$old" = "$(readlink -f "$CUR")" ] || [ "$old" = "$PREV" ] || rm -rf "$old"
done
