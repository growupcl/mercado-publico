"""Arma el resumen diario y el detalle de cada licitación para WhatsApp (o correo)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Calce, Empresa, Licitacion, ahora, hora_chile

URL_FICHA = "https://www.mercadopublico.cl/fichaLicitacion.html?idLicitacion={codigo}"
URL_FICHA_COMPRA_AGIL = "https://buscador.mercadopublico.cl/ficha?code={codigo}"

Fila = tuple[Calce, Licitacion]


def formato_pesos(monto: float | None) -> str:
    if monto is None:
        return "no informado"
    return "$" + f"{round(monto):,}".replace(",", ".")


def es_compra_agil(lic: Licitacion) -> bool:
    return lic.tipo == "COT"


def url_ficha(lic: Licitacion) -> str:
    return (URL_FICHA_COMPRA_AGIL if es_compra_agil(lic) else URL_FICHA).format(codigo=lic.codigo)


def titulo(lic: Licitacion) -> str:
    return f"⚡ Compra Ágil: {lic.nombre}" if es_compra_agil(lic) else lic.nombre


def formato_cierre(lic: Licitacion) -> str:
    return lic.fecha_cierre.strftime("%d-%m-%Y %H:%M") if lic.fecha_cierre else "sin fecha"


def seleccionar_calces(
    session: Session, empresa: Empresa, *, umbral: int = 60, maximo: int = 5, momento: datetime | None = None
) -> list[Fila]:
    """Mejores calces aún no notificados de licitaciones que siguen abiertas."""
    cierre_minimo = hora_chile(momento)  # los cierres están en hora de Chile
    return [
        tuple(fila)
        for fila in session.execute(
            select(Calce, Licitacion)
            .join(Licitacion, Licitacion.codigo == Calce.licitacion_codigo)
            .where(
                Calce.empresa_id == empresa.id,
                Calce.notificado.is_(False),
                Calce.puntaje >= umbral,
                (Licitacion.fecha_cierre.is_(None)) | (Licitacion.fecha_cierre > cierre_minimo),
            )
            .order_by(Calce.puntaje.desc(), Licitacion.fecha_cierre)
            .limit(maximo)
        ).all()
    ]


def ultimo_resumen(session: Session, empresa: Empresa) -> list[Fila]:
    """Calces del último resumen enviado, en el orden en que se mostraron."""
    ultimo = session.scalar(
        select(Calce.notificado_en).where(Calce.empresa_id == empresa.id, Calce.notificado_en.is_not(None))
        .order_by(Calce.notificado_en.desc()).limit(1)
    )
    if ultimo is None:
        return []
    return [
        tuple(fila)
        for fila in session.execute(
            select(Calce, Licitacion)
            .join(Licitacion, Licitacion.codigo == Calce.licitacion_codigo)
            .where(Calce.empresa_id == empresa.id, Calce.notificado_en == ultimo)
            .order_by(Calce.posicion_resumen)
        ).all()
    ]


def marcar_enviados(session: Session, filas: list[Fila], momento: datetime | None = None) -> None:
    momento = momento or ahora()
    for posicion, (calce, _) in enumerate(filas, 1):
        calce.notificado = True
        calce.notificado_en = momento
        calce.posicion_resumen = posicion
    session.commit()


def texto_resumen(empresa: Empresa, filas: list[Fila]) -> str:
    plural = "licitación" if len(filas) == 1 else "licitaciones"
    lineas = [f"Hola {empresa.nombre} 👋", f"Hoy encontramos {len(filas)} {plural} para ti:", ""]
    for i, (calce, lic) in enumerate(filas, 1):
        lugar = " · ".join(p for p in (lic.organismo, lic.region) if p)
        lineas += [
            f"{i}. *{titulo(lic)}* ({calce.puntaje}% de calce)",
            f"   {lugar}",
            f"   Monto estimado: {formato_pesos(lic.monto_estimado)} · Cierra: {formato_cierre(lic)}",
            f"   {calce.razon}",
            "",
        ]
    lineas.append("Responde con el número de una licitación para ver su detalle.")
    return "\n".join(lineas)


def texto_detalle(calce: Calce, lic: Licitacion) -> str:
    clasif = lic.clasificacion or {}
    lineas = [f"*{titulo(lic)}*", f"Código: {lic.codigo} · Calce: {calce.puntaje}%", ""]
    if clasif.get("resumen"):
        lineas += [clasif["resumen"], ""]
    lineas += [
        f"🏛️ {lic.organismo}" + (f" ({lic.region})" if lic.region else ""),
        f"💰 Monto estimado: {formato_pesos(lic.monto_estimado)}",
        f"⏰ Cierra: {formato_cierre(lic)}",
    ]
    plazo = (lic.raw or {}).get("plazo_entrega_dias") if es_compra_agil(lic) else None
    if plazo:
        lineas.append(f"🚚 Plazo de entrega: {plazo} días")
    if lic.items:
        lineas += ["", "📦 Qué piden:"]
        for it in lic.items[:5]:
            cantidad = f"{it['cantidad']:g} × " if isinstance(it.get("cantidad"), (int, float)) else ""
            lineas.append(f"• {cantidad}{it.get('producto') or it.get('categoria')}")
        if len(lic.items) > 5:
            lineas.append(f"• … y {len(lic.items) - 5} ítems más")
    requisitos = clasif.get("requisitos_destacados") or []
    if requisitos:
        lineas += ["", "⚠️ Ojo con:"] + [f"• {r}" for r in requisitos[:4]]
    lineas += ["", f"✅ Por qué te sirve: {calce.razon}"]
    lineas += ["", f"Ficha completa: {url_ficha(lic)}"]
    return "\n".join(lineas)


def resumen_diario(
    session: Session,
    empresa: Empresa,
    *,
    umbral: int = 60,
    maximo: int = 5,
    momento: datetime | None = None,
    marcar_notificado: bool = False,
) -> str | None:
    """Texto del resumen con los mejores calces no notificados, o None si no hay nada que enviar."""
    filas = seleccionar_calces(session, empresa, umbral=umbral, maximo=maximo, momento=momento)
    if not filas:
        return None
    texto = texto_resumen(empresa, filas)
    if marcar_notificado:
        marcar_enviados(session, filas, momento)
    return texto
