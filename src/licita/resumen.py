"""Arma el resumen diario que se envía por WhatsApp (o correo en el plan gratis)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Calce, Empresa, Licitacion, ahora

URL_FICHA = "https://www.mercadopublico.cl/fichaLicitacion.html?idLicitacion={codigo}"


def formato_pesos(monto: float | None) -> str:
    if monto is None:
        return "no informado"
    return "$" + f"{round(monto):,}".replace(",", ".")


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
    momento = momento or ahora()
    filas = session.execute(
        select(Calce, Licitacion)
        .join(Licitacion, Licitacion.codigo == Calce.licitacion_codigo)
        .where(
            Calce.empresa_id == empresa.id,
            Calce.notificado.is_(False),
            Calce.puntaje >= umbral,
            (Licitacion.fecha_cierre.is_(None)) | (Licitacion.fecha_cierre > momento),
        )
        .order_by(Calce.puntaje.desc(), Licitacion.fecha_cierre)
        .limit(maximo)
    ).all()
    if not filas:
        return None

    plural = "licitación" if len(filas) == 1 else "licitaciones"
    lineas = [f"Hola {empresa.nombre} 👋", f"Hoy encontramos {len(filas)} {plural} para ti:", ""]
    for i, (calce, lic) in enumerate(filas, 1):
        cierre = lic.fecha_cierre.strftime("%d-%m-%Y %H:%M") if lic.fecha_cierre else "sin fecha"
        lugar = " · ".join(p for p in (lic.organismo, lic.region) if p)
        lineas += [
            f"{i}. *{lic.nombre}* ({calce.puntaje}% de calce)",
            f"   {lugar}",
            f"   Monto estimado: {formato_pesos(lic.monto_estimado)} · Cierra: {cierre}",
            f"   {calce.razon}",
            f"   {URL_FICHA.format(codigo=lic.codigo)}",
            "",
        ]
        if marcar_notificado:
            calce.notificado = True
    lineas.append("Responde con el número para analizar las bases de una licitación.")
    if marcar_notificado:
        session.commit()
    return "\n".join(lineas)
