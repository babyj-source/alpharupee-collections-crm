#!/usr/bin/env bash
# Runs ON the server as the deploy user. Sets up WhatsApp (MSG91) in /opt/crm/shared/.env and restarts the app.
# It reuses MSG91_AUTHKEY, the key the login OTP and the SMS use.
#   bash /opt/crm/current/server-tools/set-whatsapp.sh number 918065075438   # the WhatsApp business number connected in MSG91
#   bash /opt/crm/current/server-tools/set-whatsapp.sh token                 # make the secret for the webhook address and print the address
#   bash /opt/crm/current/server-tools/set-whatsapp.sh on
#   bash /opt/crm/current/server-tools/set-whatsapp.sh off
#   bash /opt/crm/current/server-tools/set-whatsapp.sh calls on|off          # show the WhatsApp call buttons in the CRM
#   bash /opt/crm/current/server-tools/set-whatsapp.sh calling enable|disable [--hours]   # switch calling on/off for the number in MSG91
set -euo pipefail
ENV_FILE="${ENV_FILE:-/opt/crm/shared/.env}"
MODE="${1:?usage: set-whatsapp.sh number <n>|token|on|off|calls on|off|calling enable|disable}"
[ -f "$ENV_FILE" ] || { echo "$ENV_FILE is missing. Run setup-env.sh first." >&2; exit 1; }

setkey() {  # setkey NAME VALUE : replace or append one line, keep everything else
  local rest
  rest="$(grep -v "^$1=" "$ENV_FILE" || true)"
  umask 077
  { [ -z "$rest" ] || printf '%s\n' "$rest"; printf '%s=%s\n' "$1" "$2"; } > "$ENV_FILE"
}
has() { grep -q "^$1=.\+" "$ENV_FILE"; }

case "$MODE" in
  number)
    NUM="${2:?usage: set-whatsapp.sh number <digits with country code, e.g. 918065075438>}"
    case "$NUM" in ''|*[!0-9]*) echo "Digits only, with the country code." >&2; exit 1;; esac
    setkey MSG91_WHATSAPP_NUMBER "$NUM" ;;
  token)
    TOKEN="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    setkey WHATSAPP_WEBHOOK_TOKEN "$TOKEN"
    HOST="${CRM_HOST:-uat-crm.alpharupee.in}"
    echo "Webhook address to give MSG91 (keep it secret):"
    echo "https://$HOST/api/messaging/whatsapp/webhook?token=$TOKEN" ;;
  on)
    has MSG91_AUTHKEY || { echo "MSG91_AUTHKEY is not set." >&2; exit 1; }
    has MSG91_WHATSAPP_NUMBER || { echo "Set the number first: set-whatsapp.sh number <n>" >&2; exit 1; }
    has WHATSAPP_WEBHOOK_TOKEN || { echo "Make the webhook secret first: set-whatsapp.sh token" >&2; exit 1; }
    setkey MSG91_WHATSAPP_ENABLED true ;;
  off) setkey MSG91_WHATSAPP_ENABLED false ;;
  calls)
    case "${2:-}" in
      on) setkey WHATSAPP_CALL_ENABLED true ;;
      off) setkey WHATSAPP_CALL_ENABLED false ;;
      *) echo "usage: set-whatsapp.sh calls on|off" >&2; exit 1 ;;
    esac ;;
  calling)
    case "${2:-}" in enable|disable) ;; *) echo "usage: set-whatsapp.sh calling enable|disable [--hours]" >&2; exit 1 ;; esac
    ( set -a; . "$ENV_FILE"; set +a; cd /opt/crm/current/backend && /opt/crm/current/venv/bin/python manage.py whatsapp_calling "$2" ${3:-} )
    echo "Done. (calling $2)"; exit 0 ;;
  *) echo "usage: set-whatsapp.sh number <n>|token|on|off|calls on|off|calling enable|disable" >&2; exit 1 ;;
esac
chmod 600 "$ENV_FILE"
if [ -z "${SKIP_RESTART:-}" ]; then sudo systemctl restart crm; fi
echo "Done. ($MODE)"
