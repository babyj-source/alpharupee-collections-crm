#!/bin/bash
# First-boot setup for the application server (Ubuntu 24.04). Filled in by create-env.sh.
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

DOMAIN="__DOMAIN__"
DEPLOY_PUBKEY="__DEPLOY_PUBKEY__"

apt-get update -y
apt-get install -y nginx python3-venv python3-dev build-essential libpq-dev postgresql-client \
  certbot python3-certbot-nginx fail2ban jq curl unzip unattended-upgrades

# AWS CLI v2 (used by deploy/server/setup-env.sh to read the database secret)
if ! command -v aws >/dev/null; then
  cd /tmp
  curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o awscliv2.zip
  unzip -q awscliv2.zip && ./aws/install
fi

# Deploy user: key-only, no shell tricks, owns only /opt/crm
id deploy >/dev/null 2>&1 || useradd -m -s /bin/bash deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
echo "no-agent-forwarding,no-port-forwarding,no-X11-forwarding $DEPLOY_PUBKEY" > /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys
install -d -m 755 -o deploy -g deploy /opt/crm /opt/crm/releases
install -d -m 750 -o deploy -g deploy /opt/crm/shared

# SSH hardening
cat > /etc/ssh/sshd_config.d/99-crm-hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
MaxAuthTries 3
X11Forwarding no
EOF
sshd -t && systemctl reload ssh

# Files from the repo (embedded by create-env.sh)
echo "__SUDOERS_B64__" | base64 -d > /etc/sudoers.d/crm-deploy
chmod 440 /etc/sudoers.d/crm-deploy
visudo -cf /etc/sudoers.d/crm-deploy

echo "__UNIT_B64__" | base64 -d > /etc/systemd/system/crm.service
systemctl daemon-reload
systemctl enable crm

MARK="__DOMAIN"; MARK="${MARK}__"   # built in two parts so create-env.sh does not rewrite it
echo "__NGINX_B64__" | base64 -d | sed "s/${MARK}/$DOMAIN/g" > /etc/nginx/sites-available/crm
ln -sf /etc/nginx/sites-available/crm /etc/nginx/sites-enabled/crm
rm -f /etc/nginx/sites-enabled/default

# Placeholder page until the first deploy
install -d -o deploy -g deploy /opt/crm/bootstrap/web
echo "<h1>AlphaRupee CRM</h1><p>Server ready. Waiting for first deploy.</p>" > /opt/crm/bootstrap/web/index.html
chown -R deploy:deploy /opt/crm/bootstrap
[ -e /opt/crm/current ] || ln -s /opt/crm/bootstrap /opt/crm/current
chown -h deploy:deploy /opt/crm/current

nginx -t
systemctl enable --now nginx fail2ban
systemctl reload nginx
echo "crm first-boot setup finished" > /var/log/crm-bootstrap.done
