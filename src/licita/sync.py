"""Sincroniza licitaciones y órdenes de compra desde la API a la base de datos propia.

Guardar una copia local evita consultar la API en vivo (que tiene límite diario de
peticiones por ticket) y permite construir el histórico de precios.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from .db import Licitacion, OrdenCompra
from .mercadopublico import MercadoPublicoClient, MercadoPublicoError, normalizar_licitacion, normalizar_orden_de_compra

log = logging.getLogger(__name__)


@dataclass
class ResumenSync:
    nuevas: int = 0
    actualizadas: int = 0
    sin_cambios: int = 0
    errores: int = 0


def sincronizar_licitaciones(
    session: Session, cliente: MercadoPublicoClient, fecha: date, *, max_detalles: int | None = None
) -> ResumenSync:
    """Trae las licitaciones del día y pide el detalle solo de las nuevas o con cambio de estado."""
    resumen = ResumenSync()
    detalles = 0
    for item in cliente.licitaciones_por_fecha(fecha):
        codigo = item.get("CodigoExterno")
        if not codigo:
            continue
        existente = session.get(Licitacion, codigo)
        if existente and existente.estado_codigo == item.get("CodigoEstado"):
            resumen.sin_cambios += 1
            continue
        if max_detalles is not None and detalles >= max_detalles:
            break
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
            resumen.actualizadas += 1
        else:
            session.add(Licitacion(**datos, raw=raw))
            resumen.nuevas += 1
        session.commit()
    return resumen


def sincronizar_ordenes_de_compra(
    session: Session, cliente: MercadoPublicoClient, fecha: date, *, max_detalles: int | None = None
) -> ResumenSync:
    """Trae las órdenes de compra del día: son la base del histórico de precios."""
    resumen = ResumenSync()
    detalles = 0
    for item in cliente.ordenes_de_compra_por_fecha(fecha):
        codigo = item.get("Codigo")
        if not codigo:
            continue
        existente = session.get(OrdenCompra, codigo)
        if existente:
            # Para el histórico de precios basta la primera versión de cada orden.
            resumen.sin_cambios += 1
            continue
        if max_detalles is not None and detalles >= max_detalles:
            break
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
        session.add(OrdenCompra(**normalizar_orden_de_compra(raw), raw=raw))
        resumen.nuevas += 1
        session.commit()
    return resumen
