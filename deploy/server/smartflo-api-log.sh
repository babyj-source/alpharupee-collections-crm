#!/usr/bin/env bash
# Prints the calls the CRM made to Smartflo (API name, what was sent, what came back, HTTP status, time taken), NEWEST FIRST, in Indian time.
# Tokens, passwords and whole phone numbers are never in it (numbers show only their last 4 digits).
#   sudo bash /opt/crm/current/server-tools/smartflo-api-log.sh            # the last 3 hours
#   sudo bash /opt/crm/current/server-tools/smartflo-api-log.sh 12         # the last 12 hours
#   sudo bash /opt/crm/current/server-tools/smartflo-api-log.sh 3 session  # only APIs whose name or path contains "session"
# It also saves the same text in /tmp/smartflo-api-log.txt so it can be copied out and sent to Smartflo.
set -euo pipefail
HOURS="${1:-3}"
FILTER="${2:-}"
OUT="${OUT:-/tmp/smartflo-api-log.txt}"
case "$HOURS" in ''|*[!0-9]*) echo "Hours must be a number, for example 3." >&2; exit 1 ;; esac

FORMATTER="$(mktemp)"
trap 'rm -f "$FORMATTER"' EXIT
cat > "$FORMATTER" <<'PY'
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

flt = os.environ.get("FILTER", "").lower()
rows = []
for line in sys.stdin:
    try:
        rows.append(json.loads(line.split("SMARTFLO_API ", 1)[1]))
    except (IndexError, ValueError):
        continue
rows.sort(key=lambda r: r.get("at", ""), reverse=True)
shown = 0
for r in rows:
    if flt and flt not in (r.get("api", "") + " " + r.get("path", "")).lower():
        continue
    shown += 1
    at = datetime.fromisoformat(r["at"]).astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y %H:%M:%S IST")
    status = "no reply" if r.get("status") is None else "HTTP " + str(r["status"])
    print(at, "|", r.get("api"), "|", r.get("method"), r.get("path"), "|", status, "|", r.get("ms"), "ms")
    print("  request : " + (json.dumps(r["request"]) if r.get("request") else "(none)"))
    print("  response: " + str(r.get("response") or "(empty)"))
    print()
print(str(shown) + " calls shown, newest first.", file=sys.stderr)
PY

journalctl -u crm --since "$HOURS hours ago" -o cat --no-pager 2>/dev/null | { grep -a 'SMARTFLO_API ' || true; } | FILTER="$FILTER" python3 "$FORMATTER" | tee "$OUT"
echo "Saved in $OUT"
