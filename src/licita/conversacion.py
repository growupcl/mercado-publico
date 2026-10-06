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

import anthropic

from .analisis import DocumentoInvalido, LimiteAlcanzado, ServicioAnalisis, detectar_codigo, textos_analisis
from .db import AnalisisBases, Calce, Empresa, Licitacion, MensajeWhatsApp, ahora
from .ia import ErrorIA
from .notificaciones import VENTANA, registrar
from .resumen import texto_detalle, texto_resumen, ultimo_resumen
from .whatsapp import ClienteWhatsApp, ErrorWhatsApp, normalizar_telefono

log = logging.getLogger(__name__)

PALABRAS_BAJA = {"baja", "stop", "detener", "cancelar", "no mas", "no más"}
PALABRAS_ALTA = {"alta", "activar", "reanudar"}

TEXTO_AYUDA = (
    "Soy el asistente de Calza 🤖\n\n"
    "• Cada mañana te envío las licitaciones que calzan con tu negocio.\n"
    "• Responde con el *número* de una licitación del resumen para ver su detalle.\n"
    "• Escribe *TODAS* para volver a ver el último resumen.\n"
    "• Envíame el *PDF de las bases* de una licitación y te digo qué piden, plazos, garantías y cómo se evalúa.\n"
    "• Escribe *BAJA* si ya no quieres recibir mensajes."
)


def mensajes_entrantes(payload: dict[str, Any], phone_number_id: str = "") -> Iterator[dict[str, Any]]:
    """Extrae los mensajes del webhook de Meta (ignora confirmaciones de entrega y lectura).

    Si se indica phone_number_id, solo considera los mensajes dirigidos a ese número: la app de
    Meta puede ser compartida con otros números (por ejemplo, los de Masivo App).
    """
    for entrada in payload.get("entry", []):
        for cambio in entrada.get("changes", []):
            valor = cambio.get("value", {})
            if phone_number_id and valor.get("metadata", {}).get("phone_number_id") != phone_number_id:
                continue
            yield from valor.get("messages", []) or []


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
    if tipo == "document":
        return "documento", msg.get("document", {}).get("filename", "")
    return tipo or "desconocido", ""


INVITACION_BASES = (
    "\n\n📄 ¿Quieres saber qué piden exactamente? Descarga el PDF de las bases desde la ficha y "
    "envíamelo aquí: te digo requisitos, garantías, plazos y cómo se evalúa."
)


def _detalle(empresa: Empresa, calce: Calce, lic: Licitacion, momento: datetime) -> str:
    empresa.licitacion_activa = lic.codigo
    empresa.contexto_actualizado_en = momento
    return texto_detalle(calce, lic) + INVITACION_BASES


def _contexto_vigente(empresa: Empresa, momento: datetime) -> bool:
    return empresa.contexto_actualizado_en is not None and momento - empresa.contexto_actualizado_en < VENTANA


def responder(
    session: Session,
    empresa: Empresa,
    tipo: str,
    contenido: str,
    *,
    analisis: ServicioAnalisis | None = None,
    momento: datetime | None = None,
) -> list[str]:
    """Decide la respuesta (uno o más mensajes) para un mensaje de texto o botón de una empresa registrada."""
    momento = momento or ahora()
    texto = contenido.strip()
    comando = texto.lower()

    if tipo == "boton" and texto.startswith("DETALLE:"):
        codigo = texto.removeprefix("DETALLE:")
        fila = session.execute(
            select(Calce, Licitacion).join(Licitacion, Licitacion.codigo == Calce.licitacion_codigo)
            .where(Calce.empresa_id == empresa.id, Calce.licitacion_codigo == codigo)
        ).first()
        return [_detalle(empresa, *fila, momento)] if fila else ["No encontré esa licitación. Escribe *TODAS* para ver tu último resumen."]

    if texto == "VER_TODAS" or comando == "todas":
        filas = ultimo_resumen(session, empresa)
        return [texto_resumen(empresa, filas) if filas else "Todavía no tienes un resumen. Te enviaré el próximo mañana temprano."]

    if comando in PALABRAS_BAJA:
        empresa.whatsapp_activo = False
        return ["Listo, no te enviaremos más resúmenes por WhatsApp. Si cambias de opinión escribe *ALTA*."]

    if comando in PALABRAS_ALTA:
        empresa.whatsapp_activo = True
        return ["¡Bienvenido de vuelta! Mañana temprano recibirás tu resumen de licitaciones."]

    if comando.isdigit():
        filas = ultimo_resumen(session, empresa)
        n = int(comando)
        if 1 <= n <= len(filas):
            return [_detalle(empresa, *filas[n - 1], momento)]
        return [f"Tu último resumen tiene {len(filas)} licitaciones. Responde con un número entre 1 y {len(filas)}." if filas else TEXTO_AYUDA]

    # Texto libre con un análisis reciente: es una pregunta sobre esas bases.
    if tipo == "texto" and analisis and empresa.analisis_activo_id and _contexto_vigente(empresa, momento):
        analisis_bases = session.get(AnalisisBases, empresa.analisis_activo_id)
        if analisis_bases is not None:
            try:
                return [analisis.preguntar(session, analisis_bases, texto)]
            except (ErrorIA, anthropic.APIError) as e:
                log.warning("No se pudo responder la pregunta sobre bases: %s", e)
                return ["No pude responder esa pregunta ahora. Inténtalo de nuevo en unos minutos."]

    return [TEXTO_AYUDA]


def procesar_documento(
    session: Session,
    wa: ClienteWhatsApp,
    empresa: Empresa,
    msg: dict[str, Any],
    *,
    analisis: ServicioAnalisis | None,
    momento: datetime,
    enviar,
) -> None:
    """Analiza un PDF de bases recibido por WhatsApp. `enviar(texto)` manda un mensaje al usuario."""
    if analisis is None:
        enviar("Por ahora no puedo analizar documentos. Inténtalo más tarde.")
        return
    documento = msg.get("document", {})
    if documento.get("mime_type") != "application/pdf":
        enviar("Solo puedo analizar bases en *PDF*. Descárgalas desde la ficha de la licitación y envíamelas aquí.")
        return
    codigo = detectar_codigo(documento.get("caption"), documento.get("filename"))
    if codigo is None and empresa.licitacion_activa and _contexto_vigente(empresa, momento):
        codigo = empresa.licitacion_activa
    try:
        contenido, _ = wa.descargar_media(documento.get("id", ""))
        existente = analisis.buscar_existente(session, contenido)
        if existente is None:
            enviar("Recibí las bases 📄 Las estoy leyendo; te respondo en uno o dos minutos ⏳")
        resultado, _ = analisis.analizar(
            session, contenido, empresa=empresa, nombre_archivo=documento.get("filename", ""), codigo=codigo, momento=momento,
        )
    except LimiteAlcanzado as e:
        enviar(f"{e} Si necesitas más, puedes cambiarte al plan Pro.")
        return
    except DocumentoInvalido as e:
        enviar(f"No pude analizar ese archivo: {e}")
        return
    except (ErrorWhatsApp, ErrorIA, anthropic.APIError) as e:
        session.rollback()
        log.warning("Falló el análisis de bases: %s", e)
        enviar("No pude analizar las bases en este momento. Inténtalo de nuevo en unos minutos.")
        return
    lic = session.get(Licitacion, resultado.licitacion_codigo) if resultado.licitacion_codigo else None
    for texto in textos_analisis(resultado.resultado, titulo=lic.nombre if lic else ""):
        enviar(texto)


def procesar_webhook(
    session: Session,
    wa: ClienteWhatsApp,
    payload: dict[str, Any],
    *,
    url_registro: str = "",
    phone_number_id: str = "",
    analisis: ServicioAnalisis | None = None,
    momento: datetime | None = None,
) -> int:
    """Procesa un webhook de Meta y responde cada mensaje. Devuelve cuántos mensajes entrantes respondió."""
    momento = momento or ahora()
    respondidos = 0
    for msg in mensajes_entrantes(payload, phone_number_id):
        wamid_entrante = msg.get("id", "")
        # Meta puede reenviar el mismo webhook: no respondemos dos veces el mismo mensaje.
        if wamid_entrante and session.scalar(
            select(MensajeWhatsApp.id).where(MensajeWhatsApp.wamid == wamid_entrante, MensajeWhatsApp.direccion == "entrante")
        ):
            continue
        telefono = normalizar_telefono(msg.get("from", ""))
        tipo, contenido = leer_mensaje(msg)
        empresa = session.scalar(select(Empresa).where(Empresa.whatsapp == telefono))
        registrar(session, empresa, telefono, "entrante", tipo, contenido, wamid_entrante)
        session.commit()

        enviados = 0

        def enviar(texto: str) -> None:
            nonlocal enviados
            try:
                wamid = wa.enviar_texto(telefono, texto)
                registrar(session, empresa, telefono, "saliente", "texto", texto, wamid)
                enviados += 1
            except ErrorWhatsApp as e:
                log.warning("No se pudo responder a %s: %s", telefono, e)

        if empresa is None:
            respuesta = "Hola 👋 Este número no está registrado en Calza."
            if url_registro:
                respuesta += f" Puedes inscribirte en {url_registro}"
            enviar(respuesta)
        else:
            empresa.ultimo_mensaje_entrante = momento
            if tipo == "documento":
                procesar_documento(session, wa, empresa, msg, analisis=analisis, momento=momento, enviar=enviar)
            else:
                for texto in responder(session, empresa, tipo, contenido, analisis=analisis, momento=momento):
                    enviar(texto)
        if enviados:
            respondidos += 1
        session.commit()
    return respondidos
