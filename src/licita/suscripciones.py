"""Registro de empresas, prueba gratuita, pagos con Flow y vencimientos.

Modelo de cobro: pago por período (mensual o anual) con un enlace de Flow, que acepta Webpay
(débito y crédito). Al pagar, la suscripción se extiende desde la fecha de término vigente,
así nadie pierde días de su prueba ni de un período ya pagado.
"""

from __future__ import annotations

import calendar
import hashlib
import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import anthropic
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import rut as rutlib
from .calce import tokens
from .db import Empresa, Pago, Suscripcion, ahora
from .flow import ESTADO_ANULADO, ESTADO_RECHAZADO, ClienteFlow
from .ia import AsistenteIA, ErrorIA
from .planes import CUPOS_FUNDADOR, DIAS_PRUEBA, PERIODICIDADES, PLANES, monto
from .whatsapp import normalizar_telefono

log = logging.getLogger(__name__)

REGIONES = (
    "Arica y Parinacota", "Tarapacá", "Antofagasta", "Atacama", "Coquimbo", "Valparaíso",
    "Metropolitana", "O'Higgins", "Maule", "Ñuble", "Biobío", "Araucanía", "Los Ríos",
    "Los Lagos", "Aysén", "Magallanes",
)


class ErrorRegistro(Exception):
    def __init__(self, errores: dict[str, str]):
        super().__init__("; ".join(errores.values()))
        self.errores = errores


@dataclass
class DatosRegistro:
    nombre: str
    rut: str
    razon_social: str
    giro: str
    email: str
    whatsapp: str
    descripcion: str
    regiones: list[str] = field(default_factory=list)
    monto_max: float | None = None
    plan: str = "pyme"
    periodicidad: str = "mensual"
    acepta_whatsapp: bool = False
    acepta_terminos: bool = False


# --- Acceso a la cuenta con enlace privado ---

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generar_token(empresa: Empresa) -> str:
    """Crea un enlace de acceso nuevo (invalida el anterior). Solo guardamos su hash."""
    token = secrets.token_urlsafe(32)
    empresa.token_cuenta_hash = _hash(token)
    return token


def empresa_por_token(session: Session, token: str) -> Empresa | None:
    if not token or len(token) < 20:
        return None
    return session.scalar(select(Empresa).where(Empresa.token_cuenta_hash == _hash(token)))


def suscripcion_de(session: Session, empresa: Empresa) -> Suscripcion | None:
    return session.scalar(select(Suscripcion).where(Suscripcion.empresa_id == empresa.id))


# --- Registro ---

def validar(datos: DatosRegistro) -> dict[str, str]:
    errores: dict[str, str] = {}
    if len(datos.nombre.strip()) < 2:
        errores["nombre"] = "Escribe el nombre de tu empresa."
    if not rutlib.es_valido(datos.rut):
        errores["rut"] = "El RUT no es válido. Revisa el dígito verificador."
    if len(datos.razon_social.strip()) < 2:
        errores["razon_social"] = "Escribe la razón social (la necesitamos para la factura)."
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", datos.email.strip()):
        errores["email"] = "Escribe un correo válido."
    telefono = normalizar_telefono(datos.whatsapp)
    if not re.fullmatch(r"569\d{8}", telefono):
        errores["whatsapp"] = "Escribe un celular chileno, por ejemplo +56 9 1234 5678."
    if len(datos.descripcion.strip()) < 20:
        errores["descripcion"] = "Cuéntanos con más detalle qué vende tu empresa (al menos una frase)."
    if any(r not in REGIONES for r in datos.regiones):
        errores["regiones"] = "Elige regiones de la lista."
    if datos.plan not in PLANES:
        errores["plan"] = "Elige un plan."
    if datos.periodicidad not in PERIODICIDADES:
        errores["periodicidad"] = "Elige pago mensual o anual."
    if not datos.acepta_whatsapp:
        errores["acepta_whatsapp"] = "Necesitamos tu autorización para enviarte las licitaciones por WhatsApp."
    if not datos.acepta_terminos:
        errores["acepta_terminos"] = "Debes aceptar los términos y la política de privacidad."
    return errores


def registrar_empresa(
    session: Session, datos: DatosRegistro, *, ia: AsistenteIA | None = None, momento: datetime | None = None
) -> tuple[Empresa, str]:
    """Crea la empresa con 14 días de prueba del plan Pro. Devuelve (empresa, token de acceso)."""
    momento = momento or ahora()
    errores = validar(datos)
    rut = rutlib.limpiar(datos.rut)
    telefono = normalizar_telefono(datos.whatsapp)
    if not errores and session.scalar(select(Empresa.id).where(Empresa.rut == rut)):
        errores["rut"] = "Ya existe una cuenta con este RUT. Escribe CUENTA a Calza por WhatsApp para recibir tu enlace de acceso."
    if not errores and session.scalar(select(Empresa.id).where(Empresa.whatsapp == telefono)):
        errores["whatsapp"] = "Este WhatsApp ya está registrado. Escribe CUENTA a Calza por WhatsApp para recibir tu enlace de acceso."
    if errores:
        raise ErrorRegistro(errores)

    palabras_clave: list[str] = []
    if ia is not None:
        try:
            perfil = ia.extraer_perfil(datos.descripcion)
            palabras_clave = perfil.palabras_clave + perfil.rubros
        except (ErrorIA, anthropic.APIError) as e:
            log.warning("No se pudo extraer el perfil con IA; se usan las palabras de la descripción: %s", e)
    if not palabras_clave:
        palabras_clave = sorted(tokens(datos.descripcion))

    empresa = Empresa(
        nombre=datos.nombre.strip(), descripcion=datos.descripcion.strip(), regiones=datos.regiones,
        monto_max=datos.monto_max, palabras_clave=palabras_clave, whatsapp=telefono,
        plan="pro",  # durante la prueba tiene todo
        rut=rut, razon_social=datos.razon_social.strip(), giro=datos.giro.strip(), email=datos.email.strip().lower(),
        consentimiento_whatsapp_en=momento,
    )
    session.add(empresa)
    session.flush()
    token = generar_token(empresa)
    session.add(Suscripcion(
        empresa_id=empresa.id, plan=datos.plan, periodicidad=datos.periodicidad, estado="prueba",
        vigente_hasta=momento + timedelta(days=DIAS_PRUEBA),
    ))
    session.commit()
    return empresa, token


# --- Pagos ---

def sumar_meses(fecha: datetime, meses: int) -> datetime:
    mes = fecha.month - 1 + meses
    anio, mes = fecha.year + mes // 12, mes % 12 + 1
    return fecha.replace(year=anio, month=mes, day=min(fecha.day, calendar.monthrange(anio, mes)[1]))


def fundador_disponible(session: Session) -> bool:
    usados = session.scalar(select(func.count()).select_from(Suscripcion).where(Suscripcion.precio_fundador.is_(True))) or 0
    return usados < CUPOS_FUNDADOR


def cotizar(session: Session, suscripcion: Suscripcion, plan: str, periodicidad: str) -> tuple[int, bool]:
    """Monto a pagar y si aplica el precio fundador (se mantiene de por vida una vez obtenido)."""
    fundador = plan == "pro" and (suscripcion.precio_fundador or fundador_disponible(session))
    return monto(plan, periodicidad, fundador=fundador), fundador


def iniciar_pago(
    session: Session, empresa: Empresa, flow: ClienteFlow, *, plan: str, periodicidad: str, url_publica: str
) -> str:
    """Registra el pago pendiente y devuelve la URL de Flow donde el cliente paga."""
    if plan not in PLANES or periodicidad not in PERIODICIDADES:
        raise ValueError("Plan o periodicidad no válidos.")
    suscripcion = suscripcion_de(session, empresa)
    valor, fundador = cotizar(session, suscripcion, plan, periodicidad)
    pago = Pago(
        empresa_id=empresa.id, plan=plan, periodicidad=periodicidad, monto=valor, precio_fundador=fundador,
        orden_comercio=f"CALZA-{empresa.id}-{secrets.token_hex(4).upper()}",
    )
    session.add(pago)
    session.flush()
    creado = flow.crear_pago(
        orden_comercio=pago.orden_comercio,
        asunto=f"Calza plan {PLANES[plan].nombre} {periodicidad}",
        monto=valor, email=empresa.email,
        url_confirmacion=f"{url_publica}/pagos/flow/confirmacion",
        url_retorno=f"{url_publica}/pagos/flow/retorno",
    )
    pago.flow_token, pago.flow_order = creado.token, creado.flow_order
    session.commit()
    return creado.url


def confirmar_pago(session: Session, flow: ClienteFlow, token: str, *, momento: datetime | None = None) -> Pago | None:
    """Consulta a Flow el resultado y, si está pagado, activa o extiende la suscripción. Es idempotente."""
    momento = momento or ahora()
    pago = session.scalar(select(Pago).where(Pago.flow_token == token))
    if pago is None:
        log.warning("Confirmación de Flow con un token desconocido")
        return None
    if pago.estado == "pagado":
        return pago
    estado = flow.estado_pago(token)
    if estado.orden_comercio != pago.orden_comercio or round(estado.monto) != pago.monto:
        log.error("El pago %s no coincide con lo informado por Flow (orden %s, monto %s)", pago.orden_comercio, estado.orden_comercio, estado.monto)
        return pago
    if estado.pagado:
        empresa = session.get(Empresa, pago.empresa_id)
        suscripcion = suscripcion_de(session, empresa)
        inicio = max(momento, suscripcion.vigente_hasta)
        suscripcion.vigente_hasta = sumar_meses(inicio, PERIODICIDADES[pago.periodicidad])
        suscripcion.plan, suscripcion.periodicidad, suscripcion.estado = pago.plan, pago.periodicidad, "activa"
        suscripcion.precio_fundador = suscripcion.precio_fundador or pago.precio_fundador
        # El plan pagado se activa de inmediato; los días de prueba que quedaban se suman al período.
        empresa.plan = pago.plan
        pago.estado, pago.pagado_en = "pagado", momento
    elif estado.estado == ESTADO_RECHAZADO:
        pago.estado = "rechazado"
    elif estado.estado == ESTADO_ANULADO:
        pago.estado = "anulado"
    session.commit()
    return pago


# --- Vencimientos ---

def revisar_vencimientos(session: Session, *, momento: datetime | None = None) -> list[Empresa]:
    """Pasa al plan gratis a quienes terminaron su prueba o período sin pagar. Devuelve esas empresas."""
    momento = momento or ahora()
    vencidas = []
    for suscripcion in session.scalars(select(Suscripcion).where(
        Suscripcion.estado.in_(("prueba", "activa")), Suscripcion.vigente_hasta < momento,
    )):
        suscripcion.estado = "vencida"
        empresa = session.get(Empresa, suscripcion.empresa_id)
        empresa.plan = "gratis"
        vencidas.append(empresa)
    session.commit()
    return vencidas


def por_vencer(session: Session, *, dias: int = 3, momento: datetime | None = None) -> list[tuple[Empresa, Suscripcion]]:
    momento = momento or ahora()
    filas = session.execute(
        select(Empresa, Suscripcion).join(Suscripcion, Suscripcion.empresa_id == Empresa.id).where(
            Suscripcion.estado.in_(("prueba", "activa")),
            Suscripcion.vigente_hasta >= momento, Suscripcion.vigente_hasta < momento + timedelta(days=dias),
        )
    ).all()
    return [tuple(f) for f in filas]
