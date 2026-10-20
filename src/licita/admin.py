"""Panel privado del piloto (/admin): estado de cada empresa y a quién contactar.

Protegido con usuario y contraseña (HTTP Basic, sobre HTTPS gracias a Caddy). Usuario "admin" y la contraseña de
ADMIN_CLAVE en el .env. Sin ADMIN_CLAVE el panel no existe (404), así no queda expuesto por error.
"""

from __future__ import annotations

import base64
import binascii
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from .db import Calce, Empresa, MandatoPago, MensajeWhatsApp, Pago, Suscripcion, ahora, hora_chile
from .planes import PERIODICIDADES, PLANES

USUARIO = "admin"
# Sin mensajes en este plazo, la empresa en prueba o activa se marca para contactar.
SIN_ACTIVIDAD = timedelta(days=7)
DIAS_POR_VENCER = 3


@dataclass
class FilaEmpresa:
    empresa: Empresa
    suscripcion: Suscripcion | None
    mandato: MandatoPago | None
    dias_restantes: int | None
    resumenes: int  # días en que se le envió el resumen
    ultimo_resumen: datetime | None
    mensajes_recibidos: int
    pagado: int
    motivos: list[str]  # por qué contactarla (vacío = nada urgente)

    @property
    def estado(self) -> str:
        return self.suscripcion.estado if self.suscripcion else "sin suscripción"


@dataclass
class Indicadores:
    empresas: int
    en_prueba: int
    activas: int
    vencidas: int
    con_renovacion: int
    ingreso_mensual: int  # suscripciones con renovación automática, llevadas a pesos al mes
    cobrado_mes: int
    por_contactar: int


def _autorizado(request: Request, clave: str) -> bool:
    encabezado = request.headers.get("authorization", "")
    if not encabezado.lower().startswith("basic "):
        return False
    try:
        usuario, _, dada = base64.b64decode(encabezado[6:]).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    return secrets.compare_digest(usuario.encode(), USUARIO.encode()) & secrets.compare_digest(dada.encode(), clave.encode())


def datos_panel(session, momento: datetime | None = None) -> tuple[Indicadores, list[FilaEmpresa]]:
    momento = momento or ahora()
    suscripciones = {s.empresa_id: s for s in session.scalars(select(Suscripcion))}
    mandatos = {m.id: m for m in session.scalars(select(MandatoPago))}
    resumenes = {
        empresa_id: (dias, ultimo) for empresa_id, dias, ultimo in session.execute(
            select(Calce.empresa_id, func.count(func.distinct(Calce.notificado_en)), func.max(Calce.notificado_en))
            .where(Calce.notificado_en.is_not(None)).group_by(Calce.empresa_id)
        )
    }
    recibidos = dict(session.execute(
        select(MensajeWhatsApp.empresa_id, func.count()).where(MensajeWhatsApp.direccion == "entrante")
        .group_by(MensajeWhatsApp.empresa_id)
    ).all())
    pagado = dict(session.execute(
        select(Pago.empresa_id, func.sum(Pago.monto)).where(Pago.estado == "pagado").group_by(Pago.empresa_id)
    ).all())
    inicio_mes = momento.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    cobrado_mes = session.scalar(select(func.sum(Pago.monto)).where(
        Pago.estado == "pagado", Pago.pagado_en >= inicio_mes)) or 0

    filas = []
    for empresa in session.scalars(select(Empresa).order_by(Empresa.creada_en.desc())):
        sus = suscripciones.get(empresa.id)
        mandato = mandatos.get(sus.mandato_activo_id) if sus and sus.mandato_activo_id else None
        dias = (sus.vigente_hasta - momento).days if sus else None
        motivos = []
        if sus and sus.estado == "prueba" and mandato is None and dias is not None and dias < DIAS_POR_VENCER:
            motivos.append("prueba por terminar sin suscripción")
        if sus and sus.estado in ("prueba", "activa"):
            ultimo = empresa.ultimo_mensaje_entrante
            if ultimo is None and momento - empresa.creada_en > timedelta(days=2):
                motivos.append("nunca ha escrito por WhatsApp")
            elif ultimo is not None and momento - ultimo > SIN_ACTIVIDAD:
                motivos.append(f"sin escribir hace {(momento - ultimo).days} días")  # siempre más de 7
            if not empresa.whatsapp_activo:
                motivos.append("dio de baja WhatsApp")
        if sus and sus.estado == "vencida" and dias is not None and dias > -7:
            motivos.append("venció hace poco")
        dias_resumen, ultimo_resumen = resumenes.get(empresa.id, (0, None))
        filas.append(FilaEmpresa(
            empresa=empresa, suscripcion=sus, mandato=mandato, dias_restantes=dias, resumenes=dias_resumen,
            ultimo_resumen=ultimo_resumen, mensajes_recibidos=recibidos.get(empresa.id, 0),
            pagado=int(pagado.get(empresa.id) or 0), motivos=motivos,
        ))
    # Primero a quién contactar; luego pruebas por fecha de término, activas y vencidas.
    orden_estado = {"prueba": 0, "activa": 1, "vencida": 2}
    filas.sort(key=lambda f: (not f.motivos, orden_estado.get(f.estado, 3),
                              f.suscripcion.vigente_hasta if f.suscripcion else momento))

    activas_con_mandato = [f for f in filas if f.mandato and f.estado in ("prueba", "activa")]
    indicadores = Indicadores(
        empresas=len(filas),
        en_prueba=sum(f.estado == "prueba" for f in filas),
        activas=sum(f.estado == "activa" for f in filas),
        vencidas=sum(f.estado == "vencida" for f in filas),
        con_renovacion=len(activas_con_mandato),
        ingreso_mensual=round(sum(f.mandato.monto / PERIODICIDADES.get(f.mandato.periodicidad, 1)
                                  for f in activas_con_mandato)),
        cobrado_mes=int(cobrado_mes),
        por_contactar=sum(bool(f.motivos) for f in filas),
    )
    return indicadores, filas


def crear_router_admin(Sesion: sessionmaker, *, plantillas: Jinja2Templates, clave: str) -> APIRouter:
    router = APIRouter()

    @router.get("/admin", response_class=HTMLResponse)
    def panel(request: Request):
        if not clave:
            return Response(status_code=404)
        if not _autorizado(request, clave):
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Calza admin", charset="UTF-8"'})
        momento = ahora()
        with Sesion() as s:
            indicadores, filas = datos_panel(s, momento)
            nombres = {**{c: p.nombre for c, p in PLANES.items()}, "gratis": "Gratis"}
            return plantillas.TemplateResponse(request, "admin.html", {
                "ind": indicadores, "filas": filas, "nombres": nombres, "momento": momento, "chile": hora_chile,
            }, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})

    return router
