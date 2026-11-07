#!/usr/bin/env bash
# One-time, run with sudo on the server: installs a systemd timer that asks Easebuzz every minute whether the open payment links
# have been paid (so payments are noticed even when nobody has the account open).
#   sudo bash /opt/crm/current/server-tools/install-payments-timer.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo." >&2; exit 1; }

cat > /etc/systemd/system/crm-payments.service <<'UNIT'
[Unit]
Description=AlphaRupee CRM: check open Easebuzz payment links
After=network-online.target
ConditionPathExists=/opt/crm/current/backend/manage.py

[Service]
Type=oneshot
User=deploy
Group=deploy
WorkingDirectory=/opt/crm/current/backend
EnvironmentFile=/opt/crm/shared/.env
ExecStart=/opt/crm/current/venv/bin/python manage.py check_payment_links
TimeoutStartSec=120
NoNewPrivileges=true
PrivateTmp=true
UNIT

cat > /etc/systemd/system/crm-payments.timer <<'UNIT'
[Unit]
Description=Check open payment links every minute

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now crm-payments.timer
echo "Installed. See it run with: systemctl list-timers crm-payments.timer"
