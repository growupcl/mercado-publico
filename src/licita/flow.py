"""Cliente de la API de pagos de Flow (flow.cl): Webpay, tarjetas de crédito y débito.

Flujo de un pago:
1. `crear_pago` devuelve una URL; el cliente paga ahí.
2. Flow avisa a nuestra URL de confirmación con un token (servidor a servidor).
3. Con `estado_pago(token)` confirmamos el resultado antes de activar el plan.

Cada solicitud se firma con HMAC-SHA256 usando la clave secreta (parámetro "s").
Documentación: https://www.flow.cl/docs/api.html
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

import httpx

URL_PRODUCCION = "https://www.flow.cl/api"
URL_SANDBOX = "https://sandbox.flow.cl/api"

ESTADO_PENDIENTE, ESTADO_PAGADO, ESTADO_RECHAZADO, ESTADO_ANULADO = 1, 2, 3, 4


class ErrorFlow(Exception):
    pass


def firmar(parametros: dict[str, Any], secreto: str) -> str:
    """Concatena nombre+valor de cada parámetro en orden alfabético y firma con HMAC-SHA256."""
    texto = "".join(f"{k}{parametros[k]}" for k in sorted(parametros))
    return hmac.new(secreto.encode(), texto.encode(), hashlib.sha256).hexdigest()


@dataclass
class PagoCreado:
    url: str
    token: str
    flow_order: int | None


@dataclass
class EstadoPago:
    estado: int
    orden_comercio: str
    monto: float
    flow_order: int | None

    @property
    def pagado(self) -> bool:
        return self.estado == ESTADO_PAGADO


class ClienteFlow:
    def __init__(self, api_key: str, secreto: str, *, url_base: str = URL_SANDBOX, http: httpx.Client | None = None) -> None:
        if not api_key or not secreto:
            raise ErrorFlow("Faltan FLOW_API_KEY o FLOW_SECRET_KEY.")
        self._api_key = api_key
        self._secreto = secreto
        self._url = url_base.rstrip("/")
        self._http = http or httpx.Client(timeout=20)

    def _firmados(self, parametros: dict[str, Any]) -> dict[str, Any]:
        p = {**parametros, "apiKey": self._api_key}
        p["s"] = firmar(p, self._secreto)
        return p

    def _respuesta(self, r: httpx.Response) -> dict[str, Any]:
        try:
            datos = r.json()
        except ValueError:
            raise ErrorFlow(f"Flow respondió HTTP {r.status_code} sin JSON")
        if r.status_code >= 400:
            raise ErrorFlow(f"Flow respondió HTTP {r.status_code}: {datos.get('message') or datos}")
        return datos

    def crear_pago(
        self, *, orden_comercio: str, asunto: str, monto: int, email: str, url_confirmacion: str, url_retorno: str
    ) -> PagoCreado:
        try:
            r = self._http.post(f"{self._url}/payment/create", data=self._firmados({
                "commerceOrder": orden_comercio, "subject": asunto, "currency": "CLP", "amount": monto,
                "email": email, "urlConfirmation": url_confirmacion, "urlReturn": url_retorno,
            }))
        except httpx.TransportError as e:
            raise ErrorFlow(f"Error de red con Flow: {e}") from e
        datos = self._respuesta(r)
        return PagoCreado(url=f"{datos['url']}?token={datos['token']}", token=datos["token"], flow_order=datos.get("flowOrder"))

    def estado_pago(self, token: str) -> EstadoPago:
        try:
            r = self._http.get(f"{self._url}/payment/getStatus", params=self._firmados({"token": token}))
        except httpx.TransportError as e:
            raise ErrorFlow(f"Error de red con Flow: {e}") from e
        datos = self._respuesta(r)
        return EstadoPago(
            estado=int(datos.get("status", 0)), orden_comercio=str(datos.get("commerceOrder", "")),
            monto=float(datos.get("amount") or 0), flow_order=datos.get("flowOrder"),
        )
