"""Avisos al cliente sobre su cuenta: por ahora, cobros rechazados de la suscripción.

Mercado Pago reintenta los cobros rechazados por su cuenta; nosotros le avisamos al cliente para que
revise su tarjeta antes de que termine el período (más los días de gracia).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Empresa, MandatoPago, Pago, Suscripcion, ahora
from .notificaciones import registrar, ventana_abierta
from .planes import PLANES
from .plantillas_whatsapp import COBRO_RECHAZADO
from .suscripciones import DIAS_GRACIA_RENOVACION, generar_token
from .whatsapp import ClienteWhatsApp, ErrorWhatsApp

log = logging.getLogger(__name__)

# Entre un aviso y otro del mismo cobro recurrente (Mercado Pago reintenta varias veces).
INTERVALO_AVISOS = timedelta(days=3)

MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre")


def fecha_larga(fecha: datetime) -> str:
    return f"{fecha.day} de {MESES[fecha.month - 1]} de {fecha.year}"


def nombre_plan(mandato: MandatoPago) -> str:
    return f"{PLANES[mandato.plan].nombre} {mandato.periodicidad}"


def _hay_que_avisar(session: Session, pago: Pago, momento: datetime) -> tuple[bool, str]:
    mandato = session.get(MandatoPago, pago.mandato_id)
    suscripcion = session.scalar(select(Suscripcion).where(Suscripcion.empresa_id == pago.empresa_id))
    empresa = session.get(Empresa, pago.empresa_id)
    if suscripcion is None or suscripcion.mandato_activo_id != mandato.id:
        return False, "la suscripción ya no está activa"
    aprobado_despues = session.scalar(select(Pago.id).where(
        Pago.mandato_id == mandato.id, Pago.estado == "pagado", Pago.creado_en >= pago.creado_en,
    ))
    if aprobado_despues:
        return False, "un cobro posterior fue aprobado"
    avisado_hace_poco = session.scalar(select(Pago.id).where(
        Pago.mandato_id == mandato.id, Pago.aviso_enviado_en.is_not(None), Pago.aviso_enviado_en > momento - INTERVALO_AVISOS,
    ))
    if avisado_hace_poco:
        return False, "ya se avisó hace poco"
    if not empresa.whatsapp or not empresa.whatsapp_activo:
        return False, "sin WhatsApp activo"
    return True, ""


def avisar_cobros_rechazados(
    session: Session,
    wa: ClienteWhatsApp,
    *,
    url_publica: str,
    idioma: str = "es",
    momento: datetime | None = None,
) -> int:
    """Avisa por WhatsApp los cobros rechazados pendientes. Devuelve cuántos avisos envió."""
    momento = momento or ahora()
    enviados = 0
    pendientes = session.scalars(
        select(Pago).where(Pago.estado == "rechazado", Pago.aviso_enviado_en.is_(None)).order_by(Pago.creado_en)
    ).all()
    for pago in pendientes:
        avisar, motivo = _hay_que_avisar(session, pago, momento)
        if not avisar:
            log.info("Cobro rechazado %s sin aviso: %s", pago.mp_cobro_id, motivo)
            pago.aviso_enviado_en = momento  # queda resuelto: no hay que volver a evaluarlo
            session.commit()
            continue
        empresa = session.get(Empresa, pago.empresa_id)
        mandato = session.get(MandatoPago, pago.mandato_id)
        suscripcion = session.scalar(select(Suscripcion).where(Suscripcion.empresa_id == empresa.id))
        limite = fecha_larga(suscripcion.vigente_hasta + timedelta(days=DIAS_GRACIA_RENOVACION))
        try:
            if ventana_abierta(empresa, momento):
                token = generar_token(empresa)
                texto = (f"Hola {empresa.nombre}, no pudimos cobrar tu plan {nombre_plan(mandato)} de Calza con la tarjeta "
                         f"registrada en Mercado Pago. Para seguir recibiendo tus licitaciones, revisa tu medio de pago "
                         f"antes del {limite}.\n\nEntra a tu cuenta aquí (enlace privado):\n{url_publica}/cuenta/{token}")
                wamid = wa.enviar_texto(empresa.whatsapp, texto)
                registrar(session, empresa, empresa.whatsapp, "saliente", "texto", texto.split("\n\n")[0], wamid)
            else:
                parametros = [empresa.nombre, nombre_plan(mandato), limite]
                wamid = wa.enviar_plantilla(empresa.whatsapp, COBRO_RECHAZADO.nombre, idioma=idioma,
                                            parametros=parametros, payloads_botones=["CUENTA"])
                registrar(session, empresa, empresa.whatsapp, "saliente", "plantilla", " | ".join(parametros), wamid)
        except ErrorWhatsApp as e:
            session.rollback()
            log.warning("No se pudo avisar el cobro rechazado a la empresa #%s: %s", empresa.id, e)
            continue  # se reintenta en la próxima revisión
        pago.aviso_enviado_en = momento
        session.commit()
        enviados += 1
    return enviados
