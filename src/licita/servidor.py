"""Servidor web: recibe los webhooks de WhatsApp.

Configurar en Meta (App > WhatsApp > Configuración) la URL https://<tu-dominio>/webhook/whatsapp
y el mismo token de verificación que WHATSAPP_VERIFY_TOKEN.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import sessionmaker

from .analisis import ServicioAnalisis
from .conversacion import procesar_webhook
from .whatsapp import ClienteWhatsApp, verificar_firma

log = logging.getLogger(__name__)


def crear_app(
    Sesion: sessionmaker,
    wa: ClienteWhatsApp | None = None,
    *,
    verify_token: str = "",
    app_secret: str = "",
    url_registro: str = "",
    phone_number_id: str = "",
    analisis: ServicioAnalisis | None = None,
    url_publica: str = "",
    permitir_sin_firma: bool = False,
    router_web: APIRouter | None = None,
) -> FastAPI:
    app = FastAPI(title="Calza")
    if router_web is not None:
        app.include_router(router_web)

    @app.get("/salud")
    def salud():
        return {"ok": True}

    if wa is None:
        return app
    if not verify_token:
        raise ValueError("Falta WHATSAPP_VERIFY_TOKEN.")
    if not app_secret and not permitir_sin_firma:
        raise ValueError("Falta WHATSAPP_APP_SECRET: sin él no se puede verificar que los mensajes vengan de Meta.")

    @app.get("/webhook/whatsapp")
    def verificar(request: Request):
        p = request.query_params
        if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == verify_token:
            return PlainTextResponse(p.get("hub.challenge", ""))
        raise HTTPException(status_code=403)

    def procesar(payload: dict) -> None:
        try:
            with Sesion() as s:
                procesar_webhook(
                    s, wa, payload, url_registro=url_registro, phone_number_id=phone_number_id, analisis=analisis,
                    url_publica=url_publica,
                )
        except Exception:
            log.exception("Error procesando webhook de WhatsApp")

    @app.post("/webhook/whatsapp")
    async def recibir(request: Request, tareas: BackgroundTasks):
        cuerpo = await request.body()
        if app_secret and not verificar_firma(app_secret, cuerpo, request.headers.get("X-Hub-Signature-256")):
            raise HTTPException(status_code=401, detail="Firma inválida")
        try:
            payload = json.loads(cuerpo)
        except ValueError:
            raise HTTPException(status_code=400, detail="JSON inválido")
        # Respondemos a Meta de inmediato y procesamos después: analizar unas bases toma más
        # que el tiempo de espera de Meta, que reintentaría y duplicaría las respuestas.
        tareas.add_task(procesar, payload)
        return {"ok": True}

    return app
