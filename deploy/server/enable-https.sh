#!/usr/bin/env bash
# Runs ON the application server with sudo (Session Manager user), after the DNS record points here.
#   sudo bash enable-https.sh uat-crm.alpharupee.in you@alpharupee.in
set -euo pipefail
DOMAIN="${1:?usage: enable-https.sh DOMAIN EMAIL}"
EMAIL="${2:?usage: enable-https.sh DOMAIN EMAIL}"
getent hosts "$DOMAIN" >/dev/null || { echo "$DOMAIN does not resolve yet. Create the DNS A record first and wait a few minutes."; exit 1; }
certbot --nginx -d "$DOMAIN" -m "$EMAIL" --agree-tos --non-interactive --redirect
systemctl list-timers | grep -i certbot || true
echo "HTTPS is on. Certificates renew automatically."
