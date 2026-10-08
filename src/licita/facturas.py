"""Facturas de los pagos recibidos, para emitirlas a mano en el portal gratuito del SII.

Mientras el volumen sea bajo, Calza no emite la factura: deja cada una lista para copiar al formulario del SII
(datos del cliente, glosa, neto, IVA y total) y guarda el folio cuando ya se emitió.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import rut as rutlib
from .db import Empresa, Pago, ahora
from .planes import PERIODICIDADES, PLANES, formato_pesos

TASA_IVA_PORCENTAJE = 19


def iva_de(neto: int) -> int:
    """IVA como lo calcula el SII: 19% del neto, redondeado al peso (0,5 hacia arriba)."""
    return (neto * TASA_IVA_PORCENTAJE + 50) // 100


def desglose_iva(total: int) -> tuple[int, int]:
    """Separa un monto con IVA incluido en (neto, IVA).

    El portal del SII calcula el IVA a partir del neto, así que se busca el neto que reproduce el total exacto.
    Si no existe (pasa con algunos montos), se usa el más cercano y el total de la factura difiere en $1.
    """
    total = int(total)
    base = round(total * 100 / (100 + TASA_IVA_PORCENTAJE))
    candidatos = sorted(range(base - 2, base + 3), key=lambda n: (abs(n + iva_de(n) - total), n + iva_de(n) > total))
    neto = candidatos[0]
    return neto, iva_de(neto)


@dataclass(frozen=True)
class FacturaPendiente:
    pago_id: int
    fecha_pago: datetime
    razon_social: str
    rut: str
    giro: str
    direccion: str
    comuna: str
    email: str
    glosa: str
    neto: int
    iva: int
    total: int  # lo cobrado por Mercado Pago

    @property
    def diferencia(self) -> int:
        """Total de la factura menos lo cobrado (0 salvo en montos que el SII no puede reproducir)."""
        return self.neto + self.iva - self.total

    @property
    def datos_incompletos(self) -> list[str]:
        faltan = [nombre for nombre, valor in (
            ("RUT", self.rut), ("razón social", self.razon_social), ("giro", self.giro),
            ("dirección", self.direccion), ("comuna", self.comuna),
        ) if not valor]
        return faltan


def glosa(pago: Pago) -> str:
    plan = PLANES[pago.plan].nombre if pago.plan in PLANES else pago.plan
    meses = PERIODICIDADES.get(pago.periodicidad, 1)
    duracion = "1 mes" if meses == 1 else f"{meses} meses"
    return f"Suscripción Calza plan {plan} {pago.periodicidad} ({duracion})"


def pendientes(session: Session) -> list[FacturaPendiente]:
    """Pagos aprobados que aún no tienen factura, del más antiguo al más reciente."""
    pagos = session.scalars(
        select(Pago).where(Pago.estado == "pagado", Pago.factura_emitida.is_(False)).order_by(Pago.pagado_en, Pago.id)
    )
    facturas = []
    for p in pagos:
        e = session.get(Empresa, p.empresa_id)
        neto, iva = desglose_iva(p.monto)
        facturas.append(FacturaPendiente(
            pago_id=p.id, fecha_pago=p.pagado_en or p.creado_en, razon_social=e.razon_social or "",
            rut=rutlib.formatear(e.rut) if e.rut else "", giro=e.giro or "", direccion=e.direccion or "",
            comuna=e.comuna or "", email=e.email or "", glosa=glosa(p), neto=neto, iva=iva, total=p.monto,
        ))
    return facturas


def marcar_emitida(session: Session, pago_id: int, folio: str, *, momento: datetime | None = None) -> Pago:
    folio = folio.strip()
    if not folio.isdigit():
        raise ValueError("El folio debe ser el número de la factura que entregó el SII.")
    pago = session.get(Pago, pago_id)
    if pago is None:
        raise ValueError(f"No existe el pago #{pago_id}.")
    if pago.estado != "pagado":
        raise ValueError(f"El pago #{pago_id} no está pagado: no corresponde factura.")
    if pago.factura_emitida and pago.factura_folio != folio:
        raise ValueError(f"El pago #{pago_id} ya tiene la factura folio {pago.factura_folio}.")
    pago.factura_emitida, pago.factura_folio = True, folio
    pago.factura_emitida_en = pago.factura_emitida_en or momento or ahora()
    return pago


def texto(f: FacturaPendiente) -> str:
    lineas = [
        f"Pago #{f.pago_id} · pagado el {f.fecha_pago:%d-%m-%Y}",
        f"  RUT receptor:   {f.rut}",
        f"  Razón social:   {f.razon_social}",
        f"  Giro:           {f.giro}",
        f"  Dirección:      {f.direccion}",
        f"  Comuna:         {f.comuna}",
        f"  Correo:         {f.email}",
        f"  Detalle:        {f.glosa} · cantidad 1 · precio {formato_pesos(f.neto)}",
        f"  Neto {formato_pesos(f.neto)} · IVA {formato_pesos(f.iva)} · Total {formato_pesos(f.neto + f.iva)}",
    ]
    if f.diferencia:
        lineas.append(f"  ⚠ Con el cálculo del SII el total queda {formato_pesos(f.neto + f.iva)}, "
                      f"no {formato_pesos(f.total)} como se cobró (diferencia de ${abs(f.diferencia)}).")
    if f.datos_incompletos:
        lineas.append(f"  ⚠ Faltan datos: {', '.join(f.datos_incompletos)} (pídeselos al cliente antes de emitir)")
    return "\n".join(lineas)


def csv_facturas(facturas: list[FacturaPendiente]) -> str:
    salida = io.StringIO()
    w = csv.writer(salida, delimiter=";")
    w.writerow(["pago_id", "fecha_pago", "rut", "razon_social", "giro", "direccion", "comuna", "email",
                "detalle", "neto", "iva", "total"])
    for f in facturas:
        w.writerow([f.pago_id, f"{f.fecha_pago:%d-%m-%Y}", f.rut, f.razon_social, f.giro, f.direccion, f.comuna,
                    f.email, f.glosa, f.neto, f.iva, f.neto + f.iva])
    return salida.getvalue()
