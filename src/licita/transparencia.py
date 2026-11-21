"""Cómo compra cada organismo y cuánto paga frente al resto del Estado.

Todo sale de datos públicos de Mercado Público y se presenta como cifras, nunca como juicio: un precio alto puede
tener razones válidas (zona extrema, urgencia, calidad distinta). Para clientes, un organismo que paga mucho más que el
resto es una oportunidad: si ofertan a precio de mercado, compiten bien.

- Precios: mediana de lo que pagó el organismo por un producto frente a la mediana del resto del Estado, en la misma
  unidad de medida y sin atípicos. Solo si hay compras suficientes y el precio del producto no es muy disperso.
- Cómo compra: de cada $100 comprados, cuánto fue por licitación, Compra Ágil, Convenio Marco o trato directo.
- Competencia: cuánto se lleva su principal proveedor y cuántas licitaciones adjudicadas tuvieron un solo oferente.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .calce import normalizar
from .db import Licitacion, Precio, ahora
from .precios import DISPERSION_MAXIMA, FUENTES_PAGADAS, MESES_HISTORIA, _cuartiles, _sin_atipicos

MINIMO_MERCADO = 10  # compras del resto del Estado para tener una referencia
MINIMO_ORGANISMO = 2  # compras del organismo
UMBRAL_OPORTUNIDAD = 1.8  # paga 1,8 veces o más que el resto: se le avisa al cliente
PRODUCTOS_EN_PAGINA = 6
NOMBRES_ORIGEN = {"licitacion": "Licitación", "compra_agil": "Compra Ágil", "convenio_marco": "Convenio Marco",
                  "trato_directo": "Trato directo", "otro": "Otras modalidades"}
ESTADO_ADJUDICADA = 8


def _unidad(texto: str) -> str:
    return " ".join(normalizar(texto or "").split())


def _desde(momento: datetime | None) -> datetime:
    return (momento or ahora()) - timedelta(days=30 * MESES_HISTORIA)


@dataclass
class PrecioComparado:
    codigo_producto: str
    producto: str
    unidad: str
    mediana_organismo: float
    mediana_estado: float
    compras_organismo: int
    compras_estado: int

    @property
    def veces(self) -> float:
        return self.mediana_organismo / self.mediana_estado

    @property
    def diferencia(self) -> str:
        """"+180%", "-25%", "igual"."""
        pct = round((self.veces - 1) * 100)
        return "igual" if abs(pct) < 5 else f"{pct:+d}%"


def comparar_precio(session: Session, organismo: str, codigo: str, *, momento: datetime | None = None,
                    filas_organismo: list[Precio] | None = None) -> PrecioComparado | None:
    """Lo que pagó el organismo por el producto frente al resto del Estado, en su unidad de medida más usada."""
    desde = _desde(momento)
    clave_org = normalizar(organismo).strip()
    todas = list(session.scalars(select(Precio).where(
        Precio.codigo_producto == codigo, Precio.fuente.in_(FUENTES_PAGADAS), Precio.fecha >= desde)))
    propias = filas_organismo if filas_organismo is not None else [
        p for p in todas if normalizar(p.organismo or "").strip() == clave_org]
    if len(propias) < MINIMO_ORGANISMO:
        return None
    unidad = Counter(_unidad(p.unidad) for p in propias).most_common(1)[0][0]
    propias = [p for p in propias if _unidad(p.unidad) == unidad]
    resto = [p.precio_unitario for p in todas
             if _unidad(p.unidad) == unidad and normalizar(p.organismo or "").strip() != clave_org]
    if len(propias) < MINIMO_ORGANISMO or len(resto) < MINIMO_MERCADO:
        return None
    resto = sorted(_sin_atipicos(resto))
    p25, mediana_estado, p75 = _cuartiles(resto)
    if mediana_estado <= 0 or (p25 > 0 and p75 / p25 > DISPERSION_MAXIMA):
        return None  # el código mezcla cosas de tamaños muy distintos: no hay referencia honesta
    nombre = min((" ".join((p.producto or "").split()[:8]) for p in propias if p.producto), key=len, default=codigo)
    return PrecioComparado(codigo, nombre, unidad, statistics.median(p.precio_unitario for p in propias),
                           mediana_estado, len(propias), len(resto))


def pago_caro(session: Session, lic: Licitacion, momento: datetime | None = None) -> PrecioComparado | None:
    """El producto de la licitación en que el organismo más ha pagado sobre el resto del Estado (si es 1,8x o más)."""
    if not lic.organismo:
        return None
    mejor = None
    for codigo in {str(it.get("codigo_producto")) for it in lic.items or [] if it.get("codigo_producto")}:
        comparado = comparar_precio(session, lic.organismo, codigo, momento=momento)
        if comparado and comparado.veces >= UMBRAL_OPORTUNIDAD and (mejor is None or comparado.veces > mejor.veces):
            mejor = comparado
    return mejor


@dataclass
class IndicadoresOrganismo:
    como_compra: list[tuple[str, int]]  # (modalidad, % del monto)
    monto_total: float
    proveedor_principal: tuple[str, int] | None  # (nombre, % del monto)
    un_oferente: tuple[int, int] | None  # (licitaciones con un solo oferente, adjudicadas con el dato)
    precios: list[PrecioComparado] = field(default_factory=list)

    @property
    def vacio(self) -> bool:
        return not (self.como_compra or self.un_oferente or self.precios)


def indicadores_organismo(session: Session, organismo: str, momento: datetime | None = None) -> IndicadoresOrganismo:
    desde = _desde(momento)
    filas = list(session.scalars(select(Precio).where(
        Precio.organismo == organismo, Precio.fuente.in_(FUENTES_PAGADAS), Precio.fecha >= desde)))
    por_origen, por_proveedor, por_codigo = Counter(), Counter(), defaultdict(list)
    monto_codigo = Counter()
    for p in filas:
        monto = p.precio_unitario * (p.cantidad or 1)
        if p.origen:
            por_origen[p.origen] += monto
        if p.proveedor_nombre:
            por_proveedor[p.proveedor_nombre.strip()] += monto
        if p.codigo_producto:
            por_codigo[p.codigo_producto].append(p)
            monto_codigo[p.codigo_producto] += monto
    total_origen = sum(por_origen.values())
    como_compra = [(NOMBRES_ORIGEN.get(o, o), round(100 * m / total_origen))
                   for o, m in por_origen.most_common()] if total_origen else []
    total = sum(por_proveedor.values())
    principal = None
    if total and len(por_proveedor) >= 2:
        nombre, monto = por_proveedor.most_common(1)[0]
        principal = (nombre, round(100 * monto / total))
    oferentes = []
    for (raw,) in session.execute(select(Licitacion.raw).where(
            Licitacion.organismo == organismo, Licitacion.estado_codigo == ESTADO_ADJUDICADA)):
        numero = ((raw or {}).get("Adjudicacion") or {}).get("NumeroOferentes")
        if isinstance(numero, int) and numero > 0:
            oferentes.append(numero)
    un_oferente = (sum(1 for n in oferentes if n == 1), len(oferentes)) if len(oferentes) >= 3 else None
    precios = []
    for codigo, _ in monto_codigo.most_common(PRODUCTOS_EN_PAGINA * 3):
        comparado = comparar_precio(session, organismo, codigo, momento=momento, filas_organismo=por_codigo[codigo])
        if comparado:
            precios.append(comparado)
        if len(precios) == PRODUCTOS_EN_PAGINA:
            break
    return IndicadoresOrganismo(como_compra, total_origen or total, principal, un_oferente, precios)
