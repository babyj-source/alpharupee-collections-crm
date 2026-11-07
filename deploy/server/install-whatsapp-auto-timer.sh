#!/usr/bin/env bash
# One-time, run with sudo on the server: installs a systemd timer that runs `manage.py auto_whatsapp` every 30 minutes.
# Nothing is sent unless the Admin has switched a WhatsApp template to "automatic" (Messages screen) and it is inside calling hours;
# each account gets at most one automatic template a day.
#   sudo bash /opt/crm/current/server-tools/install-whatsapp-auto-timer.sh
# Remove with: sudo systemctl disable --now crm-whatsapp-auto.timer
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo." >&2; exit 1; }

cat > /etc/systemd/system/crm-whatsapp-auto.service <<'UNIT'
[Unit]
Description=AlphaRupee CRM: automatic WhatsApp messages
After=network-online.target
ConditionPathExists=/opt/crm/current/backend/manage.py

[Service]
Type=oneshot
User=deploy
Group=deploy
WorkingDirectory=/opt/crm/current/backend
EnvironmentFile=/opt/crm/shared/.env
ExecStart=/opt/crm/current/venv/bin/python manage.py auto_whatsapp
TimeoutStartSec=600
NoNewPrivileges=true
PrivateTmp=true
UNIT

cat > /etc/systemd/system/crm-whatsapp-auto.timer <<'UNIT'
[Unit]
Description=Run the automatic WhatsApp messages every 30 minutes

[Timer]
OnCalendar=*:0/30
Persistent=false

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now crm-whatsapp-auto.timer
echo "Installed. See it with: systemctl list-timers crm-whatsapp-auto.timer"
