"""Envío del resumen diario por WhatsApp, cuidando el costo.

- Si el usuario nos escribió en las últimas 24 h, el resumen completo va como texto libre (gratis).
- Si no, se envía una sola plantilla "utility" con botones. Cuando el usuario toca un botón
  se abre la ventana de 24 h y todo lo que sigue es gratis (ver conversacion.py).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Empresa, MensajeWhatsApp, ahora
from .plantillas_whatsapp import PAYLOADS_RESUMEN, PLANTILLAS, RESUMEN_DIARIO
from .resumen import Fila, formato_cierre, marcar_enviados, seleccionar_calces, texto_resumen
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


def enviar_resumenes(
    session: Session,
    wa: ClienteWhatsApp,
    *,
    plantilla: str = "resumen_diario_licitaciones",
    idioma: str = "es",
    umbral: int = 60,
    maximo: int = 5,
    momento: datetime | None = None,
) -> ResumenEnvio:
    momento = momento or ahora()
    resultado = ResumenEnvio()
    # El plan gratis no recibe el resumen diario por WhatsApp.
    empresas = session.scalars(select(Empresa).where(
        Empresa.whatsapp != "", Empresa.whatsapp_activo.is_(True), Empresa.plan != "gratis",
    ))
    for empresa in empresas:
        filas = seleccionar_calces(session, empresa, umbral=umbral, maximo=maximo, momento=momento)
        if not filas:
            resultado.sin_novedades += 1
            continue
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
        except ErrorWhatsApp as e:
            log.warning("No se pudo enviar el resumen a la empresa #%s: %s", empresa.id, e)
            session.rollback()
            resultado.errores += 1
            continue
        marcar_enviados(session, filas, momento)
    session.commit()
    return resultado
