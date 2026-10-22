"""Panel privado del piloto (/admin): estado de cada empresa y a quién contactar.

Se entra en /admin/entrar con el usuario "admin" y la contraseña de ADMIN_CLAVE en el .env. La sesión es una cookie
firmada con esa clave (cambiarla cierra todas las sesiones) que dura DIAS_SESION días. Tras INTENTOS_MAXIMOS claves
erradas desde una misma IP, esa IP queda bloqueada durante BLOQUEO. Para scripts (curl) sigue sirviendo HTTP Basic.
Sin ADMIN_CLAVE el panel no existe (404), así no queda expuesto por error.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import math
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from .db import Calce, Empresa, MandatoPago, MensajeWhatsApp, Pago, Suscripcion, ahora, hora_chile
from .planes import DIAS_PRUEBA, PERIODICIDADES, PLANES, formato_pesos
from .publico import REGION_POR_SLUG, REGIONES_PUBLICAS, Ficha, Region, buscar, cargar_abiertas
from .whatsapp import normalizar_telefono

USUARIO = "admin"
COOKIE = "calza_admin"
DIAS_SESION = 7
INTENTOS_MAXIMOS = 5
BLOQUEO = timedelta(minutes=15)
# Sin mensajes en este plazo, la empresa en prueba o activa se marca para contactar.
SIN_ACTIVIDAD = timedelta(days=7)
DIAS_POR_VENCER = 3


@dataclass
class FilaEmpresa:
    empresa: Empresa
    suscripcion: Suscripcion | None
    mandato: MandatoPago | None
    dias_restantes: int | None
    resumenes: int  # días en que se le envió el resumen
    ultimo_resumen: datetime | None
    mensajes_recibidos: int
    pagado: int
    motivos: list[tuple[str, str]]  # (texto, nivel: "critico" | "atencion" | "info"); vacío = nada urgente
    utiles: int = 0  # licitaciones que marcó con 👍
    no_utiles: int = 0  # y con 👎
    dias_prueba_usados: int | None = None  # para la barra de avance de la prueba

    @property
    def critica(self) -> bool:
        return any(nivel == "critico" for _, nivel in self.motivos)

    @property
    def estado(self) -> str:
        return self.suscripcion.estado if self.suscripcion else "sin suscripción"


@dataclass
class Indicadores:
    empresas: int
    en_prueba: int
    activas: int
    vencidas: int
    con_renovacion: int
    ingreso_mensual: int  # suscripciones con renovación automática, llevadas a pesos al mes
    cobrado_mes: int
    por_contactar: int
    pagaron: int  # empresas con al menos un pago aprobado
    terminaron_prueba: int  # activas + vencidas: las que ya tuvieron que decidir
    utiles: int = 0
    no_utiles: int = 0
    con_tarjeta: int = 0  # registraron la tarjeta (activaron la prueba)
    sin_tarjeta: int = 0  # se registraron y aún no la registran
    reciben_resumen: int = 0  # en prueba o activas que ya recibieron al menos un resumen
    usan: int = 0  # de esas, las que respondieron algo más que el saludo o valoraron una licitación

    @property
    def activacion(self) -> int | None:
        """Porcentaje de las que registraron la tarjeta, entre las que se registraron con la prueba con tarjeta."""
        total = self.con_tarjeta + self.sin_tarjeta
        return round(100 * self.con_tarjeta / total) if total else None

    @property
    def uso(self) -> int | None:
        return round(100 * self.usan / self.reciben_resumen) if self.reciben_resumen else None

    @property
    def calce_util(self) -> int | None:
        """Porcentaje de 👍 entre las licitaciones que los clientes valoraron."""
        total = self.utiles + self.no_utiles
        return round(100 * self.utiles / total) if total else None

    @property
    def conversion(self) -> int | None:
        """Porcentaje de las que terminaron la prueba y pagaron."""
        return round(100 * self.pagaron / self.terminaron_prueba) if self.terminaron_prueba else None


FILTROS = (("todas", "Todas"), ("contactar", "Por contactar"), ("pendiente", "Sin tarjeta"), ("prueba", "En prueba"),
           ("activa", "Activas"), ("vencida", "Vencidas"))


def hace(fecha: datetime | None, momento: datetime) -> str:
    """Tiempo relativo corto en español: "hoy", "ayer", "hace 5 días", "hace 3 semanas"."""
    if fecha is None:
        return "nunca"
    dias = (momento.date() - fecha.date()).days
    if dias <= 0:
        return "hoy"
    if dias == 1:
        return "ayer"
    if dias < 14:
        return f"hace {dias} días"
    if dias < 60:
        return f"hace {dias // 7} semanas"
    return f"hace {dias // 30} meses"


def _iguales(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode(), b.encode())


def _firma(clave: str, expira: int) -> str:
    return hmac.new(clave.encode(), f"admin:{expira}".encode(), hashlib.sha256).hexdigest()


def token_sesion(clave: str, ahora_ts: float | None = None) -> str:
    expira = int((ahora_ts if ahora_ts is not None else time.time()) + DIAS_SESION * 86400)
    return f"{expira}.{_firma(clave, expira)}"


def sesion_valida(token: str, clave: str, ahora_ts: float | None = None) -> bool:
    expira, _, firma = token.partition(".")
    if not expira.isdigit() or int(expira) < (ahora_ts if ahora_ts is not None else time.time()):
        return False
    return _iguales(firma, _firma(clave, int(expira)))


class Intentos:
    """Claves erradas por IP en la ventana de BLOQUEO (en memoria: se reinicia con la app, lo que basta aquí)."""

    def __init__(self) -> None:
        self._fallos: dict[str, list[float]] = {}

    def _recientes(self, ip: str, ahora_ts: float) -> list[float]:
        fallos = [t for t in self._fallos.get(ip, []) if ahora_ts - t < BLOQUEO.total_seconds()]
        if fallos:
            self._fallos[ip] = fallos
        else:
            self._fallos.pop(ip, None)
        return fallos

    def bloqueada(self, ip: str, ahora_ts: float | None = None) -> bool:
        return len(self._recientes(ip, ahora_ts if ahora_ts is not None else time.time())) >= INTENTOS_MAXIMOS

    def fallo(self, ip: str, ahora_ts: float | None = None) -> None:
        ahora_ts = ahora_ts if ahora_ts is not None else time.time()
        self._fallos[ip] = self._recientes(ip, ahora_ts) + [ahora_ts]

    def limpiar(self, ip: str) -> None:
        self._fallos.pop(ip, None)


def _basic_valido(request: Request, clave: str) -> bool:
    encabezado = request.headers.get("authorization", "")
    if not encabezado.lower().startswith("basic "):
        return False
    try:
        usuario, _, dada = base64.b64decode(encabezado[6:]).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    return _iguales(usuario, USUARIO) & _iguales(dada, clave)


def _cierre(ficha: Ficha) -> str:
    dias = ficha.dias_para_cierre()
    if dias is None:
        return ""
    return "cierra hoy" if dias <= 0 else "cierra mañana" if dias == 1 else f"cierra en {dias} días"


def texto_invitacion(*, nombre: str, empresa: str, remitente: str, total: int, ejemplo: Ficha | None,
                     region: Region | None) -> str:
    """Invitación al piloto (docs/piloto.md) con un ejemplo real de lo que hay abierto hoy para esa empresa."""
    nombre, empresa, remitente = nombre.strip() or "[Nombre]", empresa.strip() or "tu empresa", remitente.strip() or "[tu nombre]"
    lineas = [f"Hola {nombre}, soy {remitente}."]
    if ejemplo is not None:
        donde = f" en {region.titulo}" if region else ""
        cantidad = "una licitación abierta" if total == 1 else f"{total} licitaciones abiertas"
        detalle = ", ".join(d for d in (
            f"de {ejemplo.organismo}" if ejemplo.organismo else "",
            f"por {formato_pesos(ejemplo.monto_estimado)}" if ejemplo.monto_estimado else "", _cierre(ejemplo),
        ) if d)
        tipo = "una Compra Ágil" if ejemplo.es_compra_agil else "una"
        lineas[0] += (f" Hoy vi {cantidad} en Mercado Público{donde} que calzan con lo que vende {empresa}. "
                      f"Por ejemplo, {tipo} de \"{ejemplo.nombre.strip()}\"" + (f" {detalle}" if detalle else "") + ".")
    lineas += [
        "",
        f"Estamos lanzando *Calza*, un servicio que cada día hábil a las 8:00 te manda por WhatsApp las licitaciones y "
        f"Compras Ágiles de Mercado Público que calzan con lo que vende {empresa}.",
        "",
        f"Te invito a ser parte del piloto: son {DIAS_PRUEBA} días gratis. Registras tu tarjeta, pero no se cobra nada si "
        "cancelas antes de que terminen. Solo te pido que me cuentes qué te sirvió y qué no.",
        "",
        "Te registras en 2 minutos aquí: https://calza.cl/registro",
        "",
        "¿Te animas?",
    ]
    return "\n".join(lineas)


def _autorizado(request: Request, clave: str) -> bool:
    return sesion_valida(request.cookies.get(COOKIE, ""), clave) or _basic_valido(request, clave)


def _es_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def datos_panel(session, momento: datetime | None = None) -> tuple[Indicadores, list[FilaEmpresa]]:
    momento = momento or ahora()
    suscripciones = {s.empresa_id: s for s in session.scalars(select(Suscripcion))}
    mandatos = {m.id: m for m in session.scalars(select(MandatoPago))}
    # Empresas que alguna vez autorizaron un cobro en Mercado Pago (un mandato que dejó de estar pendiente).
    autorizaron = {m.empresa_id for m in mandatos.values() if m.estado != "pending"}
    resumenes = {
        empresa_id: (dias, ultimo) for empresa_id, dias, ultimo in session.execute(
            select(Calce.empresa_id, func.count(func.distinct(Calce.notificado_en)), func.max(Calce.notificado_en))
            .where(Calce.notificado_en.is_not(None)).group_by(Calce.empresa_id)
        )
    }
    recibidos = dict(session.execute(
        select(MensajeWhatsApp.empresa_id, func.count()).where(MensajeWhatsApp.direccion == "entrante")
        .group_by(MensajeWhatsApp.empresa_id)
    ).all())
    pagado = dict(session.execute(
        select(Pago.empresa_id, func.sum(Pago.monto)).where(Pago.estado == "pagado").group_by(Pago.empresa_id)
    ).all())
    valoraciones: dict[int, dict[int, int]] = {}
    for empresa_id, valor, n in session.execute(
        select(Calce.empresa_id, Calce.valoracion, func.count()).where(Calce.valoracion.is_not(None))
        .group_by(Calce.empresa_id, Calce.valoracion)
    ):
        valoraciones.setdefault(empresa_id, {})[valor] = n
    inicio_mes = momento.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    cobrado_mes = session.scalar(select(func.sum(Pago.monto)).where(
        Pago.estado == "pagado", Pago.pagado_en >= inicio_mes)) or 0

    filas = []
    for empresa in session.scalars(select(Empresa).order_by(Empresa.creada_en.desc())):
        sus = suscripciones.get(empresa.id)
        mandato = mandatos.get(sus.mandato_activo_id) if sus and sus.mandato_activo_id else None
        dias = (sus.vigente_hasta - momento).days if sus else None
        motivos: list[tuple[str, str]] = []
        if sus and sus.estado == "prueba" and mandato is None and dias is not None and dias < DIAS_POR_VENCER:
            motivos.append(("Prueba termina sin suscripción", "critico"))
        if sus and sus.estado == "pendiente" and momento - empresa.creada_en > timedelta(hours=24):
            motivos.append(("No activó la prueba (falta tarjeta)", "atencion"))
        if sus and sus.prueba_repetida and sus.estado == "vencida":
            motivos.append(("Prueba repetida: su cuenta de Mercado Pago ya la usó", "info"))
        elif sus and sus.estado == "vencida" and dias is not None and dias > -7:
            motivos.append(("Venció hace poco", "critico"))
        if sus and sus.estado in ("prueba", "activa"):
            ultimo = empresa.ultimo_mensaje_entrante
            if ultimo is None and momento - empresa.creada_en > timedelta(days=2):
                motivos.append(("Nunca ha escrito", "atencion"))
            elif ultimo is not None and momento - ultimo > SIN_ACTIVIDAD:
                motivos.append((f"Sin escribir hace {(momento - ultimo).days} días", "atencion"))  # siempre más de 7
            if not empresa.whatsapp_activo:
                motivos.append(("Dio de baja WhatsApp", "info"))
        dias_resumen, ultimo_resumen = resumenes.get(empresa.id, (0, None))
        filas.append(FilaEmpresa(
            empresa=empresa, suscripcion=sus, mandato=mandato, dias_restantes=dias, resumenes=dias_resumen,
            ultimo_resumen=ultimo_resumen, mensajes_recibidos=recibidos.get(empresa.id, 0),
            pagado=int(pagado.get(empresa.id) or 0), motivos=motivos,
            utiles=valoraciones.get(empresa.id, {}).get(1, 0), no_utiles=valoraciones.get(empresa.id, {}).get(-1, 0),
            # La prueba puede empezar al registrar la tarjeta (no al crear la cuenta): se cuenta desde su término.
            dias_prueba_usados=(min(DIAS_PRUEBA, max(0, DIAS_PRUEBA - math.ceil((sus.vigente_hasta - momento).total_seconds() / 86400)))
                                if sus and sus.estado == "prueba" else None),
        ))
    # Primero a quién contactar; luego pruebas por fecha de término, activas y vencidas.
    orden_estado = {"pendiente": 0, "prueba": 1, "activa": 2, "vencida": 3}
    filas.sort(key=lambda f: (not f.critica, not f.motivos, orden_estado.get(f.estado, 3),
                              f.suscripcion.vigente_hasta if f.suscripcion else momento))

    activas_con_mandato = [f for f in filas if f.mandato and f.estado in ("prueba", "activa")]
    indicadores = Indicadores(
        empresas=len(filas),
        en_prueba=sum(f.estado == "prueba" for f in filas),
        activas=sum(f.estado == "activa" for f in filas),
        vencidas=sum(f.estado == "vencida" for f in filas),
        con_renovacion=len(activas_con_mandato),
        ingreso_mensual=round(sum(f.mandato.monto / PERIODICIDADES.get(f.mandato.periodicidad, 1)
                                  for f in activas_con_mandato)),
        cobrado_mes=int(cobrado_mes),
        por_contactar=sum(bool(f.motivos) for f in filas),
        pagaron=sum(f.pagado > 0 for f in filas),
        terminaron_prueba=sum(f.estado in ("activa", "vencida") and not f.suscripcion.prueba_repetida for f in filas),
        utiles=sum(f.utiles for f in filas), no_utiles=sum(f.no_utiles for f in filas),
        con_tarjeta=sum(f.empresa.id in autorizaron and not f.suscripcion.prueba_repetida
                        for f in filas if f.suscripcion),
        sin_tarjeta=sum(f.estado == "pendiente" for f in filas),
        reciben_resumen=len(reciben := [f for f in filas if f.estado in ("prueba", "activa") and f.resumenes]),
        usan=sum(f.mensajes_recibidos >= 2 or f.utiles + f.no_utiles > 0 for f in reciben),
    )
    return indicadores, filas


def crear_router_admin(Sesion: sessionmaker, *, plantillas: Jinja2Templates, clave: str) -> APIRouter:
    router = APIRouter()
    intentos = Intentos()
    sin_cache = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"}

    def entrar_html(request: Request, *, error: str = "", usuario: str = "", salio: bool = False, estado: int = 200):
        return plantillas.TemplateResponse(request, "admin_entrar.html", {
            "error": error, "usuario": usuario, "salio": salio, "dias_sesion": DIAS_SESION,
        }, status_code=estado, headers=sin_cache)

    @router.get("/admin/entrar", response_class=HTMLResponse)
    def entrar(request: Request, salio: str = ""):
        if not clave:
            return Response(status_code=404)
        if _autorizado(request, clave):
            return RedirectResponse("/admin", status_code=303)
        return entrar_html(request, salio=bool(salio))

    @router.post("/admin/entrar", response_class=HTMLResponse)
    def entrar_post(request: Request, usuario: str = Form(""), clave_dada: str = Form("", alias="clave")):
        if not clave:
            return Response(status_code=404)
        ip = request.client.host if request.client else ""
        if intentos.bloqueada(ip):
            return entrar_html(request, usuario=usuario, estado=429,
                               error=f"Demasiados intentos. Espera {int(BLOQUEO.total_seconds() // 60)} minutos e inténtalo de nuevo.")
        if not (_iguales(usuario.strip(), USUARIO) & _iguales(clave_dada, clave)):
            intentos.fallo(ip)
            return entrar_html(request, usuario=usuario, estado=401, error="Usuario o contraseña incorrectos.")
        intentos.limpiar(ip)
        r = RedirectResponse("/admin", status_code=303, headers=sin_cache)
        r.set_cookie(COOKIE, token_sesion(clave), max_age=DIAS_SESION * 86400, path="/admin", httponly=True,
                     secure=_es_https(request), samesite="strict")
        return r

    @router.post("/admin/salir")
    def salir(request: Request):
        r = RedirectResponse("/admin/entrar?salio=1", status_code=303, headers=sin_cache)
        r.delete_cookie(COOKIE, path="/admin", httponly=True, secure=_es_https(request), samesite="strict")
        return r

    @router.get("/admin/invitar", response_class=HTMLResponse)
    def invitar(request: Request, nombre: str = "", empresa: str = "", vende: str = "", region: str = "",
                telefono: str = "", remitente: str = ""):
        if not clave:
            return Response(status_code=404)
        if not _autorizado(request, clave):
            return RedirectResponse("/admin/entrar", status_code=303, headers=sin_cache)
        vende, reg = vende.strip()[:300], REGION_POR_SLUG.get(region)
        resultados: list[tuple[Ficha, float]] = []
        if vende:
            with Sesion() as s:
                resultados = buscar(cargar_abiertas(s), vende, reg, limite=500)
        texto = texto_invitacion(nombre=nombre, empresa=empresa, remitente=remitente, total=len(resultados),
                                 ejemplo=resultados[0][0] if resultados else None, region=reg) if vende else ""
        numero = normalizar_telefono(telefono) if telefono.strip() else ""
        return plantillas.TemplateResponse(request, "admin_invitar.html", {
            "f": {"nombre": nombre, "empresa": empresa, "vende": vende, "region": region, "telefono": telefono,
                  "remitente": remitente},
            "regiones": REGIONES_PUBLICAS, "region": reg, "resultados": resultados[:5], "total": len(resultados),
            "texto": texto, "numero": numero if len(numero) >= 11 else "", "fmt": formato_pesos, "cierre": _cierre,
        }, headers=sin_cache)

    @router.get("/admin", response_class=HTMLResponse)
    def panel(request: Request, estado: str = "todas"):
        if not clave:
            return Response(status_code=404)
        if not _autorizado(request, clave):
            return RedirectResponse("/admin/entrar", status_code=303, headers=sin_cache)
        momento = ahora()
        with Sesion() as s:
            indicadores, filas = datos_panel(s, momento)
            filtro = estado if estado in dict(FILTROS) else "todas"
            cuentas = {"todas": len(filas), "contactar": indicadores.por_contactar,
                       "pendiente": sum(f.estado == "pendiente" for f in filas), "prueba": indicadores.en_prueba,
                       "activa": indicadores.activas, "vencida": indicadores.vencidas}
            visibles = [f for f in filas if filtro == "todas" or (filtro == "contactar" and f.motivos)
                        or f.estado == filtro]
            nombres = {**{c: p.nombre for c, p in PLANES.items()}, "gratis": "Gratis"}
            return plantillas.TemplateResponse(request, "admin.html", {
                "ind": indicadores, "filas": visibles, "nombres": nombres, "momento": momento, "chile": hora_chile,
                "filtros": FILTROS, "filtro": filtro, "cuentas": cuentas, "hace": hace, "dias_prueba": DIAS_PRUEBA,
                "contactar": [f for f in filas if f.motivos],
            }, headers=sin_cache)

    return router
