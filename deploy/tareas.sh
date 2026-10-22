#!/usr/bin/env bash
# Ejecuta un comando de Calza dentro del contenedor y lo registra en logs/tareas.log.
# Evita que la misma tarea corra dos veces en paralelo, y no parte mientras deploy/actualizar.sh reinicia la app.
# Si la tarea falla, avisa por correo (deploy/avisar.sh).
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
salida=$(mktemp)
trap 'rm -f "$salida"' EXIT
estado=0
docker compose exec -T app licita "$@" > "$salida" 2>&1 || estado=$?
{
  echo "== $(date -Is) licita $*"
  cat "$salida"
  [ "$estado" -eq 0 ] || echo "!! $(date -Is) licita $1 terminó con error"
} >> logs/tareas.log
# Si falló, aviso por correo al equipo (ALERTAS_EMAIL); si venía fallando y ahora anduvo, aviso de que se resolvió.
source deploy/avisar.sh
avisar "$1" "$estado" "$salida"
