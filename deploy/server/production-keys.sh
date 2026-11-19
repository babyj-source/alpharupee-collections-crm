#!/usr/bin/env bash
# Runs ON the PRODUCTION server as the deploy user, once, after the first release is live. Walks through every integration
# key with hidden prompts (nothing is printed, shown, or kept in the shell history) and restarts the app once at the end.
#   bash /opt/crm/current/server-tools/production-keys.sh
# Each part asks first, so you can do it in pieces and rerun it later. It refuses to run on the UAT server, and it refuses an
# LMS address that looks like UAT. The Easebuzz and WhatsApp webhook secrets are made once: say no to them on a rerun.
set -euo pipefail
ENV_FILE="${ENV_FILE:-/opt/crm/shared/.env}"
TOOLS="$(cd "$(dirname "$0")" && pwd)"
export ENV_FILE SKIP_RESTART=1 CRM_HOST="${CRM_HOST:-crm.alpharupee.in}"
[ -f "$ENV_FILE" ] || { echo "$ENV_FILE is missing. Run setup-env.sh first." >&2; exit 1; }
grep -q "^DJANGO_ALLOWED_HOSTS=$CRM_HOST" "$ENV_FILE" || { echo "DJANGO_ALLOWED_HOSTS in $ENV_FILE is not $CRM_HOST: this is not the Production server." >&2; exit 1; }
yes() { local a; read -r -p "$1 [y/N] " a; [ "$a" = y ] || [ "$a" = Y ]; }
say() { printf '\n==> %s\n' "$1"; }
ADDRESSES=""

say "1. Smartflo (dialler)"
if yes "Set the Smartflo token and switch the dialler on?"; then
  bash "$TOOLS/set-smartflo.sh" on
  read -r -p "Outgoing number customers see (digits only, Enter to skip): " CALLER
  [ -z "$CALLER" ] || bash "$TOOLS/set-smartflo.sh" caller "$CALLER"
fi

say "2. Easebuzz (payment links): Production key, salt and sub-merchant id"
if yes "Set the Production Easebuzz key and salt?"; then
  bash "$TOOLS/set-easebuzz.sh" key
  bash "$TOOLS/set-easebuzz.sh" production
  read -r -p "Easebuzz sub-merchant id (Enter or 'none' if you have none): " SUB
  bash "$TOOLS/set-easebuzz.sh" submerchant "${SUB:-none}"
  if yes "Make the Easebuzz webhook secret (first time only)?"; then
    ADDRESSES="$ADDRESSES"$'\n'"$(bash "$TOOLS/set-easebuzz.sh" token | grep '^https://')"
  fi
  bash "$TOOLS/set-easebuzz.sh" on
fi

say "3. LMS read-only database (reads the secret alpharupee/lms/readonlyuser in AWS Secrets Manager)"
if yes "Copy the LMS connection from Secrets Manager now? (the LMS team must have allow-listed this server's IP)"; then
  bash "$TOOLS/set-lms.sh"
fi

say "4. LMS posting: paid payment links are posted to Encore as repayments (REAL MONEY ENTRIES)"
if yes "Set up posting to the Production LMS?"; then
  read -r -p "Production LMS posting address (the full https://... postTransactionListDirect address): " URL
  case "$URL" in https://*) ;; *) echo "The address must start with https://" >&2; exit 1 ;; esac
  case "$URL" in *uat*|*UAT*|*sandbox*|*test*) echo "That address looks like a test one. Production needs the Production LMS address." >&2; exit 1 ;; esac
  bash "$TOOLS/set-lms-posting.sh" url "$URL"
  bash "$TOOLS/set-lms-posting.sh" login
  bash "$TOOLS/set-lms-posting.sh" bank
  echo "Posting writes repayments into the live LMS. Post ONE small real payment first and check it in Encore before agents send links."
  read -r -p "Type POST to switch posting on now (anything else leaves it off): " a
  if [ "$a" = "POST" ]; then bash "$TOOLS/set-lms-posting.sh" on; else echo "Posting left OFF. Switch on later: set-lms-posting.sh on"; fi
fi

say "5. SMS and WhatsApp (MSG91). The MSG91 key was set when the settings file was made."
if yes "Switch collections SMS on?"; then bash "$TOOLS/set-sms.sh" on; fi
if yes "Set up WhatsApp?"; then
  read -r -p "WhatsApp business number (digits, for example 918065075438): " WN
  bash "$TOOLS/set-whatsapp.sh" number "$WN"
  if yes "Make the WhatsApp webhook secret (first time only)?"; then
    ADDRESSES="$ADDRESSES"$'\n'"$(bash "$TOOLS/set-whatsapp.sh" token | grep '^https://')"
  fi
  bash "$TOOLS/set-whatsapp.sh" on
fi

say "6. Restart the app once, with all the new values"
if [ -z "${NO_RESTART:-}" ]; then sudo systemctl restart crm; echo "Restarted."; fi

say "What is set (values are not shown)"
bash "$TOOLS/set-easebuzz.sh" status || true
bash "$TOOLS/set-lms-posting.sh" status || true
if [ -n "$ADDRESSES" ]; then
  printf '\nWebhook addresses to register (keep them secret; they are shown once):%s\n' "$ADDRESSES"
fi
echo
echo "Next: install the timers (sudo bash $TOOLS/install-payments-timer.sh, and the sync and WhatsApp ones when ready)."
