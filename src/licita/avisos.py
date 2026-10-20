"""Avisos al cliente sobre su cuenta: cobros rechazados de la suscripción y fin de la prueba gratuita.

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
from .plantillas_whatsapp import COBRO_RECHAZADO, FIN_PRUEBA, SUSCRIPCION_TERMINADA
from .planes import PRECIO_FUNDADOR_PRO, formato_pesos
from .suscripciones import DIAS_GRACIA_RENOVACION, fundador_disponible, generar_token
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


# --- Fin de la prueba gratuita ---

# Cuántos días antes de que termine la prueba se avisa (día 11 de 14).
DIAS_AVISO_FIN_PRUEBA = 2
# Pasado este plazo desde el vencimiento, el aviso de "terminó" ya no se envía (llegaría atrasado).
VIGENCIA_AVISO_TERMINADA = timedelta(days=3)


def _correo_html(empresa: Empresa, parrafos: list[str], enlace: str, boton: str) -> str:
    e = html.escape
    cuerpo = "".join(f"<p>{p}</p>" for p in parrafos)  # los párrafos ya vienen escapados
    return f"""<!doctype html>
<html lang="es"><body style="margin:0;padding:24px;background:#f4f6f8;font-family:Arial,Helvetica,sans-serif;color:#1f2933">
<div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:8px;padding:28px">
<p style="font-size:20px;font-weight:bold;margin:0 0 16px">Calza</p>
<p>Hola {e(empresa.nombre)}:</p>
{cuerpo}
<p style="text-align:center;margin:28px 0"><a href="{e(enlace)}" style="background:#0b6e4f;color:#ffffff;text-decoration:none;padding:12px 22px;border-radius:6px;font-weight:bold">{e(boton)}</a></p>
<p style="font-size:13px;color:#52606d">Este enlace es privado: funciona como una contraseña, no lo compartas.</p>
<p style="font-size:13px;color:#52606d">Equipo Calza · hola@calza.cl</p>
</div></body></html>"""


def _texto_fundador(fundador: bool) -> str:
    if not fundador:
        return ""
    return (f"Si eliges el plan Pro ahora, quedas con el precio fundador de {formato_pesos(PRECIO_FUNDADOR_PRO)} "
            "al mes de por vida (solo para los primeros clientes).")


def mensajes_fin_prueba(empresa: Empresa, termina: str, enlace: str, fundador: bool) -> tuple[str, str, str, str]:
    """(texto de WhatsApp, asunto, texto y html del correo) del aviso de que la prueba termina pronto."""
    fundador_txt = _texto_fundador(fundador)
    whatsapp = (f"Hola {empresa.nombre}, tu prueba gratuita de Calza termina el {termina}. Para seguir recibiendo "
                "cada mañana las licitaciones que calzan con tu negocio, elige tu plan aquí (enlace privado):\n"
                f"{enlace}" + (f"\n\n{fundador_txt}" if fundador_txt else "") +
                "\n\nSi te suscribes ahora no pierdes días de prueba: el primer cobro es al terminarla.")
    asunto = f"Tu prueba de Calza termina el {termina}"
    texto = (f"Hola {empresa.nombre}:\n\nTu prueba gratuita de Calza termina el {termina}. Desde ese día dejas de "
             "recibir el resumen diario de licitaciones por WhatsApp.\n\n"
             f"Para seguir recibiéndolo, elige tu plan desde tu cuenta:\n\n{enlace}\n\n"
             + (f"{fundador_txt}\n" if fundador_txt else "") +
             "Si te suscribes ahora no pierdes días de prueba: el primer cobro es al terminarla.\n\n"
             "Este enlace es privado: funciona como una contraseña, no lo compartas.\n\n"
             "Saludos,\nEquipo Calza · hola@calza.cl")
    e = html.escape
    parrafos = [f"Tu prueba gratuita de Calza termina el <strong>{e(termina)}</strong>. Desde ese día dejas de "
                "recibir el resumen diario de licitaciones por WhatsApp.",
                "Para seguir recibiéndolo, elige tu plan desde tu cuenta. Si te suscribes ahora no pierdes días de "
                "prueba: el primer cobro es al terminarla."]
    if fundador_txt:
        parrafos.append(e(fundador_txt))
    return whatsapp, asunto, texto, _correo_html(empresa, parrafos, enlace, "Elegir mi plan")


def mensajes_primer_cobro(empresa: Empresa, termina: str, plan: str, monto: int, enlace: str) -> tuple[str, str, str, str]:
    """Aviso de que la prueba termina y se hará el primer cobro (la empresa ya registró su tarjeta)."""
    valor = formato_pesos(monto)
    whatsapp = (f"Hola {empresa.nombre}, tu prueba gratuita de Calza termina el {termina}. Ese día se hará el primer cobro "
                f"de tu plan {plan} ({valor}) en tu tarjeta registrada en Mercado Pago y seguirás recibiendo tus licitaciones. "
                f"Si no quieres continuar, cancela antes desde tu cuenta (enlace privado):\n{enlace}")
    asunto = f"Tu prueba de Calza termina el {termina}: primer cobro de {valor}"
    texto = (f"Hola {empresa.nombre}:\n\nTu prueba gratuita de Calza termina el {termina}. Ese día se hará el primer cobro "
             f"de tu plan {plan}, {valor}, en la tarjeta que registraste en Mercado Pago, y seguirás recibiendo tus "
             "licitaciones sin interrupciones.\n\n"
             f"Si no quieres continuar, cancela la renovación antes de esa fecha desde tu cuenta y no se cobrará nada:\n\n{enlace}\n\n"
             "Este enlace es privado: funciona como una contraseña, no lo compartas.\n\n"
             "Saludos,\nEquipo Calza · hola@calza.cl")
    e = html.escape
    parrafos = [f"Tu prueba gratuita de Calza termina el <strong>{e(termina)}</strong>. Ese día se hará el primer cobro de tu "
                f"plan {e(plan)}, <strong>{e(valor)}</strong>, en la tarjeta que registraste en Mercado Pago, y seguirás "
                "recibiendo tus licitaciones sin interrupciones.",
                "Si no quieres continuar, cancela la renovación antes de esa fecha desde tu cuenta y no se cobrará nada."]
    return whatsapp, asunto, texto, _correo_html(empresa, parrafos, enlace, "Ir a mi cuenta")


def mensajes_terminada(empresa: Empresa, que: str, termino: str, enlace: str, fundador: bool) -> tuple[str, str, str, str]:
    """Lo mismo para el aviso de que la prueba o el plan terminó. `que`: "prueba gratuita" o "plan Pyme mensual"."""
    fundador_txt = _texto_fundador(fundador)
    whatsapp = (f"Hola {empresa.nombre}, tu {que} de Calza terminó el {termino} y dejamos de enviarte el resumen "
                "diario de licitaciones. Si quieres volver a recibirlas, elige tu plan aquí (enlace privado):\n"
                f"{enlace}" + (f"\n\n{fundador_txt}" if fundador_txt else ""))
    asunto = f"Tu {que} de Calza terminó"
    texto = (f"Hola {empresa.nombre}:\n\nTu {que} de Calza terminó el {termino} y dejamos de enviarte el resumen "
             "diario de licitaciones por WhatsApp.\n\n"
             f"Si quieres volver a recibirlas, elige tu plan desde tu cuenta:\n\n{enlace}\n\n"
             + (f"{fundador_txt}\n\n" if fundador_txt else "") +
             "Si no te convenció, nos ayudaría mucho saber por qué: responde este correo con una línea.\n\n"
             "Este enlace es privado: funciona como una contraseña, no lo compartas.\n\n"
             "Saludos,\nEquipo Calza · hola@calza.cl")
    e = html.escape
    parrafos = [f"Tu {e(que)} de Calza terminó el <strong>{e(termino)}</strong> y dejamos de enviarte el resumen "
                "diario de licitaciones por WhatsApp.",
                "Si quieres volver a recibirlas, elige tu plan desde tu cuenta."]
    if fundador_txt:
        parrafos.append(e(fundador_txt))
    parrafos.append("Si no te convenció, nos ayudaría mucho saber por qué: responde este correo con una línea.")
    return whatsapp, asunto, texto, _correo_html(empresa, parrafos, enlace, "Volver a Calza")


def _enviar_aviso(session, wa, correo, empresa, *, texto_wa, plantilla, parametros, asunto, texto, cuerpo_html,
                  idioma, momento) -> tuple[bool, bool]:
    """Envía por WhatsApp (si lo tiene activo) y por correo. Devuelve (whatsapp_ok, correo_ok)."""
    ok_wa = ok_correo = False
    # Sin plantilla aprobada (plantilla=None), por WhatsApp solo va si la ventana de 24 h está abierta.
    if wa is not None and empresa.whatsapp and empresa.whatsapp_activo and (plantilla or ventana_abierta(empresa, momento)):
        nombre = plantilla.nombre if plantilla else "primer_cobro"
        try:
            if ventana_abierta(empresa, momento):
                wamid = wa.enviar_texto(empresa.whatsapp, texto_wa)
                registrar(session, empresa, empresa.whatsapp, "saliente", "texto", texto_wa.split("\n")[0], wamid)
            else:
                wamid = wa.enviar_plantilla(empresa.whatsapp, plantilla.nombre, idioma=idioma, parametros=parametros,
                                            payloads_botones=["CUENTA"])
                registrar(session, empresa, empresa.whatsapp, "saliente", "plantilla", " | ".join(parametros), wamid)
            ok_wa = True
        except ErrorWhatsApp as e:
            log.warning("No se pudo enviar el aviso %s por WhatsApp a la empresa #%s: %s", nombre, empresa.id, e)
    if correo is not None and empresa.email:
        try:
            correo.enviar(empresa.email, asunto, texto, cuerpo_html)
            ok_correo = True
        except ErrorCorreo as e:
            log.warning("No se pudo enviar el aviso %s por correo a la empresa #%s: %s",
                        plantilla.nombre if plantilla else "primer_cobro", empresa.id, e)
    return ok_wa, ok_correo


def _plan_terminado(session: Session, empresa: Empresa, suscripcion: Suscripcion) -> str:
    """"prueba gratuita" si nunca pagó; si no, el plan que tenía."""
    pago = session.scalar(select(Pago).where(Pago.empresa_id == empresa.id, Pago.estado == "pagado")
                          .order_by(Pago.creado_en.desc()).limit(1))
    if pago is None:
        return "prueba gratuita"
    return f"plan {PLANES[pago.plan].nombre} {pago.periodicidad}" if pago.plan in PLANES else "plan"


def avisar_fin_de_prueba(
    session: Session,
    wa: ClienteWhatsApp | None,
    *,
    url_publica: str,
    correo: ClienteCorreo | None = None,
    idioma: str = "es",
    momento: datetime | None = None,
) -> ResultadoAvisos:
    """Avisa a quienes están por terminar la prueba sin suscribirse y a quienes acaban de pasar al plan gratis.

    Cada aviso se marca como enviado si salió por al menos un canal; si fallan ambos, se reintenta en la próxima
    revisión. Un aviso de "terminó" que lleva más de 3 días pendiente ya no se envía."""
    momento = momento or ahora()
    resultado = ResultadoAvisos()
    fundador = fundador_disponible(session)
    por_terminar = session.scalars(select(Suscripcion).where(
        Suscripcion.estado == "prueba", Suscripcion.aviso_fin_prueba_en.is_(None),
        Suscripcion.vigente_hasta > momento, Suscripcion.vigente_hasta <= momento + timedelta(days=DIAS_AVISO_FIN_PRUEBA),
    )).all()
    for suscripcion in por_terminar:
        empresa = session.get(Empresa, suscripcion.empresa_id)
        termina = fecha_larga(suscripcion.vigente_hasta)
        enlace = f"{url_publica}/cuenta/{generar_token(empresa)}"
        mandato = session.get(MandatoPago, suscripcion.mandato_activo_id) if suscripcion.mandato_activo_id else None
        if mandato is not None:
            # Registró su tarjeta: se le recuerda cuándo y cuánto se cobrará, y cómo cancelar si no quiere seguir.
            texto_wa, asunto, texto, cuerpo_html = mensajes_primer_cobro(empresa, termina, nombre_plan(mandato),
                                                                         mandato.monto, enlace)
            plantilla, parametros = None, []
        else:
            texto_wa, asunto, texto, cuerpo_html = mensajes_fin_prueba(empresa, termina, enlace, fundador)
            plantilla, parametros = FIN_PRUEBA, [empresa.nombre, termina]
        ok_wa, ok_correo = _enviar_aviso(
            session, wa, correo, empresa, texto_wa=texto_wa, plantilla=plantilla, parametros=parametros,
            asunto=asunto, texto=texto, cuerpo_html=cuerpo_html, idioma=idioma, momento=momento)
        if ok_wa or ok_correo:
            suscripcion.aviso_fin_prueba_en = momento
        resultado.whatsapp += ok_wa
        resultado.correos += ok_correo
        session.commit()

    terminadas = session.scalars(select(Suscripcion).where(
        Suscripcion.estado == "vencida", Suscripcion.aviso_vencida_en.is_(None),
    )).all()
    for suscripcion in terminadas:
        if momento - suscripcion.vigente_hasta > VIGENCIA_AVISO_TERMINADA:
            suscripcion.aviso_vencida_en = momento  # atrasado: ya no se envía
            session.commit()
            continue
        empresa = session.get(Empresa, suscripcion.empresa_id)
        que, termino = _plan_terminado(session, empresa, suscripcion), fecha_larga(suscripcion.vigente_hasta)
        enlace = f"{url_publica}/cuenta/{generar_token(empresa)}"
        texto_wa, asunto, texto, cuerpo_html = mensajes_terminada(empresa, que, termino, enlace, fundador)
        ok_wa, ok_correo = _enviar_aviso(
            session, wa, correo, empresa, texto_wa=texto_wa, plantilla=SUSCRIPCION_TERMINADA,
            parametros=[empresa.nombre, que, termino], asunto=asunto, texto=texto, cuerpo_html=cuerpo_html,
            idioma=idioma, momento=momento)
        if ok_wa or ok_correo:
            suscripcion.aviso_vencida_en = momento
        resultado.whatsapp += ok_wa
        resultado.correos += ok_correo
        session.commit()
    return resultado
