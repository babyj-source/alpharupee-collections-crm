#!/usr/bin/env bash
# Runs ON the server as the deploy user. Switches the collections SMS (MSG91 reminder / payment-link templates) on or off in
# /opt/crm/shared/.env and restarts the app. It reuses MSG91_AUTHKEY, the same key the login OTP uses.
#   bash /opt/crm/current/server-tools/set-sms.sh on     # asks for the https link put in the payment-link SMS, then switches on
#   bash /opt/crm/current/server-tools/set-sms.sh off
set -euo pipefail
ENV_FILE="${ENV_FILE:-/opt/crm/shared/.env}"
MODE="${1:?usage: set-sms.sh on|off}"
[ -f "$ENV_FILE" ] || { echo "$ENV_FILE is missing. Run setup-env.sh first." >&2; exit 1; }

setkey() {  # setkey NAME VALUE : replace or append one line, keep everything else
  local rest
  rest="$(grep -v "^$1=" "$ENV_FILE" || true)"
  umask 077
  { [ -z "$rest" ] || printf '%s\n' "$rest"; printf '%s=%s\n' "$1" "$2"; } > "$ENV_FILE"
}

case "$MODE" in
  on)
    grep -q '^MSG91_AUTHKEY=.\+' "$ENV_FILE" || { echo "MSG91_AUTHKEY is not set in $ENV_FILE." >&2; exit 1; }
    read -r -p "Payment link for the SMS (https://..., Enter keeps the current one): " LINK
    if [ -n "$LINK" ]; then
      case "$LINK" in https://*) ;; *) echo "The link must start with https://" >&2; exit 1;; esac
      case "$LINK" in *\'*|*\"*|*' '*) echo "Link has a quote or space; not written." >&2; exit 1;; esac
      setkey PAYMENT_LINK_DEFAULT "$LINK"
    fi
    setkey MSG91_SMS_ENABLED true ;;
  off) setkey MSG91_SMS_ENABLED false ;;
  *) echo "usage: set-sms.sh on|off" >&2; exit 1 ;;
esac
chmod 600 "$ENV_FILE"
if [ -z "${SKIP_RESTART:-}" ]; then sudo systemctl restart crm; fi
echo "Done. Collections SMS is now: $MODE"
