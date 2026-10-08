"""Inteligencia de precios: cuánto ha pagado el Estado por lo mismo y quién suele ganar.

Fuentes (tabla `precios`, se llena al sincronizar):
- Órdenes de compra: precio unitario neto efectivamente pagado.
- Licitaciones adjudicadas: precio unitario del proveedor ganador.
- Cotizaciones de Compra Ágil: lo que ofrecieron los proveedores (no necesariamente lo que se pagó). Se muestran
  aparte, junto con la cotización más baja de cada proceso, que en Compra Ágil suele ser la que gana.

Se compara por código de producto de ChileCompra (clasificador ONU). Si hay pocos datos,
se usan productos parecidos de la misma familia (mismos 6 primeros dígitos del código).
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .calce import tokens
from .db import Licitacion, OrdenCompra, Precio, ahora
from .resumen import formato_pesos

FUENTE_COTIZACION = "cotizacion_ca"
FUENTES_PAGADAS = ("orden_de_compra", "adjudicacion")
MINIMO_OBSERVACIONES = 3
MESES_HISTORIA = 24
SIMILITUD_MINIMA = 0.5


# --- Carga de precios desde lo sincronizado ---

def _cantidad(valor) -> float | None:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def registrar_precios_orden(session: Session, oc: OrdenCompra) -> int:
    """Agrega los precios de una orden de compra. Ignora órdenes canceladas o en otra moneda."""
    estado = (oc.estado or "").lower()
    if "cancel" in estado or "elimin" in estado or oc.moneda not in ("", "CLP"):
        return 0
    nuevos = 0
    for posicion, it in enumerate(oc.items):
        precio = it.get("precio_neto")
        if not precio or precio <= 0:
            continue
        if session.scalar(select(Precio.id).where(Precio.fuente == "orden_de_compra", Precio.referencia == oc.codigo, Precio.posicion == posicion)):
            continue
        session.add(Precio(
            fuente="orden_de_compra", referencia=oc.codigo, posicion=posicion,
            codigo_producto=str(it.get("codigo_producto") or ""), producto=" ".join(filter(None, [it.get("producto"), it.get("especificacion")])),
            unidad=it.get("unidad") or "", precio_unitario=precio, cantidad=_cantidad(it.get("cantidad")),
            proveedor_rut=oc.proveedor_rut, proveedor_nombre=oc.proveedor_nombre,
            organismo=oc.organismo, region=oc.region, fecha=oc.fecha_envio,
        ))
        nuevos += 1
    return nuevos


def registrar_precios_adjudicacion(session: Session, lic: Licitacion) -> int:
    """Agrega los precios ganadores de una licitación adjudicada (en pesos)."""
    if lic.moneda not in ("", "CLP"):
        return 0
    nuevos = 0
    for posicion, it in enumerate(lic.items):
        adj = it.get("adjudicacion")
        if not adj or not adj.get("precio_unitario"):
            continue
        if session.scalar(select(Precio.id).where(Precio.fuente == "adjudicacion", Precio.referencia == lic.codigo, Precio.posicion == posicion)):
            continue
        session.add(Precio(
            fuente="adjudicacion", referencia=lic.codigo, posicion=posicion,
            codigo_producto=str(it.get("codigo_producto") or ""), producto=" ".join(filter(None, [it.get("producto"), it.get("descripcion")])),
            unidad=it.get("unidad") or "", precio_unitario=adj["precio_unitario"], cantidad=_cantidad(adj.get("cantidad")),
            proveedor_rut=adj.get("proveedor_rut") or "", proveedor_nombre=adj.get("proveedor_nombre") or "",
            organismo=lic.organismo, region=lic.region, fecha=lic.fecha_adjudicacion or lic.fecha_cierre,
        ))
        nuevos += 1
    return nuevos


def registrar_cotizaciones_compra_agil(session: Session, lic: Licitacion, proveedores: list[dict]) -> int:
    """Guarda las cotizaciones admisibles de una Compra Ágil (proveedores_cotizando del detalle).

    Reemplaza las anteriores del mismo proceso: en un segundo llamado la API muestra las cotizaciones nuevas.
    """
    unidades = {str(it.get("codigo_producto") or ""): it.get("unidad") or "" for it in lic.items}
    filas = []
    for prov in proveedores:
        if (prov.get("justificacion_inadmisibilidad") or "").strip() or prov.get("activo") is False:
            continue  # inadmisible o retirada: su precio no compite
        for prod in prov.get("productos_cotizados") or []:
            precio = prod.get("precio_unitario")
            if not isinstance(precio, (int, float)) or precio <= 0:
                continue
            codigo = str(prod.get("codigo_producto") or "")
            filas.append((prov, prod, codigo, float(precio)))
    if not filas:
        return 0
    for anterior in session.scalars(select(Precio).where(Precio.fuente == FUENTE_COTIZACION, Precio.referencia == lic.codigo)):
        session.delete(anterior)
    session.flush()
    for posicion, (prov, prod, codigo, precio) in enumerate(filas):
        session.add(Precio(
            fuente=FUENTE_COTIZACION, referencia=lic.codigo, posicion=posicion, codigo_producto=codigo,
            producto=" ".join(filter(None, [prod.get("nombre_producto"), prod.get("descripcion")])).strip(),
            unidad=unidades.get(codigo, ""), precio_unitario=precio, cantidad=_cantidad(prod.get("cantidad")),
            proveedor_rut=prov.get("rut_proveedor") or "", proveedor_nombre=prov.get("razon_social") or "",
            organismo=lic.organismo, region=lic.region, fecha=lic.fecha_cierre,
        ))
    return len(filas)


def reconstruir_precios(session: Session) -> int:
    """Recorre lo ya sincronizado y llena la tabla de precios (útil la primera vez)."""
    nuevos = sum(registrar_precios_orden(session, oc) for oc in session.scalars(select(OrdenCompra)))
    nuevos += sum(registrar_precios_adjudicacion(session, lic) for lic in session.scalars(select(Licitacion).where(Licitacion.estado_codigo == 8)))
    session.commit()
    return nuevos


# --- Cálculo de referencias ---

@dataclass
class ReferenciaPrecio:
    producto: str
    cantidad: float | None
    unidad: str
    observaciones: int
    mediana: float
    p25: float
    p75: float
    minimo: float
    maximo: float
    en_region: int
    proveedores_frecuentes: list[tuple[str, int]]
    por_similitud: bool  # True si se usaron productos parecidos y no el mismo código

    @property
    def rango_competitivo(self) -> tuple[float, float]:
        """Entre el cuartil inferior y la mediana: competitivo sin regalar margen."""
        return self.p25, self.mediana


@dataclass
class ReferenciaCotizaciones:
    """A cuánto cotizan los proveedores en Compras Ágiles del mismo producto."""

    cotizaciones: int
    procesos: int
    mediana: float
    p25: float
    p75: float
    ganadora_mediana: float  # mediana de la cotización más baja de cada proceso
    ganadora_p25: float
    ganadora_p75: float
    por_similitud: bool


@dataclass
class InformePrecios:
    codigo: str
    nombre: str
    monto_estimado: float | None
    referencias: list[tuple[dict, ReferenciaPrecio | None]] = field(default_factory=list)
    cotizaciones: list[ReferenciaCotizaciones | None] = field(default_factory=list)  # una por ítem, en el mismo orden

    def cotizacion(self, i: int) -> ReferenciaCotizaciones | None:
        return self.cotizaciones[i] if i < len(self.cotizaciones) else None

    @property
    def cobertura(self) -> tuple[int, int]:
        con = sum(1 for i, (_, r) in enumerate(self.referencias) if r or self.cotizacion(i))
        return con, len(self.referencias)

    @property
    def total_mediano(self) -> float | None:
        """Costo de la licitación a precio mediano, si todos los ítems tienen referencia y cantidad."""
        if not self.referencias or any(r is None or not r.cantidad for _, r in self.referencias):
            return None
        return sum(r.mediana * r.cantidad for _, r in self.referencias)


def _cuartiles(valores: list[float]) -> tuple[float, float, float]:
    if len(valores) == 1:
        return valores[0], valores[0], valores[0]
    q1, q2, q3 = statistics.quantiles(valores, n=4, method="inclusive")
    return q1, q2, q3


def _sin_atipicos(valores: list[float]) -> list[float]:
    """Quita precios atípicos (regla de 1,5 × rango intercuartil), por ejemplo cajas contra unidades."""
    if len(valores) < 4:
        return valores
    q1, _, q3 = _cuartiles(valores)
    rango = q3 - q1
    filtrados = [v for v in valores if q1 - 1.5 * rango <= v <= q3 + 1.5 * rango]
    return filtrados or valores


def _observaciones(
    session: Session, item: dict, desde: datetime, fuentes: tuple[str, ...] = FUENTES_PAGADAS
) -> tuple[list[Precio], bool]:
    codigo = str(item.get("codigo_producto") or "")
    base = select(Precio).where((Precio.fecha.is_(None)) | (Precio.fecha >= desde), Precio.fuente.in_(fuentes))
    if codigo:
        exactas = list(session.scalars(base.where(Precio.codigo_producto == codigo)))
        if len(exactas) >= MINIMO_OBSERVACIONES:
            return exactas, False
    # Productos parecidos de la misma familia del clasificador.
    texto = tokens(f"{item.get('producto', '')} {item.get('descripcion', '')}")
    if not texto:
        return [], True
    candidatas = session.scalars(base.where(Precio.codigo_producto.startswith(codigo[:6]))) if len(codigo) >= 6 else session.scalars(base)
    parecidas = [p for p in candidatas if texto and len(texto & tokens(p.producto)) / len(texto) >= SIMILITUD_MINIMA]
    return parecidas, True


def referencia_item(
    session: Session, item: dict, *, region: str = "", momento: datetime | None = None, meses: int = MESES_HISTORIA
) -> ReferenciaPrecio | None:
    momento = momento or ahora()
    obs, por_similitud = _observaciones(session, item, momento - timedelta(days=30 * meses))
    if len(obs) < MINIMO_OBSERVACIONES:
        return None
    valores = sorted(_sin_atipicos([p.precio_unitario for p in obs]))
    p25, mediana, p75 = _cuartiles(valores)
    proveedores = Counter(p.proveedor_nombre for p in obs if p.proveedor_nombre and valores[0] <= p.precio_unitario <= valores[-1])
    return ReferenciaPrecio(
        producto=item.get("producto") or item.get("categoria") or "",
        cantidad=_cantidad(item.get("cantidad")),
        unidad=item.get("unidad") or "",
        observaciones=len(valores),
        mediana=mediana, p25=p25, p75=p75, minimo=valores[0], maximo=valores[-1],
        en_region=sum(1 for p in obs if region and p.region == region),
        proveedores_frecuentes=proveedores.most_common(3),
        por_similitud=por_similitud,
    )


def referencia_cotizaciones(
    session: Session, item: dict, *, momento: datetime | None = None, meses: int = MESES_HISTORIA
) -> ReferenciaCotizaciones | None:
    momento = momento or ahora()
    obs, por_similitud = _observaciones(session, item, momento - timedelta(days=30 * meses), (FUENTE_COTIZACION,))
    if len(obs) < MINIMO_OBSERVACIONES:
        return None
    validos = set(_sin_atipicos([p.precio_unitario for p in obs]))
    obs = [p for p in obs if p.precio_unitario in validos]
    mas_bajas: dict[str, float] = {}
    for p in obs:
        mas_bajas[p.referencia] = min(p.precio_unitario, mas_bajas.get(p.referencia, p.precio_unitario))
    if len(mas_bajas) < 2:
        return None  # un solo proceso no alcanza para hablar de "lo habitual"
    p25, mediana, p75 = _cuartiles(sorted(p.precio_unitario for p in obs))
    g25, gmediana, g75 = _cuartiles(sorted(mas_bajas.values()))
    return ReferenciaCotizaciones(
        cotizaciones=len(obs), procesos=len(mas_bajas), mediana=mediana, p25=p25, p75=p75,
        ganadora_mediana=gmediana, ganadora_p25=g25, ganadora_p75=g75, por_similitud=por_similitud,
    )


def informe_precios(session: Session, lic: Licitacion, *, momento: datetime | None = None) -> InformePrecios:
    informe = InformePrecios(codigo=lic.codigo, nombre=lic.nombre, monto_estimado=lic.monto_estimado)
    for item in lic.items:
        informe.referencias.append((item, referencia_item(session, item, region=lic.region, momento=momento)))
        informe.cotizaciones.append(referencia_cotizaciones(session, item, momento=momento))
    return informe


def texto_precios(informe: InformePrecios, *, maximo_items: int = 8) -> str:
    con, total = informe.cobertura
    lineas = [f"💲 *Precios de referencia: {informe.nombre}*", f"Compras públicas de los últimos {MESES_HISTORIA} meses.", ""]
    if any(informe.cotizaciones):
        lineas[1] = f"Compras públicas y cotizaciones de Compra Ágil de los últimos {MESES_HISTORIA} meses."
    if con == 0:
        lineas.append("Todavía no tengo suficientes compras anteriores de estos productos para darte una referencia confiable.")
        return "\n".join(lineas)
    mostrados = 0
    for i, (item, ref) in enumerate(informe.referencias):
        cot = informe.cotizacion(i)
        if (ref is None and cot is None) or mostrados >= maximo_items:
            continue
        mostrados += 1
        cantidad = _cantidad(item.get("cantidad"))
        producto = item.get("producto") or item.get("categoria") or ""
        lineas.append(f"{mostrados}. *{producto}*" + (f" × {cantidad:g}" if cantidad else ""))
        if ref:
            bajo, alto = ref.rango_competitivo
            lineas += [
                f"   Mediana: {formato_pesos(ref.mediana)} c/u · habitual {formato_pesos(ref.p25)} – {formato_pesos(ref.p75)} ({ref.observaciones} compras)",
                f"   👉 Precio competitivo: {formato_pesos(bajo)} – {formato_pesos(alto)}",
            ]
            if ref.proveedores_frecuentes:
                lineas.append("   Suelen ganar: " + ", ".join(f"{n} ({c})" for n, c in ref.proveedores_frecuentes))
        if cot:
            lineas += [
                f"   ⚡ En Compra Ágil cotizan {formato_pesos(cot.p25)} – {formato_pesos(cot.p75)} c/u "
                f"({cot.cotizaciones} cotizaciones en {cot.procesos} procesos)",
                f"   🏁 La más baja de cada proceso suele ser {formato_pesos(cot.ganadora_p25)} – {formato_pesos(cot.ganadora_p75)} "
                f"(mediana {formato_pesos(cot.ganadora_mediana)})",
            ]
        if (ref and ref.por_similitud) or (cot and cot.por_similitud):
            lineas.append("   (Referencia con productos parecidos: revisa que sean comparables)")
        lineas.append("")
    if con < total:
        lineas.append(f"Sin referencia suficiente para {total - con} de {total} ítems.")
    if informe.total_mediano is not None:
        lineas.append(f"📦 Total a precio mediano: {formato_pesos(informe.total_mediano)}")
        if informe.monto_estimado:
            holgura = informe.monto_estimado / informe.total_mediano - 1
            if holgura >= 0.15:
                lineas.append(f"El presupuesto ({formato_pesos(informe.monto_estimado)}) está {holgura:.0%} sobre el precio de mercado: hay espacio.")
            elif holgura >= -0.05:
                lineas.append(f"El presupuesto ({formato_pesos(informe.monto_estimado)}) está cerca del precio de mercado: compite con precio ajustado.")
            else:
                lineas.append(f"Ojo: el presupuesto ({formato_pesos(informe.monto_estimado)}) está {-holgura:.0%} bajo el precio de mercado.")
    lineas += ["", "Recuerda que el precio es solo uno de los criterios: revisa cuánto pondera en las bases."]
    return "\n".join(lineas)
