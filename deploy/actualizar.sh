#!/usr/bin/env bash
# Publica la última versión: baja los cambios de GitHub y reconstruye la aplicación.
# Antes espera a que terminen las tareas en curso (deploy/tareas.sh): reiniciar el contenedor las cortaría a la mitad.
# Mientras actualiza, las tareas del cron que toque iniciar se omiten (quedan anotadas en logs/tareas.log).
set -euo pipefail
cd "$(dirname "$0")/.."
CANDADO=/tmp/calza-tareas.lock
[ -e "$CANDADO" ] || (umask 000; : > "$CANDADO")
exec 8<"$CANDADO"
if ! flock -x -n 8; then
  corriendo=""
  for f in /tmp/calza-tarea-*.lock; do
    [ -e "$f" ] && ! flock -n "$f" true 2>/dev/null && corriendo+="$(basename "$f" .lock | sed 's/^calza-tarea-//') "
  done
  echo "Hay tareas de Calza corriendo: ${corriendo:-(sin identificar)}"
  echo "Esperando a que terminen (máximo 2 horas; Ctrl+C para cancelar)…"
  flock -x -w 7200 8 || { echo "Siguen corriendo después de 2 horas: no se actualizó."; exit 1; }
fi
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
