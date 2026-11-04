#!/bin/bash
# First-boot setup for the application server (Ubuntu 24.04). Filled in by create-env.sh.
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

DOMAIN="uat-crm.alpharupee.in"
DEPLOY_PUBKEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPUSuPjwAc3FsnVLrMxnhhdZUfuc+6/7am2CeyZh25lo crm-deploy-uat"

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
echo "ZGVwbG95IEFMTD0ocm9vdCkgTk9QQVNTV0Q6IC91c3IvYmluL3N5c3RlbWN0bCByZXN0YXJ0IGNybSwgL3Vzci9iaW4vc3lzdGVtY3RsIHJlbG9hZCBuZ2lueAo=" | base64 -d > /etc/sudoers.d/crm-deploy
chmod 440 /etc/sudoers.d/crm-deploy
visudo -cf /etc/sudoers.d/crm-deploy

echo "W1VuaXRdCkRlc2NyaXB0aW9uPUFscGhhUnVwZWUgQ29sbGVjdGlvbnMgQ1JNIChndW5pY29ybikKQWZ0ZXI9bmV0d29yay50YXJnZXQKIyBEb2VzIG5vdGhpbmcgdW50aWwgdGhlIERqYW5nbyBiYWNrZW5kIGhhcyBiZWVuIGRlcGxveWVkCkNvbmRpdGlvblBhdGhFeGlzdHM9L29wdC9jcm0vY3VycmVudC9iYWNrZW5kL21hbmFnZS5weQoKW1NlcnZpY2VdClVzZXI9ZGVwbG95Ckdyb3VwPWRlcGxveQpXb3JraW5nRGlyZWN0b3J5PS9vcHQvY3JtL2N1cnJlbnQvYmFja2VuZApFbnZpcm9ubWVudEZpbGU9L29wdC9jcm0vc2hhcmVkLy5lbnYKRXhlY1N0YXJ0PS9vcHQvY3JtL2N1cnJlbnQvdmVudi9iaW4vZ3VuaWNvcm4gY29uZmlnLndzZ2k6YXBwbGljYXRpb24gLS1iaW5kIDEyNy4wLjAuMTo4MDAwIC0td29ya2VycyAzIC0tdGltZW91dCA2MCAtLWFjY2Vzcy1sb2dmaWxlIC0KUmVzdGFydD1vbi1mYWlsdXJlClJlc3RhcnRTZWM9MwpOb05ld1ByaXZpbGVnZXM9dHJ1ZQpQcml2YXRlVG1wPXRydWUKCltJbnN0YWxsXQpXYW50ZWRCeT1tdWx0aS11c2VyLnRhcmdldAo=" | base64 -d > /etc/systemd/system/crm.service
systemctl daemon-reload
systemctl enable crm

MARK="__DOMAIN"; MARK="${MARK}__"   # built in two parts so create-env.sh does not rewrite it
echo "c2VydmVyIHsKICAgIGxpc3RlbiA4MDsKICAgIGxpc3RlbiBbOjpdOjgwOwogICAgc2VydmVyX25hbWUgX19ET01BSU5fXzsKCiAgICByb290IC9vcHQvY3JtL2N1cnJlbnQvd2ViOwogICAgaW5kZXggaW5kZXguaHRtbDsKCiAgICBjbGllbnRfbWF4X2JvZHlfc2l6ZSAyMG07CgogICAgIyBVc2VkIGJ5IGRlcGxveS5zaCBhZnRlciBlYWNoIHJlbGVhc2UKICAgIGxvY2F0aW9uID0gL2hlYWx0aHogewogICAgICAgIGFjY2Vzc19sb2cgb2ZmOwogICAgICAgIGRlZmF1bHRfdHlwZSB0ZXh0L3BsYWluOwogICAgICAgIHJldHVybiAyMDAgIm9rXG4iOwogICAgfQoKICAgICMgRGphbmdvIChhcHBlYXJzIG9uY2UgdGhlIGJhY2tlbmQgaXMgZGVwbG95ZWQpCiAgICBsb2NhdGlvbiAvYXBpLyB7CiAgICAgICAgcHJveHlfcGFzcyBodHRwOi8vMTI3LjAuMC4xOjgwMDA7CiAgICAgICAgcHJveHlfc2V0X2hlYWRlciBIb3N0ICRob3N0OwogICAgICAgIHByb3h5X3NldF9oZWFkZXIgWC1SZWFsLUlQICRyZW1vdGVfYWRkcjsKICAgICAgICBwcm94eV9zZXRfaGVhZGVyIFgtRm9yd2FyZGVkLUZvciAkcHJveHlfYWRkX3hfZm9yd2FyZGVkX2ZvcjsKICAgICAgICBwcm94eV9zZXRfaGVhZGVyIFgtRm9yd2FyZGVkLVByb3RvICRzY2hlbWU7CiAgICAgICAgcHJveHlfcmVhZF90aW1lb3V0IDYwczsKICAgIH0KICAgIGxvY2F0aW9uIC9hZG1pbi8gewogICAgICAgIHByb3h5X3Bhc3MgaHR0cDovLzEyNy4wLjAuMTo4MDAwOwogICAgICAgIHByb3h5X3NldF9oZWFkZXIgSG9zdCAkaG9zdDsKICAgICAgICBwcm94eV9zZXRfaGVhZGVyIFgtRm9yd2FyZGVkLVByb3RvICRzY2hlbWU7CiAgICB9CiAgICBsb2NhdGlvbiAvc3RhdGljLyB7CiAgICAgICAgYWxpYXMgL29wdC9jcm0vY3VycmVudC9iYWNrZW5kL3N0YXRpY2ZpbGVzLzsKICAgICAgICBleHBpcmVzIDdkOwogICAgfQoKICAgICMgUmVhY3QgLyBwcm90b3R5cGUKICAgIGxvY2F0aW9uIC8gewogICAgICAgIHRyeV9maWxlcyAkdXJpICR1cmkvIC9pbmRleC5odG1sOwogICAgfQoKICAgIGFkZF9oZWFkZXIgWC1Db250ZW50LVR5cGUtT3B0aW9ucyBub3NuaWZmIGFsd2F5czsKICAgIGFkZF9oZWFkZXIgWC1GcmFtZS1PcHRpb25zIFNBTUVPUklHSU4gYWx3YXlzOwogICAgYWRkX2hlYWRlciBSZWZlcnJlci1Qb2xpY3kgc3RyaWN0LW9yaWdpbi13aGVuLWNyb3NzLW9yaWdpbiBhbHdheXM7CiAgICBzZXJ2ZXJfdG9rZW5zIG9mZjsKfQo=" | base64 -d | sed "s/${MARK}/$DOMAIN/g" > /etc/nginx/sites-available/crm
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
