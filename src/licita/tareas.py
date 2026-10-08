"""Ciclo de trabajo periódico: sincronizar, clasificar y buscar calces.

Se ejecuta varias veces al día (ver deploy/crontab). Cada paso es independiente: si uno
falla (por ejemplo, Mercado Público no responde), se registra y el resto sigue.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import anthropic
from sqlalchemy import select
from sqlalchemy.orm import Session

from .alertas import ResultadoAlertas, enviar_alertas_compra_agil
from .calce import buscar_calces, interes_de_clientes
from .compra_agil import ClienteCompraAgil, ResumenCompraAgil, sincronizar_compras_agiles
from .db import Empresa, hoy_en_chile
from .planes import PLANES_CON_COMPRA_AGIL
from .whatsapp import ClienteWhatsApp
from .ia import AsistenteIA, ErrorIA, clasificar_pendientes
from .mercadopublico import MercadoPublicoClient, MercadoPublicoError
from .sync import sincronizar_licitaciones

log = logging.getLogger(__name__)


@dataclass
class ResultadoCiclo:
    licitaciones_nuevas: int = 0
    clasificadas: int = 0
    calces: int = 0
    errores: list[str] = field(default_factory=list)


def ejecutar_ciclo(
    session: Session,
    cliente: MercadoPublicoClient | None,
    ia: AsistenteIA,
    *,
    hoy: date | None = None,
    max_detalles: int | None = None,
) -> ResultadoCiclo:
    resultado = ResultadoCiclo()
    hoy = hoy or hoy_en_chile()

    # 1. Licitaciones de hoy y de ayer (las de ayer pueden haber cambiado de estado).
    if cliente is None:
        resultado.errores.append("sync: falta el ticket de Mercado Público")
    else:
        for fecha in (hoy - timedelta(days=1), hoy):
            try:
                resultado.licitaciones_nuevas += sincronizar_licitaciones(session, cliente, fecha, max_detalles=max_detalles).nuevas
            except MercadoPublicoError as e:
                session.rollback()
                resultado.errores.append(f"sync {fecha}: {e}")

    # 2. Clasificación con IA de lo nuevo (una vez por licitación).
    try:
        ok, errores = clasificar_pendientes(session, ia)
        resultado.clasificadas = ok
        if errores:
            resultado.errores.append(f"clasificar: {errores} licitaciones con error")
    except (ErrorIA, anthropic.APIError) as e:
        session.rollback()
        resultado.errores.append(f"clasificar: {e}")

    # 3. Calces para cada empresa con plan activo (el plan gratis no recibe resumen diario).
    empresas = session.scalars(select(Empresa).where(Empresa.plan != "gratis", Empresa.whatsapp_activo.is_(True))).all()
    for empresa in empresas:
        try:
            resultado.calces += len(buscar_calces(session, empresa, ia))
        except (ErrorIA, anthropic.APIError) as e:
            session.rollback()
            resultado.errores.append(f"calce empresa #{empresa.id}: {e}")

    for error in resultado.errores:
        log.warning(error)
    return resultado


@dataclass
class ResultadoCompraAgil:
    sync: ResumenCompraAgil | None = None
    calces: int = 0
    alertas: ResultadoAlertas | None = None
    errores: list[str] = field(default_factory=list)


def ciclo_compra_agil(
    session: Session,
    cliente: ClienteCompraAgil | None,
    ia: AsistenteIA,
    wa: ClienteWhatsApp | None,
    *,
    idioma: str = "es",
    max_detalles: int = 25,
    momento: datetime | None = None,
) -> ResultadoCompraAgil:
    """Compras Ágiles nuevas → calce con IA para los clientes Pro → alertas urgentes. Corre cada 20 minutos."""
    resultado = ResultadoCompraAgil()
    empresas = session.scalars(select(Empresa).where(
        Empresa.plan.in_(PLANES_CON_COMPRA_AGIL), Empresa.whatsapp_activo.is_(True),
    )).all()
    if not empresas:
        return resultado  # nadie con plan Pro: no gastar consultas del ticket

    if cliente is None:
        resultado.errores.append("compra ágil: falta el ticket de Mercado Público")
    else:
        try:
            interes = interes_de_clientes(session, planes=PLANES_CON_COMPRA_AGIL)
            resultado.sync = sincronizar_compras_agiles(
                session, cliente, interes=interes, max_detalles=max_detalles, momento=momento)
        except MercadoPublicoError as e:
            session.rollback()
            resultado.errores.append(f"compra ágil: {e}")

    for empresa in empresas:
        try:
            resultado.calces += len(buscar_calces(session, empresa, ia, solo_compra_agil=True, momento=momento))
        except (ErrorIA, anthropic.APIError) as e:
            session.rollback()
            resultado.errores.append(f"calce compra ágil empresa #{empresa.id}: {e}")

    if wa is not None:
        resultado.alertas = enviar_alertas_compra_agil(session, wa, idioma=idioma, momento=momento)

    for error in resultado.errores:
        log.warning(error)
    return resultado
