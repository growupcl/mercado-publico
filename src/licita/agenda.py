"""Agenda "Voy a postular": la empresa marca una licitación y Calza la acompaña hasta el resultado.

Avisa por WhatsApp (y por correo si no se pudo) cada plazo que importa: el fin de las preguntas al foro, la visita a
terreno, la publicación de las respuestas, dos días antes y el día del cierre. Después del cierre consulta el
resultado a Mercado Público y le cuenta quién ganó, a qué precio y, si fue ella, la felicita.
Las fechas salen del detalle de la licitación que ya guardamos (`raw["Fechas"]`), en hora de Chile.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import rut as rutlib
from .correo import ClienteCorreo, ErrorCorreo
from .db import Empresa, Licitacion, Postulacion, ahora, hora_chile
from .mercadopublico import MercadoPublicoClient, MercadoPublicoError, normalizar_licitacion, parsear_fecha
from .notificaciones import registrar, ventana_abierta
from .plantillas_whatsapp import RECORDATORIO_POSTULACION
from .precios import registrar_precios_adjudicacion
from .resumen import formato_pesos, url_ficha
from .whatsapp import ClienteWhatsApp, ErrorWhatsApp

log = logging.getLogger(__name__)

ESTADO_DESIERTA, ESTADO_ADJUDICADA, ESTADO_REVOCADA = 7, 8, 18
ESTADOS_FINALES = (ESTADO_DESIERTA, ESTADO_ADJUDICADA, ESTADO_REVOCADA)
# Tras el cierre se consulta el resultado una vez al día; pasado este plazo sin resultado, se deja de seguir.
DIAS_ESPERA_RESULTADO = 90
CONSULTAS_POR_EJECUCION = 150
MAXIMO_EN_AGENDA = 30


@dataclass
class Hito:
    clave: str
    cuando: datetime  # hora de Chile
    nombre: str  # para la lista de la agenda: "Preguntas al foro", "Cierre"…


def _fecha(lic: Licitacion, campo: str) -> datetime | None:
    return parsear_fecha(((lic.raw or {}).get("Fechas") or {}).get(campo))


def hitos(lic: Licitacion) -> list[Hito]:
    """Plazos de la licitación, en orden. Las Compras Ágiles solo tienen el cierre."""
    lista = [Hito("preguntas", _fecha(lic, "FechaFinal"), "Último día de preguntas al foro"),
             Hito("visita", _fecha(lic, "FechaVisitaTerreno"), "Visita a terreno"),
             Hito("respuestas", _fecha(lic, "FechaPubRespuestas"), "Respuestas del foro"),
             Hito("cierre", lic.fecha_cierre, "Cierre de ofertas")]
    lista = [h for h in lista if h.cuando and (lic.fecha_cierre is None or h.cuando <= lic.fecha_cierre or h.clave == "cierre")]
    return sorted(lista, key=lambda h: h.cuando)


def _cuando(fecha: datetime, ahora_cl: datetime) -> str:
    dias = (fecha.date() - ahora_cl.date()).days
    hora = fecha.strftime("%H:%M")
    if dias == 0:
        return f"hoy a las {hora}"
    if dias == 1:
        return f"mañana a las {hora}"
    return f"el {fecha.strftime('%d-%m')} a las {hora}"


def avisos_pendientes(post: Postulacion, lic: Licitacion, ahora_cl: datetime) -> list[tuple[str, str]]:
    """(clave, texto) de los plazos que toca avisar ahora y aún no se avisaron."""
    hechos, cierre = set(post.avisos or []), lic.fecha_cierre
    abierta = cierre is None or ahora_cl < cierre
    pendientes = []
    for h in hitos(lic):
        # "El día antes": desde la mañana del día anterior (la tarea corre temprano), hasta la hora del plazo.
        dia_antes = (h.cuando.date() - ahora_cl.date()).days <= 1 and ahora_cl < h.cuando
        if h.clave == "preguntas" and dia_antes:
            pendientes.append(("preguntas", f"⏰ El plazo para hacer preguntas en el foro cierra {_cuando(h.cuando, ahora_cl)}. "
                                            "Si algo de las bases no está claro, pregúntalo antes."))
        elif h.clave == "visita" and dia_antes:
            direccion = ((lic.raw or {}).get("DireccionVisita") or "").strip()
            pendientes.append(("visita", f"📍 La visita a terreno es {_cuando(h.cuando, ahora_cl)}"
                                         + (f" en {direccion}" if direccion else "") + ". Suele ser obligatoria: revisa las bases."))
        elif h.clave == "respuestas" and abierta and h.cuando <= ahora_cl <= h.cuando + timedelta(days=2):
            pendientes.append(("respuestas", "📢 Ya se publicaron las respuestas del foro. Revísalas en la ficha antes de "
                                             "cerrar tu oferta: a veces cambian lo que piden."))
    if cierre and abierta:
        dias = (cierre.date() - ahora_cl.date()).days
        if dias == 0:
            pendientes.append(("cierre_hoy", f"🚨 *Hoy a las {cierre.strftime('%H:%M')} cierra.* Sube tu oferta en Mercado "
                                             "Público antes de esa hora: después no se aceptan."))
        elif 1 <= dias <= 2:
            pendientes.append(("cierre_2d", f"⏳ Quedan {dias} {'día' if dias == 1 else 'días'}: cierra "
                                            f"{_cuando(cierre, ahora_cl)}. ¿Ya tienes lista tu oferta?"))
    return [(clave, texto) for clave, texto in pendientes if clave not in hechos]


def texto_resultado(lic: Licitacion, empresa: Empresa, url_publica: str = "") -> str | None:
    """Qué pasó con la licitación (None si todavía no hay resultado)."""
    from .publico import Ficha

    if lic.estado_codigo == ESTADO_DESIERTA:
        return ("🏁 Se declaró *desierta*: nadie fue adjudicado. Muchas veces el organismo la vuelve a publicar; si "
                "calza contigo, te llegará en tu resumen.")
    if lic.estado_codigo == ESTADO_REVOCADA:
        return "🏁 El organismo *revocó* esta licitación: no habrá adjudicación."
    if lic.estado_codigo != ESTADO_ADJUDICADA:
        return None
    ficha = Ficha.de(lic)
    mi_rut = rutlib.limpiar(empresa.rut or "")
    ruts = {rutlib.limpiar(str((it.get("adjudicacion") or {}).get("proveedor_rut") or "")) for it in lic.items or []}
    if mi_rut and mi_rut in ruts:
        return "🎉 *¡Ganaste!* Se adjudicó a tu empresa. Felicitaciones: revisa en Mercado Público los pasos para la orden de compra."
    ganadores = ficha.ganadores
    if not ganadores:
        return "🏁 Ya se *adjudicó*. Revisa en la ficha oficial a quién y por cuánto."
    nombre, monto = ganadores[0]
    otros = f" y {len(ganadores) - 1} proveedor{'es' if len(ganadores) > 2 else ''} más" if len(ganadores) > 1 else ""
    total = ficha.total_adjudicado
    texto = f"🏁 Se *adjudicó* a {nombre}{otros}" + (f" por {formato_pesos(total)}" if total else "")
    if total and lic.monto_estimado:
        texto += f" (el presupuesto era {formato_pesos(lic.monto_estimado)})"
    texto += "."
    if url_publica and ficha.resultado:
        texto += f" Mira los precios ganadores por ítem, te sirven para tu próxima oferta: {url_publica}/licitacion/{lic.codigo}"
    return texto


# --- Desde WhatsApp ---

def marcar_postulacion(session: Session, empresa: Empresa, lic: Licitacion, momento: datetime | None = None) -> str:
    momento = momento or ahora()
    post = session.scalar(select(Postulacion).where(Postulacion.empresa_id == empresa.id,
                                                    Postulacion.licitacion_codigo == lic.codigo))
    ahora_cl = hora_chile(momento)
    if lic.fecha_cierre and lic.fecha_cierre <= ahora_cl:
        return "Esta licitación ya cerró, así que no alcanzas a postular. Si quieres, te aviso de las próximas parecidas en tu resumen."
    if post is None:
        activas = session.query(Postulacion).filter_by(empresa_id=empresa.id, activa=True).count()
        if activas >= MAXIMO_EN_AGENDA:
            return f"Tu agenda ya tiene {MAXIMO_EN_AGENDA} licitaciones. Escribe *AGENDA* para ver y quitar alguna."
        post = Postulacion(empresa_id=empresa.id, licitacion_codigo=lic.codigo, avisos=[])
        session.add(post)
    post.activa = True
    lineas = [f"📌 Anotado: vas a postular a *{lic.nombre.strip()}*.", "", "Te aviso por aquí de cada plazo:"]
    for h in hitos(lic):
        if h.cuando > ahora_cl:
            lineas.append(f"• {h.nombre}: {h.cuando.strftime('%d-%m')} a las {h.cuando.strftime('%H:%M')}")
    lineas += ["• Y cuando se adjudique, te cuento quién ganó y a qué precio.", "",
               "Escribe *AGENDA* para ver tus postulaciones."]
    return "\n".join(lineas)


def texto_agenda(session: Session, empresa: Empresa, momento: datetime | None = None) -> str:
    posts = _agenda(session, empresa)
    if not posts:
        return ("Tu agenda está vacía. Cuando veas una licitación que te interese, toca *📌 Voy a postular* y te aviso "
                "de sus plazos hasta saber quién ganó.")
    ahora_cl = hora_chile(momento)
    lineas = ["📌 *Tu agenda de postulaciones*", ""]
    for i, (post, lic) in enumerate(posts, 1):
        proximo = next((h for h in hitos(lic) if h.cuando > ahora_cl), None)
        estado = (f"{proximo.nombre}: {_cuando(proximo.cuando, ahora_cl)}" if proximo
                  else "Cerró: esperando el resultado")
        lineas += [f"{i}. *{lic.nombre.strip()}*", f"   {estado}"]
    lineas += ["", "Para sacar una, escribe *QUITAR* y su número (por ejemplo, QUITAR 2)."]
    return "\n".join(lineas)


def quitar_de_agenda(session: Session, empresa: Empresa, numero: int) -> str:
    posts = _agenda(session, empresa)
    if not 1 <= numero <= len(posts):
        return "No encontré ese número en tu agenda. Escribe *AGENDA* para verla."
    post, lic = posts[numero - 1]
    post.activa = False
    return f"Listo, saqué *{lic.nombre.strip()}* de tu agenda: ya no te avisaré sus plazos."


def _agenda(session: Session, empresa: Empresa) -> list[tuple[Postulacion, Licitacion]]:
    filas = session.execute(
        select(Postulacion, Licitacion).join(Licitacion, Licitacion.codigo == Postulacion.licitacion_codigo)
        .where(Postulacion.empresa_id == empresa.id, Postulacion.activa.is_(True))
        .order_by(Licitacion.fecha_cierre.nulls_last(), Postulacion.id)
    ).all()
    return [tuple(f) for f in filas]


# --- Tarea diaria ---

@dataclass
class ResultadoAgenda:
    avisos: int = 0
    resultados: int = 0
    consultadas: int = 0
    sin_enviar: int = 0
    errores: list[str] = field(default_factory=list)


def _actualizar_resultado(session: Session, cliente: MercadoPublicoClient, lic: Licitacion) -> None:
    raw = cliente.licitacion(lic.codigo)
    if raw is None:
        return
    for campo, valor in normalizar_licitacion(raw).items():
        setattr(lic, campo, valor)
    lic.raw = raw
    if lic.estado_codigo == ESTADO_ADJUDICADA:
        registrar_precios_adjudicacion(session, lic)


def _enviar(session: Session, wa: ClienteWhatsApp | None, correo: ClienteCorreo | None, empresa: Empresa,
            lic: Licitacion, lineas: list[str], momento: datetime, idioma: str) -> bool:
    """Por WhatsApp (texto con la ventana abierta, si no la plantilla); por correo solo si WhatsApp no se pudo."""
    nombre = lic.nombre.strip()
    texto = "\n".join([f"📌 *{nombre}*", ""] + lineas + ["", f"Ficha oficial: {url_ficha(lic)}"])
    if wa is not None and empresa.whatsapp and empresa.whatsapp_activo:
        try:
            if ventana_abierta(empresa, momento):
                wamid = wa.enviar_texto(empresa.whatsapp, texto)
                registrar(session, empresa, empresa.whatsapp, "saliente", "texto", f"Agenda: {lic.codigo}", wamid)
            else:
                resumen = " ".join(l.replace("*", "") for l in lineas)
                parametros = [empresa.nombre, nombre[:60], resumen[:400]]
                wamid = wa.enviar_plantilla(empresa.whatsapp, RECORDATORIO_POSTULACION.nombre, idioma=idioma,
                                            parametros=parametros, payloads_botones=[f"DETALLE:{lic.codigo}"])
                registrar(session, empresa, empresa.whatsapp, "saliente", "plantilla", " | ".join(parametros), wamid)
            return True
        except ErrorWhatsApp as e:
            log.warning("No se pudo enviar el aviso de agenda de %s a la empresa #%s: %s", lic.codigo, empresa.id, e)
    if correo is not None and empresa.email:
        cuerpo = "\n".join([f"Hola {empresa.nombre}:", "", f"Sobre la licitación que vas a postular, {nombre}:", ""]
                           + [l.replace("*", "") for l in lineas] + ["", f"Ficha oficial: {url_ficha(lic)}", "",
                                                                      "Equipo Calza · hola@calza.cl"])
        try:
            correo.enviar(empresa.email, f"Tu postulación: {nombre[:70]}", cuerpo)
            return True
        except ErrorCorreo as e:
            log.warning("No se pudo enviar por correo el aviso de agenda de %s: %s", lic.codigo, e)
    return False


def ejecutar_agenda(
    session: Session, *, wa: ClienteWhatsApp | None = None, correo: ClienteCorreo | None = None,
    cliente: MercadoPublicoClient | None = None, url_publica: str = "", idioma: str = "es",
    momento: datetime | None = None,
) -> ResultadoAgenda:
    """Avisa los plazos que tocan hoy y los resultados nuevos. Pensada para correr una vez al día en la mañana."""
    momento = momento or ahora()
    ahora_cl = hora_chile(momento)
    r = ResultadoAgenda()
    filas = session.execute(
        select(Postulacion, Licitacion, Empresa)
        .join(Licitacion, Licitacion.codigo == Postulacion.licitacion_codigo)
        .join(Empresa, Empresa.id == Postulacion.empresa_id)
        .where(Postulacion.activa.is_(True), Empresa.plan != "gratis")
    ).all()
    for post, lic, empresa in filas:
        cerrada = lic.fecha_cierre is not None and lic.fecha_cierre <= ahora_cl
        if cerrada and lic.estado_codigo not in ESTADOS_FINALES:
            if lic.fecha_cierre < ahora_cl - timedelta(days=DIAS_ESPERA_RESULTADO):
                post.activa = False  # el organismo no publicó el resultado a tiempo: se deja de seguir
                continue
            ya_hoy = post.revisada_en is not None and post.revisada_en > momento - timedelta(hours=20)
            if cliente is not None and lic.tipo != "COT" and not ya_hoy and r.consultadas < CONSULTAS_POR_EJECUCION:
                r.consultadas += 1
                post.revisada_en = momento
                try:
                    _actualizar_resultado(session, cliente, lic)
                except MercadoPublicoError as e:
                    r.errores.append(f"{lic.codigo}: {e}")
        lineas, claves = [], []
        resultado = texto_resultado(lic, empresa, url_publica) if lic.estado_codigo in ESTADOS_FINALES else None
        if resultado:
            lineas, claves = [resultado], ["resultado"]
        else:
            for clave, texto in avisos_pendientes(post, lic, ahora_cl):
                lineas.append(texto)
                claves.append(clave)
        if lineas:
            if _enviar(session, wa, correo, empresa, lic, lineas, momento, idioma):
                post.avisos = list(post.avisos or []) + claves
                if "resultado" in claves:
                    post.activa = False
                    r.resultados += 1
                else:
                    r.avisos += 1
            else:
                r.sin_enviar += 1
        session.commit()
    return r
