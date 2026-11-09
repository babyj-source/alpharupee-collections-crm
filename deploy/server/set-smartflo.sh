#!/usr/bin/env bash
# Runs ON the server as the deploy user. Sets the Smartflo account token (hidden input) and the on/off switch in
# /opt/crm/shared/.env, then restarts the app. The token is never printed or kept in shell history.
#   bash /opt/crm/current/server-tools/set-smartflo.sh on     # ask for the token, switch the dialler on
#   bash /opt/crm/current/server-tools/set-smartflo.sh off    # switch the dialler off (token kept)
#   bash /opt/crm/current/server-tools/set-smartflo.sh caller 1600322818   # the outgoing number customers see (caller_id)
#   bash /opt/crm/current/server-tools/set-smartflo.sh did 1000631         # only if the CRM cannot find that number's id in Smartflo itself
set -euo pipefail
ENV_FILE="${ENV_FILE:-/opt/crm/shared/.env}"
MODE="${1:?usage: set-smartflo.sh on|off}"
[ -f "$ENV_FILE" ] || { echo "$ENV_FILE is missing. Run setup-env.sh first." >&2; exit 1; }

setkey() {  # setkey NAME VALUE : replace or append one line, keep everything else
  local rest
  rest="$(grep -v "^$1=" "$ENV_FILE" || true)"
  umask 077
  { [ -z "$rest" ] || printf '%s\n' "$rest"; printf '%s=%s\n' "$1" "$2"; } > "$ENV_FILE"
}

case "$MODE" in
  on)
    read -r -s -p "Smartflo account API token (hidden, Enter keeps the current one): " TOKEN; echo
    if [ -n "$TOKEN" ]; then
      case "$TOKEN" in *\'*|*\"*|*' '*) echo "Token has a quote or space; not written." >&2; exit 1;; esac
      setkey SMARTFLO_API_TOKEN "$TOKEN"
    fi
    grep -q '^SMARTFLO_API_TOKEN=.\+' "$ENV_FILE" || { echo "No token is set yet." >&2; exit 1; }
    setkey SMARTFLO_ENABLED true ;;
  off) setkey SMARTFLO_ENABLED false ;;
  caller)
    NUM="${2:?usage: set-smartflo.sh caller <outgoing number, digits only>}"
    case "$NUM" in ''|*[!0-9]*) echo "Digits only, for example 1600322818." >&2; exit 1;; esac
    setkey SMARTFLO_CALLER_ID "$NUM" ;;
  did)
    ID="${2:?usage: set-smartflo.sh did <number id from the My Numbers list in Smartflo>}"
    case "$ID" in ''|*[!0-9]*) echo "Digits only, for example 1000631." >&2; exit 1;; esac
    setkey SMARTFLO_CALLER_DID_ID "$ID" ;;
  *) echo "usage: set-smartflo.sh on|off|caller <number>|did <id>" >&2; exit 1 ;;
esac
chmod 600 "$ENV_FILE"
if [ -z "${SKIP_RESTART:-}" ]; then sudo systemctl restart crm; fi
echo "Done. ($MODE)"
