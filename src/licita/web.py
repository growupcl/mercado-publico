"""Sitio web: página de inicio, registro, cuenta y pagos con Flow."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from . import legal
from . import rut as rutlib
from .db import Pago, ahora
from .flow import ClienteFlow, ErrorFlow
from .ia import AsistenteIA
from .planes import DIAS_PRUEBA, PLANES, PRECIO_FUNDADOR_PRO, formato_pesos, monto
from .suscripciones import (
    REGIONES, DatosRegistro, ErrorRegistro, confirmar_pago, cotizar, empresa_por_token, fundador_disponible,
    iniciar_pago, registrar_empresa, suscripcion_de,
)

log = logging.getLogger(__name__)
plantillas = Jinja2Templates(directory=str(Path(__file__).parent / "plantillas"))
plantillas.env.globals["fmt"] = formato_pesos


def _fecha(dt: datetime) -> str:
    return dt.strftime("%d-%m-%Y")


def crear_router_web(
    Sesion: sessionmaker,
    *,
    flow: ClienteFlow | None,
    ia: AsistenteIA | None,
    url_publica: str,
    whatsapp_publico: str = "",
) -> APIRouter:
    router = APIRouter()
    url_publica = url_publica.rstrip("/")
    wa_link = f"https://wa.me/{whatsapp_publico}?text={quote('Hola Calza')}" if whatsapp_publico else ""

    def mensaje(request: Request, titulo: str, texto: str, *, chip: str = "", chip_clase: str = "", estado: int = 200):
        return plantillas.TemplateResponse(request, "mensaje.html", {
            "titulo": titulo, "texto": texto, "chip": chip, "chip_clase": chip_clase, "wa_link": wa_link,
        }, status_code=estado)

    @router.get("/", response_class=HTMLResponse)
    def inicio(request: Request):
        with Sesion() as s:
            fundador = fundador_disponible(s)
        return plantillas.TemplateResponse(request, "inicio.html", {
            "planes": list(PLANES.values()), "anual": {c: monto(c, "anual") for c in PLANES},
            "fundador": fundador, "precio_fundador": PRECIO_FUNDADOR_PRO, "dias_prueba": DIAS_PRUEBA,
        })

    def _form(request: Request, datos: DatosRegistro, errores: dict, estado: int = 200):
        with Sesion() as s:
            fundador = fundador_disponible(s)
        precios = {c: (PRECIO_FUNDADOR_PRO if c == "pro" and fundador else p.mensual) for c, p in PLANES.items()}
        return plantillas.TemplateResponse(request, "registro.html", {
            "datos": datos, "errores": errores, "regiones": REGIONES, "planes": list(PLANES.values()),
            "precios": precios, "dias_prueba": DIAS_PRUEBA,
        }, status_code=estado)

    @router.get("/registro", response_class=HTMLResponse)
    def registro(request: Request, plan: str = "pyme"):
        datos = DatosRegistro(nombre="", rut="", razon_social="", giro="", email="", whatsapp="", descripcion="",
                              plan=plan if plan in PLANES else "pyme")
        return _form(request, datos, {})

    @router.post("/registro", response_class=HTMLResponse)
    def registrar(
        request: Request,
        nombre: str = Form(""), rut: str = Form(""), razon_social: str = Form(""), giro: str = Form(""),
        email: str = Form(""), whatsapp: str = Form(""), descripcion: str = Form(""),
        regiones: list[str] = Form([]), monto_max: str = Form(""), plan: str = Form("pyme"),
        periodicidad: str = Form("mensual"), acepta_whatsapp: str = Form(""), acepta_terminos: str = Form(""),
    ):
        try:
            tope = float(monto_max) if monto_max.strip() else None
        except ValueError:
            tope = None
        datos = DatosRegistro(
            nombre=nombre, rut=rut, razon_social=razon_social, giro=giro, email=email, whatsapp=whatsapp,
            descripcion=descripcion, regiones=regiones, monto_max=tope, plan=plan, periodicidad=periodicidad,
            acepta_whatsapp=bool(acepta_whatsapp), acepta_terminos=bool(acepta_terminos),
        )
        with Sesion() as s:
            try:
                empresa, token = registrar_empresa(s, datos, ia=ia)
            except ErrorRegistro as e:
                return _form(request, datos, e.errores, estado=422)
            vigente = suscripcion_de(s, empresa).vigente_hasta
            return plantillas.TemplateResponse(request, "registro_listo.html", {
                "empresa": empresa, "vigente_hasta": _fecha(vigente),
                "enlace_cuenta": f"{url_publica}/cuenta/{token}", "wa_link": wa_link,
            })

    @router.get("/cuenta/{token}", response_class=HTMLResponse)
    def cuenta(request: Request, token: str):
        with Sesion() as s:
            empresa = empresa_por_token(s, token)
            if empresa is None:
                return mensaje(request, "Enlace no válido",
                               "Este enlace de acceso no existe o fue reemplazado. Escribe CUENTA a Calza por WhatsApp para recibir uno nuevo.",
                               estado=404)
            suscripcion = suscripcion_de(s, empresa)
            opciones = []
            for codigo, plan in PLANES.items():
                for periodicidad in ("mensual", "anual"):
                    valor, fundador = cotizar(s, suscripcion, codigo, periodicidad)
                    opciones.append({"plan": codigo, "nombre": plan.nombre, "periodicidad": periodicidad, "monto": valor, "fundador": fundador})
            pagos = list(s.scalars(select(Pago).where(Pago.empresa_id == empresa.id).order_by(Pago.creado_en.desc()).limit(12)))
            restantes = max(0, (suscripcion.vigente_hasta - ahora()).days)
            nombres = {**{c: p.nombre for c, p in PLANES.items()}, "gratis": "Gratis", "consultora": "Consultoras"}
            return plantillas.TemplateResponse(request, "cuenta.html", {
                "empresa": empresa, "suscripcion": suscripcion, "plan_actual": nombres.get(empresa.plan, empresa.plan),
                "vigente_hasta": _fecha(suscripcion.vigente_hasta), "dias_restantes": restantes,
                "opciones": opciones, "pagos": pagos, "token": token, "rut": rutlib.formatear(empresa.rut) if empresa.rut else "",
            })

    @router.post("/cuenta/{token}/pagar")
    def pagar(request: Request, token: str, plan: str = Form(...), periodicidad: str = Form(...)):
        if flow is None:
            return mensaje(request, "Pagos no disponibles", "Todavía no podemos recibir pagos en línea. Escríbenos a hola@calza.cl.", estado=503)
        with Sesion() as s:
            empresa = empresa_por_token(s, token)
            if empresa is None:
                return mensaje(request, "Enlace no válido", "Escribe CUENTA a Calza por WhatsApp para recibir un enlace nuevo.", estado=404)
            try:
                url = iniciar_pago(s, empresa, flow, plan=plan, periodicidad=periodicidad, url_publica=url_publica)
            except ValueError:
                return mensaje(request, "Plan no válido", "Vuelve a tu cuenta y elige uno de los planes.", estado=400)
            except ErrorFlow as e:
                s.rollback()
                log.error("No se pudo crear el pago en Flow: %s", e)
                return mensaje(request, "No pudimos iniciar el pago", "Inténtalo de nuevo en unos minutos.", estado=502)
        return RedirectResponse(url, status_code=303)

    @router.post("/pagos/flow/confirmacion")
    def confirmacion(token: str = Form(...)):
        """Aviso de Flow (servidor a servidor). Siempre se verifica el estado consultando a Flow."""
        if flow is None:
            return PlainTextResponse("no configurado", status_code=503)
        with Sesion() as s:
            try:
                confirmar_pago(s, flow, token)
            except ErrorFlow as e:
                log.error("No se pudo confirmar el pago: %s", e)
                return PlainTextResponse("error", status_code=502)
        return PlainTextResponse("ok")

    @router.api_route("/pagos/flow/retorno", methods=["GET", "POST"], response_class=HTMLResponse)
    async def retorno(request: Request):
        """Flow devuelve aquí al cliente después de pagar."""
        token = request.query_params.get("token") or (await request.form()).get("token", "")
        pago = None
        if flow is not None and token:
            with Sesion() as s:
                try:
                    pago = confirmar_pago(s, flow, str(token))
                except ErrorFlow as e:
                    log.error("No se pudo confirmar el pago en el retorno: %s", e)
                estado = pago.estado if pago else None
        else:
            estado = None
        if estado == "pagado":
            return mensaje(request, "¡Pago recibido!", "Tu plan ya está activo. Te enviaremos la factura a tu correo.", chip="Pagado", chip_clase="ok")
        if estado in ("rechazado", "anulado"):
            return mensaje(request, "El pago no se completó", "No se realizó ningún cargo. Puedes intentarlo de nuevo desde tu cuenta.", chip="Sin cargo", chip_clase="error")
        return mensaje(request, "Estamos confirmando tu pago", "Puede tardar unos minutos. Te avisaremos cuando esté listo.", chip="Pendiente", chip_clase="oro")

    @router.get("/terminos", response_class=HTMLResponse)
    def terminos(request: Request):
        return plantillas.TemplateResponse(request, "legal.html", {"titulo": "Términos del servicio", "fecha": legal.FECHA, "secciones": legal.TERMINOS})

    @router.get("/privacidad", response_class=HTMLResponse)
    def privacidad(request: Request):
        return plantillas.TemplateResponse(request, "legal.html", {"titulo": "Política de privacidad", "fecha": legal.FECHA, "secciones": legal.PRIVACIDAD})

    return router
