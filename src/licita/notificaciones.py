"""Envío del resumen diario por WhatsApp (cuidando el costo) y su copia por correo.

- Si el usuario nos escribió en las últimas 24 h, el resumen completo va como texto libre (gratis).
- Si no, se envía una sola plantilla "utility" con botones. Cuando el usuario toca un botón
  se abre la ventana de 24 h y todo lo que sigue es gratis (ver conversacion.py).
- Copia por correo a quienes no la desactivaron: Meta puede limitar la entrega de plantillas de marketing sin avisar
  al enviarlas (el error llega después), así el resumen llega igual. Usa la misma numeración que el WhatsApp.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Empresa, MensajeWhatsApp, ahora
from .plantillas_whatsapp import PAYLOADS_RESUMEN, PLANTILLAS, RESUMEN_DIARIO
from .correo import ClienteCorreo, ErrorCorreo
from .resumen import Fila, correo_resumen, formato_cierre, marcar_enviados, seleccionar_calces, texto_resumen
from .whatsapp import ClienteWhatsApp, ErrorWhatsApp, normalizar_telefono

log = logging.getLogger(__name__)

# Margen de seguridad: tratamos la ventana como de 23 h para no rozar el límite de Meta.
VENTANA = timedelta(hours=23)


@dataclass
class ResumenEnvio:
    plantillas: int = 0
    textos: int = 0
    sin_novedades: int = 0
    errores: int = 0
    correos: int = 0
    errores_correo: int = 0


def ventana_abierta(empresa: Empresa, momento: datetime) -> bool:
    return empresa.ultimo_mensaje_entrante is not None and momento - empresa.ultimo_mensaje_entrante < VENTANA


def parametros_plantilla(empresa: Empresa, filas: list[Fila], plantilla: str = RESUMEN_DIARIO.nombre) -> list[str]:
    """Variables del resumen diario. La plantilla de respaldo usa solo las primeras."""
    calce, lic = filas[0]
    cantidad = "1 licitación nueva" if len(filas) == 1 else f"{len(filas)} licitaciones nuevas"
    todas = [empresa.nombre, cantidad, lic.nombre, str(calce.puntaje), formato_cierre(lic)]
    return todas[: PLANTILLAS[plantilla].variables] if plantilla in PLANTILLAS else todas


def payloads_resumen(plantilla: str, codigo: str) -> list[str]:
    return [p.format(codigo=codigo) for p in PAYLOADS_RESUMEN.get(plantilla, PAYLOADS_RESUMEN[RESUMEN_DIARIO.nombre])]


def registrar(session: Session, empresa: Empresa | None, telefono: str, direccion: str, tipo: str, contenido: str, wamid: str = "") -> None:
    session.add(MensajeWhatsApp(
        empresa_id=empresa.id if empresa else None, telefono=normalizar_telefono(telefono),
        direccion=direccion, tipo=tipo, contenido=contenido, wamid=wamid,
    ))


def enlace_baja_correo(empresa: Empresa, url_publica: str) -> str:
    """Enlace para dejar de recibir el resumen por correo. El token se crea la primera vez y no da acceso a la cuenta."""
    if not empresa.token_correo:
        empresa.token_correo = secrets.token_urlsafe(24)
    return f"{url_publica.rstrip('/')}/correo/baja/{empresa.token_correo}"


def enviar_resumenes(
    session: Session,
    wa: ClienteWhatsApp | None,
    *,
    plantilla: str = "resumen_diario_licitaciones",
    idioma: str = "es",
    umbral: int = 60,
    maximo: int = 5,
    momento: datetime | None = None,
    correo: ClienteCorreo | None = None,
    url_publica: str = "",
    whatsapp_publico: str = "",
) -> ResumenEnvio:
    """Envía el resumen por WhatsApp y, si hay `correo`, su copia por correo. Las licitaciones quedan como enviadas
    si salieron por al menos un canal; si fallan todos, se reintentan al día siguiente."""
    momento = momento or ahora()
    resultado = ResumenEnvio()
    por_whatsapp = (Empresa.whatsapp != "") & Empresa.whatsapp_activo.is_(True)
    por_correo = (Empresa.email != "") & Empresa.resumen_por_correo.is_(True)
    # El plan gratis no recibe el resumen diario.
    condicion = por_whatsapp | por_correo if correo is not None else por_whatsapp
    empresas = session.scalars(select(Empresa).where(Empresa.plan != "gratis", condicion)).all()
    for empresa in empresas:
        filas = seleccionar_calces(session, empresa, umbral=umbral, maximo=maximo, momento=momento)
        if not filas:
            resultado.sin_novedades += 1
            continue
        enviado = False
        if wa is not None and empresa.whatsapp and empresa.whatsapp_activo:
            try:
                if ventana_abierta(empresa, momento):
                    texto = texto_resumen(empresa, filas)
                    wamid = wa.enviar_texto(empresa.whatsapp, texto)
                    registrar(session, empresa, empresa.whatsapp, "saliente", "texto", texto, wamid)
                    resultado.textos += 1
                else:
                    parametros = parametros_plantilla(empresa, filas, plantilla)
                    wamid = wa.enviar_plantilla(
                        empresa.whatsapp, plantilla, idioma=idioma, parametros=parametros,
                        payloads_botones=payloads_resumen(plantilla, filas[0][1].codigo),
                    )
                    registrar(session, empresa, empresa.whatsapp, "saliente", "plantilla", " | ".join(parametros), wamid)
                    resultado.plantillas += 1
                enviado = True
            except ErrorWhatsApp as e:
                log.warning("No se pudo enviar el resumen a la empresa #%s: %s", empresa.id, e)
                resultado.errores += 1
        if correo is not None and empresa.email and empresa.resumen_por_correo:
            asunto, texto, cuerpo_html = correo_resumen(
                empresa, filas, url_publica=url_publica, enlace_baja=enlace_baja_correo(empresa, url_publica),
                whatsapp_publico=whatsapp_publico, momento=momento,
            )
            baja = enlace_baja_correo(empresa, url_publica)
            try:
                correo.enviar(empresa.email, asunto, texto, cuerpo_html, encabezados={
                    "List-Unsubscribe": f"<{baja}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
                })
                resultado.correos += 1
                enviado = True
            except ErrorCorreo as e:
                log.warning("No se pudo enviar el resumen por correo a la empresa #%s: %s", empresa.id, e)
                resultado.errores_correo += 1
        if enviado:
            marcar_enviados(session, filas, momento)
        else:
            session.commit()  # guarda lo registrado (token de baja); las licitaciones se reintentan mañana
    session.commit()
    return resultado


def correo_bienvenida(nombre: str, enlace_cuenta: str, *, pendiente_de: str = "", numero: str = "",
                      wa_link: str = "") -> tuple[str, str, str]:
    """(asunto, texto, html) del correo al registrarse: el paso que falta, qué viene y el enlace a su cuenta (por si
    cerró la página de registro)."""
    import html as htmllib

    from .planes import DIAS_PRUEBA

    if pendiente_de == "whatsapp":
        asunto = "Activa tu prueba de Calza: escríbenos \"Hola\" por WhatsApp"
        paso = (f"Te falta un paso para empezar tus {DIAS_PRUEBA} días gratis: escríbenos \"Hola\" por WhatsApp desde "
                f"el número que registraste ({numero}). Apenas lo hagas empieza la prueba, sin tarjeta.")
    elif pendiente_de == "tarjeta":
        asunto = "Activa tu prueba de Calza: registra tu tarjeta"
        paso = (f"Te falta un paso para empezar tus {DIAS_PRUEBA} días gratis: registrar tu tarjeta en Mercado Pago "
                "desde tu cuenta. Hoy no se cobra nada y, si cancelas antes de que termine la prueba, no pagas.")
    else:
        asunto = "Tu cuenta de Calza está lista"
        paso = f"Tu prueba de {DIAS_PRUEBA} días ya empezó, con todo lo del plan Pro."
    queviene = ("Cada día hábil a las 8:00 te llega por WhatsApp (y una copia a este correo) el resumen con las "
                "licitaciones nuevas que calzan con tu empresa. Respondes con el número de una para ver qué piden, "
                "plazos y requisitos, y puedes enviarnos el PDF de las bases para que te las expliquemos en simple.")
    cuenta = ("En tu cuenta ves tu plan, cambias lo que vende tu empresa o tus regiones y, cuando quieras, te suscribes. "
              "Este enlace es privado: funciona como una contraseña, no lo compartas.")
    texto = "\n\n".join([f"Hola {nombre}:", paso] + ([f"Abrir WhatsApp: {wa_link}"] if pendiente_de == "whatsapp" and wa_link else [])
                        + [queviene, cuenta, enlace_cuenta, "Equipo Calza · hola@calza.cl"])
    e = htmllib.escape
    boton = ""
    if pendiente_de == "whatsapp" and wa_link:
        boton = (f'<p style="text-align:center;margin:24px 0"><a href="{e(wa_link)}" style="background:#0b6e4f;color:#ffffff;'
                 'text-decoration:none;padding:12px 22px;border-radius:6px;font-weight:bold">Escribir "Hola" por WhatsApp</a></p>')
    cuerpo = f"""<!doctype html>
<html lang="es"><body style="margin:0;padding:24px;background:#f4f6f8;font-family:Arial,Helvetica,sans-serif;color:#1f2933">
<div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:8px;padding:28px">
<p style="font-size:20px;font-weight:bold;margin:0 0 16px">Calza</p>
<p>Hola {e(nombre)}:</p>
<p><strong>{e(paso)}</strong></p>
{boton}
<p>{e(queviene)}</p>
<p>{e(cuenta)}</p>
<p><a href="{e(enlace_cuenta)}" style="color:#0b6e4f">Ir a mi cuenta</a></p>
<p style="font-size:13px;color:#52606d">Equipo Calza · hola@calza.cl</p>
</div></body></html>"""
    return asunto, texto, cuerpo
