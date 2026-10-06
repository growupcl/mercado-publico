"""Cliente de la WhatsApp Cloud API de Meta (sin intermediarios, sin recargo por mensaje).

Reglas de cobro que guían el diseño:
- Fuera de la ventana de 24 h solo se pueden enviar plantillas aprobadas por Meta (con costo).
- Dentro de la ventana (el usuario escribió o tocó un botón hace menos de 24 h) los mensajes
  de texto libre son gratis. Por eso el resumen diario es UNA plantilla con botones, y todo
  el detalle se envía después, cuando el usuario responde.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any

import httpx

GRAPH_URL = "https://graph.facebook.com"


class ErrorWhatsApp(Exception):
    pass


def normalizar_telefono(telefono: str) -> str:
    """WhatsApp usa el número en formato internacional sin '+' ni espacios: 56912345678."""
    return re.sub(r"\D", "", telefono or "")


def limpiar_parametro(texto: str, largo: int = 60) -> str:
    """Los parámetros de plantilla no admiten saltos de línea, tabs ni más de 4 espacios seguidos."""
    texto = re.sub(r"\s+", " ", str(texto)).strip()
    return texto if len(texto) <= largo else texto[: largo - 1].rstrip() + "…"


def verificar_firma(app_secret: str, cuerpo: bytes, firma: str | None) -> bool:
    """Valida el encabezado X-Hub-Signature-256 que Meta agrega a cada webhook."""
    if not firma or not firma.startswith("sha256="):
        return False
    esperado = hmac.new(app_secret.encode(), cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, firma.removeprefix("sha256="))


class ClienteWhatsApp:
    def __init__(
        self, token: str, phone_number_id: str, *, version: str = "v23.0", http: httpx.Client | None = None
    ) -> None:
        if not token or not phone_number_id:
            raise ErrorWhatsApp("Faltan WHATSAPP_TOKEN o WHATSAPP_PHONE_NUMBER_ID.")
        self._url = f"{GRAPH_URL}/{version}/{phone_number_id}/messages"
        self._http = http or httpx.Client(timeout=20)
        self._headers = {"Authorization": f"Bearer {token}"}

    def _enviar(self, cuerpo: dict[str, Any]) -> str:
        cuerpo = {"messaging_product": "whatsapp", **cuerpo}
        try:
            r = self._http.post(self._url, json=cuerpo, headers=self._headers)
        except httpx.TransportError as e:
            raise ErrorWhatsApp(f"Error de red al enviar a WhatsApp: {e}") from e
        if r.status_code >= 400:
            try:
                error = r.json().get("error", {})
                detalle = f"{error.get('code')}: {error.get('message')}"
            except ValueError:
                detalle = r.text[:200]
            raise ErrorWhatsApp(f"WhatsApp respondió HTTP {r.status_code} ({detalle})")
        return r.json()["messages"][0]["id"]

    def enviar_texto(self, telefono: str, texto: str) -> str:
        """Texto libre. Solo funciona dentro de la ventana de 24 h (y ahí es gratis)."""
        return self._enviar({
            "to": normalizar_telefono(telefono),
            "type": "text",
            "text": {"body": texto[:4096], "preview_url": False},
        })

    def enviar_plantilla(
        self,
        telefono: str,
        nombre: str,
        *,
        idioma: str = "es",
        parametros: list[str] | None = None,
        payloads_botones: list[str] | None = None,
    ) -> str:
        """Plantilla aprobada por Meta. Los payloads identifican qué botón de respuesta rápida tocó el usuario."""
        componentes: list[dict[str, Any]] = []
        if parametros:
            componentes.append({
                "type": "body",
                "parameters": [{"type": "text", "text": limpiar_parametro(p)} for p in parametros],
            })
        for i, payload in enumerate(payloads_botones or []):
            componentes.append({
                "type": "button",
                "sub_type": "quick_reply",
                "index": str(i),
                "parameters": [{"type": "payload", "payload": payload}],
            })
        return self._enviar({
            "to": normalizar_telefono(telefono),
            "type": "template",
            "template": {"name": nombre, "language": {"code": idioma}, "components": componentes},
        })
