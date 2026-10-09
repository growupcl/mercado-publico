"""Sitio web: registro, cuenta y suscripciones con Mercado Pago. Las páginas públicas están en sitio.py."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from . import legal
from . import rut as rutlib
from .correo import ClienteCorreo
from .db import MandatoPago, Pago, Suscripcion, ahora
from .mercadopago import ClienteMercadoPago, ErrorMercadoPago, verificar_firma
from .whatsapp import ClienteWhatsApp
from .ia import AsistenteIA
from .planes import DIAS_PRUEBA, PLANES, PRECIO_FUNDADOR_PRO, formato_pesos
from .publico import REGIONES_PUBLICAS, RUBROS, Cache
from .sitio import crear_router_publico
from .suscripciones import (
    REGIONES, DatosRegistro, ErrorRegistro, actualizar_mandato, cancelar_renovacion, cotizar, empresa_por_token,
    fundador_disponible, iniciar_suscripcion, registrar_cobro, registrar_empresa, suscripcion_de,
)

log = logging.getLogger(__name__)
plantillas = Jinja2Templates(directory=str(Path(__file__).parent / "plantillas"))
plantillas.env.globals["fmt"] = formato_pesos
plantillas.env.globals["sitio"] = ""
plantillas.env.globals["regiones_publicas"] = REGIONES_PUBLICAS
plantillas.env.globals["rubros_publicos"] = RUBROS


MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre")


def _fecha(dt: datetime) -> str:
    return f"{dt.day} de {MESES[dt.month - 1]} de {dt.year}"


def crear_router_web(
    Sesion: sessionmaker,
    *,
    mp: ClienteMercadoPago | None,
    ia: AsistenteIA | None,
    url_publica: str,
    whatsapp_publico: str = "",
    mp_webhook_secreto: str = "",
    prestador: str = "Virtus SpA",
    permitir_sin_firma: bool = False,
    wa: ClienteWhatsApp | None = None,
    idioma_whatsapp: str = "es",
    correo: ClienteCorreo | None = None,
    cache_publico: Cache | None = None,
) -> APIRouter:
    if mp is not None and not mp_webhook_secreto and not permitir_sin_firma:
        raise ValueError("Falta MERCADOPAGO_WEBHOOK_SECRET: sin él no se puede verificar que los avisos vengan de Mercado Pago.")
    router = APIRouter()
    url_publica = url_publica.rstrip("/")
    plantillas.env.globals["sitio"] = url_publica  # para URL canónicas y Open Graph
    router.include_router(crear_router_publico(
        Sesion, plantillas=plantillas, url_publica=url_publica, fundador_disponible=fundador_disponible,
        cache=cache_publico,
    ))
    wa_link = f"https://wa.me/{whatsapp_publico}?text={quote('Hola Calza')}" if whatsapp_publico else ""

    def mensaje(request: Request, titulo: str, texto: str, *, chip: str = "", chip_clase: str = "", estado: int = 200):
        return plantillas.TemplateResponse(request, "mensaje.html", {
            "titulo": titulo, "texto": texto, "chip": chip, "chip_clase": chip_clase, "wa_link": wa_link,
        }, status_code=estado)

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
        direccion: str = Form(""), comuna: str = Form(""),
        email: str = Form(""), whatsapp: str = Form(""), descripcion: str = Form(""),
        regiones: list[str] = Form([]), monto_max: str = Form(""), plan: str = Form("pyme"),
        periodicidad: str = Form("mensual"), acepta_whatsapp: str = Form(""), acepta_terminos: str = Form(""),
    ):
        try:
            tope = float(monto_max) if monto_max.strip() else None
        except ValueError:
            tope = None
        datos = DatosRegistro(
            nombre=nombre, rut=rut, razon_social=razon_social, giro=giro, direccion=direccion, comuna=comuna,
            email=email, whatsapp=whatsapp,
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
            mandato = s.get(MandatoPago, suscripcion.mandato_activo_id) if suscripcion.mandato_activo_id else None
            opciones = []
            for codigo, plan in PLANES.items():
                for periodicidad in ("mensual", "anual"):
                    valor, fundador = cotizar(s, suscripcion, codigo, periodicidad)
                    actual = mandato is not None and (mandato.plan, mandato.periodicidad) == (codigo, periodicidad)
                    opciones.append({"plan": codigo, "nombre": plan.nombre, "periodicidad": periodicidad, "monto": valor,
                                     "fundador": fundador, "actual": actual})
            pagos = list(s.scalars(select(Pago).where(Pago.empresa_id == empresa.id).order_by(Pago.creado_en.desc()).limit(12)))
            # Último cobro rechazado de la suscripción activa, si no hubo uno aprobado después.
            rechazo = None
            if mandato is not None and pagos and pagos[0].mandato_id == mandato.id and pagos[0].estado == "rechazado":
                rechazo = _fecha(pagos[0].creado_en)
            restantes = max(0, (suscripcion.vigente_hasta - ahora()).days)
            nombres = {**{c: p.nombre for c, p in PLANES.items()}, "gratis": "Gratis", "consultora": "Consultoras"}
            return plantillas.TemplateResponse(request, "cuenta.html", {
                "empresa": empresa, "suscripcion": suscripcion, "plan_actual": nombres.get(empresa.plan, empresa.plan),
                "vigente_hasta": _fecha(suscripcion.vigente_hasta), "dias_restantes": restantes, "mandato": mandato,
                "nombre_mandato": nombres.get(mandato.plan) if mandato else "",
                "opciones": opciones, "pagos": pagos, "token": token, "prestador": prestador,
                "rut": rutlib.formatear(empresa.rut) if empresa.rut else "", "pagos_activos": mp is not None,
                "rechazo": rechazo,
            })

    @router.post("/cuenta/{token}/suscribir")
    def suscribir(request: Request, token: str, plan: str = Form(...), periodicidad: str = Form(...)):
        if mp is None:
            return mensaje(request, "Pagos no disponibles", "Todavía no podemos recibir pagos en línea. Escríbenos a hola@calza.cl.", estado=503)
        with Sesion() as s:
            empresa = empresa_por_token(s, token)
            if empresa is None:
                return mensaje(request, "Enlace no válido", "Escribe CUENTA a Calza por WhatsApp para recibir un enlace nuevo.", estado=404)
            try:
                url = iniciar_suscripcion(s, empresa, mp, plan=plan, periodicidad=periodicidad, url_publica=url_publica)
            except ValueError:
                return mensaje(request, "Plan no válido", "Vuelve a tu cuenta y elige uno de los planes.", estado=400)
            except ErrorMercadoPago as e:
                s.rollback()
                log.error("No se pudo crear la suscripción en Mercado Pago: %s", e)
                return mensaje(request, "No pudimos iniciar la suscripción", "Inténtalo de nuevo en unos minutos.", estado=502)
        return RedirectResponse(url, status_code=303)

    @router.post("/cuenta/{token}/cancelar")
    def cancelar(request: Request, token: str):
        if mp is None:
            return mensaje(request, "Pagos no disponibles", "Escríbenos a hola@calza.cl.", estado=503)
        with Sesion() as s:
            empresa = empresa_por_token(s, token)
            if empresa is None:
                return mensaje(request, "Enlace no válido", "Escribe CUENTA a Calza por WhatsApp para recibir un enlace nuevo.", estado=404)
            try:
                cancelar_renovacion(s, empresa, mp)
            except ErrorMercadoPago as e:
                s.rollback()
                log.error("No se pudo cancelar la suscripción: %s", e)
                return mensaje(request, "No pudimos cancelar", "Inténtalo de nuevo en unos minutos o escríbenos a hola@calza.cl.", estado=502)
        return RedirectResponse(f"/cuenta/{token}", status_code=303)

    def avisar_rechazos() -> None:
        from .avisos import avisar_cobros_rechazados

        try:
            with Sesion() as s:
                avisar_cobros_rechazados(s, wa, url_publica=url_publica, correo=correo, idioma=idioma_whatsapp)
        except Exception:
            log.exception("No se pudieron enviar los avisos de cobro rechazado")

    @router.post("/pagos/mercadopago/webhook")
    async def webhook_mp(request: Request, tareas: BackgroundTasks):
        """Avisos de Mercado Pago. Se verifica la firma y luego se consulta la API: nunca se confía en el aviso."""
        if mp is None:
            return PlainTextResponse("no configurado", status_code=503)
        try:
            cuerpo = await request.json()
        except ValueError:
            cuerpo = {}
        tipo = request.query_params.get("type") or cuerpo.get("type") or ""
        data_id = request.query_params.get("data.id") or str((cuerpo.get("data") or {}).get("id") or "")
        if not data_id:
            return PlainTextResponse("ok")
        if mp_webhook_secreto and not verificar_firma(
            mp_webhook_secreto, request.headers.get("x-signature"), request.headers.get("x-request-id"), data_id,
        ):
            return PlainTextResponse("firma inválida", status_code=401)
        with Sesion() as s:
            try:
                if tipo == "subscription_preapproval":
                    actualizar_mandato(s, mp, data_id)
                elif tipo == "subscription_authorized_payment":
                    pago = registrar_cobro(s, mp, data_id)
                    if pago is not None and pago.estado == "rechazado" and (wa is not None or correo is not None):
                        tareas.add_task(avisar_rechazos)  # después de responder a Mercado Pago
            except ErrorMercadoPago as e:
                if e.no_existe:  # p. ej. la notificación de prueba del panel: reintentarla no sirve
                    log.warning("Aviso de Mercado Pago de un recurso que no existe (%s %s): se ignora", tipo, data_id)
                    return PlainTextResponse("ok")
                log.error("No se pudo procesar el aviso de Mercado Pago (%s %s): %s", tipo, data_id, e)
                return PlainTextResponse("error", status_code=502)  # Mercado Pago reintentará
        return PlainTextResponse("ok")

    @router.get("/pagos/mercadopago/retorno", response_class=HTMLResponse)
    def retorno(request: Request, preapproval_id: str = ""):
        """Mercado Pago devuelve aquí al cliente después de suscribirse."""
        mandato, inicio_cobro = None, None
        if mp is not None and preapproval_id:
            with Sesion() as s:
                try:
                    mandato = actualizar_mandato(s, mp, preapproval_id)
                except ErrorMercadoPago as e:
                    log.error("No se pudo confirmar la suscripción en el retorno: %s", e)
                if mandato is not None:
                    suscripcion = s.scalar(select(Suscripcion).where(Suscripcion.empresa_id == mandato.empresa_id))
                    if suscripcion.vigente_hasta > ahora():
                        inicio_cobro = _fecha(suscripcion.vigente_hasta)
        estado = mandato.estado if mandato else None
        if estado == "authorized":
            texto = (f"Tu plan quedó con renovación automática. El primer cobro será el {inicio_cobro}: hasta entonces no pagas nada."
                     if inicio_cobro else "Tu plan quedó activo con renovación automática. Te enviaremos la factura a tu correo.")
            return mensaje(request, "¡Suscripción lista!", texto, chip="Activa", chip_clase="ok")
        if estado in ("cancelled", "paused"):
            return mensaje(request, "La suscripción no se completó", "No se realizó ningún cargo. Puedes intentarlo de nuevo desde tu cuenta.", chip="Sin cargo", chip_clase="error")
        return mensaje(request, "Estamos confirmando tu suscripción", "Puede tardar unos minutos. Te avisaremos cuando esté lista.", chip="Pendiente", chip_clase="oro")

    @router.get("/terminos", response_class=HTMLResponse)
    def terminos(request: Request):
        return plantillas.TemplateResponse(request, "legal.html", {"titulo": "Términos del servicio", "fecha": legal.FECHA, "secciones": legal.terminos(prestador)})

    @router.get("/privacidad", response_class=HTMLResponse)
    def privacidad(request: Request):
        return plantillas.TemplateResponse(request, "legal.html", {"titulo": "Política de privacidad", "fecha": legal.FECHA, "secciones": legal.privacidad(prestador)})

    return router
