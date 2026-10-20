"""Arma el resumen diario y el detalle de cada licitación para WhatsApp (o correo)."""

from __future__ import annotations

import html
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


LARGO_RAZON_RESUMEN = 90


def razon_corta(razon: str, largo: int = LARGO_RAZON_RESUMEN) -> str:
    """Motivo del calce para el resumen: solo la primera frase y, si aún es larga, cortada en una palabra.
    El detalle de la licitación muestra el motivo completo."""
    razon = " ".join(razon.split())
    for fin in (". ", "; "):
        if fin in razon:
            razon = razon.split(fin, 1)[0] + "."
            break
    if len(razon) <= largo:
        return razon
    return razon[:largo].rsplit(" ", 1)[0].rstrip(",;:") + "…"


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
            f"   {razon_corta(calce.razon)}",
            "",
        ]
    lineas.append("Responde con el número de una licitación para ver su detalle.")
    return "\n".join(lineas)


MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre")


def correo_resumen(
    empresa: Empresa, filas: list[Fila], *, url_publica: str, enlace_baja: str, whatsapp_publico: str = "",
    momento: datetime | None = None,
) -> tuple[str, str, str]:
    """(asunto, texto, html) de la copia diaria del resumen por correo."""
    momento = hora_chile(momento)
    dia = f"{momento.day} de {MESES[momento.month - 1]}"
    cantidad = "1 licitación nueva" if len(filas) == 1 else f"{len(filas)} licitaciones nuevas"
    asunto = f"{cantidad.capitalize()} para {empresa.nombre} · {dia}"
    e = html.escape
    wa = f"https://wa.me/{whatsapp_publico}" if whatsapp_publico else ""

    lineas = [f"Hola {empresa.nombre}:", "", f"Hoy encontramos {cantidad} que calzan con tu negocio:", ""]
    bloques = []
    for i, (calce, lic) in enumerate(filas, 1):
        lugar = " · ".join(p for p in (lic.organismo, lic.region) if p)
        ficha = f"{url_publica}/licitacion/{lic.codigo}"
        lineas += [
            f"{i}. {titulo(lic)} ({calce.puntaje}% de calce)",
            f"   {lugar}",
            f"   Monto estimado: {formato_pesos(lic.monto_estimado)} · Cierra: {formato_cierre(lic)}",
            f"   {razon_corta(calce.razon)}",
            f"   {ficha}",
            "",
        ]
        bloques.append(f"""<tr><td style="padding:14px 0;border-top:1px solid #e4e7eb">
<p style="margin:0 0 4px;font-weight:bold"><a href="{e(ficha)}" style="color:#0b6e4f;text-decoration:none">{i}. {e(titulo(lic))}</a>
<span style="font-weight:normal;color:#52606d">({calce.puntaje}% de calce)</span></p>
<p style="margin:0 0 4px;font-size:14px;color:#52606d">{e(lugar)}</p>
<p style="margin:0 0 4px;font-size:14px">Monto estimado: {e(formato_pesos(lic.monto_estimado))} · Cierra: {e(formato_cierre(lic))}</p>
<p style="margin:0;font-size:14px">{e(razon_corta(calce.razon))}</p>
</td></tr>""")
    if wa:
        lineas += ["Para ver qué piden, precios de referencia o analizar las bases, escribe el número de la licitación "
                   f"al WhatsApp de Calza: {wa}", ""]
    lineas += [
        "Recibes este correo porque te registraste en Calza (calza.cl). Para no recibir más el resumen por correo:",
        enlace_baja, "", "Equipo Calza · hola@calza.cl",
    ]
    boton_wa = ('<p style="font-size:14px">Para ver qué piden, precios de referencia o analizar las bases, escribe el '
                'número de la licitación al WhatsApp de Calza.</p>'
                f'<p style="text-align:center;margin:24px 0"><a href="{e(wa)}" style="background:#0b6e4f;color:#ffffff;'
                'text-decoration:none;padding:12px 22px;border-radius:6px;font-weight:bold">Abrir WhatsApp</a></p>'
                if wa else "")
    cuerpo_html = f"""<!doctype html>
<html lang="es"><body style="margin:0;padding:24px;background:#f4f6f8;font-family:Arial,Helvetica,sans-serif;color:#1f2933">
<div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:8px;padding:28px">
<p style="font-size:20px;font-weight:bold;margin:0 0 16px">Calza</p>
<p>Hola {e(empresa.nombre)}:</p>
<p>Hoy encontramos <strong>{e(cantidad)}</strong> que calzan con tu negocio:</p>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0">{"".join(bloques)}</table>
{boton_wa}
<p style="font-size:13px;color:#52606d">Recibes este correo porque te registraste en Calza.
<a href="{e(enlace_baja)}" style="color:#52606d">No recibir más el resumen por correo</a>.</p>
<p style="font-size:13px;color:#52606d">Equipo Calza · hola@calza.cl</p>
</div></body></html>"""
    return asunto, "\n".join(lineas), cuerpo_html


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
