from datetime import timedelta

from fastapi.testclient import TestClient

from licita.admin import (
    BLOQUEO, DIAS_SESION, INTENTOS_MAXIMOS, Intentos, datos_panel, sesion_valida, token_sesion,
)
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


def test_sin_sesion_lleva_a_la_pagina_de_entrada(Sesion):
    cliente = _cliente(Sesion)
    r = cliente.get("/admin", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/entrar"
    assert "www-authenticate" not in r.headers  # sin la ventana del navegador
    r = cliente.get("/admin/entrar")
    assert r.status_code == 200 and 'type="password"' in r.text and r.headers["cache-control"] == "no-store"


def test_entrar_y_cerrar_sesion(Sesion):
    cliente = _cliente(Sesion)
    r = cliente.post("/admin/entrar", data={"usuario": "admin", "clave": "otra"})
    assert r.status_code == 401 and "Usuario o contraseña incorrectos" in r.text
    r = cliente.post("/admin/entrar", data={"usuario": "admin", "clave": CLAVE}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    galleta = r.headers["set-cookie"]
    assert "HttpOnly" in galleta and "SameSite=strict" in galleta and "Path=/admin" in galleta
    r = cliente.get("/admin")
    assert r.status_code == 200 and "Panel del piloto" in r.text and "Cerrar sesión" in r.text
    assert r.headers["cache-control"] == "no-store"
    assert cliente.get("/admin/entrar", follow_redirects=False).status_code == 303  # ya tiene sesión
    r = cliente.post("/admin/salir")
    assert "Cerraste la sesión" in r.text
    assert cliente.get("/admin", follow_redirects=False).status_code == 303


def test_cookie_secure_detras_de_https(Sesion):
    r = _cliente(Sesion).post("/admin/entrar", data={"usuario": "admin", "clave": CLAVE},
                              headers={"x-forwarded-proto": "https"}, follow_redirects=False)
    assert "Secure" in r.headers["set-cookie"]


def test_bloquea_tras_varios_intentos_fallidos(Sesion):
    cliente = _cliente(Sesion)
    for _ in range(INTENTOS_MAXIMOS):
        assert cliente.post("/admin/entrar", data={"usuario": "admin", "clave": "mala"}).status_code == 401
    r = cliente.post("/admin/entrar", data={"usuario": "admin", "clave": CLAVE})
    assert r.status_code == 429 and "Demasiados intentos" in r.text  # ni con la clave correcta


def test_bloqueo_y_sesion_vencen():
    intentos = Intentos()
    for _ in range(INTENTOS_MAXIMOS):
        intentos.fallo("1.2.3.4", ahora_ts=1000)
    assert intentos.bloqueada("1.2.3.4", ahora_ts=1001) and not intentos.bloqueada("5.6.7.8", ahora_ts=1001)
    assert not intentos.bloqueada("1.2.3.4", ahora_ts=1000 + BLOQUEO.total_seconds() + 1)
    token = token_sesion(CLAVE, ahora_ts=1000)
    assert sesion_valida(token, CLAVE, ahora_ts=1001)
    assert not sesion_valida(token, CLAVE, ahora_ts=1000 + DIAS_SESION * 86400 + 1)
    assert not sesion_valida(token, "otra-clave", ahora_ts=1001)  # cambiar ADMIN_CLAVE cierra las sesiones
    assert not sesion_valida(token.split(".")[0] + ".x", CLAVE, ahora_ts=1001)


def test_basic_sigue_sirviendo_para_scripts(Sesion):
    cliente = _cliente(Sesion)
    assert cliente.get("/admin", auth=("admin", CLAVE)).status_code == 200
    assert cliente.get("/admin", auth=("admin", "otra"), follow_redirects=False).status_code == 303


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
    assert "Aseo Sur" in r.text and "Día 0 de 7" in r.text
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


def test_activacion_uso_y_prueba_repetida(Sesion, cliente_mp):
    from datetime import date

    from licita.db import Calce, MensajeWhatsApp
    from licita.sync import sincronizar_licitaciones

    cliente = _cliente(Sesion)
    tokens = [_registrar(cliente)[1]]
    for i, rut in enumerate(("11.111.111-1", "22.222.222-2", "33.333.333-3")):
        r = cliente.post("/registro", data={**FORM, "nombre": f"Empresa {i}", "rut": rut, "whatsapp": f"+56 9 1111 222{i}"})
        tokens.append(r.text.split("https://calza.cl/cuenta/")[1].split('"')[0])
    with Sesion() as s:
        sincronizar_licitaciones(s, cliente_mp, date(2026, 10, 6))
        usa, solo_saluda, sin_tarjeta, repetida = (empresa_por_token(s, t) for t in tokens)
        sus = {e.id: s.query(Suscripcion).filter_by(empresa_id=e.id).one() for e in (usa, solo_saluda, sin_tarjeta, repetida)}
        for e in (usa, solo_saluda, repetida):
            s.add(MandatoPago(empresa_id=e.id, referencia=f"R{e.id}", plan="pro", periodicidad="mensual", monto=39990,
                              estado="cancelled" if e is repetida else "authorized"))
        sus[sin_tarjeta.id].estado = "pendiente"
        sus[repetida.id].estado, sus[repetida.id].prueba_repetida = "vencida", True
        sus[repetida.id].vigente_hasta = ahora() - timedelta(days=1)
        enviado = ahora() - timedelta(days=1)
        s.add_all([Calce(empresa_id=usa.id, licitacion_codigo="1234-56-LE26", puntaje=90, notificado_en=enviado),
                   Calce(empresa_id=solo_saluda.id, licitacion_codigo="1234-56-LE26", puntaje=90, notificado_en=enviado)])
        s.add_all([MensajeWhatsApp(empresa_id=usa.id, telefono=usa.whatsapp, direccion="entrante", tipo="texto", contenido=c)
                   for c in ("Hola", "1")])
        s.add(MensajeWhatsApp(empresa_id=solo_saluda.id, telefono=solo_saluda.whatsapp, direccion="entrante",
                              tipo="texto", contenido="Hola"))
        s.commit()
        ind, filas = datos_panel(s)
    assert (ind.con_tarjeta, ind.sin_tarjeta, ind.activacion) == (2, 1, 67)  # la repetida no cuenta
    assert (ind.usan, ind.reciben_resumen, ind.uso) == (1, 2, 50)
    assert ind.terminaron_prueba == 0  # la repetida nunca tuvo prueba
    [fila] = [f for f in filas if f.empresa.id == repetida.id]
    assert fila.motivos == [("Prueba repetida: su cuenta de Mercado Pago ya la usó", "info")]
    r = cliente.get("/admin", auth=("admin", CLAVE))
    assert "2 de 3 registraron la tarjeta" in r.text and "1 de 2 que reciben el resumen" in r.text


def test_invitar_arma_el_mensaje_con_un_ejemplo_real(Sesion):
    from licita.db import Licitacion, hora_chile

    cliente = _cliente(Sesion)
    cierre = hora_chile(ahora()).replace(tzinfo=None) + timedelta(days=5)
    with Sesion() as s:
        s.add_all([
            Licitacion(codigo="1-1-LE26", nombre="Adquisición de artículos de aseo para CESFAM", estado_codigo=5,
                       organismo="Municipalidad de Chillán", region="Región del Ñuble", monto_estimado=4_500_000,
                       fecha_cierre=cierre),
            Licitacion(codigo="2-2-LE26", nombre="Artículos de aseo para liceo", estado_codigo=5,
                       organismo="Slep Santiago", region="Región Metropolitana de Santiago", fecha_cierre=cierre),
            Licitacion(codigo="3-3-LE26", nombre="Notebooks", estado_codigo=5, region="Región del Ñuble", fecha_cierre=cierre),
        ])
        s.commit()
    assert cliente.get("/admin/invitar", follow_redirects=False).status_code == 303  # sin sesión
    cliente.post("/admin/entrar", data={"usuario": "admin", "clave": CLAVE})
    r = cliente.get("/admin/invitar", params={"nombre": "María", "empresa": "Aseo Sur", "vende": "artículos de aseo",
                                              "region": "nuble", "telefono": "+56 9 8929 9524", "remitente": "Matías"})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert "Hola María, soy Matías. Hoy vi una licitación abierta en Mercado Público en Región de Ñuble" in r.text
    assert "de Municipalidad de Chillán, por $4.500.000, cierra en 5 días" in r.text
    assert "Liceo" not in r.text and "Notebooks" not in r.text  # otra región y otro rubro
    assert 'href="https://wa.me/56989299524?text=Hola%20Mar' in r.text
    r = cliente.get("/admin/invitar", params={"vende": "artículos de aseo"})
    assert "Hoy vi 2 licitaciones abiertas en Mercado Público que calzan con lo que vende tu empresa" in r.text
    assert "wa.me" not in r.text  # sin teléfono, solo copiar
    r = cliente.get("/admin/invitar", params={"vende": "helicópteros"})
    assert "No encontré nada abierto" in r.text and "Hola [Nombre], soy [tu nombre].\n" in r.text
