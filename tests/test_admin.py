from datetime import timedelta

from fastapi.testclient import TestClient

from licita.admin import datos_panel
from licita.db import Empresa, MandatoPago, Pago, Suscripcion, ahora
from licita.servidor import crear_app
from licita.suscripciones import empresa_por_token
from licita.web import crear_router_web
from test_web import FORM, SECRETO, IAFalsa, MPFalso, _registrar

CLAVE = "clave-larga-de-prueba"


def _cliente(Sesion, clave=CLAVE):
    router = crear_router_web(Sesion, mp=MPFalso(), ia=IAFalsa(), url_publica="https://calza.cl",
                              mp_webhook_secreto=SECRETO, admin_clave=clave)
    return TestClient(crear_app(Sesion, router_web=router))


def test_sin_clave_configurada_el_panel_no_existe(Sesion):
    assert _cliente(Sesion, clave="").get("/admin", auth=("admin", "")).status_code == 404


def test_pide_usuario_y_clave(Sesion):
    cliente = _cliente(Sesion)
    r = cliente.get("/admin")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    assert cliente.get("/admin", auth=("admin", "otra")).status_code == 401
    assert cliente.get("/admin", auth=("otro", CLAVE)).status_code == 401
    r = cliente.get("/admin", auth=("admin", CLAVE))
    assert r.status_code == 200 and "Panel del piloto" in r.text
    assert r.headers["cache-control"] == "no-store"


def test_muestra_empresas_e_indica_a_quien_contactar(Sesion):
    cliente = _cliente(Sesion)
    _, token = _registrar(cliente)
    _registrar_otra = {**FORM, "nombre": "Jardines Norte", "rut": "11.111.111-1", "whatsapp": "+56 9 1111 2222",
                       "email": "hola@jardines.cl"}
    assert cliente.post("/registro", data=_registrar_otra).status_code == 200
    with Sesion() as s:
        aseo = empresa_por_token(s, token)
        aseo.creada_en = ahora() - timedelta(days=12)
        aseo.ultimo_mensaje_entrante = ahora() - timedelta(days=9)
        sus = s.query(Suscripcion).filter_by(empresa_id=aseo.id).one()
        sus.vigente_hasta = ahora() + timedelta(days=1, hours=2)
        s.commit()
    r = cliente.get("/admin", auth=("admin", CLAVE))
    assert "Aseo Sur" in r.text and "Jardines Norte" in r.text
    assert "Prueba termina sin suscripción" in r.text and "Sin escribir hace 9 días" in r.text
    assert 'href="https://wa.me/56989299524"' in r.text and "mailto:contacto@aseosur.cl" in r.text
    assert r.text.index("Aseo Sur") < r.text.index("Jardines Norte")  # primero a quien contactar
    with Sesion() as s:
        ind, filas = datos_panel(s)
        assert (ind.empresas, ind.en_prueba, ind.por_contactar) == (2, 2, 1)
        assert filas[1].motivos == []  # recién registrada: todavía no se le pide que escriba


def test_ingreso_mensual_y_cobrado_del_mes(Sesion):
    with Sesion() as s:
        e = Empresa(nombre="Aseo Sur", descripcion="x", whatsapp="56911111111", email="a@b.cl", plan="pyme")
        s.add(e)
        s.flush()
        anual = MandatoPago(empresa_id=e.id, referencia="R1", plan="pyme", periodicidad="anual", monto=215_890,
                            estado="authorized")
        s.add(anual)
        s.flush()
        s.add(Suscripcion(empresa_id=e.id, plan="pyme", periodicidad="anual", estado="activa",
                          vigente_hasta=ahora() + timedelta(days=300), mandato_activo_id=anual.id))
        s.add(Pago(empresa_id=e.id, plan="pyme", periodicidad="anual", monto=215_890, mandato_id=anual.id,
                   mp_cobro_id="C1", estado="pagado", pagado_en=ahora()))
        s.commit()
        ind, [fila] = datos_panel(s)
    assert (ind.activas, ind.con_renovacion, ind.ingreso_mensual, ind.cobrado_mes) == (1, 1, 17_991, 215_890)
    assert fila.pagado == 215_890 and fila.mandato is not None


def test_filtros_y_estado_vacio(Sesion):
    cliente = _cliente(Sesion)
    r = cliente.get("/admin", auth=("admin", CLAVE))
    assert "Todo al día" in r.text and "Todavía no hay empresas registradas" in r.text
    _registrar(cliente)
    r = cliente.get("/admin?estado=vencida", auth=("admin", CLAVE))
    assert "No hay empresas en este filtro" in r.text and 'aria-current="page">Vencidas' in r.text
    r = cliente.get("/admin?estado=prueba", auth=("admin", CLAVE))
    assert "Aseo Sur" in r.text and "Día 0 de 14" in r.text
    assert cliente.get("/admin?estado=otra-cosa", auth=("admin", CLAVE)).status_code == 200  # cae en "Todas"


def test_tiempo_relativo():
    from datetime import datetime

    from licita.admin import hace

    ahora_ = datetime(2026, 10, 20, 10)
    assert [hace(None, ahora_), hace(ahora_, ahora_), hace(datetime(2026, 10, 19, 23), ahora_),
            hace(datetime(2026, 10, 14), ahora_), hace(datetime(2026, 9, 29), ahora_)] == [
        "nunca", "hoy", "ayer", "hace 6 días", "hace 3 semanas"]


def test_valoraciones_por_empresa_y_calce_util(Sesion, cliente_mp):
    from datetime import date

    from licita.db import Calce
    from licita.sync import sincronizar_licitaciones

    cliente = _cliente(Sesion)
    _, token = _registrar(cliente)
    with Sesion() as s:
        sincronizar_licitaciones(s, cliente_mp, date(2026, 10, 6))
        e = empresa_por_token(s, token)
        s.add_all([Calce(empresa_id=e.id, licitacion_codigo="1234-56-LE26", puntaje=90, valoracion=1),
                   Calce(empresa_id=e.id, licitacion_codigo="2345-12-L126", puntaje=70, valoracion=1),
                   Calce(empresa_id=e.id, licitacion_codigo="3456-7-LP26", puntaje=60, valoracion=-1)])
        s.commit()
        ind, [fila] = datos_panel(s)
    assert (fila.utiles, fila.no_utiles, ind.calce_util) == (2, 1, 67)
    r = cliente.get("/admin", auth=("admin", CLAVE))
    assert "Valoró: 👍 2 · 👎 1" in r.text and "67%" in r.text
