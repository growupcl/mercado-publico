# Aviso por correo cuando una tarea falla (y cuando vuelve a funcionar). Se carga con `source` desde tareas.sh y
# respaldo.sh, ya parados en /opt/calza. Mientras siga fallando, repite el aviso cada 6 horas como máximo.
# Uso: avisar TAREA ESTADO ARCHIVO_CON_LA_SALIDA

avisar() {
  local tarea=$1 estado=$2 salida=$3 marca="logs/.falla-$1"
  if [ "$estado" -ne 0 ]; then
    if [ ! -e "$marca" ] || [ -n "$(find "$marca" -mmin +360)" ]; then
      if tail -n 40 "$salida" | docker compose exec -T app licita avisar-falla "$tarea" >> logs/tareas.log 2>&1; then
        touch "$marca"
      else
        echo "!! $(date -Is) no se pudo enviar el aviso de la falla de $tarea" >> logs/tareas.log
      fi
    fi
  elif [ -e "$marca" ]; then
    if docker compose exec -T app licita avisar-falla "$tarea" --resuelta < /dev/null >> logs/tareas.log 2>&1; then
      rm -f "$marca"
    fi
  fi
}
