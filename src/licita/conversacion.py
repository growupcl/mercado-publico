"""Responde los mensajes que llegan por WhatsApp.

Como el usuario acaba de escribir, la ventana de 24 h está abierta y todas estas
respuestas son mensajes de texto libre, sin costo de WhatsApp.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Calce, Empresa, Licitacion, ahora
from .notificaciones import registrar
from .resumen import texto_detalle, texto_resumen, ultimo_resumen
from .whatsapp import ClienteWhatsApp, ErrorWhatsApp, normalizar_telefono

log = logging.getLogger(__name__)

PALABRAS_BAJA = {"baja", "stop", "detener", "cancelar", "no mas", "no más"}
PALABRAS_ALTA = {"alta", "activar", "reanudar"}

TEXTO_AYUDA = (
    "Soy el asistente de Licita 🤖\n\n"
    "• Cada mañana te envío las licitaciones que calzan con tu negocio.\n"
    "• Responde con el *número* de una licitación del resumen para ver su detalle.\n"
    "• Escribe *TODAS* para volver a ver el último resumen.\n"
    "• Escribe *BAJA* si ya no quieres recibir mensajes."
)


def mensajes_entrantes(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Extrae los mensajes del webhook de Meta (ignora confirmaciones de entrega y lectura)."""
    for entrada in payload.get("entry", []):
        for cambio in entrada.get("changes", []):
            yield from cambio.get("value", {}).get("messages", []) or []


def leer_mensaje(msg: dict[str, Any]) -> tuple[str, str]:
    """Devuelve (tipo, contenido): tipo 'boton' trae el payload; 'texto' trae lo escrito."""
    tipo = msg.get("type")
    if tipo == "button":
        return "boton", msg.get("button", {}).get("payload", "")
    if tipo == "interactive":
        interactivo = msg.get("interactive", {})
        respuesta = interactivo.get("button_reply") or interactivo.get("list_reply") or {}
        return "boton", respuesta.get("id", "")
    if tipo == "text":
        return "texto", msg.get("text", {}).get("body", "")
    return tipo or "desconocido", ""


def responder(session: Session, empresa: Empresa, tipo: str, contenido: str) -> str:
    """Decide la respuesta para un mensaje de una empresa registrada."""
    texto = contenido.strip()
    comando = texto.lower()

    if tipo == "boton" and texto.startswith("DETALLE:"):
        codigo = texto.removeprefix("DETALLE:")
        fila = session.execute(
            select(Calce, Licitacion).join(Licitacion, Licitacion.codigo == Calce.licitacion_codigo)
            .where(Calce.empresa_id == empresa.id, Calce.licitacion_codigo == codigo)
        ).first()
        return texto_detalle(*fila) if fila else "No encontré esa licitación. Escribe *TODAS* para ver tu último resumen."

    if texto == "VER_TODAS" or comando == "todas":
        filas = ultimo_resumen(session, empresa)
        return texto_resumen(empresa, filas) if filas else "Todavía no tienes un resumen. Te enviaré el próximo mañana temprano."

    if comando in PALABRAS_BAJA:
        empresa.whatsapp_activo = False
        return "Listo, no te enviaremos más resúmenes por WhatsApp. Si cambias de opinión escribe *ALTA*."

    if comando in PALABRAS_ALTA:
        empresa.whatsapp_activo = True
        return "¡Bienvenido de vuelta! Mañana temprano recibirás tu resumen de licitaciones."

    if comando.isdigit():
        filas = ultimo_resumen(session, empresa)
        n = int(comando)
        if 1 <= n <= len(filas):
            return texto_detalle(*filas[n - 1])
        return f"Tu último resumen tiene {len(filas)} licitaciones. Responde con un número entre 1 y {len(filas)}." if filas else TEXTO_AYUDA

    return TEXTO_AYUDA


def procesar_webhook(
    session: Session, wa: ClienteWhatsApp, payload: dict[str, Any], *, url_registro: str = "", momento: datetime | None = None
) -> int:
    """Procesa un webhook de Meta y responde cada mensaje. Devuelve cuántos mensajes respondió."""
    momento = momento or ahora()
    respondidos = 0
    for msg in mensajes_entrantes(payload):
        telefono = normalizar_telefono(msg.get("from", ""))
        tipo, contenido = leer_mensaje(msg)
        empresa = session.scalar(select(Empresa).where(Empresa.whatsapp == telefono))
        registrar(session, empresa, telefono, "entrante", tipo, contenido, msg.get("id", ""))
        if empresa is None:
            respuesta = "Hola 👋 Este número no está registrado en Licita."
            if url_registro:
                respuesta += f" Puedes inscribirte en {url_registro}"
        else:
            empresa.ultimo_mensaje_entrante = momento
            respuesta = responder(session, empresa, tipo, contenido)
        try:
            wamid = wa.enviar_texto(telefono, respuesta)
            registrar(session, empresa, telefono, "saliente", "texto", respuesta, wamid)
            respondidos += 1
        except ErrorWhatsApp as e:
            log.warning("No se pudo responder a %s: %s", telefono, e)
        session.commit()
    return respondidos
