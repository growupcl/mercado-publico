#!/usr/bin/env bash
# Ejecuta un comando de Calza dentro del contenedor y lo registra en logs/tareas.log.
# Evita que la misma tarea corra dos veces en paralelo, y no parte mientras deploy/actualizar.sh reinicia la app.
# Uso: deploy/tareas.sh ciclo --max-detalles 400
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
exec 9>"/tmp/calza-tarea-$1.lock"
if ! flock -n 9; then
  echo "$(date -Is) [$1] sigue corriendo la ejecución anterior; se omite" >> logs/tareas.log
  exit 0
fi
# Candado compartido con actualizar.sh: muchas tareas a la vez, pero ninguna durante una actualización.
CANDADO=/tmp/calza-tareas.lock
[ -e "$CANDADO" ] || (umask 000; : > "$CANDADO")
exec 8<"$CANDADO"
if ! flock -s -n 8; then
  echo "$(date -Is) [$1] se está actualizando la aplicación; se omite" >> logs/tareas.log
  exit 0
fi
{
  echo "== $(date -Is) licita $*"
  docker compose exec -T app licita "$@" || echo "!! $(date -Is) licita $1 terminó con error"
} >> logs/tareas.log 2>&1
