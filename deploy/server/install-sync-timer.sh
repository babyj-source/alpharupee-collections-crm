#!/usr/bin/env bash
# One-time, run with sudo on the server (Session Manager): installs a systemd timer that copies accounts and dues
# from the LMS into the CRM every 15 minutes.
#   sudo bash /opt/crm/current/server-tools/install-sync-timer.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo." >&2; exit 1; }

cat > /etc/systemd/system/crm-sync.service <<'UNIT'
[Unit]
Description=AlphaRupee CRM: sync accounts and dues from the LMS (read-only)
After=network-online.target
ConditionPathExists=/opt/crm/current/backend/manage.py

[Service]
Type=oneshot
User=deploy
Group=deploy
WorkingDirectory=/opt/crm/current/backend
EnvironmentFile=/opt/crm/shared/.env
ExecStart=/opt/crm/current/venv/bin/python manage.py sync_lms
TimeoutStartSec=600
NoNewPrivileges=true
PrivateTmp=true
UNIT

cat > /etc/systemd/system/crm-sync.timer <<'UNIT'
[Unit]
Description=Run the CRM LMS sync every 15 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
Persistent=true

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now crm-sync.timer
systemctl list-timers crm-sync.timer --no-pager
