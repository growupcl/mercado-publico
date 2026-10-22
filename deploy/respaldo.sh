#!/usr/bin/env bash
# Respaldo diario de la base de datos. Conserva los últimos 14 días.
set -euo pipefail
cd "$(dirname "$0")/.."
destino=/var/backups/calza
mkdir -p logs
source deploy/avisar.sh
falla() {
  echo "$(date -Is) el respaldo falló"
  local detalle
  detalle=$(mktemp)
  echo "pg_dump o gzip terminó con error. Revisa logs/respaldos.log y que el contenedor db esté arriba." > "$detalle"
  avisar respaldo 1 "$detalle"
  rm -f "$detalle"
}
trap falla ERR
umask 077  # los respaldos tienen datos de clientes: solo los lee el usuario calza
mkdir -p "$destino"
chmod 700 "$destino"
archivo="$destino/calza-$(date +%F).sql.gz"
docker compose exec -T db pg_dump -U calza -d calza | gzip > "$archivo.tmp"
mv "$archivo.tmp" "$archivo"
find "$destino" -name 'calza-*.sql.gz' -mtime +14 -delete
echo "$(date -Is) respaldo listo: $archivo ($(du -h "$archivo" | cut -f1))"
avisar respaldo 0 /dev/null
