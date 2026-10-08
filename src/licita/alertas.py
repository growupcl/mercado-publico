"""Alertas urgentes de Compra Ágil por WhatsApp (plan Pro).

Una Compra Ágil se cotiza en uno a tres días y suele ganar quien responde primero, así que no espera al
resumen diario: apenas la IA confirma el calce, se avisa.

Para cuidar el costo y no saturar al cliente:
- Solo entre las 8:00 y las 21:00 de Chile, y como máximo ALERTAS_POR_DIA alertas por empresa al día.
- Solo si queda al menos una hora para el cierre.
- Con la ventana de 24 h abierta va un texto con todas (gratis); si no, la plantilla con la mejor y un botón
  que abre el detalle (y la ventana).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import ZONA_CHILE, Calce, Empresa, Licitacion, ahora, hora_chile
from .notificaciones import ventana_abierta, registrar
from .planes import PLANES_CON_COMPRA_AGIL
from .plantillas_whatsapp import ALERTA_COMPRA_AGIL, PAYLOADS_RESUMEN
from .resumen import Fila, formato_cierre, formato_pesos, marcar_enviados
from .whatsapp import ClienteWhatsApp, ErrorWhatsApp

log = logging.getLogger(__name__)

TIPO = "COT"
UMBRAL = 70
ALERTAS_POR_DIA = 3
ANTICIPACION_MINIMA = timedelta(hours=1)
HORARIO = (8, 21)  # desde las 8:00 hasta antes de las 21:00, hora de Chile


@dataclass
class ResultadoAlertas:
    plantillas: int = 0
    textos: int = 0
    alertas: int = 0  # Compras Ágiles avisadas
    errores: int = 0
    fuera_de_horario: bool = False


def _inicio_del_dia_utc(momento: datetime) -> datetime:
    dia = hora_chile(momento).date()
    return datetime.combine(dia, time(0), ZONA_CHILE).astimezone(timezone.utc).replace(tzinfo=None)


def alertas_enviadas_hoy(session: Session, empresa: Empresa, momento: datetime) -> int:
    return session.scalar(
        select(func.count()).select_from(Calce).join(Licitacion, Licitacion.codigo == Calce.licitacion_codigo)
        .where(Calce.empresa_id == empresa.id, Licitacion.tipo == TIPO, Calce.notificado_en >= _inicio_del_dia_utc(momento))
    ) or 0


def pendientes(session: Session, empresa: Empresa, *, umbral: int, momento: datetime, maximo: int) -> list[Fila]:
    cierre_minimo = hora_chile(momento) + ANTICIPACION_MINIMA
    return [
        tuple(fila)
        for fila in session.execute(
            select(Calce, Licitacion).join(Licitacion, Licitacion.codigo == Calce.licitacion_codigo)
            .where(
                Calce.empresa_id == empresa.id, Calce.notificado.is_(False), Calce.puntaje >= umbral,
                Licitacion.tipo == TIPO, Licitacion.estado_codigo == 5, Licitacion.fecha_cierre > cierre_minimo,
            )
            .order_by(Calce.puntaje.desc(), Licitacion.fecha_cierre)
            .limit(maximo)
        ).all()
    ]


def texto_alerta(empresa: Empresa, filas: list[Fila]) -> str:
    if len(filas) == 1:
        lineas = [f"⚡ {empresa.nombre}, hay una Compra Ágil que calza contigo:", ""]
    else:
        lineas = [f"⚡ {empresa.nombre}, hay {len(filas)} Compras Ágiles que calzan contigo:", ""]
    for i, (calce, lic) in enumerate(filas, 1):
        lineas += [
            f"{i}. *{lic.nombre}* ({calce.puntaje}% de calce)",
            f"   {' · '.join(p for p in (lic.organismo, lic.region) if p)}",
            f"   Presupuesto: {formato_pesos(lic.monto_estimado)} · Cierra: *{formato_cierre(lic)}*",
            f"   {calce.razon}",
            "",
        ]
    lineas.append("Responde con el número para ver el detalle. En Compra Ágil suele ganar quien cotiza primero.")
    return "\n".join(lineas)


def enviar_alertas_compra_agil(
    session: Session,
    wa: ClienteWhatsApp,
    *,
    idioma: str = "es",
    umbral: int = UMBRAL,
    por_dia: int = ALERTAS_POR_DIA,
    momento: datetime | None = None,
) -> ResultadoAlertas:
    momento = momento or ahora()
    resultado = ResultadoAlertas()
    if not HORARIO[0] <= hora_chile(momento).hour < HORARIO[1]:
        resultado.fuera_de_horario = True
        return resultado
    empresas = session.scalars(select(Empresa).where(
        Empresa.whatsapp != "", Empresa.whatsapp_activo.is_(True), Empresa.plan.in_(PLANES_CON_COMPRA_AGIL),
    )).all()
    for empresa in empresas:
        cupo = por_dia - alertas_enviadas_hoy(session, empresa, momento)
        if cupo <= 0:
            continue
        filas = pendientes(session, empresa, umbral=umbral, momento=momento, maximo=cupo)
        if not filas:
            continue
        try:
            if ventana_abierta(empresa, momento):
                texto = texto_alerta(empresa, filas)
                wamid = wa.enviar_texto(empresa.whatsapp, texto)
                registrar(session, empresa, empresa.whatsapp, "saliente", "texto", texto, wamid)
                resultado.textos += 1
            else:
                filas = filas[:1]  # la plantilla muestra una; las demás siguen pendientes
                calce, lic = filas[0]
                parametros = [empresa.nombre, lic.nombre, str(calce.puntaje), formato_cierre(lic)]  # ClienteWhatsApp las limpia
                wamid = wa.enviar_plantilla(
                    empresa.whatsapp, ALERTA_COMPRA_AGIL.nombre, idioma=idioma, parametros=parametros,
                    payloads_botones=[p.format(codigo=lic.codigo) for p in PAYLOADS_RESUMEN[ALERTA_COMPRA_AGIL.nombre]],
                )
                registrar(session, empresa, empresa.whatsapp, "saliente", "plantilla", " | ".join(parametros), wamid)
                resultado.plantillas += 1
        except ErrorWhatsApp as e:
            log.warning("No se pudo enviar la alerta de Compra Ágil a la empresa #%s: %s", empresa.id, e)
            session.rollback()
            resultado.errores += 1
            continue
        marcar_enviados(session, filas, momento)
        resultado.alertas += len(filas)
    session.commit()
    return resultado
