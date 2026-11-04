#!/usr/bin/env bash
# Runs ON the server as the deploy user. Sets ONE value in /opt/crm/shared/.env with hidden input,
# without touching the rest (no new database password, no new secret key).
#   bash /opt/crm/current/server-tools/set-secret.sh MSG91_AUTHKEY
# Allowed names: MSG91_AUTHKEY MSG91_OTP_TEMPLATE_ID SMARTFLO_API_TOKEN LMS_DB_SSL_CA
set -euo pipefail

ENV_FILE=/opt/crm/shared/.env
NAME="${1:?usage: set-secret.sh NAME}"
case "$NAME" in
  MSG91_AUTHKEY|MSG91_OTP_TEMPLATE_ID|SMARTFLO_API_TOKEN|LMS_DB_SSL_CA) ;;
  *) echo "Not an allowed name: $NAME" >&2; exit 1;;
esac
[ -f "$ENV_FILE" ] || { echo "$ENV_FILE is missing. Run setup-env.sh first." >&2; exit 1; }

read -r -s -p "$NAME: " VAL; echo >&2
[ -n "$VAL" ] || { echo "Empty, nothing changed." >&2; exit 1; }
case "$VAL" in
  *\'*|*$'\n'*) echo "The value contains a quote or newline; cannot be stored safely." >&2; exit 1;;
esac

umask 077
TMP="$(mktemp "$ENV_FILE.XXXXXX")"
grep -v "^${NAME}=" "$ENV_FILE" > "$TMP" || true
printf "%s='%s'\n" "$NAME" "$VAL" >> "$TMP"
chmod 600 "$TMP"
mv "$TMP" "$ENV_FILE"
echo "$NAME saved. Restart the app to use it: sudo systemctl restart crm"
