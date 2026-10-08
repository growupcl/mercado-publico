from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from licita.avisos import avisar_cobros_rechazados, fecha_larga
from licita.suscripciones import DIAS_GRACIA_RENOVACION
from licita.db import Empresa, MensajeWhatsApp, Pago, Suscripcion, ahora
from licita.mercadopago import CobroMP
from licita.servidor import crear_app
from licita.web import crear_router_web
from licita.whatsapp import ErrorWhatsApp
from test_web import SECRETO, IAFalsa, MPFalso, _aviso, _registrar, _suscribir


class WAFalso:
    def __init__(self):
        self.enviados = []
        self.falla = False

    def enviar_texto(self, tel, texto):
        if self.falla:
            raise ErrorWhatsApp("caído")
        self.enviados.append(("texto", texto))
        return "w"

    def enviar_plantilla(self, tel, nombre, *, idioma="es", parametros=None, payloads_botones=None):
        if self.falla:
            raise ErrorWhatsApp("caído")
        self.enviados.append(("plantilla", nombre, parametros, payloads_botones))
        return "w"


@pytest.fixture
def entorno(Sesion):
    mp, wa = MPFalso(), WAFalso()
    router = crear_router_web(Sesion, mp=mp, ia=IAFalsa(), url_publica="https://calza.cl", mp_webhook_secreto=SECRETO, wa=wa)
    cliente = TestClient(crear_app(Sesion, router_web=router))
    _, token = _registrar(cliente)
    _suscribir(cliente, token, "pyme", "mensual")
    mp.estados["PRE1"] = "authorized"
    _aviso(cliente, "subscription_preapproval", "PRE1")
    return cliente, Sesion, mp, wa, token


def _rechazo(cliente, mp, cobro_id, suscripcion="PRE1", monto=19990):
    mp.cobros[cobro_id] = CobroMP(id=cobro_id, suscripcion_id=suscripcion, estado_pago="rejected", monto=monto)
    assert _aviso(cliente, "subscription_authorized_payment", cobro_id).text == "ok"


def test_cobro_rechazado_avisa_con_plantilla_si_la_ventana_esta_cerrada(entorno):
    cliente, Sesion, mp, wa, token = entorno
    _rechazo(cliente, mp, "R1")
    assert len(wa.enviados) == 1
    tipo, nombre, parametros, botones = wa.enviados[0]
    assert (tipo, nombre, botones) == ("plantilla", "cobro_rechazado", ["CUENTA"])
    with Sesion() as s:
        limite = fecha_larga(s.query(Suscripcion).one().vigente_hasta + timedelta(days=DIAS_GRACIA_RENOVACION))
    assert parametros == ["Aseo Sur", "Pyme mensual", limite]
    with Sesion() as s:
        assert s.query(Pago).one().aviso_enviado_en is not None
        assert s.query(MensajeWhatsApp).filter_by(tipo="plantilla").count() == 1
    # "Mi cuenta" muestra el aviso.
    assert "No pudimos cobrar tu plan" in cliente.get(f"/cuenta/{token}").text


def test_con_ventana_abierta_envia_texto_con_enlace_a_la_cuenta(entorno):
    cliente, Sesion, mp, wa, _ = entorno
    with Sesion() as s:
        s.query(Empresa).one().ultimo_mensaje_entrante = ahora() - timedelta(hours=2)
        s.commit()
    _rechazo(cliente, mp, "R1")
    tipo, texto = wa.enviados[0]
    assert tipo == "texto" and "no pudimos cobrar tu plan Pyme mensual" in texto
    enlace = texto.split("https://calza.cl")[1].strip()
    assert cliente.get(enlace).status_code == 200  # el enlace funciona
    with Sesion() as s:  # el enlace privado no queda guardado en el registro de mensajes
        assert "/cuenta/" not in s.query(MensajeWhatsApp).filter_by(direccion="saliente").one().contenido


def test_no_repite_el_aviso_en_los_reintentos(entorno):
    cliente, Sesion, mp, wa, _ = entorno
    _rechazo(cliente, mp, "R1")
    _rechazo(cliente, mp, "R2")  # Mercado Pago reintenta al día siguiente: no se vuelve a avisar
    assert len(wa.enviados) == 1
    with Sesion() as s:
        s.query(Pago).filter_by(mp_cobro_id="R1").one().aviso_enviado_en = ahora() - timedelta(days=4)
        s.query(Pago).filter_by(mp_cobro_id="R2").one().aviso_enviado_en = ahora() - timedelta(days=4)
        s.commit()
    _rechazo(cliente, mp, "R3")  # pasados 3 días desde el último aviso, sí se avisa
    assert len(wa.enviados) == 2


def test_no_avisa_si_un_cobro_posterior_se_aprobo_o_si_cancelo(entorno):
    cliente, Sesion, mp, wa, token = entorno
    wa.falla = True
    _rechazo(cliente, mp, "R1")  # el aviso falla y queda pendiente
    mp.cobros["OK1"] = CobroMP(id="OK1", suscripcion_id="PRE1", estado_pago="approved", monto=19990)
    _aviso(cliente, "subscription_authorized_payment", "OK1")
    wa.falla = False
    with Sesion() as s:
        assert avisar_cobros_rechazados(s, wa, url_publica="https://calza.cl") == 0
    assert wa.enviados == []
    assert "No pudimos cobrar tu plan" not in cliente.get(f"/cuenta/{token}").text

    cliente.post(f"/cuenta/{token}/cancelar")
    _rechazo(cliente, mp, "R2")
    assert wa.enviados == []


def test_si_whatsapp_falla_se_reintenta_despues(entorno):
    cliente, Sesion, mp, wa, _ = entorno
    wa.falla = True
    _rechazo(cliente, mp, "R1")
    with Sesion() as s:
        assert s.query(Pago).one().aviso_enviado_en is None
    wa.falla = False
    with Sesion() as s:
        assert avisar_cobros_rechazados(s, wa, url_publica="https://calza.cl") == 1


def test_respeta_la_baja_de_whatsapp(entorno):
    cliente, Sesion, mp, wa, _ = entorno
    with Sesion() as s:
        s.query(Empresa).one().whatsapp_activo = False
        s.commit()
    _rechazo(cliente, mp, "R1")
    assert wa.enviados == []
