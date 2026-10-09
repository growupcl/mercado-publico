import hashlib
import hmac
import itertools
from datetime import datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import select
from fastapi.testclient import TestClient

from licita import facturas
from licita import rut as rutlib
from licita.conversacion import procesar_webhook
from licita.db import Empresa, MandatoPago, Pago, Suscripcion, ahora
from licita.mercadopago import ClienteMercadoPago, CobroMP, ErrorMercadoPago, SuscripcionMP, verificar_firma
from licita.ia import ErrorIA, PerfilExtraido
from licita.notificaciones import enviar_resumenes
from licita.planes import monto
from licita.servidor import crear_app
from licita.suscripciones import (
    DatosRegistro, empresa_por_token, por_vencer, registrar_empresa, revisar_vencimientos, sumar_meses,
)
from licita.web import crear_router_web

RUT = "76.123.456-0"
FORM = {
    "nombre": "Aseo Sur", "rut": RUT, "razon_social": "Aseo Sur SpA", "giro": "Venta de artículos de aseo",
    "direccion": "Av. Colón 1234", "comuna": "Concepción",
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


SECRETO = "secreto-webhook"


class MPFalso:
    """Imita la API de Mercado Pago: suscripciones y cobros."""

    def __init__(self):
        self.creadas = []
        self.estados = {}
        self.cobros = {}
        self.canceladas = []

    def crear_suscripcion(self, **kw):
        self.creadas.append(kw)
        mp_id = f"PRE{len(self.creadas)}"
        self.estados[mp_id] = "pending"
        return SuscripcionMP(id=mp_id, estado="pending", referencia=kw["referencia"], monto=kw["monto"],
                             url_pago=f"https://www.mercadopago.cl/subscriptions/checkout?preapproval_id={mp_id}")

    def obtener_suscripcion(self, mp_id):
        kw = self.creadas[int(mp_id.removeprefix("PRE")) - 1]
        return SuscripcionMP(id=mp_id, estado=self.estados[mp_id], referencia=kw["referencia"], monto=kw["monto"])

    def cancelar_suscripcion(self, mp_id):
        self.estados[mp_id] = "cancelled"
        self.canceladas.append(mp_id)
        return self.obtener_suscripcion(mp_id)

    def obtener_cobro(self, cobro_id):
        return self.cobros[cobro_id]


def _aviso(cliente, tipo, data_id, *, secreto=SECRETO):
    ts, req = "1760000000", "req-1"
    firma = hmac.new(secreto.encode(), f"id:{data_id.lower()};request-id:{req};ts:{ts};".encode(), hashlib.sha256).hexdigest()
    return cliente.post(f"/pagos/mercadopago/webhook?type={tipo}&data.id={data_id}",
                        json={"type": tipo, "data": {"id": data_id}},
                        headers={"x-signature": f"ts={ts},v1={firma}", "x-request-id": req})


@pytest.fixture
def web(Sesion):
    mp = MPFalso()
    app = crear_app(Sesion, router_web=crear_router_web(Sesion, mp=mp, ia=IAFalsa(), url_publica="https://calza.cl",
                                                        whatsapp_publico="56900000000", mp_webhook_secreto=SECRETO))
    return TestClient(app), Sesion, mp


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
    assert monto("pyme", "mensual") == 19990 and monto("pyme", "anual") == 215890
    assert monto("pro", "anual") == 647900 and monto("pro", "mensual", fundador=True) == 39990
    assert monto("pro", "anual", fundador=True) == 431900


def test_sumar_meses():
    assert sumar_meses(datetime(2026, 1, 31), 1) == datetime(2026, 2, 28)
    assert sumar_meses(datetime(2026, 10, 20), 12) == datetime(2027, 10, 20)


def test_firma_de_webhook_de_mercado_pago():
    v1 = hmac.new(b"clave", b"id:abc123;request-id:r1;ts:170;", hashlib.sha256).hexdigest()
    assert verificar_firma("clave", f"ts=170,v1={v1}", "r1", "ABC123")
    assert not verificar_firma("clave", f"ts=170,v1={v1}", "otro", "ABC123")
    assert not verificar_firma("clave", None, "r1", "ABC123")


def test_cliente_mercado_pago():
    vistos = []

    def handler(request):
        vistos.append(request)
        if request.url.path == "/preapproval":
            return httpx.Response(201, json={"id": "2c93", "status": "pending", "external_reference": "CALZA-1-AB",
                                             "init_point": "https://www.mercadopago.cl/subscriptions/checkout?preapproval_id=2c93",
                                             "auto_recurring": {"transaction_amount": 39990}})
        return httpx.Response(200, json={"id": 7001, "preapproval_id": "2c93", "transaction_amount": 39990,
                                         "payment": {"id": 555, "status": "approved"}})

    mp = ClienteMercadoPago("TEST-123", http=httpx.Client(transport=httpx.MockTransport(handler)))
    creada = mp.crear_suscripcion(referencia="CALZA-1-AB", motivo="Calza plan Pro mensual", email="a@b.cl", monto=39990,
                                  meses=1, url_retorno="https://calza.cl/pagos/mercadopago/retorno", inicio=datetime(2026, 10, 20, 12))
    assert creada.url_pago.endswith("preapproval_id=2c93") and creada.monto == 39990
    import json
    cuerpo = json.loads(vistos[0].content)
    assert vistos[0].headers["Authorization"] == "Bearer TEST-123" and vistos[0].headers["X-Idempotency-Key"]
    assert cuerpo["auto_recurring"] == {"frequency": 1, "frequency_type": "months", "transaction_amount": 39990,
                                        "currency_id": "CLP", "start_date": "2026-10-20T12:00:00.000Z"}
    assert cuerpo["status"] == "pending" and cuerpo["external_reference"] == "CALZA-1-AB"
    cobro = mp.obtener_cobro("7001")
    assert (cobro.suscripcion_id, cobro.estado_pago, cobro.monto) == ("2c93", "approved", 39990)


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
    malo = {**FORM, "rut": "76.123.456-7", "whatsapp": "123", "acepta_whatsapp": "", "direccion": ""}
    r = cliente.post("/registro", data=malo)
    assert r.status_code == 422
    assert "El RUT no es válido" in r.text and "celular chileno" in r.text and "autorización" in r.text
    assert "la dirección comercial" in r.text
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


# --- Cuenta y suscripción ---

def _suscribir(cliente, token, plan="pro", periodicidad="mensual"):
    r = cliente.post(f"/cuenta/{token}/suscribir", data={"plan": plan, "periodicidad": periodicidad}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return r.headers["location"]


def test_suscripcion_completa_durante_la_prueba(web):
    cliente, Sesion, mp = web
    _, token = _registrar(cliente)
    cuenta = cliente.get(f"/cuenta/{token}")
    assert cuenta.status_code == 200 and "Prueba gratuita" in cuenta.text and "$39.990" in cuenta.text
    assert "Virtus SpA emite la factura a nombre de Aseo Sur SpA" in cuenta.text
    assert cliente.get("/cuenta/token-que-no-existe-xxxxxxxx").status_code == 404

    assert _suscribir(cliente, token).startswith("https://www.mercadopago.cl/subscriptions/checkout")
    pedido = mp.creadas[0]
    with Sesion() as s:
        fin_prueba = s.query(Suscripcion).one().vigente_hasta
    assert pedido["monto"] == 39990 and pedido["meses"] == 1 and pedido["inicio"] == fin_prueba  # cobra al terminar la prueba

    mp.estados["PRE1"] = "authorized"
    retorno = cliente.get("/pagos/mercadopago/retorno", params={"preapproval_id": "PRE1"})
    assert "¡Suscripción lista!" in retorno.text and "El primer cobro será el" in retorno.text
    with Sesion() as s:
        sus = s.query(Suscripcion).one()
        assert sus.mandato_activo_id is not None and sus.estado == "prueba"
    assert "Renovación automática" in cliente.get(f"/cuenta/{token}").text

    # Al terminar la prueba, Mercado Pago cobra y avisa.
    mp.cobros["9001"] = CobroMP(id="9001", suscripcion_id="PRE1", estado_pago="approved", monto=39990)
    assert _aviso(cliente, "subscription_authorized_payment", "9001").text == "ok"
    with Sesion() as s:
        sus = s.query(Suscripcion).one()
        assert (sus.estado, sus.plan, sus.precio_fundador) == ("activa", "pro", True)
        assert sus.vigente_hasta == sumar_meses(fin_prueba, 1)
        assert s.query(Pago).one().estado == "pagado"
    _aviso(cliente, "subscription_authorized_payment", "9001")  # aviso repetido
    with Sesion() as s:
        assert s.query(Pago).count() == 1 and s.query(Suscripcion).one().vigente_hasta == sumar_meses(fin_prueba, 1)

    # Factura: queda pendiente con los datos para el portal del SII y, con el folio, se ve en la cuenta.
    assert "En emisión" in cliente.get(f"/cuenta/{token}").text
    with Sesion() as s:
        [f] = facturas.pendientes(s)
        assert (f.rut, f.razon_social, f.direccion, f.comuna) == ("76.123.456-0", "Aseo Sur SpA", "Av. Colón 1234", "Concepción")
        assert (f.neto, f.iva, f.total) == (33605, 6385, 39990) and f.glosa == "Suscripción Calza plan Pro mensual (1 mes)"
        assert not f.datos_incompletos
        assert "76.123.456-0;Aseo Sur SpA" in facturas.csv_facturas([f])
        facturas.marcar_emitida(s, f.pago_id, "125")
        s.commit()
        assert facturas.pendientes(s) == []
        with pytest.raises(ValueError):
            facturas.marcar_emitida(s, f.pago_id, "126")
    assert "N° 125" in cliente.get(f"/cuenta/{token}").text


def test_aviso_con_firma_invalida_se_rechaza(web):
    cliente, _, mp = web
    assert _aviso(cliente, "subscription_preapproval", "PRE1", secreto="otro").status_code == 401


def test_cambio_de_plan_cancela_la_suscripcion_anterior(web):
    cliente, Sesion, mp = web
    _, token = _registrar(cliente)
    _suscribir(cliente, token, "pyme", "mensual")
    mp.estados["PRE1"] = "authorized"
    _aviso(cliente, "subscription_preapproval", "PRE1")
    _suscribir(cliente, token, "pro", "anual")
    mp.estados["PRE2"] = "authorized"
    _aviso(cliente, "subscription_preapproval", "PRE2")
    assert mp.canceladas == ["PRE1"]
    with Sesion() as s:
        sus = s.query(Suscripcion).one()
        assert (sus.plan, sus.periodicidad) == ("pro", "anual")
        assert s.get(MandatoPago, sus.mandato_activo_id).monto == 431900


def test_cancelar_renovacion(web):
    cliente, Sesion, mp = web
    _, token = _registrar(cliente)
    _suscribir(cliente, token, "pyme", "mensual")
    mp.estados["PRE1"] = "authorized"
    _aviso(cliente, "subscription_preapproval", "PRE1")
    r = cliente.post(f"/cuenta/{token}/cancelar", follow_redirects=False)
    assert r.status_code == 303 and mp.canceladas == ["PRE1"]
    with Sesion() as s:
        assert s.query(Suscripcion).one().mandato_activo_id is None


def test_cobro_rechazado_o_con_monto_distinto_no_extiende(web):
    cliente, Sesion, mp = web
    _, token = _registrar(cliente)
    _suscribir(cliente, token, "pyme", "anual")
    mp.estados["PRE1"] = "authorized"
    _aviso(cliente, "subscription_preapproval", "PRE1")
    mp.cobros["1"] = CobroMP(id="1", suscripcion_id="PRE1", estado_pago="approved", monto=100)
    mp.cobros["2"] = CobroMP(id="2", suscripcion_id="PRE1", estado_pago="rejected", monto=215890)
    _aviso(cliente, "subscription_authorized_payment", "1")
    _aviso(cliente, "subscription_authorized_payment", "2")
    with Sesion() as s:
        assert [p.estado for p in s.query(Pago)] == ["rechazado"]
        assert s.query(Suscripcion).one().estado == "prueba"


def test_sin_mercado_pago_configurado(Sesion):
    cliente = TestClient(crear_app(Sesion, router_web=crear_router_web(Sesion, mp=None, ia=IAFalsa(), url_publica="https://calza.cl")))
    _, token = _registrar(cliente)
    assert "todavía no están habilitados" in cliente.get(f"/cuenta/{token}").text
    assert cliente.post(f"/cuenta/{token}/suscribir", data={"plan": "pyme", "periodicidad": "mensual"}).status_code == 503


def test_exige_secreto_de_webhook(Sesion):
    with pytest.raises(ValueError, match="MERCADOPAGO_WEBHOOK_SECRET"):
        crear_router_web(Sesion, mp=MPFalso(), ia=None, url_publica="https://calza.cl")


# --- Vencimientos, WhatsApp y planes ---

def test_vencimiento_pasa_a_gratis_y_deja_de_recibir_resumen(web):
    cliente, Sesion, _ = web
    _registrar(cliente)
    with Sesion() as s:
        en_10_dias = ahora() + timedelta(days=12)
        assert [e.nombre for e, _ in por_vencer(s, dias=3, momento=en_10_dias)] == ["Aseo Sur"]
        vencidas = revisar_vencimientos(s, momento=ahora() + timedelta(days=15))
        assert [e.plan for e in vencidas] == ["gratis"]
        assert "Virtus SpA" in cliente.get("/terminos").text

        class WA:
            enviados = []
        r = enviar_resumenes(s, WA())
        assert (r.plantillas, r.textos, r.sin_novedades) == (0, 0, 0)


def test_con_renovacion_automatica_hay_dias_de_gracia(web):
    cliente, Sesion, mp = web
    _, token = _registrar(cliente)
    _suscribir(cliente, token, "pyme", "mensual")
    mp.estados["PRE1"] = "authorized"
    _aviso(cliente, "subscription_preapproval", "PRE1")
    with Sesion() as s:
        fin = s.query(Suscripcion).one().vigente_hasta
        assert revisar_vencimientos(s, momento=fin + timedelta(days=1)) == []  # el cobro puede venir atrasado
        assert len(revisar_vencimientos(s, momento=fin + timedelta(days=4))) == 1


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


def test_desglose_de_iva_como_el_sii():
    from licita.planes import monto

    for plan, periodicidad, fundador in (("pyme", "mensual", False), ("pyme", "anual", False), ("pro", "mensual", False),
                                         ("pro", "mensual", True), ("pro", "anual", False), ("pro", "anual", True)):
        total = monto(plan, periodicidad, fundador=fundador)
        neto, iva = facturas.desglose_iva(total)
        assert neto + iva == total and iva == facturas.iva_de(neto)
    # Un monto sin neto exacto (como el antiguo $215.900): se usa el más cercano sin pasarse.
    assert facturas.desglose_iva(215_900) == (181_428, 34_471)


def test_webhook_de_recurso_inexistente_responde_ok(web):
    """La notificación de prueba del panel de Mercado Pago trae un ID inventado: no hay que pedir reintentos."""
    cliente, _, mp = web

    def no_existe(mp_id):
        raise ErrorMercadoPago("Mercado Pago respondió HTTP 404: not found", estado_http=404)

    mp.obtener_suscripcion = no_existe
    r = _aviso(cliente, "subscription_preapproval", "123456")
    assert r.status_code == 200

    def caida(mp_id):
        raise ErrorMercadoPago("Mercado Pago respondió HTTP 500", estado_http=500)

    mp.obtener_suscripcion = caida
    assert _aviso(cliente, "subscription_preapproval", "123456").status_code == 502  # Mercado Pago reintentará


def test_suscripcion_de_prueba_cobra_hoy_un_monto_bajo(web):
    cliente, Sesion, mp = web
    cliente.post("/registro", data=FORM)
    with Sesion() as s:
        from licita.db import Empresa
        from licita.suscripciones import iniciar_suscripcion

        empresa = s.scalars(select(Empresa)).one()
        url = iniciar_suscripcion(s, empresa, mp, plan="pyme", periodicidad="mensual", url_publica="https://calza.cl",
                                  monto_prueba=1000)
        mandato = s.scalars(select(MandatoPago)).one()
    assert url.startswith("https://www.mercadopago.cl/")
    assert mp.creadas[-1]["monto"] == 1000 and mp.creadas[-1]["inicio"] is None  # cobra hoy, aunque esté en prueba
    assert mp.creadas[-1]["motivo"] == "Calza prueba de cobro"
    assert (mandato.monto, mandato.precio_fundador) == (1000, False)
