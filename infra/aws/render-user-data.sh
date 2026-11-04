#!/usr/bin/env bash
# Builds the first-boot script for the application server, for pasting into the EC2 console.
# Runs on your own computer (Mac or Linux); needs no AWS access.
#
#   bash infra/aws/render-user-data.sh uat ~/crm-keys/crm_deploy_uat.pub
#
# Writes ./crm-<env>-user-data.sh   (contains only the PUBLIC key, no secrets)
set -euo pipefail
ENV_NAME="${1:?usage: render-user-data.sh uat|production PATH_TO_PUBLIC_KEY}"
PUBKEY_PATH="${2:?usage: render-user-data.sh uat|production PATH_TO_PUBLIC_KEY}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
# shellcheck disable=SC1090
source "$HERE/env/$ENV_NAME.env"

[ -f "$PUBKEY_PATH" ] || { echo "Public key not found: $PUBKEY_PATH"; exit 1; }
DEPLOY_PUBKEY="$(cat "$PUBKEY_PATH")"
case "$DEPLOY_PUBKEY" in ssh-ed25519\ *|ssh-rsa\ *) ;; *) echo "That is not a public key. Use the file ending in .pub"; exit 1;; esac
case "$DEPLOY_PUBKEY" in *PRIVATE*) echo "That looks like a PRIVATE key. Stop."; exit 1;; esac

b64() { base64 < "$1" | tr -d '\n'; }
OUT="./crm-$ENV_NAME-user-data.sh"
sed -e "s|__DOMAIN__|$DOMAIN|g" \
    -e "s|__DEPLOY_PUBKEY__|$DEPLOY_PUBKEY|g" \
    -e "s|__SUDOERS_B64__|$(b64 "$ROOT/deploy/server/sudoers-deploy")|" \
    -e "s|__UNIT_B64__|$(b64 "$ROOT/deploy/server/crm.service")|" \
    -e "s|__NGINX_B64__|$(b64 "$ROOT/deploy/server/nginx-crm.conf")|" \
    "$HERE/user-data.sh" > "$OUT"

SIZE="$(wc -c < "$OUT" | tr -d ' ')"
[ "$SIZE" -lt 16000 ] || { echo "Result is $SIZE bytes, over the EC2 16 KB limit"; exit 1; }
bash -n "$OUT"
echo "Wrote $OUT ($SIZE bytes). Open it, select all, copy, and paste it into the User data box in the EC2 console."
