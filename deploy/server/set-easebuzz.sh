#!/usr/bin/env bash
# Runs ON the server as the deploy user. Sets up Easebuzz payment links in /opt/crm/shared/.env and restarts the app.
# The key and salt are typed at a prompt (the salt is not shown) so they never appear in the shell history or on screen.
#   bash /opt/crm/current/server-tools/set-easebuzz.sh key            # asks for the merchant key, then the salt
#   bash /opt/crm/current/server-tools/set-easebuzz.sh submerchant S295719J5NW
#   bash /opt/crm/current/server-tools/set-easebuzz.sh sandbox        # test environment (testdashboard.easebuzz.in) - the default
#   bash /opt/crm/current/server-tools/set-easebuzz.sh production     # live environment (dashboard.easebuzz.in)
#   bash /opt/crm/current/server-tools/set-easebuzz.sh on | off | status
set -euo pipefail
ENV_FILE="${ENV_FILE:-/opt/crm/shared/.env}"
MODE="${1:?usage: set-easebuzz.sh key|submerchant <id>|sandbox|production|on|off|status}"
[ -f "$ENV_FILE" ] || { echo "$ENV_FILE is missing. Run setup-env.sh first." >&2; exit 1; }

setkey() {  # setkey NAME VALUE : replace or append one line, keep everything else
  local rest
  rest="$(grep -v "^$1=" "$ENV_FILE" || true)"
  umask 077
  { [ -z "$rest" ] || printf '%s\n' "$rest"; printf '%s=%s\n' "$1" "$2"; } > "$ENV_FILE"
}
has() { grep -q "^$1=.\+" "$ENV_FILE"; }

case "$MODE" in
  key)
    read -r -p "Easebuzz merchant key: " K
    read -r -s -p "Easebuzz salt (hidden): " S; echo
    [ -n "$K" ] && [ -n "$S" ] || { echo "Both are needed." >&2; exit 1; }
    setkey EASEBUZZ_KEY "$K"; setkey EASEBUZZ_SALT "$S" ;;
  submerchant)
    ID="${2:?usage: set-easebuzz.sh submerchant <sub-merchant id>}"
    setkey EASEBUZZ_SUB_MERCHANT_ID "$ID" ;;
  sandbox) setkey EASEBUZZ_BASE_URL "https://testdashboard.easebuzz.in" ;;
  production) setkey EASEBUZZ_BASE_URL "https://dashboard.easebuzz.in" ;;
  on)
    for N in EASEBUZZ_KEY EASEBUZZ_SALT EASEBUZZ_SUB_MERCHANT_ID; do has "$N" || { echo "$N is not set." >&2; exit 1; }; done
    setkey EASEBUZZ_ENABLED true ;;
  off) setkey EASEBUZZ_ENABLED false ;;
  status)
    for N in EASEBUZZ_KEY EASEBUZZ_SALT EASEBUZZ_SUB_MERCHANT_ID; do has "$N" && echo "$N: set" || echo "$N: NOT set"; done
    grep -E "^(EASEBUZZ_ENABLED|EASEBUZZ_BASE_URL)=" "$ENV_FILE" || true
    exit 0 ;;
  *) echo "usage: set-easebuzz.sh key|submerchant <id>|sandbox|production|on|off|status" >&2; exit 1 ;;
esac
chmod 600 "$ENV_FILE"
if [ -z "${SKIP_RESTART:-}" ]; then sudo systemctl restart crm; fi
echo "Done. ($MODE)"
