#!/usr/bin/env bash
# Publica la última versión: baja los cambios de GitHub y reconstruye la aplicación.
set -euo pipefail
cd "$(dirname "$0")/.."
antes=$(sha256sum deploy/Caddyfile)
git pull --ff-only
docker compose up -d --build
# El Caddyfile se monta como archivo: si cambió, Caddy solo lo toma al reiniciar.
if [ "$antes" != "$(sha256sum deploy/Caddyfile)" ]; then
  echo "Cambió la configuración de Caddy: reiniciando."
  docker compose restart caddy
fi
docker image prune -f >/dev/null
docker compose ps
