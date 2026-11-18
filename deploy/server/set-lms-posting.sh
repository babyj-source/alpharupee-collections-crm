#!/usr/bin/env bash
# Runs ON the server as the deploy user. Sets up posting of paid payment links to the LMS (Encore "flow collection" API) in
# /opt/crm/shared/.env and restarts the app. The password is typed at a prompt (not shown) so it never appears in the shell history.
#   bash /opt/crm/current/server-tools/set-lms-posting.sh login      # asks for the LMS API user id (default crm), then the password
#   bash /opt/crm/current/server-tools/set-lms-posting.sh bank       # asks for the bank account code (e.g. BANK001) and the instrument (e.g. ONLINE)
#   bash /opt/crm/current/server-tools/set-lms-posting.sh url <address>   # the LMS address; the UAT one is already the default
#   bash /opt/crm/current/server-tools/set-lms-posting.sh on | off | status
# Posting is OFF until you run "on". Run "status" first: it says what is still missing.
set -euo pipefail
ENV_FILE="${ENV_FILE:-/opt/crm/shared/.env}"
MODE="${1:?usage: set-lms-posting.sh login|bank|url <address>|on|off|status}"
[ -f "$ENV_FILE" ] || { echo "$ENV_FILE is missing. Run setup-env.sh first." >&2; exit 1; }

setkey() {  # setkey NAME VALUE : replace or append one line, keep everything else. The value is single-quoted, so spaces, # and $ are safe.
  case "$2" in *"'"*|*$'\n'*) echo "The value must not contain a single quote or a line break." >&2; exit 1 ;; esac
  local rest
  rest="$(grep -v "^$1=" "$ENV_FILE" || true)"
  umask 077
  { [ -z "$rest" ] || printf '%s\n' "$rest"; printf "%s='%s'\n" "$1" "$2"; } > "$ENV_FILE"
}
has() { grep -q "^$1=.\+" "$ENV_FILE"; }

case "$MODE" in
  login)
    read -r -p "LMS API user id [crm]: " U; U="${U:-crm}"
    read -r -s -p "LMS API password (hidden): " P; echo
    read -r -s -p "Type it again: " P2; echo
    [ -n "$P" ] || { echo "A password is needed." >&2; exit 1; }
    [ "$P" = "$P2" ] || { echo "The two entries differ. Nothing was changed." >&2; exit 1; }
    setkey LMS_POST_USER "$U"; setkey LMS_POST_PASSWORD "$P" ;;
  bank)
    read -r -p "Bank account code in the LMS (for example BANK001): " B
    read -r -p "Instrument in the LMS (for example ONLINE, CASH): " I
    [ -n "$B" ] && [ -n "$I" ] || { echo "Both are needed." >&2; exit 1; }
    setkey LMS_POST_ACCOUNT_CODE "$B"; setkey LMS_POST_INSTRUMENT "$I" ;;
  url)
    A="${2:?usage: set-lms-posting.sh url <address>}"
    case "$A" in https://*) ;; *) echo "The address must start with https://" >&2; exit 1 ;; esac
    setkey LMS_POST_URL "$A" ;;
  on)
    for N in LMS_POST_USER LMS_POST_PASSWORD LMS_POST_ACCOUNT_CODE LMS_POST_INSTRUMENT; do has "$N" || { echo "$N is not set. Run: login, then bank." >&2; exit 1; }; done
    setkey LMS_POST_ENABLED true ;;
  off) setkey LMS_POST_ENABLED false ;;
  status)
    for N in LMS_POST_USER LMS_POST_PASSWORD LMS_POST_ACCOUNT_CODE LMS_POST_INSTRUMENT; do has "$N" && echo "$N: set" || echo "$N: NOT set"; done
    grep -E "^(LMS_POST_ENABLED|LMS_POST_URL|LMS_POST_USER|LMS_POST_ACCOUNT_CODE|LMS_POST_INSTRUMENT)=" "$ENV_FILE" || true
    exit 0 ;;
  *) echo "usage: set-lms-posting.sh login|bank|url <address>|on|off|status" >&2; exit 1 ;;
esac
chmod 600 "$ENV_FILE"
if [ -z "${SKIP_RESTART:-}" ]; then sudo systemctl restart crm; fi
echo "Done. ($MODE)"
