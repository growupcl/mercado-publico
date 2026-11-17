"""Semáforo "¿Te conviene?": una lectura rápida para decidir si vale la pena preparar la oferta.

Junta cuatro señales que la empresa tendría que buscar a mano:
- Tiempo para preparar la oferta (días hasta el cierre).
- Competencia: si en las compras anteriores del mismo organismo y producto gana casi siempre el mismo proveedor.
- Presupuesto frente al precio de mercado (solo en planes con precios de referencia).
- Exigencias pesadas que la IA detectó en la publicación (garantías, visita obligatoria, experiencia…).
Sin datos para una señal, esa señal no se muestra: el semáforo nunca inventa.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .calce import normalizar
from .db import Licitacion, Precio, hora_chile
from .precios import FUENTES_PAGADAS, MESES_HISTORIA, informe_precios

VERDE, AMARILLO, ROJO = "🟢", "🟡", "🔴"
# Mismo organismo y producto: con al menos estas compras se puede hablar de "quién suele ganar".
MINIMO_COMPRAS_COMPETENCIA = 3
DOMINIO_INCUMBENTE = 0.7  # un proveedor con el 70% o más de esas compras
EXIGENCIAS = (("garant", "boleta de garantía"), ("visita", "visita a terreno"), ("experiencia", "experiencia previa"),
              ("certific", "certificaciones"), ("iso", "norma ISO"), ("inscrip", "inscripción en registros"),
              ("muestra", "entrega de muestras"))


@dataclass
class Senal:
    color: str
    texto: str


def _tiempo(lic: Licitacion, ahora_cl: datetime) -> Senal | None:
    if lic.fecha_cierre is None:
        return None
    dias = (lic.fecha_cierre.date() - ahora_cl.date()).days
    compra_agil = lic.tipo == "COT"
    if dias <= 0:
        return Senal(ROJO, f"Cierra hoy a las {lic.fecha_cierre.strftime('%H:%M')}: queda muy poco tiempo.")
    if dias == 1:
        return Senal(AMARILLO if compra_agil else ROJO, "Cierra mañana: hay que preparar la oferta ya.")
    if dias <= 4 and not compra_agil:
        return Senal(AMARILLO, f"Quedan {dias} días: alcanza si partes hoy.")
    return Senal(VERDE, f"Quedan {dias} días para preparar la oferta.")


def _competencia(session: Session, lic: Licitacion, ahora_cl: datetime) -> Senal | None:
    codigos = {str(it.get("codigo_producto")) for it in lic.items or [] if it.get("codigo_producto")}
    if not codigos or not lic.organismo:
        return None
    desde = ahora_cl - timedelta(days=30 * MESES_HISTORIA)
    organismo = normalizar(lic.organismo).strip()
    ganadores: dict[str, str] = {}  # compra → proveedor
    for p in session.scalars(select(Precio).where(
            Precio.codigo_producto.in_(codigos), Precio.fuente.in_(FUENTES_PAGADAS),
            (Precio.fecha.is_(None)) | (Precio.fecha >= desde))):
        if p.referencia != lic.codigo and p.proveedor_nombre and normalizar(p.organismo or "").strip() == organismo:
            ganadores.setdefault(p.referencia, p.proveedor_nombre.strip())
    if len(ganadores) < MINIMO_COMPRAS_COMPETENCIA:
        return None
    (lider, veces), = Counter(ganadores.values()).most_common(1)
    total, distintos = len(ganadores), len(set(ganadores.values()))
    if veces / total >= DOMINIO_INCUMBENTE:
        return Senal(AMARILLO, f"{lider} le ha vendido esto {veces} de las últimas {total} veces: compite con precio "
                               "y cumple todo al pie de la letra.")
    return Senal(VERDE, f"En sus últimas {total} compras de esto ganaron {distintos} proveedores distintos: hay espacio.")


def _presupuesto(session: Session, lic: Licitacion, momento: datetime) -> Senal | None:
    if not lic.monto_estimado:
        return None
    total = informe_precios(session, lic, momento=momento).total_mediano
    if not total:
        return None
    holgura = lic.monto_estimado / total - 1
    if holgura >= 0.15:
        return Senal(VERDE, f"El presupuesto está {holgura:.0%} sobre el precio de mercado: hay margen.")
    if holgura >= -0.05:
        return Senal(AMARILLO, "El presupuesto está cerca del precio de mercado: margen justo.")
    return Senal(ROJO, f"El presupuesto está {-holgura:.0%} bajo el precio de mercado: revisa si te da el margen.")


def _exigencias(lic: Licitacion) -> Senal | None:
    requisitos = " ".join((lic.clasificacion or {}).get("requisitos_destacados") or [])
    texto = normalizar(requisitos)
    pide = [nombre for clave, nombre in EXIGENCIAS if clave in texto]
    if not pide:
        return None
    lista = ", ".join(pide[:-1]) + (" y " if len(pide) > 1 else "") + pide[-1]
    return Senal(AMARILLO, f"Pide {lista}: revisa que lo tengas antes de invertir tiempo.")


def senales(session: Session, lic: Licitacion, *, con_precios: bool, momento: datetime | None = None) -> list[Senal]:
    ahora_cl = hora_chile(momento)
    lista = [_tiempo(lic, ahora_cl), _competencia(session, lic, ahora_cl), _exigencias(lic)]
    if con_precios:
        lista.insert(2, _presupuesto(session, lic, momento))
    return [s for s in lista if s is not None]


def texto_semaforo(session: Session, lic: Licitacion, *, con_precios: bool, momento: datetime | None = None) -> str:
    """Bloque para el detalle por WhatsApp ("" si no hay ninguna señal)."""
    lista = senales(session, lic, con_precios=con_precios, momento=momento)
    if not lista:
        return ""
    colores = Counter(s.color for s in lista)
    if colores[ROJO]:
        titulo = f"{ROJO} Difícil: mira bien antes de postular"
    elif colores[AMARILLO] >= 2:
        titulo = f"{AMARILLO} Puede ser, pero revisa estos puntos"
    else:
        titulo = f"{VERDE} Buena oportunidad"
    return "\n".join([f"🚦 *¿Te conviene?* {titulo}"] + [f"{s.color} {s.texto}" for s in lista])
