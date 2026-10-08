"""Validación y formato de RUT chileno (dígito verificador con módulo 11)."""

from __future__ import annotations

import re


def limpiar(rut: str) -> str:
    return re.sub(r"[^0-9kK]", "", rut or "").upper()


def digito_verificador(cuerpo: str) -> str:
    suma, factor = 0, 2
    for d in reversed(cuerpo):
        suma += int(d) * factor
        factor = 2 if factor == 7 else factor + 1
    resto = 11 - suma % 11
    return {11: "0", 10: "K"}.get(resto, str(resto))


def es_valido(rut: str) -> bool:
    r = limpiar(rut)
    if len(r) < 2 or not r[:-1].isdigit():
        return False
    return digito_verificador(r[:-1]) == r[-1]


def formatear(rut: str) -> str:
    """'761234567' -> '76.123.456-7'."""
    r = limpiar(rut)
    cuerpo, dv = r[:-1], r[-1]
    return f"{int(cuerpo):,}".replace(",", ".") + f"-{dv}"
