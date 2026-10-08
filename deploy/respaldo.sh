#!/usr/bin/env bash
# Respaldo diario de la base de datos. Conserva los últimos 14 días.
set -euo pipefail
cd "$(dirname "$0")/.."
destino=/var/backups/calza
mkdir -p "$destino"
archivo="$destino/calza-$(date +%F).sql.gz"
docker compose exec -T db pg_dump -U calza -d calza | gzip > "$archivo.tmp"
mv "$archivo.tmp" "$archivo"
find "$destino" -name 'calza-*.sql.gz' -mtime +14 -delete
echo "$(date -Is) respaldo listo: $archivo ($(du -h "$archivo" | cut -f1))"
