"""Avisos de operación: correo al equipo cuando una tarea programada falla o vuelve a funcionar.

deploy/tareas.sh llama a `licita avisar-falla TAREA` con la salida de la ejecución que falló (y `--resuelta` cuando
vuelve a funcionar). Sin esto, una falla solo queda en logs/tareas.log y nadie se entera hasta que un cliente reclama.
"""

from __future__ import annotations

from datetime import datetime

from .db import hora_chile

# Qué deja de pasar cuando falla cada tarea, en palabras del negocio.
IMPACTO = {
    "whatsapp-enviar": "Las empresas no recibieron (o recibieron solo en parte) el resumen diario de hoy.",
    "ciclo": "No se están trayendo, clasificando o calzando las licitaciones nuevas: el próximo resumen puede salir vacío.",
    "compra-agil": "No se están revisando las Compras Ágiles nuevas ni enviando las alertas urgentes del plan Pro.",
    "compra-agil-precios": "No se guardaron las cotizaciones de las Compras Ágiles cerradas (precios de referencia).",
    "sync": "No se sincronizaron las órdenes de compra del día (precios de referencia).",
    "suscripciones": "No se revisaron los vencimientos ni los cobros rechazados.",
    "avisos-prueba": "No salieron los avisos de fin de prueba ni de primer cobro.",
    "historico-oc": "No se cargó el histórico de órdenes de compra.",
    "indexnow": "No se avisó a los buscadores de las licitaciones nuevas (no afecta a los clientes).",
    "limpiar": "No se borraron las Compras Ágiles antiguas (no afecta a los clientes).",
    "respaldo": "No se hizo el respaldo de la base de datos de hoy.",
}
LARGO_DETALLE = 4000


def correo_falla(tarea: str, detalle: str, *, resuelta: bool = False, momento: datetime | None = None) -> tuple[str, str]:
    """(asunto, texto) del aviso al equipo."""
    cuando = hora_chile(momento).strftime("%d-%m-%Y %H:%M")
    if resuelta:
        return (f"✅ Calza: la tarea {tarea} volvió a funcionar",
                f"La tarea {tarea} terminó bien a las {cuando} (hora de Chile), después de haber fallado.\n\n"
                "No hay que hacer nada.")
    detalle = detalle.strip()
    if len(detalle) > LARGO_DETALLE:
        detalle = "…\n" + detalle[-LARGO_DETALLE:]
    impacto = IMPACTO.get(tarea, "")
    texto = (f"La tarea {tarea} falló a las {cuando} (hora de Chile).\n\n"
             + (f"Qué significa: {impacto}\n\n" if impacto else "")
             + "Últimas líneas de la ejecución:\n\n" + (detalle or "(sin salida)") + "\n\n"
             "Para revisar en el servidor (como usuario calza, en /opt/calza):\n"
             "  tail -n 80 logs/tareas.log\n"
             "  docker compose ps\n"
             "  docker compose logs --tail 100 app\n\n"
             "Si sigue fallando te aviso de nuevo cada 6 horas, y otra vez cuando vuelva a funcionar.")
    return f"⚠️ Calza: falló la tarea {tarea}", texto
