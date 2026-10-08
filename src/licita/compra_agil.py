"""Compra Ágil: cliente de la API v2 de ChileCompra y sincronización.

Guía oficial: "Guía de Uso API Compra Ágil v2" (ChileCompra, mayo 2026), URL base https://api2.mercadopublico.cl.
- El ticket es el mismo de Mercado Público, pero va en el header `ticket` (nunca en la URL).
- El listado trae nombre, organismo, región, monto y cierre; el detalle agrega descripción y productos, pero es
  lento (20 a 25 s). Por eso el detalle se pide solo para las que pueden interesarle a un cliente.
- Las fechas vienen en hora de Chile aunque algunas terminen en "Z"; se guardan así, igual que las licitaciones.

Cada Compra Ágil se guarda en la tabla de licitaciones con tipo "COT", así el calce, el detalle por WhatsApp,
los precios y el análisis de documentos funcionan igual que para una licitación.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Iterator

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import Licitacion, hora_chile
from .mercadopublico import MercadoPublicoError, parsear_fecha, parsear_monto

log = logging.getLogger(__name__)

BASE_URL = "https://api2.mercadopublico.cl/v2/compra-agil"
TIPO = "COT"
URL_FICHA = "https://buscador.mercadopublico.cl/ficha?code={codigo}"

# Estados de Compra Ágil traducidos a los códigos de licitación que ya usa Calza.
ESTADOS = {
    "publicada": (5, "Publicada"),
    "cerrada": (6, "Cerrada"),
    "desierta": (7, "Desierta"),
    "proveedor_seleccionado": (8, "Proveedor seleccionado"),
    "oc_emitida": (8, "OC emitida"),
    "cancelada": (18, "Cancelada"),
}
REGIONES = {
    1: "Tarapacá", 2: "Antofagasta", 3: "Atacama", 4: "Coquimbo", 5: "Valparaíso", 6: "O'Higgins", 7: "Maule",
    8: "Biobío", 9: "Araucanía", 10: "Los Lagos", 11: "Aysén", 12: "Magallanes", 13: "Metropolitana",
    14: "Los Ríos", 15: "Arica y Parinacota", 16: "Ñuble",
}


class CuotaAgotada(MercadoPublicoError):
    """La API respondió 429: se acabó la cuota del ticket por ahora."""


class ClienteCompraAgil:
    def __init__(
        self,
        ticket: str,
        *,
        http: httpx.Client | None = None,
        pausa: float = 0.5,
        reintentos: int = 2,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        if not ticket:
            raise MercadoPublicoError("Falta el ticket de Mercado Público (MERCADOPUBLICO_TICKET).")
        self._ticket = ticket
        # El detalle demora 20 a 25 s y la pasarela corta a los ~30 s.
        self._http = http or httpx.Client(timeout=35)
        self._pausa = pausa
        self._reintentos = reintentos
        self._dormir = dormir

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        ultimo_error = ""
        for intento in range(self._reintentos + 1):
            if intento:
                self._dormir(self._pausa * 2**intento)
            try:
                r = self._http.get(url, params=params, headers={"ticket": self._ticket})
            except httpx.TransportError as e:
                ultimo_error = f"error de red: {e}"
                continue
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                espera = r.headers.get("retry-after", "")
                if espera.isdigit() and int(espera) <= 30 and intento < self._reintentos:
                    self._dormir(int(espera))
                    continue
                raise CuotaAgotada("Compra Ágil: se alcanzó el límite de consultas del ticket (HTTP 429).")
            if r.status_code >= 500:
                ultimo_error = f"HTTP {r.status_code}"
                continue
            try:
                datos = r.json()
            except ValueError:
                raise MercadoPublicoError(f"Compra Ágil: respuesta no es JSON (HTTP {r.status_code})")
            if r.status_code != 200 or datos.get("success") != "OK":
                errores = datos.get("errors") or [{}]
                raise MercadoPublicoError(f"Compra Ágil: HTTP {r.status_code} {errores[0].get('mensaje', '')}".strip())
            self._dormir(self._pausa)
            return datos.get("payload") or {}
        raise MercadoPublicoError(f"Compra Ágil: sin respuesta válida tras {self._reintentos + 1} intentos ({ultimo_error})")

    def listar(self, **params: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Una página del listado. La API exige al menos un filtro y tamano_pagina entre 10 y 50."""
        payload = self._get(BASE_URL, params) or {}
        return payload.get("items") or [], payload.get("paginacion") or {}

    def publicadas_desde(self, desde: datetime, *, max_paginas: int = 20) -> Iterator[dict[str, Any]]:
        """Compras Ágiles abiertas publicadas desde una hora de Chile, de la más nueva a la más antigua."""
        params = {
            "estado": "publicada", "publicado_desde": desde.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ordenar_por": "FechaPublicacion", "tamano_pagina": 50, "numero_pagina": 1,
        }
        while params["numero_pagina"] <= max_paginas:
            items, paginacion = self.listar(**params)
            yield from items
            if not items or params["numero_pagina"] >= int(paginacion.get("total_paginas") or 1):
                return
            params["numero_pagina"] += 1
        log.warning("Compra Ágil: se alcanzó el máximo de %s páginas; quedan publicaciones sin revisar", max_paginas)

    def detalle(self, codigo: str) -> dict[str, Any] | None:
        return self._get(f"{BASE_URL}/{codigo}")


def _region(institucion: dict[str, Any]) -> str:
    return (institucion.get("nombre_region") or REGIONES.get(institucion.get("region") or 0, "")).strip()


def normalizar_compra_agil(raw: dict[str, Any]) -> dict[str, Any]:
    """Campos de Licitacion a partir de un ítem del listado o del detalle."""
    estado = raw.get("estado") or {}
    estado_codigo, glosa = ESTADOS.get(estado.get("codigo") or "", (None, estado.get("glosa") or ""))
    institucion = raw.get("institucion") or {}
    fechas = raw.get("fechas") or {}
    montos = raw.get("presupuesto") or raw.get("montos") or {}
    convocatoria = raw.get("convocatoria") or {}
    datos = {
        "codigo": raw["codigo"],
        "nombre": (raw.get("nombre") or "").strip(),
        "estado_codigo": estado_codigo,
        "estado": estado.get("glosa") or glosa,
        "tipo": TIPO,
        "organismo": institucion.get("organismo_comprador") or "",
        "unidad": institucion.get("unidad_compra") or "",
        "region": _region(institucion),
        "monto_estimado": parsear_monto(montos.get("monto_disponible_clp") or montos.get("presupuesto_estimado")),
        "moneda": "CLP",
        "fecha_publicacion": parsear_fecha(fechas.get("fecha_publicacion")),
        "fecha_cierre": parsear_fecha(fechas.get("fecha_cierre")),
        "raw": {
            "llamado": convocatoria.get("estado_convocatoria"),
            "ofertas": (raw.get("resumen") or {}).get("total_ofertas_recibidas"),
            "documentos": len(raw.get("documentos") or []),
        },
    }
    if "productos_solicitados" in raw:  # solo viene en el detalle
        entrega = raw.get("entrega") or {}
        datos["descripcion"] = (raw.get("descripcion") or "").strip()
        datos["items"] = [
            {
                "codigo_producto": p.get("codigo_producto"),
                "categoria": "",
                "producto": p.get("nombre") or "",
                "descripcion": (p.get("descripcion") or "").strip(),
                "unidad": p.get("unidad_medida") or "",
                "cantidad": p.get("cantidad"),
            }
            for p in raw.get("productos_solicitados") or []
        ]
        datos["raw"] |= {
            "detalle": True, "plazo_entrega_dias": entrega.get("plazo_entrega_dias"),
            "direccion_entrega": entrega.get("direccion_entrega") or "",
        }
    return datos


def guardar(session: Session, datos: dict[str, Any]) -> tuple[Licitacion, bool]:
    """Crea o actualiza la Compra Ágil. Un listado no borra lo que ya trajo el detalle."""
    lic = session.get(Licitacion, datos["codigo"])
    nueva = lic is None
    if nueva:
        lic = Licitacion(codigo=datos["codigo"], items=[], raw={})
        session.add(lic)
    raw = dict(lic.raw or {}) | datos["raw"]
    for campo, valor in datos.items():
        if campo != "raw":
            setattr(lic, campo, valor)
    lic.raw = raw
    return lic, nueva


def tiene_detalle(lic: Licitacion) -> bool:
    return bool((lic.raw or {}).get("detalle"))


@dataclass
class ResumenCompraAgil:
    nuevas: int = 0
    actualizadas: int = 0
    detalles: int = 0
    pendientes: int = 0  # interesantes que quedaron sin detalle por el máximo
    errores: int = 0


def sincronizar_compras_agiles(
    session: Session,
    cliente: ClienteCompraAgil,
    *,
    interes: Callable[[str], bool],
    momento: datetime | None = None,
    horas_iniciales: int = 6,
    max_detalles: int = 25,
    max_paginas: int = 20,
) -> ResumenCompraAgil:
    """Trae las Compras Ágiles publicadas desde la última vez y el detalle de las que le pueden servir a un cliente.

    `interes(texto)` dice si el nombre comparte palabras clave con algún cliente (ver calce.interes_de_clientes).
    """
    resumen = ResumenCompraAgil()
    ahora_cl = hora_chile(momento)
    ultima = session.scalar(select(func.max(Licitacion.fecha_publicacion)).where(Licitacion.tipo == TIPO))
    # Margen de 30 minutos: una publicación puede aparecer en la API con algo de retraso.
    desde = max(ultima - timedelta(minutes=30), ahora_cl - timedelta(days=2)) if ultima else ahora_cl - timedelta(hours=horas_iniciales)

    por_detallar: list[Licitacion] = []
    for raw in cliente.publicadas_desde(desde, max_paginas=max_paginas):
        try:
            lic, nueva = guardar(session, normalizar_compra_agil(raw))
        except (KeyError, TypeError, ValueError) as e:
            log.warning("Compra Ágil con formato inesperado (%s): %s", raw.get("codigo"), e)
            resumen.errores += 1
            continue
        resumen.nuevas += nueva
        resumen.actualizadas += not nueva
        abierta = lic.fecha_cierre is None or lic.fecha_cierre > ahora_cl
        if abierta and not tiene_detalle(lic) and interes(lic.nombre):
            por_detallar.append(lic)
    session.commit()

    # Primero las que cierran antes: son las más urgentes.
    por_detallar.sort(key=lambda lic: lic.fecha_cierre or datetime.max)
    resumen.pendientes = max(0, len(por_detallar) - max_detalles)
    for lic in por_detallar[:max_detalles]:
        try:
            raw = cliente.detalle(lic.codigo)
        except CuotaAgotada:
            raise
        except MercadoPublicoError as e:
            log.warning("No se pudo obtener el detalle de %s: %s", lic.codigo, e)
            resumen.errores += 1
            continue
        if raw:
            guardar(session, normalizar_compra_agil(raw))
            resumen.detalles += 1
            session.commit()
    return resumen
