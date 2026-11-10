#!/usr/bin/env bash
# Runs ON the application server as the deploy user, after the first deploy has put it in the release:
#   sudo -iu deploy
#   bash /opt/crm/current/server-tools/setup-env.sh crm-uat uat-crm.alpharupee.in
# Production (before its first deploy, with the script copied to /opt/crm/shared/tools/, see docs/AWS_PRODUCTION_SETUP.md):
#   bash /opt/crm/shared/tools/setup-env.sh crm-prod crm.alpharupee.in production
#
# - reads the database admin password from AWS Secrets Manager (never shown)
# - creates the application database user with its own random password
# - asks you, with hidden input, for the MSG91 / Smartflo values; LMS details are read from Secrets Manager (set-lms.sh)
# - writes /opt/crm/shared/.env (mode 600)
# Nothing typed here appears on screen, in shell history, or in chat.
set -euo pipefail

ENV_FILE=/opt/crm/shared/.env
REGION="${AWS_REGION:-$(curl -fsS -H "X-aws-ec2-metadata-token: $(curl -fsS -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')" http://169.254.169.254/latest/meta-data/placement/region)}"
export AWS_DEFAULT_REGION="$REGION"
PREFIX="${1:?usage: setup-env.sh <name-prefix>   e.g. crm-uat}"
DOMAIN="${2:?usage: setup-env.sh <name-prefix> <domain> [production]   e.g. crm-uat uat-crm.alpharupee.in}"
MODE="${3:-}"   # "production" also verifies the database certificate and prepares monitoring
case "$MODE" in ""|production) ;; *) echo "Third argument must be 'production' or left out." >&2; exit 1;; esac

if [ -f "$ENV_FILE" ]; then
  read -r -p "$ENV_FILE exists. Overwrite? (type yes) " a; [ "$a" = "yes" ] || { echo "Left as is."; exit 0; }
fi

askhidden() { local v; read -r -s -p "$1: " v; echo >&2; printf '%s' "$v"; }
asktext()   { local v; read -r -p "$1: " v; printf '%s' "$v"; }

echo "Reading the database details from AWS..."
DB_HOST="$(aws rds describe-db-instances --db-instance-identifier "$PREFIX-db" --query 'DBInstances[0].Endpoint.Address' --output text)"
SECRET_ARN="$(aws rds describe-db-instances --db-instance-identifier "$PREFIX-db" --query 'DBInstances[0].MasterUserSecret.SecretArn' --output text)"
SECRET_JSON="$(aws secretsmanager get-secret-value --secret-id "$SECRET_ARN" --query SecretString --output text)"
ADMIN_USER="$(printf '%s' "$SECRET_JSON" | jq -r .username)"
export PGPASSWORD="$(printf '%s' "$SECRET_JSON" | jq -r .password)"
unset SECRET_JSON
if [ "$MODE" = "production" ]; then
  # Encrypted AND the database proves who it is (stops anyone in the middle pretending to be it).
  RDS_CA=/opt/crm/shared/rds-global-bundle.pem
  curl -fsSL https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem -o "$RDS_CA"
  grep -q "BEGIN CERTIFICATE" "$RDS_CA" || { echo "Could not download the RDS certificate bundle." >&2; exit 1; }
  chmod 644 "$RDS_CA"
  export PGSSLMODE=verify-full PGSSLROOTCERT="$RDS_CA"
else
  export PGSSLMODE=require
fi

APP_DB=crm; APP_USER=crm_app
APP_PW="$(openssl rand -hex 24)"
PSQL="psql -h $DB_HOST -U $ADMIN_USER -d postgres -v ON_ERROR_STOP=1 -qtA"

echo "Creating the application database user..."
if [ "$($PSQL -c "SELECT 1 FROM pg_roles WHERE rolname='$APP_USER'")" = "1" ]; then
  $PSQL -c "ALTER ROLE $APP_USER LOGIN PASSWORD '$APP_PW'"
else
  $PSQL -c "CREATE ROLE $APP_USER LOGIN PASSWORD '$APP_PW'"
fi
$PSQL -c "GRANT $APP_USER TO $ADMIN_USER"
$PSQL -c "ALTER DATABASE $APP_DB OWNER TO $APP_USER"
if [ "$MODE" = "production" ]; then
  # Lets you see which queries are slow (Performance Insights and pg_stat_statements).
  psql -h "$DB_HOST" -U "$ADMIN_USER" -d "$APP_DB" -v ON_ERROR_STOP=1 -qtA -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements"
fi
unset PGPASSWORD

echo
echo "Now the integration values. Press Enter to skip any you do not have yet (re-run later)."
MSG91_AUTHKEY="$(askhidden 'MSG91 auth key')"
MSG91_OTP_TEMPLATE_ID="$(asktext 'MSG91 OTP template id (not secret)')"
SMARTFLO_API_TOKEN="$(askhidden 'Smartflo API token')"

umask 077
{
  echo "DJANGO_SECRET_KEY=$(openssl rand -hex 32)"
  echo "DJANGO_DEBUG=0"
  echo "DJANGO_ALLOWED_HOSTS=$DOMAIN"
  echo "DB_HOST=$DB_HOST"
  echo "DB_PORT=5432"
  echo "DB_NAME=$APP_DB"
  echo "DB_USER=$APP_USER"
  echo "DB_PASSWORD=$APP_PW"
  echo "MSG91_AUTHKEY=$MSG91_AUTHKEY"
  echo "MSG91_OTP_TEMPLATE_ID=$MSG91_OTP_TEMPLATE_ID"
  echo "SMARTFLO_API_TOKEN=$SMARTFLO_API_TOKEN"
  echo "LMS_TENANT_CODE=NBFC1"
  if [ "$MODE" = "production" ]; then
    echo "DB_SSLMODE=verify-full"
    echo "DB_SSLROOTCERT=$RDS_CA"
    echo "DJANGO_HSTS_SECONDS=86400"
  fi
} > "$ENV_FILE"
chmod 600 "$ENV_FILE"
echo "Written $ENV_FILE (readable only by the deploy user)."

# LMS connection details come from AWS Secrets Manager, never typed or pasted.
bash "$(dirname "$0")/set-lms.sh" || echo "LMS details not set yet. Run set-lms.sh when the secret is readable."
