#!/usr/bin/env bash
# Publica la última versión: baja los cambios de GitHub y reconstruye la aplicación.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
docker compose up -d --build
docker image prune -f >/dev/null
docker compose ps
