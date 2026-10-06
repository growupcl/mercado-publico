#!/usr/bin/env bash
# Prepara un VPS nuevo con Ubuntu 24.04 para Calza. Ejecutar UNA vez como root:
#   bash preparar-servidor.sh
set -euo pipefail

echo "==> Zona horaria de Chile"
timedatectl set-timezone America/Santiago

echo "==> Actualizaciones y herramientas"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get -y upgrade
DEBIAN_FRONTEND=noninteractive apt-get install -y git ufw fail2ban unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades   # parches de seguridad automáticos

echo "==> Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi

echo "==> Cortafuegos: solo SSH, HTTP y HTTPS"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

echo "==> Usuario calza"
id calza >/dev/null 2>&1 || adduser --disabled-password --gecos "" calza
usermod -aG docker calza
mkdir -p /opt/calza /var/backups/calza
chown -R calza:calza /opt/calza /var/backups/calza
mkdir -p /home/calza/.ssh
# Acceso SSH y llave de despliegue de GitHub (para que calza pueda hacer git pull).
for f in authorized_keys github_calza github_calza.pub config known_hosts; do
  [ -f "/root/.ssh/$f" ] && cp "/root/.ssh/$f" /home/calza/.ssh/
done
chown -R calza:calza /home/calza/.ssh && chmod 700 /home/calza/.ssh && chmod 600 /home/calza/.ssh/* 2>/dev/null || true

echo "==> Listo. Sigue con el paso 4 de docs/despliegue.md como usuario calza:  su - calza"
