import hashlib
import itertools
import hmac
import json
from datetime import date, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from licita.conversacion import procesar_webhook
from licita.db import Calce, Empresa, MensajeWhatsApp
from licita.notificaciones import enviar_resumenes
from licita.servidor import crear_app
from licita.sync import sincronizar_licitaciones
from licita.whatsapp import ClienteWhatsApp, ErrorWhatsApp, limpiar_parametro, normalizar_telefono, verificar_firma

MOMENTO = datetime(2026, 10, 7, 8, 0)
TEL = "56989299524"


class WhatsAppFalso:
    """Registra lo que se enviaría a Meta."""

    def __init__(self, falla=False):
        self.enviados = []
        self.botones = []  # mensajes con botones (👍/👎), aparte para no mezclarlos con los textos
        self.falla = falla

    def enviar_texto(self, telefono, texto):
        if self.falla:
            raise ErrorWhatsApp("caído")
        self.enviados.append(("texto", telefono, texto))
        return f"wamid.{len(self.enviados)}"

    def enviar_botones(self, telefono, texto, botones):
        if self.falla:
            raise ErrorWhatsApp("caído")
        self.botones.append((telefono, texto, botones))
        return f"wamid.b{len(self.botones)}"

    def enviar_plantilla(self, telefono, nombre, *, idioma="es", parametros=None, payloads_botones=None):
        if self.falla:
            raise ErrorWhatsApp("caído")
        self.enviados.append(("plantilla", telefono, nombre, parametros, payloads_botones))
        return f"wamid.{len(self.enviados)}"


@pytest.fixture
def escenario(Sesion, cliente_mp):
    """Base con licitaciones, una empresa y dos calces listos para notificar."""
    s = Sesion()
    sincronizar_licitaciones(s, cliente_mp, date(2026, 10, 6))
    e = Empresa(nombre="Aseo Sur", descripcion="Insumos de aseo", regiones=[], palabras_clave=["aseo"], whatsapp=TEL)
    s.add(e)
    s.commit()
    s.add_all([
        Calce(empresa_id=e.id, licitacion_codigo="1234-56-LE26", puntaje=92, razon="Piden detergente y guantes: es tu rubro."),
        Calce(empresa_id=e.id, licitacion_codigo="2345-12-L126", puntaje=65, razon="Podría servir si haces mantención."),
    ])
    s.commit()
    return s, e


_ids = itertools.count(1)


def mensaje_entrante(texto=None, payload=None, de=TEL):
    wamid = f"wamid.in{next(_ids)}"
    if payload is not None:
        msg = {"from": de, "id": wamid, "type": "button", "button": {"payload": payload, "text": "Ver"}}
    else:
        msg = {"from": de, "id": wamid, "type": "text", "text": {"body": texto}}
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {"messages": [msg]}}]}]}


# --- Cliente de la Cloud API ---

def test_cliente_envia_plantilla_con_formato_de_meta():
    capturado = {}

    def handler(request):
        capturado["url"] = str(request.url)
        capturado["auth"] = request.headers["Authorization"]
        capturado["json"] = json.loads(request.content)
        return httpx.Response(200, json={"messages": [{"id": "wamid.ABC"}]})

    wa = ClienteWhatsApp("TOKEN", "123", http=httpx.Client(transport=httpx.MockTransport(handler)))
    wamid = wa.enviar_plantilla("+56 9 8929 9524", "resumen", parametros=["Aseo\nSur"], payloads_botones=["VER_TODAS"])
    assert wamid == "wamid.ABC"
    assert capturado["url"] == "https://graph.facebook.com/v23.0/123/messages"
    assert capturado["auth"] == "Bearer TOKEN"
    cuerpo = capturado["json"]
    assert cuerpo["to"] == TEL and cuerpo["messaging_product"] == "whatsapp"
    componentes = cuerpo["template"]["components"]
    assert componentes[0]["parameters"][0]["text"] == "Aseo Sur"
    assert componentes[1] == {"type": "button", "sub_type": "quick_reply", "index": "0", "parameters": [{"type": "payload", "payload": "VER_TODAS"}]}


def test_cliente_reporta_errores_de_meta():
    wa = ClienteWhatsApp("T", "1", http=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(400, json={"error": {"code": 131047, "message": "Re-engagement message"}}))))
    with pytest.raises(ErrorWhatsApp, match="131047"):
        wa.enviar_texto(TEL, "hola")


def test_utilidades():
    assert normalizar_telefono("+56 9 8929-9524") == TEL
    assert limpiar_parametro("a\n\tb    c") == "a b c"
    assert len(limpiar_parametro("x" * 100)) == 60
    firma = "sha256=" + hmac.new(b"secreto", b"{}", hashlib.sha256).hexdigest()
    assert verificar_firma("secreto", b"{}", firma)
    assert not verificar_firma("secreto", b"{}", "sha256=otra")
    assert not verificar_firma("secreto", b"{}", None)


# --- Envío del resumen diario ---

def test_sin_ventana_envia_una_plantilla_con_botones(escenario):
    s, e = escenario
    wa = WhatsAppFalso()
    r = enviar_resumenes(s, wa, momento=MOMENTO)
    assert (r.plantillas, r.textos) == (1, 0)
    tipo, tel, nombre, params, botones = wa.enviados[0]
    assert (tipo, tel, nombre) == ("plantilla", TEL, "resumen_diario_licitaciones")
    assert params == ["Aseo Sur", "2 licitaciones nuevas", "Adquisición de insumos de aseo para CESFAM", "92", "20-10-2026 15:00"]
    assert botones == ["VER_TODAS", "DETALLE:1234-56-LE26"]
    # Quedan marcados y numerados para que el usuario pueda responder "1" o "2".
    assert [c.posicion_resumen for c in s.query(Calce).order_by(Calce.puntaje.desc())] == [1, 2]
    # Al día siguiente no se repiten.
    assert enviar_resumenes(s, wa, momento=MOMENTO + timedelta(days=1)).sin_novedades == 1


def test_con_ventana_abierta_envia_texto_gratis(escenario):
    s, e = escenario
    e.ultimo_mensaje_entrante = MOMENTO - timedelta(hours=5)
    wa = WhatsAppFalso()
    r = enviar_resumenes(s, wa, momento=MOMENTO)
    assert (r.plantillas, r.textos) == (0, 1)
    assert "1. *Adquisición de insumos de aseo para CESFAM* (92% de calce)" in wa.enviados[0][2]


def test_respeta_baja_y_no_marca_si_falla(escenario):
    s, e = escenario
    r = enviar_resumenes(s, WhatsAppFalso(falla=True), momento=MOMENTO)
    assert r.errores == 1
    assert s.query(Calce).filter(Calce.notificado.is_(True)).count() == 0
    e.whatsapp_activo = False
    s.commit()
    wa = WhatsAppFalso()
    enviar_resumenes(s, wa, momento=MOMENTO)
    assert wa.enviados == []


# --- Conversación ---

def test_flujo_boton_y_numero(escenario):
    s, e = escenario
    enviar_resumenes(s, WhatsAppFalso(), momento=MOMENTO)
    wa = WhatsAppFalso()

    procesar_webhook(s, wa, mensaje_entrante(payload="DETALLE:1234-56-LE26"), momento=MOMENTO)
    detalle = wa.enviados[-1][2]
    assert "*Adquisición de insumos de aseo para CESFAM*" in detalle
    assert "• 200 × Detergente" in detalle
    assert "idLicitacion=1234-56-LE26" in detalle
    assert e.ultimo_mensaje_entrante == MOMENTO  # se abrió la ventana gratis

    procesar_webhook(s, wa, mensaje_entrante(payload="VER_TODAS"), momento=MOMENTO)
    assert "Hoy encontramos 2 licitaciones" in wa.enviados[-1][2]

    procesar_webhook(s, wa, mensaje_entrante("2"), momento=MOMENTO)
    assert "*Mantención de áreas verdes plaza central*" in wa.enviados[-1][2]

    procesar_webhook(s, wa, mensaje_entrante("9"), momento=MOMENTO)
    assert "número entre 1 y 2" in wa.enviados[-1][2]

    procesar_webhook(s, wa, mensaje_entrante("hola"), momento=MOMENTO)
    assert "Soy el asistente de Calza" in wa.enviados[-1][2]
    # 5 entrantes + 5 respuestas + la plantilla + la pregunta 👍/👎 tras cada uno de los 2 detalles
    assert s.query(MensajeWhatsApp).count() == 10 + 1 + 2


def test_baja_y_alta(escenario):
    s, e = escenario
    wa = WhatsAppFalso()
    procesar_webhook(s, wa, mensaje_entrante("BAJA"), momento=MOMENTO)
    assert e.whatsapp_activo is False
    procesar_webhook(s, wa, mensaje_entrante("alta"), momento=MOMENTO)
    assert e.whatsapp_activo is True


def test_numero_no_registrado(escenario):
    s, _ = escenario
    wa = WhatsAppFalso()
    procesar_webhook(s, wa, mensaje_entrante("hola", de="56911111111"), url_registro="https://calza.cl", momento=MOMENTO)
    assert wa.enviados == [("texto", "56911111111", "Hola 👋 Este número no está registrado en Calza. Puedes inscribirte en https://calza.cl")]


def test_solo_responde_mensajes_dirigidos_al_numero_de_licita(escenario):
    s, _ = escenario
    wa = WhatsAppFalso()
    payload = mensaje_entrante("hola")
    payload["entry"][0]["changes"][0]["value"]["metadata"] = {"phone_number_id": "NUMERO_MASIVO"}
    assert procesar_webhook(s, wa, payload, phone_number_id="NUMERO_LICITA", momento=MOMENTO) == 0
    payload["entry"][0]["changes"][0]["value"]["metadata"] = {"phone_number_id": "NUMERO_LICITA"}
    assert procesar_webhook(s, wa, payload, phone_number_id="NUMERO_LICITA", momento=MOMENTO) == 1


def test_no_responde_dos_veces_el_mismo_mensaje(escenario):
    s, _ = escenario
    wa = WhatsAppFalso()
    payload = mensaje_entrante("hola")
    assert procesar_webhook(s, wa, payload, momento=MOMENTO) == 1
    assert procesar_webhook(s, wa, payload, momento=MOMENTO) == 0  # Meta reintentó el mismo webhook
    assert len(wa.enviados) == 1


def test_ignora_confirmaciones_de_lectura(escenario):
    s, _ = escenario
    wa = WhatsAppFalso()
    payload = {"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.1", "status": "read"}]}}]}]}
    assert procesar_webhook(s, wa, payload, momento=MOMENTO) == 0


# --- Servidor (webhook) ---

def _app(Sesion, wa):
    return TestClient(crear_app(Sesion, wa, verify_token="VERIFICA", app_secret="SECRETO"))


def test_verificacion_del_webhook(Sesion):
    c = _app(Sesion, WhatsAppFalso())
    ok = c.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "VERIFICA", "hub.challenge": "123"})
    assert ok.status_code == 200 and ok.text == "123"
    assert c.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "otro", "hub.challenge": "1"}).status_code == 403


def test_webhook_exige_firma_valida(Sesion):
    wa = WhatsAppFalso()
    c = _app(Sesion, wa)
    cuerpo = json.dumps(mensaje_entrante("hola", de="56911111111")).encode()
    assert c.post("/webhook/whatsapp", content=cuerpo, headers={"X-Hub-Signature-256": "sha256=falsa"}).status_code == 401
    firma = "sha256=" + hmac.new(b"SECRETO", cuerpo, hashlib.sha256).hexdigest()
    r = c.post("/webhook/whatsapp", content=cuerpo, headers={"X-Hub-Signature-256": firma, "Content-Type": "application/json"})
    assert r.status_code == 200
    assert len(wa.enviados) == 1


def test_app_sin_secreto_no_arranca(Sesion):
    with pytest.raises(ValueError, match="WHATSAPP_APP_SECRET"):
        crear_app(Sesion, WhatsAppFalso(), verify_token="x", app_secret="")


def test_descargar_media_en_dos_pasos():
    def handler(request):
        assert request.headers["Authorization"] == "Bearer T"
        if request.url.path.endswith("/MEDIA1"):
            return httpx.Response(200, json={"url": "https://lookaside.fbsbx.com/archivo", "mime_type": "application/pdf", "file_size": 10})
        return httpx.Response(200, content=b"%PDF-1.7 hola")

    wa = ClienteWhatsApp("T", "1", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert wa.descargar_media("MEDIA1") == (b"%PDF-1.7 hola", "application/pdf")


def test_descargar_media_rechaza_archivos_grandes():
    wa = ClienteWhatsApp("T", "1", http=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"url": "https://x", "file_size": 50_000_000}))))
    with pytest.raises(ErrorWhatsApp, match="demasiado grande"):
        wa.descargar_media("MEDIA1")


# --- Copia del resumen por correo ---

class CorreoFalso:
    def __init__(self, falla=False):
        self.enviados, self.falla = [], falla

    def enviar(self, para, asunto, texto, html=None, encabezados=None):
        from licita.correo import ErrorCorreo

        if self.falla:
            raise ErrorCorreo("SMTP caído")
        self.enviados.append((para, asunto, texto, html, encabezados))
        return "<id@calza.cl>"


def _con_correo(escenario):
    s, e = escenario
    e.email = "contacto@aseosur.cl"
    s.commit()
    return s, e


def test_resumen_tambien_llega_por_correo_con_la_misma_numeracion(escenario):
    s, e = _con_correo(escenario)
    wa, correo = WhatsAppFalso(), CorreoFalso()
    r = enviar_resumenes(s, wa, momento=MOMENTO, correo=correo, url_publica="https://calza.cl", whatsapp_publico="56900000000")
    assert (r.plantillas, r.correos) == (1, 1)
    [(para, asunto, texto, html, encabezados)] = correo.enviados
    assert para == "contacto@aseosur.cl" and asunto == "2 licitaciones nuevas para Aseo Sur · 7 de octubre"
    assert "1. Adquisición de insumos de aseo para CESFAM (92% de calce)" in texto
    assert "https://calza.cl/licitacion/1234-56-LE26" in texto and 'href="https://calza.cl/licitacion/1234-56-LE26"' in html
    assert "escribe el número de la licitación al WhatsApp" in texto and "https://wa.me/56900000000" in html
    baja = f"https://calza.cl/correo/baja/{e.token_correo}"
    assert len(e.token_correo) >= 20 and baja in texto and baja in html
    assert encabezados == {"List-Unsubscribe": f"<{baja}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    assert [c.posicion_resumen for c in s.query(Calce).order_by(Calce.puntaje.desc())] == [1, 2]


def test_si_whatsapp_falla_el_correo_basta_y_no_se_repite(escenario):
    s, e = _con_correo(escenario)
    correo = CorreoFalso()
    r = enviar_resumenes(s, WhatsAppFalso(falla=True), momento=MOMENTO, correo=correo, url_publica="https://calza.cl")
    assert (r.errores, r.correos) == (1, 1)
    assert s.query(Calce).filter(Calce.notificado.is_(True)).count() == 2


def test_si_fallan_ambos_canales_se_reintenta_al_dia_siguiente(escenario):
    s, e = _con_correo(escenario)
    r = enviar_resumenes(s, WhatsAppFalso(falla=True), momento=MOMENTO, correo=CorreoFalso(falla=True), url_publica="https://calza.cl")
    assert (r.errores, r.errores_correo) == (1, 1)
    assert s.query(Calce).filter(Calce.notificado.is_(True)).count() == 0


def test_correo_respeta_su_baja_y_llega_aunque_haya_baja_de_whatsapp(escenario):
    s, e = _con_correo(escenario)
    e.whatsapp_activo = False
    s.commit()
    wa, correo = WhatsAppFalso(), CorreoFalso()
    assert enviar_resumenes(s, wa, momento=MOMENTO, correo=correo, url_publica="https://calza.cl").correos == 1
    assert wa.enviados == []
    e.resumen_por_correo = False
    s.add(Calce(empresa_id=e.id, licitacion_codigo="3456-7-LP26", puntaje=80, razon="Calza."))
    s.commit()
    r = enviar_resumenes(s, wa, momento=MOMENTO, correo=correo, url_publica="https://calza.cl")
    assert r.correos == 0 and len(correo.enviados) == 1  # sin canales activos: no se envía nada


def test_cliente_de_correo_agrega_encabezados_de_baja():
    from licita.correo import ClienteCorreo

    c = ClienteCorreo("smtp.gmail.com", 587, "u", "c", "hola@calza.cl")
    m = c.mensaje("a@b.cl", "Asunto", "texto", encabezados={"List-Unsubscribe": "<https://calza.cl/correo/baja/x>"})
    assert m["List-Unsubscribe"] == "<https://calza.cl/correo/baja/x>"



# --- 👍/👎 en el detalle ---

def test_tras_el_detalle_pregunta_si_sirve_y_guarda_la_valoracion(escenario):
    from licita.conversacion import responder

    s, e = escenario
    wa = WhatsAppFalso()
    enviar_resumenes(s, wa, momento=MOMENTO)
    procesar_webhook(s, wa, mensaje_entrante(texto="1"), momento=MOMENTO)
    [(tel, texto, botones)] = wa.botones
    assert "¿Te sirve esta licitación?" in texto
    assert botones == [("VALOR:+:1234-56-LE26", "👍 Me sirve"), ("VALOR:-:1234-56-LE26", "👎 No me sirve")]

    procesar_webhook(s, wa, mensaje_entrante(payload="VALOR:-:1234-56-LE26"), momento=MOMENTO)
    _, texto, botones = wa.botones[-1]
    assert "¿Por qué no te sirve?" in texto
    assert [titulo for _, titulo in botones] == ["No es mi rubro", "Monto o plazo", "Otra razón"]
    procesar_webhook(s, wa, mensaje_entrante(payload="MOTIVO:rubro:1234-56-LE26"), momento=MOMENTO)
    assert "*PERFIL*" in wa.enviados[-1][2]
    calce = s.query(Calce).filter_by(licitacion_codigo="1234-56-LE26").one()
    assert (calce.valoracion, calce.motivo_valoracion) == (-1, "rubro") and calce.valorado_en == MOMENTO

    assert responder(s, e, "boton", "VALOR:+:2345-12-L126", momento=MOMENTO) == [
        "¡Gracias! Te seguiré mostrando licitaciones como esta. 🙌"]
    assert s.query(Calce).filter_by(licitacion_codigo="2345-12-L126").one().valoracion == 1
    assert "No encontré" in responder(s, e, "boton", "VALOR:+:NO-EXISTE", momento=MOMENTO)[0]


def test_cliente_envia_botones_con_formato_de_meta():
    capturado = {}

    def handler(request):
        capturado["json"] = json.loads(request.content)
        return httpx.Response(200, json={"messages": [{"id": "wamid.B"}]})

    wa = ClienteWhatsApp("T", "PHONE", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert wa.enviar_botones("+56 9 8929 9524", "¿Te sirve?", [("VALOR:+:X", "👍 Me sirve")]) == "wamid.B"
    assert capturado["json"]["type"] == "interactive"
    assert capturado["json"]["interactive"]["action"]["buttons"] == [
        {"type": "reply", "reply": {"id": "VALOR:+:X", "title": "👍 Me sirve"}}]
