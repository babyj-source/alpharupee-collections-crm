#!/usr/bin/env bash
# Prints the values you need for GitHub and DNS. Safe to share: contains no passwords.
#   bash infra/aws/show-env.sh uat
set -euo pipefail
ENV_NAME="${1:?usage: show-env.sh uat|production}"
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/env/$ENV_NAME.env"
export AWS_DEFAULT_REGION="$AWS_REGION"
P="$NAME_PREFIX"

IID="$(aws ec2 describe-instances --filters "Name=tag:Name,Values=$P-app" "Name=instance-state-name,Values=running,pending,stopped" --query 'Reservations[0].Instances[0].InstanceId' --output text)"
EIP="$(aws ec2 describe-addresses --filters "Name=tag:Name,Values=$P-ip" --query 'Addresses[0].PublicIp' --output text)"
DBH="$(aws rds describe-db-instances --db-instance-identifier "$P-db" --query 'DBInstances[0].Endpoint.Address' --output text)"
SECRET="$(aws rds describe-db-instances --db-instance-identifier "$P-db" --query 'DBInstances[0].MasterUserSecret.SecretArn' --output text)"

cat <<MSG
Instance id : $IID
Server IP   : $EIP
Database    : $DBH
DB secret   : $SECRET   (the password stays inside AWS)
Site name   : $DOMAIN

GitHub > Settings > Environments > $ENV_NAME > Variables
  DEPLOY_HOST  = $EIP
  DEPLOY_USER  = deploy
  DEPLOY_PATH  = /opt/crm
  HEALTH_URL   = http://127.0.0.1/healthz
  RESTART_CMD  = sudo systemctl restart crm

Server SSH host key (compare with what ssh-keyscan returns before saving SSH_KNOWN_HOSTS):
MSG
aws ec2 get-console-output --instance-id "$IID" --latest --output text 2>/dev/null \
  | grep -A8 "BEGIN SSH HOST KEY FINGERPRINTS" | grep -i ed25519 \
  || echo "  (console output not ready yet, try again in a few minutes)"
