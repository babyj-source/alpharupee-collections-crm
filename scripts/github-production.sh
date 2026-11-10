#!/usr/bin/env bash
# Run on YOUR machine after `gh auth login`, once the Production server exists (infra/aws/create-env.sh production).
# Sets up the GitHub "production" environment for deployments:
#   - keeps (or adds) the required reviewers, so a release waits for an approval
#   - only lets version tags (v*) and main deploy to it
#   - sets the five variables and the two secrets the deploy needs
# The private key is read from your file and sent straight to GitHub. It is never printed or stored anywhere else.
#
# The site name defaults to crm.alpharupee.in (set DOMAIN=... to change it).
#
# Usage: scripts/github-production.sh OWNER/REPO SERVER_IP [KEY_FILE] [REVIEWER_USERNAME ...]
#   e.g. scripts/github-production.sh babyj-source/alpharupee-collections-crm 13.200.1.2 ~/.ssh/crm_deploy_prod
set -euo pipefail

FULL="${1:?usage: github-production.sh OWNER/REPO SERVER_IP [KEY_FILE] [REVIEWER ...]}"
IP="${2:?usage: github-production.sh OWNER/REPO SERVER_IP [KEY_FILE] [REVIEWER ...]}"
KEY_FILE="${3:-$HOME/.ssh/crm_deploy_prod}"
shift 3 2>/dev/null || shift $#
NEW_REVIEWERS=("$@")
ENV=production
DOMAIN="${DOMAIN:-crm.alpharupee.in}"
HEALTH_URL="https://$DOMAIN/api/health"   # checks the site over HTTPS AND that Django can reach the database

command -v gh >/dev/null || { echo "Install the GitHub CLI first: https://cli.github.com"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "Run: gh auth login   (then re-run this script)"; exit 1; }
command -v ssh-keyscan >/dev/null || { echo "ssh-keyscan is needed (it comes with ssh)."; exit 1; }
[ -f "$KEY_FILE" ] || { echo "Private key not found: $KEY_FILE"; exit 1; }
head -n1 "$KEY_FILE" | grep -q "BEGIN OPENSSH PRIVATE KEY" || { echo "$KEY_FILE is not a private key (use the file WITHOUT .pub)."; exit 1; }

echo "==> 1. Check the server's identity before trusting it"
KH="$(ssh-keyscan -t ed25519 "$IP" 2>/dev/null)"
[ -n "$KH" ] || { echo "Could not reach $IP on port 22."; exit 1; }
echo "    Fingerprint the server shows now:"
printf '%s\n' "$KH" | ssh-keygen -lf - | sed 's/^/      /'
echo "    It must be identical to the ed25519 fingerprint printed by:  bash infra/aws/show-env.sh production"
read -r -p "    Identical? Type yes to continue: " a
[ "$a" = "yes" ] || { echo "Stopped. Nothing was changed."; exit 1; }

echo "==> 2. The production environment: reviewers and allowed refs"
IDS="$(gh api "repos/$FULL/environments/$ENV" --jq '[.protection_rules[]? | select(.type=="required_reviewers") | .reviewers[]? | "{\"type\":\"" + .type + "\",\"id\":" + (.reviewer.id|tostring) + "}"] | join(",")' 2>/dev/null || true)"
for u in "${NEW_REVIEWERS[@]}"; do
  id="$(gh api "users/$u" --jq .id)"
  IDS="${IDS:+$IDS,}{\"type\":\"User\",\"id\":$id}"
done
[ -n "$IDS" ] || { echo "The production environment has no reviewers. Add them: re-run with GitHub usernames after the key file, e.g.  ... $KEY_FILE baby-jose"; exit 1; }
gh api -X PUT "repos/$FULL/environments/$ENV" --input - >/dev/null <<JSON
{ "reviewers": [$IDS], "prevent_self_review": false,
  "deployment_branch_policy": { "protected_branches": false, "custom_branch_policies": true } }
JSON
for pol in "v*:tag" "main:branch"; do
  gh api -X POST "repos/$FULL/environments/$ENV/deployment-branch-policies" -f "name=${pol%%:*}" -f "type=${pol##*:}" >/dev/null 2>&1 \
    && echo "    allowed: ${pol%%:*} (${pol##*:})" || echo "    already allowed: ${pol%%:*} (${pol##*:})"
done
echo "    reviewers saved (they must approve every Production deploy and rollback)"

echo "==> 3. Variables"
gh variable set DEPLOY_HOST  --env "$ENV" --repo "$FULL" --body "$IP"
gh variable set DEPLOY_USER  --env "$ENV" --repo "$FULL" --body "deploy"
gh variable set DEPLOY_PATH  --env "$ENV" --repo "$FULL" --body "/opt/crm"
gh variable set HEALTH_URL   --env "$ENV" --repo "$FULL" --body "$HEALTH_URL"
gh variable set RESTART_CMD  --env "$ENV" --repo "$FULL" --body "sudo systemctl restart crm"

echo "==> 4. Secrets (sent to GitHub, not shown)"
gh secret set SSH_PRIVATE_KEY --env "$ENV" --repo "$FULL" < "$KEY_FILE"
printf '%s\n' "$KH" | gh secret set SSH_KNOWN_HOSTS --env "$ENV" --repo "$FULL"

cat <<MSG

Done. Production environment on $FULL:
  variables: DEPLOY_HOST=$IP  DEPLOY_USER=deploy  DEPLOY_PATH=/opt/crm  HEALTH_URL=$HEALTH_URL  RESTART_CMD='sudo systemctl restart crm'
  secrets  : SSH_PRIVATE_KEY, SSH_KNOWN_HOSTS
HTTPS must already be on at $DOMAIN (guide step 6), or the first deploy's health check fails and rolls back.
(Another site name? Run it as:  DOMAIN=other.example.com scripts/github-production.sh ...)
MSG
