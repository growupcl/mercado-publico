"""Avisos al cliente sobre su cuenta: por ahora, cobros rechazados de la suscripción.

Mercado Pago reintenta los cobros rechazados por su cuenta; nosotros le avisamos al cliente para que
revise su tarjeta antes de que termine el período (más los días de gracia).

Se avisa por dos canales independientes: WhatsApp (si lo tiene activo) y correo (siempre que haya correo, porque es
un aviso de cuenta, no publicidad). Cada canal registra su propio estado en el pago: si uno falla, se reintenta solo
ese en la próxima revisión, sin repetir el otro.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .correo import ClienteCorreo, ErrorCorreo
from .db import Empresa, MandatoPago, Pago, Suscripcion, ahora
from .notificaciones import registrar, ventana_abierta
from .planes import PLANES
from .plantillas_whatsapp import COBRO_RECHAZADO
from .suscripciones import DIAS_GRACIA_RENOVACION, generar_token
from .whatsapp import ClienteWhatsApp, ErrorWhatsApp

log = logging.getLogger(__name__)

# Entre un aviso y otro del mismo cobro recurrente (Mercado Pago reintenta varias veces).
INTERVALO_AVISOS = timedelta(days=3)
# Un aviso que no se pudo enviar en este plazo ya no sirve (el cobro se reintentó o la cuenta venció).
VIGENCIA_AVISO = timedelta(days=7)

MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre")


def fecha_larga(fecha: datetime) -> str:
    return f"{fecha.day} de {MESES[fecha.month - 1]} de {fecha.year}"


def nombre_plan(mandato: MandatoPago) -> str:
    return f"{PLANES[mandato.plan].nombre} {mandato.periodicidad}"


@dataclass
class ResultadoAvisos:
    whatsapp: int = 0
    correos: int = 0

    @property
    def total(self) -> int:
        return self.whatsapp + self.correos


def _sigue_vigente(session: Session, pago: Pago, mandato: MandatoPago) -> tuple[bool, str]:
    suscripcion = session.scalar(select(Suscripcion).where(Suscripcion.empresa_id == pago.empresa_id))
    if suscripcion is None or suscripcion.mandato_activo_id != mandato.id:
        return False, "la suscripción ya no está activa"
    aprobado_despues = session.scalar(select(Pago.id).where(
        Pago.mandato_id == mandato.id, Pago.estado == "pagado", Pago.creado_en >= pago.creado_en,
    ))
    if aprobado_despues:
        return False, "un cobro posterior fue aprobado"
    return True, ""


def _avisado_hace_poco(session: Session, mandato: MandatoPago, columna, momento: datetime) -> bool:
    """Si otro cobro del mismo mandato ya se avisó por ese canal hace poco (columna = Pago.aviso_*_en)."""
    return session.scalar(select(Pago.id).where(
        Pago.mandato_id == mandato.id, columna.is_not(None), columna > momento - INTERVALO_AVISOS,
    )) is not None


def correo_cobro_rechazado(empresa: Empresa, plan: str, limite: str, enlace: str) -> tuple[str, str, str]:
    """(asunto, texto, html) del aviso por correo."""
    asunto = f"No pudimos cobrar tu plan {plan} de Calza"
    texto = (
        f"Hola {empresa.nombre}:\n\n"
        f"No pudimos cobrar tu plan {plan} de Calza con la tarjeta registrada en Mercado Pago. "
        "Mercado Pago volverá a intentarlo en los próximos días.\n\n"
        f"Para seguir recibiendo tus licitaciones, revisa que tu tarjeta esté vigente y con cupo antes del {limite}. "
        "Puedes cambiarla en tu cuenta de Mercado Pago (sección Suscripciones) o suscribirte con otra tarjeta desde "
        f"tu cuenta en Calza:\n\n{enlace}\n\n"
        "Este enlace es privado: funciona como una contraseña, no lo compartas.\n"
        "Si ya lo solucionaste, puedes ignorar este correo.\n\n"
        "Saludos,\nEquipo Calza · hola@calza.cl"
    )
    e = html.escape
    cuerpo_html = f"""<!doctype html>
<html lang="es"><body style="margin:0;padding:24px;background:#f4f6f8;font-family:Arial,Helvetica,sans-serif;color:#1f2933">
<div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:8px;padding:28px">
<p style="font-size:20px;font-weight:bold;margin:0 0 16px">Calza</p>
<p>Hola {e(empresa.nombre)}:</p>
<p>No pudimos cobrar tu plan <strong>{e(plan)}</strong> de Calza con la tarjeta registrada en Mercado Pago.
Mercado Pago volverá a intentarlo en los próximos días.</p>
<p>Para seguir recibiendo tus licitaciones, revisa que tu tarjeta esté vigente y con cupo
<strong>antes del {e(limite)}</strong>. Puedes cambiarla en tu cuenta de Mercado Pago (sección Suscripciones)
o suscribirte con otra tarjeta desde tu cuenta en Calza.</p>
<p style="text-align:center;margin:28px 0"><a href="{e(enlace)}" style="background:#0b6e4f;color:#ffffff;text-decoration:none;padding:12px 22px;border-radius:6px;font-weight:bold">Ir a mi cuenta</a></p>
<p style="font-size:13px;color:#52606d">Este enlace es privado: funciona como una contraseña, no lo compartas.
Si ya lo solucionaste, puedes ignorar este correo.</p>
<p style="font-size:13px;color:#52606d">Equipo Calza · hola@calza.cl</p>
</div></body></html>"""
    return asunto, texto, cuerpo_html


def avisar_cobros_rechazados(
    session: Session,
    wa: ClienteWhatsApp | None,
    *,
    url_publica: str,
    correo: ClienteCorreo | None = None,
    idioma: str = "es",
    momento: datetime | None = None,
) -> ResultadoAvisos:
    """Avisa por WhatsApp y por correo los cobros rechazados pendientes."""
    momento = momento or ahora()
    resultado = ResultadoAvisos()
    pendientes = session.scalars(
        select(Pago).where(Pago.estado == "rechazado", Pago.aviso_enviado_en.is_(None) | Pago.aviso_correo_en.is_(None))
        .order_by(Pago.creado_en)
    ).all()
    for pago in pendientes:
        mandato = session.get(MandatoPago, pago.mandato_id)
        empresa = session.get(Empresa, pago.empresa_id)
        vigente, motivo = _sigue_vigente(session, pago, mandato)
        if not vigente or momento - pago.creado_en > VIGENCIA_AVISO:
            log.info("Cobro rechazado %s sin aviso: %s", pago.mp_cobro_id, motivo or "ya pasó el plazo para avisar")
            pago.aviso_enviado_en = pago.aviso_enviado_en or momento  # resuelto: no hay que volver a evaluarlo
            pago.aviso_correo_en = pago.aviso_correo_en or momento
            session.commit()
            continue

        suscripcion = session.scalar(select(Suscripcion).where(Suscripcion.empresa_id == empresa.id))
        limite = fecha_larga(suscripcion.vigente_hasta + timedelta(days=DIAS_GRACIA_RENOVACION))
        token: list[str] = []  # el enlace se genera una vez y sirve para ambos canales

        def enlace() -> str:
            if not token:
                token.append(generar_token(empresa))
            return f"{url_publica}/cuenta/{token[0]}"

        if pago.aviso_enviado_en is None:
            if wa is None or not empresa.whatsapp or not empresa.whatsapp_activo or _avisado_hace_poco(
                session, mandato, Pago.aviso_enviado_en, momento
            ):
                pago.aviso_enviado_en = momento  # no corresponde por este canal
            elif _avisar_whatsapp(session, wa, empresa, mandato, limite, enlace, idioma, momento):
                pago.aviso_enviado_en = momento
                resultado.whatsapp += 1

        if pago.aviso_correo_en is None:
            if correo is None or not empresa.email or _avisado_hace_poco(session, mandato, Pago.aviso_correo_en, momento):
                pago.aviso_correo_en = momento
            else:
                asunto, texto, cuerpo_html = correo_cobro_rechazado(empresa, nombre_plan(mandato), limite, enlace())
                try:
                    correo.enviar(empresa.email, asunto, texto, cuerpo_html)
                    pago.aviso_correo_en = momento
                    resultado.correos += 1
                except ErrorCorreo as e:
                    log.warning("No se pudo avisar por correo el cobro rechazado a la empresa #%s: %s", empresa.id, e)
        session.commit()
    return resultado


def _avisar_whatsapp(session, wa, empresa, mandato, limite, enlace, idioma, momento) -> bool:
    try:
        if ventana_abierta(empresa, momento):
            texto = (f"Hola {empresa.nombre}, no pudimos cobrar tu plan {nombre_plan(mandato)} de Calza con la tarjeta "
                     f"registrada en Mercado Pago. Para seguir recibiendo tus licitaciones, revisa tu medio de pago "
                     f"antes del {limite}.\n\nEntra a tu cuenta aquí (enlace privado):\n{enlace()}")
            wamid = wa.enviar_texto(empresa.whatsapp, texto)
            registrar(session, empresa, empresa.whatsapp, "saliente", "texto", texto.split("\n\n")[0], wamid)
        else:
            parametros = [empresa.nombre, nombre_plan(mandato), limite]
            wamid = wa.enviar_plantilla(empresa.whatsapp, COBRO_RECHAZADO.nombre, idioma=idioma,
                                        parametros=parametros, payloads_botones=["CUENTA"])
            registrar(session, empresa, empresa.whatsapp, "saliente", "plantilla", " | ".join(parametros), wamid)
    except ErrorWhatsApp as e:
        log.warning("No se pudo avisar el cobro rechazado por WhatsApp a la empresa #%s: %s", empresa.id, e)
        return False  # se reintenta en la próxima revisión
    return True
