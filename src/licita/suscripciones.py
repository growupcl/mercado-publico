"""Registro de empresas, prueba gratuita, suscripciones con Mercado Pago y vencimientos.

Modelo de cobro: suscripción de Mercado Pago (tarjeta de crédito o débito) que cobra sola cada
mes o cada año. Si el cliente se suscribe durante la prueba, el primer cobro es al terminar la
prueba. Cada cobro aprobado extiende el período desde la fecha de término vigente.
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
from .db import Empresa, MandatoPago, Pago, Suscripcion, ahora
from .mercadopago import ClienteMercadoPago
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
    direccion: str = ""
    comuna: str = ""
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
    if len(datos.giro.strip()) < 3:
        errores["giro"] = "Escribe el giro de la empresa (lo exige la factura)."
    if len(datos.direccion.strip()) < 5:
        errores["direccion"] = "Escribe la dirección comercial (la exige la factura)."
    if len(datos.comuna.strip()) < 3:
        errores["comuna"] = "Escribe la comuna."
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
        direccion=datos.direccion.strip(), comuna=datos.comuna.strip(),
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


def iniciar_suscripcion(
    session: Session, empresa: Empresa, mp: ClienteMercadoPago, *, plan: str, periodicidad: str, url_publica: str,
    momento: datetime | None = None, monto_prueba: int | None = None,
) -> str:
    """Crea la suscripción en Mercado Pago y devuelve la URL donde el cliente ingresa su tarjeta.

    monto_prueba (solo desde la línea de comandos, `licita mp-prueba`): cobra ese monto de inmediato, sin esperar el
    fin de la prueba, para probar un cobro real de punta a punta sin pagar el precio del plan.
    """
    momento = momento or ahora()
    if plan not in PLANES or periodicidad not in PERIODICIDADES:
        raise ValueError("Plan o periodicidad no válidos.")
    suscripcion = suscripcion_de(session, empresa)
    valor, fundador = cotizar(session, suscripcion, plan, periodicidad)
    if monto_prueba is not None:
        valor, fundador = monto_prueba, False
    mandato = MandatoPago(
        empresa_id=empresa.id, plan=plan, periodicidad=periodicidad, monto=valor, precio_fundador=fundador,
        referencia=f"CALZA-{empresa.id}-{secrets.token_hex(4).upper()}",
    )
    session.add(mandato)
    session.flush()
    # Si aún le quedan días (prueba o período pagado), el primer cobro es cuando terminen.
    inicio = suscripcion.vigente_hasta if suscripcion.vigente_hasta > momento + timedelta(hours=1) else None
    motivo = f"Calza plan {PLANES[plan].nombre} {periodicidad}"
    if monto_prueba is not None:
        inicio, motivo = None, "Calza prueba de cobro"
    creada = mp.crear_suscripcion(
        referencia=mandato.referencia, motivo=motivo,
        email=empresa.email, monto=valor, meses=PERIODICIDADES[periodicidad],
        url_retorno=f"{url_publica}/pagos/mercadopago/retorno", inicio=inicio,
    )
    mandato.mp_id = creada.id
    session.commit()
    return creada.url_pago


def actualizar_mandato(session: Session, mp: ClienteMercadoPago, mp_id: str) -> MandatoPago | None:
    """Sincroniza el estado de una suscripción de Mercado Pago (autorizada, pausada o cancelada)."""
    remota = mp.obtener_suscripcion(mp_id)
    mandato = session.scalar(select(MandatoPago).where(MandatoPago.referencia == remota.referencia))
    if mandato is None or (mandato.mp_id and mandato.mp_id != remota.id):
        log.warning("Suscripción de Mercado Pago %s sin mandato conocido", mp_id)
        return None
    mandato.mp_id, mandato.estado = remota.id, remota.estado
    suscripcion = session.scalar(select(Suscripcion).where(Suscripcion.empresa_id == mandato.empresa_id))
    if remota.estado == "authorized" and suscripcion.mandato_activo_id != mandato.id:
        anterior = session.get(MandatoPago, suscripcion.mandato_activo_id) if suscripcion.mandato_activo_id else None
        suscripcion.mandato_activo_id = mandato.id
        suscripcion.plan, suscripcion.periodicidad = mandato.plan, mandato.periodicidad
        suscripcion.precio_fundador = suscripcion.precio_fundador or mandato.precio_fundador
        if anterior is not None and anterior.estado != "cancelled":
            # Cambio de plan: la suscripción anterior deja de cobrar.
            mp.cancelar_suscripcion(anterior.mp_id)
            anterior.estado = "cancelled"
    elif remota.estado in ("cancelled", "paused") and suscripcion.mandato_activo_id == mandato.id:
        # Sin renovación automática: mantiene lo pagado hasta su fecha de término.
        suscripcion.mandato_activo_id = None
    session.commit()
    return mandato


def registrar_cobro(session: Session, mp: ClienteMercadoPago, cobro_id: str, *, momento: datetime | None = None) -> Pago | None:
    """Registra un cobro de Mercado Pago; si fue aprobado, extiende la suscripción. Es idempotente."""
    momento = momento or ahora()
    existente = session.scalar(select(Pago).where(Pago.mp_cobro_id == str(cobro_id)))
    if existente is not None and existente.estado == "pagado":
        return existente
    cobro = mp.obtener_cobro(cobro_id)
    mandato = session.scalar(select(MandatoPago).where(MandatoPago.mp_id == cobro.suscripcion_id))
    if mandato is None:
        log.warning("Cobro %s de una suscripción desconocida (%s)", cobro_id, cobro.suscripcion_id)
        return None
    if cobro.estado_pago not in ("approved", "rejected"):
        return existente  # aún en proceso: Mercado Pago avisará de nuevo
    if round(cobro.monto) != mandato.monto:
        log.error("El cobro %s no coincide con el monto pactado (%s ≠ %s)", cobro_id, cobro.monto, mandato.monto)
        return existente
    pago = existente or Pago(
        empresa_id=mandato.empresa_id, plan=mandato.plan, periodicidad=mandato.periodicidad, monto=mandato.monto,
        mandato_id=mandato.id, mp_cobro_id=str(cobro_id),
    )
    session.add(pago)
    if cobro.estado_pago == "approved":
        empresa = session.get(Empresa, mandato.empresa_id)
        suscripcion = suscripcion_de(session, empresa)
        inicio = max(momento, suscripcion.vigente_hasta)
        suscripcion.vigente_hasta = sumar_meses(inicio, PERIODICIDADES[mandato.periodicidad])
        suscripcion.plan, suscripcion.periodicidad, suscripcion.estado = mandato.plan, mandato.periodicidad, "activa"
        empresa.plan = mandato.plan
        pago.estado, pago.pagado_en = "pagado", momento
    else:
        pago.estado = "rechazado"  # Mercado Pago reintenta el cobro por su cuenta
    session.commit()
    return pago


def cancelar_renovacion(session: Session, empresa: Empresa, mp: ClienteMercadoPago) -> bool:
    suscripcion = suscripcion_de(session, empresa)
    if not suscripcion.mandato_activo_id:
        return False
    mandato = session.get(MandatoPago, suscripcion.mandato_activo_id)
    mp.cancelar_suscripcion(mandato.mp_id)
    mandato.estado = "cancelled"
    suscripcion.mandato_activo_id = None
    session.commit()
    return True


# --- Vencimientos ---

# Con renovación automática, el cobro y su aviso pueden llegar unas horas después del término.
DIAS_GRACIA_RENOVACION = 3


def revisar_vencimientos(session: Session, *, momento: datetime | None = None) -> list[Empresa]:
    """Pasa al plan gratis a quienes terminaron su prueba o período sin pagar. Devuelve esas empresas."""
    momento = momento or ahora()
    vencidas = []
    for suscripcion in session.scalars(select(Suscripcion).where(
        Suscripcion.estado.in_(("prueba", "activa")), Suscripcion.vigente_hasta < momento,
    )):
        if suscripcion.mandato_activo_id and momento < suscripcion.vigente_hasta + timedelta(days=DIAS_GRACIA_RENOVACION):
            continue
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
