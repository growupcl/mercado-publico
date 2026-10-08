"""Sincroniza licitaciones y órdenes de compra desde la API a la base de datos propia.

Guardar una copia local evita consultar la API en vivo (que tiene límite diario de
peticiones por ticket) y permite construir el histórico de precios.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Callable

from sqlalchemy.orm import Session

from .db import Licitacion, OrdenCompra
from .precios import registrar_precios_adjudicacion, registrar_precios_orden
from .mercadopublico import (
    ESTADOS_LICITACION, MercadoPublicoClient, MercadoPublicoError, normalizar_licitacion, normalizar_orden_de_compra,
)

log = logging.getLogger(__name__)


@dataclass
class ResumenSync:
    nuevas: int = 0
    actualizadas: int = 0
    sin_cambios: int = 0
    errores: int = 0
    omitidas: int = 0  # en estados que no aportan (cerradas, desiertas…)
    pendientes: int = 0  # quedaron para la próxima ejecución por el límite de consultas


ESTADO_PUBLICADA, ESTADO_ADJUDICADA = 5, 8
# Prioridad para pedir detalles: publicadas (oportunidades) y adjudicadas (precios ganadores).
# Las cerradas, desiertas o revocadas no aportan y no gastan consultas del ticket.
PRIORIDAD = {ESTADO_PUBLICADA: 0, ESTADO_ADJUDICADA: 1}


def sincronizar_licitaciones(
    session: Session, cliente: MercadoPublicoClient, fecha: date, *, max_detalles: int | None = None
) -> ResumenSync:
    """Trae las licitaciones del día y pide el detalle solo de las que sirven y son nuevas o cambiaron.

    - Nueva publicada o adjudicada: se pide el detalle.
    - Ya guardada que pasa a adjudicada: se pide el detalle (trae los precios ganadores).
    - Ya guardada con otro cambio de estado: se actualiza el estado sin gastar una consulta.
    - Nueva en otro estado (cerrada, desierta…): se omite.
    """
    resumen = ResumenSync()
    detalles = 0
    listado = [i for i in cliente.licitaciones_por_fecha(fecha) if i.get("CodigoExterno")]
    listado.sort(key=lambda i: PRIORIDAD.get(i.get("CodigoEstado"), 9))
    for item in listado:
        codigo, estado = item["CodigoExterno"], item.get("CodigoEstado")
        existente = session.get(Licitacion, codigo)
        if existente and existente.estado_codigo == estado:
            resumen.sin_cambios += 1
            continue
        if estado not in PRIORIDAD:
            if existente:  # cambio a cerrada, desierta, etc.: basta con el estado del listado
                existente.estado_codigo = estado
                existente.estado = ESTADOS_LICITACION.get(estado, existente.estado)
                session.commit()
                resumen.actualizadas += 1
            else:
                resumen.omitidas += 1
            continue
        if max_detalles is not None and detalles >= max_detalles:
            resumen.pendientes += 1
            continue
        detalles += 1
        try:
            raw = cliente.licitacion(codigo)
        except MercadoPublicoError as e:
            log.warning("No se pudo obtener la licitación %s: %s", codigo, e)
            resumen.errores += 1
            continue
        if raw is None:
            resumen.errores += 1
            continue
        datos = normalizar_licitacion(raw)
        if existente:
            for campo, valor in datos.items():
                setattr(existente, campo, valor)
            existente.raw = raw
            lic = existente
            resumen.actualizadas += 1
        else:
            lic = Licitacion(**datos, raw=raw)
            session.add(lic)
            resumen.nuevas += 1
        if lic.estado_codigo == ESTADO_ADJUDICADA:
            registrar_precios_adjudicacion(session, lic)
        session.commit()
    return resumen


def sincronizar_ordenes_de_compra(
    session: Session,
    cliente: MercadoPublicoClient,
    fecha: date,
    *,
    max_detalles: int | None = None,
    interes: Callable[[str], bool] | None = None,
) -> ResumenSync:
    """Trae las órdenes de compra del día: son la base del histórico de precios.

    Hay unas 20.000 órdenes al día, así que con `interes` solo se pide el detalle de las que
    tienen relación con los rubros de los clientes (según el nombre de la orden).
    """
    resumen = ResumenSync()
    detalles = 0
    for item in cliente.ordenes_de_compra_por_fecha(fecha):
        codigo = item.get("Codigo")
        if not codigo:
            continue
        if interes is not None and not interes(item.get("Nombre") or ""):
            resumen.omitidas += 1
            continue
        existente = session.get(OrdenCompra, codigo)
        if existente:
            # Para el histórico de precios basta la primera versión de cada orden.
            resumen.sin_cambios += 1
            continue
        if max_detalles is not None and detalles >= max_detalles:
            resumen.pendientes += 1
            continue
        detalles += 1
        try:
            raw = cliente.orden_de_compra(codigo)
        except MercadoPublicoError as e:
            log.warning("No se pudo obtener la orden de compra %s: %s", codigo, e)
            resumen.errores += 1
            continue
        if raw is None:
            resumen.errores += 1
            continue
        oc = OrdenCompra(**normalizar_orden_de_compra(raw), raw=raw)
        session.add(oc)
        registrar_precios_orden(session, oc)
        resumen.nuevas += 1
        session.commit()
    return resumen
