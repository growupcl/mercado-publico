"""Cliente de Mercado Pago para suscripciones (cobro automático mensual o anual).

Flujo:
1. `crear_suscripcion` crea una suscripción pendiente y devuelve la URL donde el cliente
   ingresa su tarjeta (crédito o débito).
2. Mercado Pago avisa por webhook cuando la suscripción se autoriza o cancela
   (`subscription_preapproval`) y cada vez que realiza un cobro (`subscription_authorized_payment`).
3. Nunca confiamos en el aviso por sí solo: siempre volvemos a consultar la API.

Documentación: https://www.mercadopago.cl/developers/es/docs/subscriptions
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

URL_API = "https://api.mercadopago.com"


class ErrorMercadoPago(Exception):
    pass


@dataclass
class SuscripcionMP:
    id: str
    estado: str  # pending, authorized, paused, cancelled
    referencia: str
    monto: float
    url_pago: str = ""


@dataclass
class CobroMP:
    id: str
    suscripcion_id: str
    estado_pago: str  # approved, rejected, pending... (estado del pago asociado al cobro)
    monto: float


def verificar_firma(secreto: str, x_signature: str | None, x_request_id: str | None, data_id: str) -> bool:
    """Valida el encabezado x-signature de los webhooks ("ts=...,v1=...")."""
    if not x_signature:
        return False
    partes = dict(p.strip().split("=", 1) for p in x_signature.split(",") if "=" in p)
    ts, v1 = partes.get("ts"), partes.get("v1")
    if not ts or not v1:
        return False
    manifiesto = f"id:{data_id.lower() if data_id.isalnum() else data_id};"
    if x_request_id:
        manifiesto += f"request-id:{x_request_id};"
    manifiesto += f"ts:{ts};"
    esperado = hmac.new(secreto.encode(), manifiesto.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, v1)


def _fecha_iso(fecha: datetime) -> str:
    return fecha.strftime("%Y-%m-%dT%H:%M:%S.000Z")  # nuestras fechas están en UTC


class ClienteMercadoPago:
    def __init__(self, access_token: str, *, http: httpx.Client | None = None, url_base: str = URL_API) -> None:
        if not access_token:
            raise ErrorMercadoPago("Falta MERCADOPAGO_ACCESS_TOKEN.")
        self._url = url_base.rstrip("/")
        self._http = http or httpx.Client(timeout=20)
        self._headers = {"Authorization": f"Bearer {access_token}"}

    def _pedir(self, metodo: str, ruta: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = dict(self._headers)
        if metodo in ("POST", "PUT"):
            headers["X-Idempotency-Key"] = str(uuid.uuid4())
        try:
            r = self._http.request(metodo, f"{self._url}{ruta}", json=json, headers=headers)
        except httpx.TransportError as e:
            raise ErrorMercadoPago(f"Error de red con Mercado Pago: {e}") from e
        try:
            datos = r.json()
        except ValueError:
            raise ErrorMercadoPago(f"Mercado Pago respondió HTTP {r.status_code} sin JSON")
        if r.status_code >= 400:
            raise ErrorMercadoPago(f"Mercado Pago respondió HTTP {r.status_code}: {datos.get('message') or datos}")
        return datos

    @staticmethod
    def _suscripcion(datos: dict[str, Any]) -> SuscripcionMP:
        return SuscripcionMP(
            id=str(datos["id"]), estado=datos.get("status", ""), referencia=datos.get("external_reference", ""),
            monto=float((datos.get("auto_recurring") or {}).get("transaction_amount") or 0), url_pago=datos.get("init_point", ""),
        )

    def crear_suscripcion(
        self, *, referencia: str, motivo: str, email: str, monto: int, meses: int, url_retorno: str,
        inicio: datetime | None = None,
    ) -> SuscripcionMP:
        """Suscripción pendiente: el cliente la autoriza en `url_pago`. Si hay `inicio`, el primer cobro es en esa fecha."""
        recurrencia: dict[str, Any] = {
            "frequency": meses, "frequency_type": "months", "transaction_amount": monto, "currency_id": "CLP",
        }
        if inicio is not None:
            recurrencia["start_date"] = _fecha_iso(inicio)
        datos = self._pedir("POST", "/preapproval", {
            "reason": motivo, "external_reference": referencia, "payer_email": email,
            "auto_recurring": recurrencia, "back_url": url_retorno, "status": "pending",
        })
        return self._suscripcion(datos)

    def obtener_suscripcion(self, suscripcion_id: str) -> SuscripcionMP:
        return self._suscripcion(self._pedir("GET", f"/preapproval/{suscripcion_id}"))

    def cancelar_suscripcion(self, suscripcion_id: str) -> SuscripcionMP:
        return self._suscripcion(self._pedir("PUT", f"/preapproval/{suscripcion_id}", {"status": "cancelled"}))

    def obtener_cobro(self, cobro_id: str) -> CobroMP:
        """Un cobro de una suscripción (authorized payment) y el estado de su pago."""
        datos = self._pedir("GET", f"/authorized_payments/{cobro_id}")
        pago = datos.get("payment") or {}
        return CobroMP(
            id=str(datos["id"]), suscripcion_id=str(datos.get("preapproval_id", "")),
            estado_pago=pago.get("status", ""), monto=float(datos.get("transaction_amount") or 0),
        )
