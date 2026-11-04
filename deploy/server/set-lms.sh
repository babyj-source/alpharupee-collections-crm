#!/usr/bin/env bash
# Runs ON the server as the deploy user. Copies the LMS read-only connection details from AWS Secrets Manager
# into /opt/crm/shared/.env. Nothing is printed except the host and port.
#   bash /opt/crm/current/server-tools/set-lms.sh [secret-id]
# The secret is JSON with keys: host, port, user, db, password.
set -euo pipefail

ENV_FILE=/opt/crm/shared/.env
SECRET_ID="${1:-alpharupee/lms/readonlyuser}"
TOKEN="$(curl -fsS -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')"
export AWS_DEFAULT_REGION="${AWS_REGION:-$(curl -fsS -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/placement/region)}"

[ -f "$ENV_FILE" ] || { echo "$ENV_FILE is missing. Run setup-env.sh first." >&2; exit 1; }

J="$(aws secretsmanager get-secret-value --secret-id "$SECRET_ID" --query SecretString --output text)"
get() { printf '%s' "$J" | jq -r --arg k "$1" '.[$k] // empty'; }
HOST="$(get host)"; PORT="$(get port)"; USERN="$(get user)"; DBN="$(get db)"; PW="$(get password)"
unset J
for v in HOST PORT USERN DBN PW; do
  [ -n "${!v}" ] || { echo "Secret $SECRET_ID is missing a value for $v." >&2; exit 1; }
done
case "$PW$USERN$DBN$HOST" in
  *\'*|*$'\n'*) echo "A value contains a quote or newline; cannot be written safely to .env." >&2; exit 1;;
esac

umask 077
TMP="$(mktemp "$ENV_FILE.XXXXXX")"
grep -v '^LMS_DB_' "$ENV_FILE" > "$TMP" || true
{
  printf "LMS_DB_HOST='%s'\n" "$HOST"
  printf "LMS_DB_PORT='%s'\n" "$PORT"
  printf "LMS_DB_NAME='%s'\n" "$DBN"
  printf "LMS_DB_USER='%s'\n" "$USERN"
  printf "LMS_DB_PASSWORD='%s'\n" "$PW"
} >> "$TMP"
chmod 600 "$TMP"
mv "$TMP" "$ENV_FILE"
echo "LMS settings written to $ENV_FILE (host $HOST, port $PORT). The password was not shown."
