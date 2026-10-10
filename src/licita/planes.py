"""Planes y precios de Calza (montos en CLP con IVA incluido), según el modelo financiero."""

from __future__ import annotations

from dataclasses import dataclass

DIAS_PRUEBA = 14
CUPOS_FUNDADOR = 100
DESCUENTO_ANUAL = 0.10


@dataclass(frozen=True)
class Plan:
    codigo: str
    nombre: str
    mensual: int
    beneficios: tuple[str, ...]


PLANES = {
    "pyme": Plan("pyme", "Pyme", 19_990, (
        "Resumen diario por WhatsApp con las licitaciones que calzan con tu empresa",
        "Detalle de cada licitación y puntaje de calce",
        "Análisis de bases en PDF o Word (hasta 15 al mes)",
    )),
    "pro": Plan("pro", "Pro", 59_990, (
        "Todo lo del plan Pyme",
        "Precios de referencia y quién suele ganar",
        "Alertas urgentes de Compra Ágil",
        "Análisis de bases en PDF o Word (hasta 50 al mes)",
    )),
}
PRECIO_FUNDADOR_PRO = 39_990
# Planes con precios de referencia y alertas urgentes de Compra Ágil (la prueba gratuita es Pro).
PLANES_CON_PRECIOS = ("pro", "consultora")
PLANES_CON_COMPRA_AGIL = ("pro", "consultora")
PERIODICIDADES = {"mensual": 1, "anual": 12}
# Precios anuales fijados a mano: el SII calcula el IVA desde el neto y con $215.900 la factura no cuadraba exacto.
ANUAL_AJUSTADO = {("pyme", False): 215_890}


def precio_mensual(plan: str, *, fundador: bool = False) -> int:
    if plan == "pro" and fundador:
        return PRECIO_FUNDADOR_PRO
    return PLANES[plan].mensual


def monto(plan: str, periodicidad: str, *, fundador: bool = False) -> int:
    """Monto a pagar por el período, redondeado a la centena (como en el modelo financiero)."""
    mensual = precio_mensual(plan, fundador=fundador)
    if periodicidad == "anual":
        return ANUAL_AJUSTADO.get((plan, fundador and plan == "pro"), int(round(mensual * 12 * (1 - DESCUENTO_ANUAL), -2)))
    return mensual


def formato_pesos(valor: int | float) -> str:
    return "$" + f"{round(valor):,}".replace(",", ".")
