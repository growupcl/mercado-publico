"""Carga del histórico de órdenes de compra desde los datos abiertos de ChileCompra.

ChileCompra publica un archivo por mes en https://transparenciachc.blob.core.windows.net/oc-da/AAAA-M.zip
(mes sin cero inicial). Trae un CSV con una fila por línea de producto (varias por orden de compra), separado por
punto y coma, en Latin-1 y con saltos de línea dentro de campos entre comillas. El archivo de un mes se sigue
completando por algunas semanas, por eso conviene volver a cargar el mes anterior.

Solo se guardan los precios (tabla `precios`, fuente "orden_de_compra"); por volumen (~1,5 millones de líneas al mes)
no se guardan las órdenes completas. Por defecto se filtran las líneas que comparten palabras clave con algún cliente:
cuando entra un cliente de un rubro nuevo, se vuelve a correr la carga y se agregan solo sus productos.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterator

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Precio
from .mercadopublico import MercadoPublicoError

log = logging.getLogger(__name__)

URL_MES = "https://transparenciachc.blob.core.windows.net/oc-da/{anio}-{mes}.zip"
FUENTE = "orden_de_compra"
LOTE = 2000  # órdenes por transacción
ESTADOS_ANULADOS = {"9"}  # Cancelada

# ChileCompra ha cambiado mayúsculas y tiene errores de tipeo históricos en los encabezados: se prueban variantes.
COLUMNAS = {
    "codigo": ("Codigo", "CodigoOC"),
    "estado": ("Estado", "EstadoOC"),
    "codigo_estado": ("codigoEstado", "CodigoEstado"),
    "codigo_producto": ("codigoProductoONU", "CodigoProductoONU", "CodigoProducto"),
    "producto": ("NombreroductoGenerico", "NombreProductoGenerico", "Producto"),
    "especificacion": ("EspecificacionComprador", "EspecificacionProveedor"),
    "unidad": ("UnidadMedida", "unidadMedida"),
    "cantidad": ("cantidad", "Cantidad"),
    "precio": ("precioNeto", "PrecioNeto"),
    "moneda": ("monedaItem", "TipoMonedaOC", "Moneda"),
    "proveedor_rut": ("RutSucursal", "RutProveedor"),
    "proveedor_nombre": ("NombreProveedor", "Sucursal"),
    "organismo": ("OrganismoPublico", "NombreOrganismo"),
    "region": ("RegionUnidadCompra", "RegionUnidad", "Region"),
    "fecha": ("FechaEnvio", "FechaCreacion"),
    "nombre": ("Nombre",),
    "rubro": ("RubroN3", "RubroN2", "RubroN1", "Categoria"),
}


def numero(valor: str | None) -> float | None:
    """Número en formato chileno o con punto decimal: "1.234,5", "1234,5", "1234.5", "1.234"."""
    s = re.sub(r"[^0-9,.\-]", "", valor or "")
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")  # solo separadores de miles
    try:
        return float(s)
    except ValueError:
        return None


def fecha(valor: str | None) -> datetime | None:
    s = (valor or "").strip()
    for patron, formato in ((r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", "%Y-%m-%d %H:%M:%S"),
                            (r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}", "%Y-%m-%d %H:%M"),
                            (r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"),
                            (r"\d{2}[/-]\d{2}[/-]\d{4}", "%d-%m-%Y")):
        if m := re.match(patron, s):
            try:
                return datetime.strptime(m.group(0).replace("T", " ").replace("/", "-"), formato)
            except ValueError:
                return None
    return None


def moneda_pesos(valor: str | None) -> bool:
    v = (valor or "").strip().lower()
    return v in ("", "clp", "$") or "peso" in v


@dataclass
class ResumenHistorico:
    lineas: int = 0
    ordenes: int = 0
    guardados: int = 0
    ya_estaban: int = 0
    filtrados: int = 0  # sin relación con los clientes
    descartados: int = 0  # canceladas, otra moneda o sin precio

    def sumar(self, otro: "ResumenHistorico") -> None:
        for campo in self.__dataclass_fields__:
            setattr(self, campo, getattr(self, campo) + getattr(otro, campo))


def leer_csv(texto: io.TextIOBase) -> Iterator[dict[str, str]]:
    """Filas del CSV como diccionarios con los nombres normalizados de COLUMNAS."""
    csv.field_size_limit(10_000_000)
    lector = csv.reader(texto, delimiter=";", quotechar='"')
    encabezado = [c.strip().lstrip("﻿") for c in next(lector, [])]
    indices = {}
    for nombre, variantes in COLUMNAS.items():
        indices[nombre] = [encabezado.index(v) for v in variantes if v in encabezado]
    faltan = [n for n in ("codigo", "precio") if not indices[n]]
    if faltan:
        raise MercadoPublicoError(f"El archivo no tiene las columnas esperadas ({', '.join(faltan)}). Encabezado: {encabezado[:12]}…")
    for fila in lector:
        if not fila:
            continue
        datos = {}
        for nombre, posiciones in indices.items():
            datos[nombre] = next((fila[i].strip() for i in posiciones if i < len(fila) and fila[i].strip()), "")
        yield datos


def abrir_archivo(ruta: Path) -> Iterator[io.TextIOBase]:
    """Abre el CSV (o los CSV dentro del .zip) en Latin-1."""
    if ruta.suffix.lower() == ".zip":
        with zipfile.ZipFile(ruta) as z:
            for nombre in sorted(n for n in z.namelist() if n.lower().endswith(".csv")):
                with z.open(nombre) as crudo:
                    yield io.TextIOWrapper(crudo, encoding="latin-1", newline="")
    else:
        with open(ruta, encoding="latin-1", newline="") as f:
            yield f


def _guardar_lote(session: Session, lote: list[Precio], resumen: ResumenHistorico) -> None:
    if not lote:
        return
    codigos = {p.referencia for p in lote}
    existentes = set(session.execute(
        select(Precio.referencia, Precio.posicion).where(Precio.fuente == FUENTE, Precio.referencia.in_(codigos))
    ).all())
    nuevos = [p for p in lote if (p.referencia, p.posicion) not in existentes]
    resumen.ya_estaban += len(lote) - len(nuevos)
    session.add_all(nuevos)
    session.commit()
    resumen.guardados += len(nuevos)


def importar_csv(
    session: Session, texto: io.TextIOBase, *, interes: Callable[[str], bool] | None = None
) -> ResumenHistorico:
    """Guarda los precios de un CSV de órdenes de compra. Es idempotente: volver a cargarlo no duplica."""
    resumen = ResumenHistorico()
    posiciones: dict[str, int] = {}  # la posición cuenta todas las líneas de la orden, guardadas o no
    lote: list[Precio] = []
    ordenes_en_lote: set[str] = set()
    for fila in leer_csv(texto):
        codigo = fila["codigo"]
        if not codigo:
            continue
        resumen.lineas += 1
        posicion = posiciones.get(codigo, 0)
        posiciones[codigo] = posicion + 1
        if posicion == 0:
            resumen.ordenes += 1
        precio = numero(fila["precio"])
        anulada = fila["codigo_estado"] in ESTADOS_ANULADOS or re.search(r"cancel|elimin|anulad", fila["estado"], re.I)
        if anulada or not moneda_pesos(fila["moneda"]) or not precio or precio <= 0:
            resumen.descartados += 1
            continue
        producto = " ".join(filter(None, [fila["producto"], fila["especificacion"]]))
        if interes is not None and not interes(" ".join([producto, fila["nombre"], fila["rubro"]])):
            resumen.filtrados += 1
            continue
        if codigo not in ordenes_en_lote and len(ordenes_en_lote) >= LOTE:
            _guardar_lote(session, lote, resumen)
            lote, ordenes_en_lote = [], set()
        ordenes_en_lote.add(codigo)
        lote.append(Precio(
            fuente=FUENTE, referencia=codigo, posicion=posicion, codigo_producto=fila["codigo_producto"].split(".")[0],
            producto=producto[:2000], unidad=fila["unidad"][:60], precio_unitario=precio, cantidad=numero(fila["cantidad"]),
            proveedor_rut=fila["proveedor_rut"][:20], proveedor_nombre=fila["proveedor_nombre"], organismo=fila["organismo"],
            region=fila["region"][:120], fecha=fecha(fila["fecha"]),
        ))
    _guardar_lote(session, lote, resumen)
    return resumen


def importar_archivo(session: Session, ruta: Path, *, interes: Callable[[str], bool] | None = None) -> ResumenHistorico:
    total = ResumenHistorico()
    for texto in abrir_archivo(ruta):
        total.sumar(importar_csv(session, texto, interes=interes))
    return total


def meses_hacia_atras(hasta: date, cantidad: int) -> list[tuple[int, int]]:
    """Los últimos `cantidad` meses hasta `hasta` inclusive, del más antiguo al más reciente."""
    anio, mes = hasta.year, hasta.month
    meses = []
    for _ in range(cantidad):
        meses.append((anio, mes))
        anio, mes = (anio, mes - 1) if mes > 1 else (anio - 1, 12)
    return list(reversed(meses))


def descargar_mes(anio: int, mes: int, destino: Path, *, http: httpx.Client | None = None) -> Path | None:
    """Descarga el .zip del mes. None si ChileCompra aún no lo publica."""
    http = http or httpx.Client(timeout=httpx.Timeout(60, read=300), follow_redirects=True)
    url = URL_MES.format(anio=anio, mes=mes)
    ruta = destino / f"oc-{anio}-{mes}.zip"
    try:
        with http.stream("GET", url) as r:
            if r.status_code == 404:
                return None
            if r.status_code != 200:
                raise MercadoPublicoError(f"No se pudo descargar {url} (HTTP {r.status_code})")
            with open(ruta, "wb") as f:
                for parte in r.iter_bytes(1 << 20):
                    f.write(parte)
    except httpx.TransportError as e:
        raise MercadoPublicoError(f"No se pudo descargar {url}: {e}") from e
    return ruta


def importar_meses(
    session: Session,
    meses: list[tuple[int, int]],
    *,
    interes: Callable[[str], bool] | None = None,
    http: httpx.Client | None = None,
    al_terminar_mes: Callable[[int, int, ResumenHistorico | None], Any] | None = None,
) -> ResumenHistorico:
    """Descarga e importa cada mes, uno a la vez, borrando el archivo al terminar."""
    total = ResumenHistorico()
    carpeta = Path(tempfile.mkdtemp(prefix="calza-oc-"))
    try:
        for anio, mes in meses:
            ruta = descargar_mes(anio, mes, carpeta, http=http)
            resumen = importar_archivo(session, ruta, interes=interes) if ruta else None
            if ruta:
                ruta.unlink()
                total.sumar(resumen)
            if al_terminar_mes:
                al_terminar_mes(anio, mes, resumen)
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)
    return total
