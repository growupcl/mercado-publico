import hashlib
import hmac
import itertools
from datetime import datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from licita import rut as rutlib
from licita.conversacion import procesar_webhook
from licita.db import Empresa, Pago, Suscripcion, ahora
from licita.flow import ClienteFlow, EstadoPago, PagoCreado, firmar
from licita.ia import ErrorIA, PerfilExtraido
from licita.notificaciones import enviar_resumenes
from licita.planes import monto
from licita.servidor import crear_app
from licita.suscripciones import (
    DatosRegistro, ErrorRegistro, confirmar_pago, empresa_por_token, por_vencer, registrar_empresa,
    revisar_vencimientos, sumar_meses,
)
from licita.web import crear_router_web

RUT = "76.123.456-0"
FORM = {
    "nombre": "Aseo Sur", "rut": RUT, "razon_social": "Aseo Sur SpA", "giro": "Venta de artículos de aseo",
    "email": "contacto@aseosur.cl", "whatsapp": "+56 9 8929 9524",
    "descripcion": "Vendemos insumos de aseo, guantes y bolsas de basura a municipios y centros de salud.",
    "regiones": ["Biobío", "Ñuble"], "monto_max": "30000000", "plan": "pro", "periodicidad": "mensual",
    "acepta_whatsapp": "1", "acepta_terminos": "1",
}


class IAFalsa:
    def __init__(self, falla=False):
        self.falla = falla

    def extraer_perfil(self, descripcion):
        if self.falla:
            raise ErrorIA("caída")
        return PerfilExtraido(rubros=["artículos de aseo"], palabras_clave=["detergente", "guantes"])


class FlowFalso:
    def __init__(self, estado=2, monto=None):
        self.creados = []
        self.estado = estado
        self.monto = monto

    def crear_pago(self, **kw):
        self.creados.append(kw)
        return PagoCreado(url=f"https://sandbox.flow.cl/app/web/pay.php?token=TOK{len(self.creados)}", token=f"TOK{len(self.creados)}", flow_order=99)

    def estado_pago(self, token):
        pedido = self.creados[int(token.removeprefix("TOK")) - 1]
        return EstadoPago(estado=self.estado, orden_comercio=pedido["orden_comercio"],
                          monto=self.monto if self.monto is not None else pedido["monto"], flow_order=99)


@pytest.fixture
def web(Sesion):
    flow = FlowFalso()
    app = crear_app(Sesion, router_web=crear_router_web(Sesion, flow=flow, ia=IAFalsa(), url_publica="https://calza.cl",
                                                        whatsapp_publico="56900000000"))
    return TestClient(app), Sesion, flow


def _registrar(cliente):
    r = cliente.post("/registro", data=FORM)
    assert r.status_code == 200, r.text
    token = r.text.split("https://calza.cl/cuenta/")[1].split('"')[0]
    return r, token


# --- Utilidades ---

def test_rut():
    assert rutlib.es_valido("76.123.456-0") and rutlib.es_valido("11.111.111-1") and rutlib.es_valido("10.000.013-k")
    assert not rutlib.es_valido("76.123.456-7")
    assert rutlib.formatear("761234560") == "76.123.456-0"


def test_montos_de_los_planes():
    assert monto("pyme", "mensual") == 19990 and monto("pyme", "anual") == 215900
    assert monto("pro", "anual") == 647900 and monto("pro", "mensual", fundador=True) == 39990
    assert monto("pro", "anual", fundador=True) == 431900


def test_sumar_meses():
    assert sumar_meses(datetime(2026, 1, 31), 1) == datetime(2026, 2, 28)
    assert sumar_meses(datetime(2026, 10, 20), 12) == datetime(2027, 10, 20)


def test_firma_y_cliente_flow():
    esperado = hmac.new(b"secreto", b"amount5000apiKeyAKcommerceOrderX", hashlib.sha256).hexdigest()
    assert firmar({"commerceOrder": "X", "apiKey": "AK", "amount": 5000}, "secreto") == esperado
    capturado = {}

    def handler(request):
        if request.method == "POST":
            capturado["form"] = parse_qs(request.content.decode())
            return httpx.Response(200, json={"url": "https://sandbox.flow.cl/app/web/pay.php", "token": "T1", "flowOrder": 7})
        capturado["get"] = dict(request.url.params)
        return httpx.Response(200, json={"status": 2, "commerceOrder": "CALZA-1", "amount": "19990", "flowOrder": 7})

    flow = ClienteFlow("AK", "secreto", http=httpx.Client(transport=httpx.MockTransport(handler)))
    creado = flow.crear_pago(orden_comercio="CALZA-1", asunto="Calza", monto=19990, email="a@b.cl",
                             url_confirmacion="https://calza.cl/c", url_retorno="https://calza.cl/r")
    assert creado.url == "https://sandbox.flow.cl/app/web/pay.php?token=T1"
    form = {k: v[0] for k, v in capturado["form"].items()}
    firma = form.pop("s")
    assert firma == firmar(form, "secreto") and form["amount"] == "19990" and form["currency"] == "CLP"
    estado = flow.estado_pago("T1")
    assert estado.pagado and estado.monto == 19990
    assert capturado["get"]["s"] == firmar({"token": "T1", "apiKey": "AK"}, "secreto")


# --- Registro ---

def test_pagina_de_inicio_y_legales(web):
    cliente, _, _ = web
    inicio = cliente.get("/")
    assert inicio.status_code == 200 and "$19.990" in inicio.text and "$39.990" in inicio.text
    assert "no afiliado a ChileCompra" in inicio.text
    assert cliente.get("/terminos").status_code == 200 and "BAJA" in cliente.get("/privacidad").text


def test_registro_valido_crea_prueba_pro(web):
    cliente, Sesion, _ = web
    r, token = _registrar(cliente)
    assert "¡Listo, Aseo Sur!" in r.text and "wa.me/56900000000" in r.text
    with Sesion() as s:
        e = empresa_por_token(s, token)
        assert (e.plan, e.rut, e.whatsapp, e.regiones) == ("pro", "761234560", "56989299524", ["Biobío", "Ñuble"])
        assert e.palabras_clave == ["detergente", "guantes", "artículos de aseo"]
        assert e.consentimiento_whatsapp_en is not None
        sus = s.query(Suscripcion).one()
        assert (sus.estado, sus.plan) == ("prueba", "pro")
        assert timedelta(days=13) < sus.vigente_hasta - ahora() <= timedelta(days=14)


def test_registro_con_errores_muestra_mensajes(web):
    cliente, Sesion, _ = web
    malo = {**FORM, "rut": "76.123.456-7", "whatsapp": "123", "acepta_whatsapp": ""}
    r = cliente.post("/registro", data=malo)
    assert r.status_code == 422
    assert "El RUT no es válido" in r.text and "celular chileno" in r.text and "autorización" in r.text
    assert 'value="Aseo Sur"' in r.text  # conserva lo escrito
    with Sesion() as s:
        assert s.query(Empresa).count() == 0


def test_registro_duplicado(web):
    cliente, _, _ = web
    _registrar(cliente)
    r = cliente.post("/registro", data={**FORM, "whatsapp": "+56 9 1111 2222"})
    assert r.status_code == 422 and "Ya existe una cuenta con este RUT" in r.text


def test_si_la_ia_falla_usa_las_palabras_de_la_descripcion(Sesion):
    with Sesion() as s:
        datos = DatosRegistro(**{**{k: v for k, v in FORM.items() if k not in ("acepta_whatsapp", "acepta_terminos", "monto_max")},
                                 "acepta_whatsapp": True, "acepta_terminos": True})
        e, _ = registrar_empresa(s, datos, ia=IAFalsa(falla=True))
        assert "guantes" in e.palabras_clave and "basura" in e.palabras_clave


# --- Cuenta y pagos ---

def test_cuenta_y_pago_completo(web):
    cliente, Sesion, flow = web
    _, token = _registrar(cliente)
    cuenta = cliente.get(f"/cuenta/{token}")
    assert cuenta.status_code == 200 and "Prueba gratuita" in cuenta.text and "$39.990" in cuenta.text
    assert cliente.get("/cuenta/token-que-no-existe-xxxxxxxx").status_code == 404

    r = cliente.post(f"/cuenta/{token}/pagar", data={"plan": "pro", "periodicidad": "mensual"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("https://sandbox.flow.cl/")
    assert flow.creados[0]["monto"] == 39990 and flow.creados[0]["url_confirmacion"] == "https://calza.cl/pagos/flow/confirmacion"

    assert cliente.post("/pagos/flow/confirmacion", data={"token": "TOK1"}).text == "ok"
    with Sesion() as s:
        sus = s.query(Suscripcion).one()
        pago = s.query(Pago).one()
        assert (pago.estado, sus.estado, sus.plan, sus.precio_fundador) == ("pagado", "activa", "pro", True)
        # Los días de prueba se suman al mes pagado.
        assert timedelta(days=13 + 28) < sus.vigente_hasta - ahora() <= timedelta(days=14 + 31)
        fin = sus.vigente_hasta

    retorno = cliente.post("/pagos/flow/retorno", data={"token": "TOK1"})
    assert "¡Pago recibido!" in retorno.text
    with Sesion() as s:  # confirmar dos veces no extiende dos veces
        assert s.query(Suscripcion).one().vigente_hasta == fin


def test_pago_con_monto_distinto_no_activa(web):
    cliente, Sesion, flow = web
    _, token = _registrar(cliente)
    cliente.post(f"/cuenta/{token}/pagar", data={"plan": "pyme", "periodicidad": "anual"}, follow_redirects=False)
    flow.monto = 100
    cliente.post("/pagos/flow/confirmacion", data={"token": "TOK1"})
    with Sesion() as s:
        assert s.query(Pago).one().estado == "pendiente"
        assert s.query(Suscripcion).one().estado == "prueba"


def test_pago_rechazado(web):
    cliente, Sesion, flow = web
    _, token = _registrar(cliente)
    cliente.post(f"/cuenta/{token}/pagar", data={"plan": "pyme", "periodicidad": "mensual"}, follow_redirects=False)
    flow.estado = 3
    r = cliente.get("/pagos/flow/retorno", params={"token": "TOK1"})
    assert "El pago no se completó" in r.text
    with Sesion() as s:
        assert s.query(Pago).one().estado == "rechazado"


def test_pagar_sin_flow_configurado(Sesion):
    cliente = TestClient(crear_app(Sesion, router_web=crear_router_web(Sesion, flow=None, ia=IAFalsa(), url_publica="https://calza.cl")))
    _, token = _registrar(cliente)
    r = cliente.post(f"/cuenta/{token}/pagar", data={"plan": "pyme", "periodicidad": "mensual"})
    assert r.status_code == 503


# --- Vencimientos, WhatsApp y planes ---

def test_vencimiento_pasa_a_gratis_y_deja_de_recibir_resumen(web):
    cliente, Sesion, _ = web
    _registrar(cliente)
    with Sesion() as s:
        en_10_dias = ahora() + timedelta(days=12)
        assert [e.nombre for e, _ in por_vencer(s, dias=3, momento=en_10_dias)] == ["Aseo Sur"]
        vencidas = revisar_vencimientos(s, momento=ahora() + timedelta(days=15))
        assert [e.plan for e in vencidas] == ["gratis"]

        class WA:
            enviados = []
        r = enviar_resumenes(s, WA())
        assert (r.plantillas, r.textos, r.sin_novedades) == (0, 0, 0)


def test_cuenta_por_whatsapp_genera_enlace_nuevo(web):
    cliente, Sesion, _ = web
    _, token_viejo = _registrar(cliente)

    class WA:
        def __init__(self):
            self.enviados = []

        def enviar_texto(self, tel, texto):
            self.enviados.append(texto)
            return "w"

    wa = WA()
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "56989299524", "id": "wamid.c1", "type": "text", "text": {"body": "CUENTA"}}]}}]}]}
    with Sesion() as s:
        procesar_webhook(s, wa, payload, url_publica="https://calza.cl")
    token_nuevo = wa.enviados[0].split("https://calza.cl/cuenta/")[1].split()[0]
    assert cliente.get(f"/cuenta/{token_nuevo}").status_code == 200
    assert cliente.get(f"/cuenta/{token_viejo}").status_code == 404
