"""Páginas públicas para buscadores e IA: licitaciones abiertas por región, por rubro y por código.

Todo sale de la base de Calza (datos públicos de la API de Mercado Público), sin IA ni consultas externas por visita.
Las licitaciones abiertas se cargan una vez cada pocos minutos como fichas livianas (sin el JSON crudo), con las
raíces de sus palabras ya calculadas: listar, filtrar y buscar no vuelve a tocar la base en cada visita.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from .calce import _compacto, _raiz, _texto_licitacion, normalizar, tokens
from .db import Licitacion, hora_chile
from .resumen import URL_FICHA, URL_FICHA_COMPRA_AGIL
from .suscripciones import REGIONES

ESTADO_PUBLICADA = 5
POR_PAGINA = 30
MAX_FRASES_BUSQUEDA = 12


def slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalizar(texto).replace("'", "")).strip("-")


def raices(texto: str) -> frozenset[str]:
    return frozenset(_raiz(t) for t in tokens(texto))


@dataclass(frozen=True)
class Ficha:
    """Lo que muestran las páginas públicas de una licitación o Compra Ágil (sin el JSON crudo de la API)."""

    codigo: str
    nombre: str = ""
    descripcion: str = ""
    tipo: str = ""
    estado_codigo: int | None = None
    organismo: str = ""
    region: str = ""
    comuna: str = ""
    monto_estimado: float | None = None
    moneda: str = ""
    fecha_publicacion: datetime | None = None
    fecha_cierre: datetime | None = None
    items: tuple[dict[str, Any], ...] = ()
    clasificacion: dict[str, Any] | None = None
    actualizada_en: datetime | None = None
    raices: frozenset[str] = field(default=frozenset(), compare=False, repr=False)

    @classmethod
    def de(cls, lic: Licitacion) -> Ficha:
        return cls(
            codigo=lic.codigo, nombre=lic.nombre or "", descripcion=lic.descripcion or "", tipo=lic.tipo or "",
            estado_codigo=lic.estado_codigo, organismo=lic.organismo or "", region=lic.region or "",
            comuna=lic.comuna or "", monto_estimado=lic.monto_estimado, moneda=lic.moneda or "",
            fecha_publicacion=lic.fecha_publicacion, fecha_cierre=lic.fecha_cierre, items=tuple(lic.items or ()),
            clasificacion=lic.clasificacion, actualizada_en=lic.actualizada_en, raices=raices(_texto_licitacion(lic)),
        )

    @property
    def es_compra_agil(self) -> bool:
        return self.tipo == "COT"

    @property
    def url_oficial(self) -> str:
        return (URL_FICHA_COMPRA_AGIL if self.es_compra_agil else URL_FICHA).format(codigo=self.codigo)

    @property
    def resumen(self) -> str:
        return ((self.clasificacion or {}).get("resumen") or "").strip()

    def descripcion_corta(self, largo: int = 155) -> str:
        texto = re.sub(r"\s+", " ", self.resumen or self.descripcion or self.nombre).strip()
        return texto if len(texto) <= largo else texto[: largo - 1].rstrip() + "…"

    def abierta(self, momento: datetime | None = None) -> bool:
        return self.estado_codigo == ESTADO_PUBLICADA and (self.fecha_cierre is None or self.fecha_cierre > hora_chile(momento))

    def dias_para_cierre(self, momento: datetime | None = None) -> int | None:
        if self.fecha_cierre is None:
            return None
        return (self.fecha_cierre.date() - hora_chile(momento).date()).days


NOMBRES_REGION = {
    "Metropolitana": "Región Metropolitana", "Biobío": "Región del Biobío", "Maule": "Región del Maule",
    "Araucanía": "Región de La Araucanía", "O'Higgins": "Región de O'Higgins", "Ñuble": "Región de Ñuble",
}


@dataclass(frozen=True)
class Region:
    nombre: str  # como en el formulario de registro: "Biobío"
    slug: str

    @property
    def titulo(self) -> str:
        return NOMBRES_REGION.get(self.nombre, f"Región de {self.nombre}")

    def contiene(self, ficha: Ficha) -> bool:
        return bool(ficha.region) and _compacto(self.nombre) in _compacto(ficha.region)


REGIONES_PUBLICAS = [Region(r, slug(r)) for r in REGIONES]
REGION_POR_SLUG = {r.slug: r for r in REGIONES_PUBLICAS}


@dataclass(frozen=True)
class Rubro:
    slug: str
    nombre: str
    frases: tuple[str, ...]  # basta que una aparezca completa en la licitación

    def __post_init__(self):
        object.__setattr__(self, "_raices", [r for r in map(raices, self.frases) if r])

    def contiene(self, ficha: Ficha) -> bool:
        return any(r <= ficha.raices for r in self._raices)


RUBROS = [
    Rubro("aseo", "Aseo y limpieza", ("aseo", "limpieza", "detergente", "desinfectante", "papel higiénico", "bolsas de basura")),
    Rubro("construccion", "Construcción y obras", ("construcción", "obras civiles", "pavimentación", "ferretería", "materiales de construcción")),
    Rubro("computacion", "Computación y tecnología", ("computador", "notebook", "software", "impresora", "tóner", "informática", "licencias")),
    Rubro("alimentos", "Alimentos y alimentación", ("alimentos", "alimentación", "colaciones", "abarrotes", "catering", "frutas", "verduras")),
    Rubro("salud", "Insumos médicos y salud", ("insumos médicos", "medicamentos", "insumos clínicos", "quirúrgico", "guantes", "mascarillas", "dental")),
    Rubro("oficina", "Artículos de oficina y escolares", ("artículos de oficina", "papelería", "resmas", "útiles escolares", "útiles de oficina")),
    Rubro("mobiliario", "Mobiliario", ("mobiliario", "sillas", "escritorios", "muebles")),
    Rubro("vestuario", "Vestuario, uniformes y EPP", ("vestuario", "uniformes", "calzado", "ropa de trabajo", "elementos de protección personal")),
    Rubro("vehiculos", "Vehículos, repuestos y combustible", ("vehículos", "repuestos", "neumáticos", "combustible", "lubricantes")),
    Rubro("seguridad", "Seguridad y vigilancia", ("vigilancia", "guardias", "cámaras de seguridad", "alarmas", "seguridad privada")),
    Rubro("mantencion", "Mantención y servicios generales", ("mantención", "mantenimiento", "gasfitería", "climatización", "jardinería")),
    Rubro("transporte", "Transporte y fletes", ("transporte", "traslado", "flete", "buses")),
    Rubro("eventos", "Eventos y producción", ("eventos", "amplificación", "escenario", "banquetería")),
    Rubro("capacitacion", "Capacitación y consultoría", ("capacitación", "curso", "consultoría", "asesoría")),
    Rubro("publicidad", "Publicidad, impresión y señalética", ("publicidad", "impresión", "imprenta", "señalética", "diseño gráfico")),
]
RUBRO_POR_SLUG = {r.slug: r for r in RUBROS}


def cargar_abiertas(session: Session, momento: datetime | None = None) -> list[Ficha]:
    """Licitaciones y Compras Ágiles abiertas, de la más nueva a la más antigua."""
    ahora_cl = hora_chile(momento)
    columnas = [getattr(Licitacion, c) for c in (
        "codigo", "nombre", "descripcion", "tipo", "estado_codigo", "organismo", "region", "comuna", "monto_estimado",
        "moneda", "fecha_publicacion", "fecha_cierre", "items", "clasificacion", "actualizada_en",
    )]
    consulta = (
        select(Licitacion).options(load_only(*columnas))
        .where(
            Licitacion.estado_codigo == ESTADO_PUBLICADA,
            (Licitacion.fecha_cierre.is_(None)) | (Licitacion.fecha_cierre > ahora_cl),
        )
        .order_by(Licitacion.fecha_publicacion.desc().nulls_last(), Licitacion.codigo)
        .execution_options(yield_per=500)
    )
    return [Ficha.de(lic) for lic in session.scalars(consulta)]


def cargar_ficha(session: Session, codigo: str) -> Ficha | None:
    lic = session.get(Licitacion, codigo)
    return Ficha.de(lic) if lic is not None else None


@dataclass
class Estadisticas:
    abiertas: int = 0
    compras_agiles: int = 0
    monto_abierto: float = 0
    publicadas_hoy: int = 0
    cierran_hoy: int = 0


def estadisticas(fichas: Iterable[Ficha], momento: datetime | None = None) -> Estadisticas:
    hoy = hora_chile(momento).date()
    e = Estadisticas()
    for f in fichas:
        e.abiertas += 1
        e.compras_agiles += f.es_compra_agil
        if (f.moneda or "CLP") == "CLP":
            e.monto_abierto += f.monto_estimado or 0
        e.publicadas_hoy += bool(f.fecha_publicacion and f.fecha_publicacion.date() == hoy)
        e.cierran_hoy += bool(f.fecha_cierre and f.fecha_cierre.date() == hoy)
    return e


def frases_busqueda(consulta: str) -> list[str]:
    partes = re.split(r"[,;/\n]|\s+y\s+|\s+o\s+", consulta[:300])
    return [p.strip() for p in partes if p.strip()][:MAX_FRASES_BUSQUEDA]


def buscar(fichas: Iterable[Ficha], consulta: str, region: Region | None = None, *, limite: int = 10) -> list[tuple[Ficha, float]]:
    """Demo sin registro: las abiertas que mejor calzan con lo que la persona dice vender.

    Mismo criterio que el prefiltro de calce.puntaje_prefiltro (frases completas valen 1, parciales 0,25), sin IA.
    """
    frases = [r for r in map(raices, frases_busqueda(consulta)) if r]
    if not frases:
        return []
    resultado = []
    for f in fichas:
        if region is not None and f.region and not region.contiene(f):
            continue
        completas = sum(1 for r in frases if r <= f.raices)
        if not completas:  # en la demo, al menos una frase completa: las parciales solas son ruido
            continue
        parciales = sum(len(r & f.raices) / len(r) for r in frases if not r <= f.raices)
        resultado.append((f, (completas + 0.25 * parciales) / len(frases)))
    resultado.sort(key=lambda par: (-par[1], par[0].fecha_cierre or datetime.max))
    return resultado[:limite]


def pagina(lista: list, numero: int, por_pagina: int = POR_PAGINA) -> tuple[list, int, int]:
    """(elementos de la página, número de página corregido, total de páginas)."""
    total = max(1, -(-len(lista) // por_pagina))
    numero = min(max(1, numero), total)
    return lista[(numero - 1) * por_pagina: numero * por_pagina], numero, total


def recientes(fichas: Iterable[Ficha], dias: float = 3, momento: datetime | None = None) -> list[Ficha]:
    limite = hora_chile(momento) - timedelta(days=dias)
    return [f for f in fichas if f.fecha_publicacion and f.fecha_publicacion >= limite]


def ejemplo_resumen(fichas: list[Ficha], cantidad: int = 3) -> list[Ficha]:
    """Licitaciones reales para el ejemplo de resumen de WhatsApp en la portada: con monto, de distintos rubros."""
    elegidas, rubros_usados = [], set()
    for f in fichas:
        if f.es_compra_agil or not f.monto_estimado or (f.moneda or "CLP") != "CLP" or (f.dias_para_cierre() or 0) < 3:
            continue
        rubro = next((r.slug for r in RUBROS if r.contiene(f)), None)
        if rubro is None or rubro in rubros_usados:
            continue
        elegidas.append(f)
        rubros_usados.add(rubro)
        if len(elegidas) == cantidad:
            break
    return elegidas


class Cache:
    """Cache en memoria de pocos minutos, segura entre hilos: un solo cálculo a la vez por clave."""

    def __init__(self, segundos: float = 300, reloj: Callable[[], float] = time.monotonic):
        self.segundos, self.reloj, self._datos = segundos, reloj, {}
        self._candado = threading.RLock()  # un cálculo puede pedir otra clave (el sitemap usa las abiertas)

    def obtener(self, clave: str, calcular: Callable[[], Any]):
        valor, vence = self._datos.get(clave, (None, 0.0))
        if self.reloj() < vence:
            return valor
        with self._candado:
            valor, vence = self._datos.get(clave, (None, 0.0))
            if self.reloj() >= vence:
                valor = calcular()
                self._datos[clave] = (valor, self.reloj() + self.segundos)
            return valor
