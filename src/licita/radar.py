"""Radar "Se viene" (plan Pro): organismos que licitan lo mismo seguido y podrían volver a hacerlo pronto.

Con las órdenes de compra de los últimos 30 meses que salieron de una licitación pública (el histórico de ChileCompra
dice de qué licitación salió cada una), agrupa por organismo y producto. Cuenta licitaciones distintas, no órdenes: un
contrato de cinco años con una orden mensual es una sola licitación. Si un organismo licitó lo mismo al menos dos
veces con un ritmo de entre 5 y 14 meses, la próxima vez se espera un ciclo después de la última. Se avisa entre una
semana y dos meses antes, para que la empresa se prepare antes de que se publique.

Las compras por Convenio Marco, Compra Ágil o trato directo no cuentan: no se publican como licitación.
"""

from __future__ import annotations

import logging
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .calce import _compacto, normalizar
from .correo import ClienteCorreo, ErrorCorreo
from .db import AvisoRadar, Calce, Empresa, Licitacion, Precio, ahora, hora_chile
from .notificaciones import registrar, ventana_abierta
from .planes import PLANES_CON_PRECIOS
from .plantillas_whatsapp import SE_VIENE
from .precios import filas_precio
from .resumen import MESES, URL_FICHA, formato_pesos
from .whatsapp import ClienteWhatsApp, ErrorWhatsApp

log = logging.getLogger(__name__)

MESES_HISTORIA = 30
CICLO_MINIMO, CICLO_MAXIMO = 150, 430  # días entre una licitación y la siguiente (semestral a anual)
AVISO_DESDE, AVISO_HASTA = 7, 60  # días antes de la fecha prevista
MAXIMO_PREVISIONES = 5
CODIGOS_DE_INTERES = 25
MESES_CORTOS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


@dataclass
class Prevision:
    organismo: str
    codigo_producto: str
    producto: str
    licitaciones: list[tuple[date, str]]  # (fecha, código) de cada licitación anterior, de la más antigua a la última
    proxima: date
    monto: float | None  # de la última licitación
    clave_organismo: str = field(repr=False, default="")

    @property
    def clave(self) -> str:
        return f"{self.clave_organismo}|{self.codigo_producto}|{self.proxima:%Y-%m}"[:200]

    @property
    def cuando(self) -> str:
        mes = MESES[self.proxima.month - 1]
        parte = "principios" if self.proxima.day <= 10 else "mediados" if self.proxima.day <= 20 else "fines"
        return f"a {parte} de {mes}"


def estimar_proxima(fechas: list[date]) -> date | None:
    """Fecha probable de la próxima licitación, si las anteriores muestran un ritmo de 5 a 14 meses."""
    ordenadas = sorted(fechas)
    ciclos = [(b - a).days for a, b in zip(ordenadas, ordenadas[1:])]
    ciclos = [c for c in ciclos if CICLO_MINIMO <= c <= CICLO_MAXIMO]
    if not ciclos:
        return None
    return ordenadas[-1] + timedelta(days=round(statistics.median(ciclos)))


@dataclass
class Recurrente:
    """Algo que un organismo licita seguido: para su página pública."""

    codigo_producto: str
    producto: str
    fechas: list[date]
    proxima: date

    @property
    def licito_en(self) -> str:
        return _fechas([(f, "") for f in self.fechas])

    @property
    def cuando(self) -> str:
        return f"{MESES[self.proxima.month - 1]} de {self.proxima.year}"


# Si la fecha estimada pasó hace más de esto sin una licitación nueva, el ritmo se rompió: no se muestra.
TOLERANCIA_ATRASO = 45
MAXIMO_RECURRENTES = 8


def calendario_organismo(session: Session, organismo: str, momento: datetime | None = None) -> list[Recurrente]:
    """Lo que el organismo licita seguido y cuándo podría volver a hacerlo (solo compras licitadas)."""
    momento = momento or ahora()
    hoy = hora_chile(momento).date()
    desde = momento - timedelta(days=30 * MESES_HISTORIA)
    por_codigo: dict[str, dict[str, date]] = defaultdict(dict)  # código de producto → licitación → fecha
    nombres: dict[str, str] = {}
    for p in filas_precio(session, Precio.organismo == organismo, Precio.origen == "licitacion",
                          Precio.codigo_licitacion != "", Precio.fecha >= desde, limite=50_000,
                          columnas=(Precio.codigo_producto, Precio.codigo_licitacion, Precio.fecha, Precio.producto)):
        if not p.codigo_producto or not p.fecha:
            continue
        actual = por_codigo[p.codigo_producto].get(p.codigo_licitacion)
        por_codigo[p.codigo_producto][p.codigo_licitacion] = min(actual, p.fecha.date()) if actual else p.fecha.date()
        nombre = " ".join((p.producto or "").split()[:8])
        if nombre and (p.codigo_producto not in nombres or len(nombre) < len(nombres[p.codigo_producto])):
            nombres[p.codigo_producto] = nombre  # el más corto suele ser el nombre genérico
    resultado = []
    for codigo, lics in por_codigo.items():
        fechas = sorted(lics.values())
        proxima = estimar_proxima(fechas) if len(fechas) >= 2 else None
        if proxima is None or (hoy - proxima).days > TOLERANCIA_ATRASO:
            continue
        resultado.append(Recurrente(codigo, nombres.get(codigo, f"producto {codigo}"), fechas, proxima))
    return sorted(resultado, key=lambda r: r.proxima)[:MAXIMO_RECURRENTES]


def codigos_de_interes(session: Session, empresa: Empresa, momento: datetime | None = None) -> dict[str, str]:
    """Códigos de producto de lo que le calza a la empresa (calces buenos o 👍 de los últimos 6 meses) → nombre."""
    desde = (momento or ahora()) - timedelta(days=180)
    conteo, nombres = Counter(), {}
    filas = session.execute(
        select(Licitacion.items).join(Calce, Calce.licitacion_codigo == Licitacion.codigo)
        .where(Calce.empresa_id == empresa.id, Calce.creado_en >= desde,
               (Calce.valoracion == 1) | ((Calce.puntaje >= 70) & Calce.valoracion.is_(None)))
    ).all()
    for (items,) in filas:
        for it in items or []:
            codigo = str(it.get("codigo_producto") or "")
            if codigo:
                conteo[codigo] += 1
                nombres.setdefault(codigo, (it.get("producto") or it.get("categoria") or "").strip())
    return {c: nombres[c] for c, _ in conteo.most_common(CODIGOS_DE_INTERES)}


def _en_sus_regiones(empresa: Empresa, region: str) -> bool:
    if not empresa.regiones or not region:
        return True
    return any(_compacto(r) in _compacto(region) for r in empresa.regiones)


def previsiones(session: Session, empresa: Empresa, momento: datetime | None = None) -> list[Prevision]:
    momento = momento or ahora()
    hoy = hora_chile(momento).date()
    codigos = codigos_de_interes(session, empresa, momento)
    if not codigos:
        return []
    desde = momento - timedelta(days=30 * MESES_HISTORIA)
    grupos: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)  # (organismo, código) → licitación → datos
    for p in filas_precio(session, Precio.codigo_producto.in_(codigos), Precio.origen == "licitacion",
                          Precio.codigo_licitacion != "", Precio.fecha >= desde, limite=100_000,
                          columnas=(Precio.organismo, Precio.codigo_producto, Precio.codigo_licitacion, Precio.fecha,
                                    Precio.region, Precio.precio_unitario, Precio.cantidad)):
        if not p.organismo or not p.fecha or not _en_sus_regiones(empresa, p.region):
            continue
        clave = (normalizar(p.organismo).strip(), p.codigo_producto)
        lic = grupos[clave].setdefault(p.codigo_licitacion, {"fecha": p.fecha.date(), "monto": 0.0, "organismo": p.organismo})
        lic["fecha"] = min(lic["fecha"], p.fecha.date())
        lic["monto"] += p.precio_unitario * (p.cantidad or 1)
    abiertas = _abiertas_por_organismo(session, momento)
    resultado = []
    for (organismo, codigo), lics in grupos.items():
        if len(lics) < 2 or (organismo, codigo) in abiertas:
            continue  # una sola licitación no muestra ritmo; si ya está abierta, le llega en el resumen
        ordenadas = sorted((d["fecha"], c) for c, d in lics.items())
        proxima = estimar_proxima([f for f, _ in ordenadas])
        if proxima is None or not AVISO_DESDE <= (proxima - hoy).days <= AVISO_HASTA:
            continue
        ultima = lics[ordenadas[-1][1]]
        resultado.append(Prevision(
            organismo=ultima["organismo"], codigo_producto=codigo, producto=codigos[codigo], licitaciones=ordenadas,
            proxima=proxima, monto=ultima["monto"] or None, clave_organismo=organismo,
        ))
    return sorted(resultado, key=lambda p: p.proxima)[:MAXIMO_PREVISIONES]


def _abiertas_por_organismo(session: Session, momento: datetime) -> set[tuple[str, str]]:
    ahora_cl = hora_chile(momento)
    pares = set()
    for organismo, items in session.execute(
            select(Licitacion.organismo, Licitacion.items)
            .where(Licitacion.estado_codigo == 5, Licitacion.fecha_cierre > ahora_cl)):
        for it in items or []:
            if it.get("codigo_producto"):
                pares.add((normalizar(organismo or "").strip(), str(it["codigo_producto"])))
    return pares


def _fechas(lista: list[tuple[date, str]]) -> str:
    textos = [f"{MESES_CORTOS[d.month - 1]} {d.year}" for d, _ in lista[-3:]]
    return ", ".join(textos[:-1]) + " y " + textos[-1] if len(textos) > 1 else textos[0]


def texto_radar(lista: list[Prevision]) -> str:
    if not lista:
        return ("🔭 *Se viene*\n\nPor ahora no veo licitaciones de lo tuyo que se repitan en las próximas semanas. "
                "Te aviso apenas detecte una.")
    from .publico import nombre_organismo

    lineas = ["🔭 *Se viene: licitaciones que suelen repetirse*",
              "Organismos que licitan lo mismo seguido y podrían volver a hacerlo pronto. Prepárate antes de que se publique.", ""]
    for i, p in enumerate(lista, 1):
        monto = f" (la última sumó {formato_pesos(p.monto)})" if p.monto else ""
        lineas += [f"{i}. *{nombre_organismo(p.organismo)}* · {p.producto.lower() or 'producto ' + p.codigo_producto}",
                   f"   Licitó en {_fechas(p.licitaciones)}{monto}. Probablemente vuelva {p.cuando}.",
                   f"   Mira qué pidieron la última vez: {URL_FICHA.format(codigo=p.licitaciones[-1][1])}"]
    lineas += ["", "Cuando se publique, te llegará en tu resumen. Es una estimación: el organismo puede cambiar sus fechas."]
    return "\n".join(lineas)


# --- Tarea semanal ---

@dataclass
class ResultadoRadar:
    empresas: int = 0
    previsiones: int = 0
    sin_enviar: int = 0


def ejecutar_radar(session: Session, *, wa: ClienteWhatsApp | None = None, correo: ClienteCorreo | None = None,
                   idioma: str = "es", momento: datetime | None = None) -> ResultadoRadar:
    """Avisa a cada empresa Pro las previsiones nuevas (una vez cada una). Pensada para correr los lunes."""
    momento = momento or ahora()
    r = ResultadoRadar()
    for empresa in session.scalars(select(Empresa).where(Empresa.plan.in_(PLANES_CON_PRECIOS))):
        ya = set(session.scalars(select(AvisoRadar.clave).where(AvisoRadar.empresa_id == empresa.id)))
        nuevas = [p for p in previsiones(session, empresa, momento) if p.clave not in ya]
        if not nuevas:
            continue
        if _enviar(session, wa, correo, empresa, nuevas, momento, idioma):
            session.add_all([AvisoRadar(empresa_id=empresa.id, clave=p.clave, enviado_en=momento) for p in nuevas])
            r.empresas += 1
            r.previsiones += len(nuevas)
        else:
            r.sin_enviar += 1
        session.commit()
    return r


def _enviar(session: Session, wa: ClienteWhatsApp | None, correo: ClienteCorreo | None, empresa: Empresa,
            lista: list[Prevision], momento: datetime, idioma: str) -> bool:
    from .publico import nombre_organismo

    texto = texto_radar(lista)
    if wa is not None and empresa.whatsapp and empresa.whatsapp_activo:
        try:
            if ventana_abierta(empresa, momento):
                wamid = wa.enviar_texto(empresa.whatsapp, texto)
                registrar(session, empresa, empresa.whatsapp, "saliente", "texto", "Se viene", wamid)
            else:
                p = lista[0]
                cantidad = "1 licitación" if len(lista) == 1 else f"{len(lista)} licitaciones"
                ejemplo = f"{nombre_organismo(p.organismo)}, {p.producto.lower()}, {p.cuando}"[:200]
                parametros = [empresa.nombre, cantidad, ejemplo]
                wamid = wa.enviar_plantilla(empresa.whatsapp, SE_VIENE.nombre, idioma=idioma, parametros=parametros,
                                            payloads_botones=["SE_VIENE"])
                registrar(session, empresa, empresa.whatsapp, "saliente", "plantilla", " | ".join(parametros), wamid)
            return True
        except ErrorWhatsApp as e:
            log.warning("No se pudo enviar el radar a la empresa #%s: %s", empresa.id, e)
    if correo is not None and empresa.email:
        try:
            correo.enviar(empresa.email, "Se viene: licitaciones que suelen repetirse",
                          f"Hola {empresa.nombre}:\n\n" + texto.replace("*", "") + "\n\nEquipo Calza · hola@calza.cl")
            return True
        except ErrorCorreo as e:
            log.warning("No se pudo enviar el radar por correo a la empresa #%s: %s", empresa.id, e)
    return False
