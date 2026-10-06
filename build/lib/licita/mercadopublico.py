"""Cliente de la API pública de Mercado Público (ChileCompra).

Documentación oficial: https://api.mercadopublico.cl/modules/api.aspx
Cada consulta requiere un ticket, que se solicita con Clave Única en chilecompra.cl/api.
La API no admite consultas simultáneas con el mismo ticket, por lo que este cliente
hace las peticiones en serie, con una pausa entre ellas y reintentos.
"""

from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any, Callable

import httpx

BASE_URL = "https://api.mercadopublico.cl/servicios/v1/publico"

# Código que devuelve la API cuando detecta peticiones simultáneas con el mismo ticket.
CODIGO_PETICIONES_SIMULTANEAS = 10500

ESTADOS_LICITACION = {
    5: "Publicada",
    6: "Cerrada",
    7: "Desierta",
    8: "Adjudicada",
    18: "Revocada",
    19: "Suspendida",
}


class MercadoPublicoError(Exception):
    pass


class MercadoPublicoClient:
    def __init__(
        self,
        ticket: str,
        *,
        http: httpx.Client | None = None,
        pausa: float = 0.5,
        reintentos: int = 3,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        if not ticket:
            raise MercadoPublicoError("Falta el ticket de Mercado Público (MERCADOPUBLICO_TICKET).")
        self._ticket = ticket
        self._http = http or httpx.Client(timeout=30)
        self._pausa = pausa
        self._reintentos = reintentos
        self._dormir = dormir

    def _get(self, recurso: str, **params: str) -> list[dict[str, Any]]:
        params["ticket"] = self._ticket
        url = f"{BASE_URL}/{recurso}.json"
        ultimo_error = ""
        for intento in range(self._reintentos + 1):
            if intento:
                self._dormir(self._pausa * 2**intento)
            try:
                respuesta = self._http.get(url, params=params)
            except httpx.TransportError as e:
                ultimo_error = f"error de red: {e}"
                continue
            if respuesta.status_code == 429 or respuesta.status_code >= 500:
                ultimo_error = f"HTTP {respuesta.status_code}"
                continue
            try:
                datos = respuesta.json()
            except ValueError:
                raise MercadoPublicoError(f"Respuesta no es JSON (HTTP {respuesta.status_code})")
            if "Listado" in datos:
                self._dormir(self._pausa)
                return datos["Listado"] or []
            codigo = datos.get("Codigo")
            ultimo_error = f"{codigo}: {datos.get('Mensaje', 'error desconocido')}"
            if codigo != CODIGO_PETICIONES_SIMULTANEAS:
                raise MercadoPublicoError(ultimo_error)
        raise MercadoPublicoError(f"Sin respuesta válida tras {self._reintentos + 1} intentos ({ultimo_error})")

    def licitaciones_por_fecha(self, fecha: date) -> list[dict[str, Any]]:
        """Listado resumido de licitaciones de un día (código, nombre, estado, cierre)."""
        return self._get("licitaciones", fecha=fecha.strftime("%d%m%Y"))

    def licitaciones_activas(self) -> list[dict[str, Any]]:
        return self._get("licitaciones", estado="activas")

    def licitacion(self, codigo: str) -> dict[str, Any] | None:
        """Detalle completo de una licitación (comprador, montos, fechas, ítems)."""
        listado = self._get("licitaciones", codigo=codigo)
        return listado[0] if listado else None

    def ordenes_de_compra_por_fecha(self, fecha: date) -> list[dict[str, Any]]:
        return self._get("ordenesdecompra", fecha=fecha.strftime("%d%m%Y"))

    def orden_de_compra(self, codigo: str) -> dict[str, Any] | None:
        listado = self._get("ordenesdecompra", codigo=codigo)
        return listado[0] if listado else None


def parsear_fecha(valor: str | None) -> datetime | None:
    if not valor:
        return None
    try:
        return datetime.fromisoformat(valor.split(".")[0].rstrip("Z"))
    except ValueError:
        return None


def parsear_monto(valor: Any) -> float | None:
    try:
        monto = float(valor)
    except (TypeError, ValueError):
        return None
    return monto if monto > 0 else None


def normalizar_licitacion(raw: dict[str, Any]) -> dict[str, Any]:
    """Convierte el detalle de la API en los campos que guardamos."""
    comprador = raw.get("Comprador") or {}
    fechas = raw.get("Fechas") or {}
    items = (raw.get("Items") or {}).get("Listado") or []
    estado_codigo = raw.get("CodigoEstado")
    return {
        "codigo": raw["CodigoExterno"],
        "nombre": raw.get("Nombre") or "",
        "descripcion": raw.get("Descripcion") or "",
        "estado_codigo": estado_codigo,
        "estado": raw.get("Estado") or ESTADOS_LICITACION.get(estado_codigo, ""),
        "tipo": raw.get("Tipo") or "",
        "organismo": comprador.get("NombreOrganismo") or "",
        "unidad": comprador.get("NombreUnidad") or "",
        "region": (comprador.get("RegionUnidad") or "").strip(),
        "comuna": (comprador.get("ComunaUnidad") or "").strip(),
        "monto_estimado": parsear_monto(raw.get("MontoEstimado")),
        "moneda": raw.get("Moneda") or "",
        "fecha_publicacion": parsear_fecha(fechas.get("FechaPublicacion")),
        "fecha_cierre": parsear_fecha(fechas.get("FechaCierre") or raw.get("FechaCierre")),
        "fecha_adjudicacion": parsear_fecha(fechas.get("FechaAdjudicacion")),
        "items": [
            {
                "codigo_producto": it.get("CodigoProducto"),
                "categoria": it.get("Categoria") or "",
                "producto": it.get("NombreProducto") or "",
                "descripcion": it.get("Descripcion") or "",
                "unidad": it.get("UnidadMedida") or "",
                "cantidad": it.get("Cantidad"),
                "adjudicacion": _normalizar_adjudicacion(it.get("Adjudicacion")),
            }
            for it in items
        ],
    }


def _normalizar_adjudicacion(adj: Any) -> dict[str, Any] | None:
    """Proveedor ganador y precio unitario adjudicado de un ítem (solo en licitaciones adjudicadas)."""
    if not isinstance(adj, dict):
        return None
    precio = parsear_monto(adj.get("MontoUnitario"))
    if precio is None:
        return None
    return {
        "proveedor_rut": adj.get("NumeroDocumento") or adj.get("RutProveedor") or "",
        "proveedor_nombre": adj.get("NombreProveedor") or "",
        "cantidad": adj.get("Cantidad") or adj.get("CantidadAdjudicada"),
        "precio_unitario": precio,
    }


def normalizar_orden_de_compra(raw: dict[str, Any]) -> dict[str, Any]:
    comprador = raw.get("Comprador") or {}
    proveedor = raw.get("Proveedor") or {}
    fechas = raw.get("Fechas") or {}
    items = (raw.get("Items") or {}).get("Listado") or []
    return {
        "codigo": raw["Codigo"],
        "nombre": raw.get("Nombre") or "",
        "estado": raw.get("Estado") or "",
        "codigo_licitacion": raw.get("CodigoLicitacion") or None,
        "organismo": comprador.get("NombreOrganismo") or "",
        "region": (comprador.get("RegionUnidad") or "").strip(),
        "proveedor_rut": proveedor.get("RutSucursal") or "",
        "proveedor_nombre": proveedor.get("Nombre") or "",
        "total": parsear_monto(raw.get("Total")),
        "total_neto": parsear_monto(raw.get("TotalNeto")),
        "moneda": raw.get("TipoMoneda") or raw.get("Moneda") or "",
        "fecha_envio": parsear_fecha(fechas.get("FechaEnvio") or fechas.get("FechaCreacion")),
        "items": [
            {
                "codigo_producto": it.get("CodigoProducto"),
                "categoria": it.get("Categoria") or "",
                "producto": it.get("Producto") or "",
                "especificacion": it.get("EspecificacionComprador") or "",
                "unidad": it.get("UnidadMedida") or it.get("Unidad") or "",
                "cantidad": it.get("Cantidad"),
                "precio_neto": parsear_monto(it.get("PrecioNeto")),
                "total": parsear_monto(it.get("Total")),
            }
            for it in items
        ],
    }
