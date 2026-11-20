from datetime import datetime, timedelta

from licita.db import AvisoRadar, Calce, Empresa, Licitacion, Precio
from licita.historico import origen_compra
from licita.radar import ejecutar_radar, previsiones, texto_radar

UTC = datetime(2026, 11, 18, 12, 0)
GUANTES = "46181504"


def test_origen_de_cada_compra():
    assert origen_compra({"procedencia": "Proveniente de licitación pública", "codigo_licitacion": "2581-31-LR23"}) == ("licitacion", "2581-31-LR23")
    assert origen_compra({"procedencia": "Convenio Marco", "convenio_marco": "2239-8-LR22", "codigo_licitacion": "NA"}) == ("convenio_marco", "")
    assert origen_compra({"es_compra_agil": "Si", "codigo_licitacion": "1234-5-COT26"}) == ("compra_agil", "1234-5-COT26")
    assert origen_compra({"procedencia": "Trato Directo", "es_trato_directo": "Si", "codigo_licitacion": "NA"}) == ("trato_directo", "")
    assert origen_compra({"producto": "x"}) == ("", "")  # archivos antiguos sin esas columnas


def _base(s, plan="pro"):
    e = Empresa(nombre="Aseo Sur", descripcion="", whatsapp="56989299524", plan=plan, regiones=["Ñuble"])
    lic = Licitacion(codigo="1-1-LE26", nombre="Guantes", estado_codigo=8, organismo="Otro", fecha_cierre=UTC - timedelta(days=30),
                     items=[{"codigo_producto": GUANTES, "producto": "Guantes de nitrilo"}], raw={})
    s.add_all([e, lic])
    s.flush()
    s.add(Calce(empresa_id=e.id, licitacion_codigo=lic.codigo, puntaje=90, razon="", creado_en=UTC - timedelta(days=20)))
    return e


def _oc(s, ref, licitacion, fecha, *, origen="licitacion", organismo="HOSPITAL DE SAN CARLOS", region="Región del Ñuble"):
    s.add(Precio(fuente="orden_de_compra", referencia=ref, posicion=0, codigo_producto=GUANTES, producto="Guantes",
                 precio_unitario=32_000, cantidad=100, organismo=organismo, region=region, fecha=fecha,
                 origen=origen, codigo_licitacion=licitacion))


def test_preve_la_proxima_licitacion_anual(Sesion):
    with Sesion() as s:
        e = _base(s)
        _oc(s, "OC-1", "100-1-LE24", datetime(2024, 12, 10))
        _oc(s, "OC-2", "100-9-LE25", datetime(2025, 12, 5))
        _oc(s, "OC-3", "100-9-LE25", datetime(2026, 3, 5))  # otra orden del mismo contrato: no es otra licitación
        _oc(s, "OC-4", "", datetime(2025, 6, 1), origen="convenio_marco")  # Convenio Marco: no cuenta
        _oc(s, "OC-5", "200-1-LE24", datetime(2024, 12, 1), region="Región de Arica y Parinacota", organismo="HOSPITAL ARICA")
        _oc(s, "OC-6", "200-2-LE25", datetime(2025, 12, 1), region="Región de Arica y Parinacota", organismo="HOSPITAL ARICA")
        s.commit()
        [p] = previsiones(s, e, UTC)  # Arica queda fuera: no es su región
        assert p.proxima.isoformat() == "2026-11-30" and p.cuando == "a fines de noviembre"
        assert [c for _, c in p.licitaciones] == ["100-1-LE24", "100-9-LE25"] and p.monto == 6_400_000  # suma las órdenes del contrato
        texto = texto_radar([p])
        assert "*Hospital de San Carlos* · guantes de nitrilo" in texto
        assert "Licitó en dic 2024 y dic 2025 (la última sumó $6.400.000). Probablemente vuelva a fines de noviembre." in texto
        assert "idLicitacion=100-9-LE25" in texto


def test_sin_ritmo_o_ya_abierta_no_preve(Sesion):
    with Sesion() as s:
        e = _base(s)
        _oc(s, "OC-1", "100-9-LE25", datetime(2025, 12, 5))  # una sola licitación
        s.commit()
        assert previsiones(s, e, UTC) == []
        _oc(s, "OC-0", "100-1-LE24", datetime(2024, 12, 10))
        s.add(Licitacion(codigo="100-3-LE26", nombre="Guantes 2027", estado_codigo=5, organismo="Hospital de San Carlos",
                         fecha_cierre=datetime(2026, 12, 20), items=[{"codigo_producto": GUANTES}], raw={}))
        s.commit()
        assert previsiones(s, e, UTC) == []  # ya está publicada: le llega en el resumen


class WAFalso:
    def __init__(self):
        self.plantillas = []

    def enviar_plantilla(self, telefono, nombre, *, idioma="es", parametros=None, payloads_botones=None):
        self.plantillas.append((nombre, parametros, payloads_botones))
        return "wamid.p"


def test_tarea_semanal_avisa_una_vez_y_solo_pro(Sesion):
    with Sesion() as s:
        e = _base(s)
        _oc(s, "OC-1", "100-1-LE24", datetime(2024, 12, 10))
        _oc(s, "OC-2", "100-9-LE25", datetime(2025, 12, 5))
        s.commit()
        wa = WAFalso()
        r = ejecutar_radar(s, wa=wa, momento=UTC)
        assert (r.empresas, r.previsiones) == (1, 1)
        [(nombre, parametros, botones)] = wa.plantillas
        assert nombre == "se_viene_recompras" and botones == ["SE_VIENE"]
        assert parametros == ["Aseo Sur", "1 licitación", "Hospital de San Carlos, guantes de nitrilo, a fines de noviembre"]
        assert ejecutar_radar(s, wa=wa, momento=UTC + timedelta(days=7)).previsiones == 0  # no se repite
        assert s.query(AvisoRadar).count() == 1
        e.plan = "pyme"
        s.query(AvisoRadar).delete()
        s.commit()
        assert ejecutar_radar(s, wa=wa, momento=UTC).previsiones == 0


def test_comando_se_viene(Sesion):
    from licita.conversacion import responder

    with Sesion() as s:
        e = _base(s, plan="pyme")
        s.commit()
        assert "es parte del plan Pro" in responder(s, e, "texto", "se viene", momento=UTC)[0]
        e.plan = "pro"
        assert "Por ahora no veo licitaciones" in responder(s, e, "boton", "SE_VIENE", momento=UTC)[0]


def test_calendario_del_organismo(Sesion):
    from licita.radar import calendario_organismo

    with Sesion() as s:
        _oc(s, "OC-1", "100-1-LE24", datetime(2024, 12, 10))
        _oc(s, "OC-2", "100-9-LE25", datetime(2025, 12, 5))
        _oc(s, "OC-3", "", datetime(2025, 3, 1), origen="convenio_marco")
        s.add(Precio(fuente="orden_de_compra", referencia="OC-9", posicion=0, codigo_producto="44121700",
                     producto="Lápices pasta azul marca X caja de 50", precio_unitario=100, cantidad=1,
                     organismo="HOSPITAL DE SAN CARLOS", fecha=datetime(2023, 6, 1), origen="licitacion",
                     codigo_licitacion="300-1-LE23"))
        s.add(Precio(fuente="orden_de_compra", referencia="OC-10", posicion=0, codigo_producto="44121700",
                     producto="Lápices pasta", precio_unitario=100, cantidad=1, organismo="HOSPITAL DE SAN CARLOS",
                     fecha=datetime(2025, 1, 10), origen="licitacion", codigo_licitacion="300-2-LE25"))
        s.commit()
        [r] = calendario_organismo(s, "HOSPITAL DE SAN CARLOS", UTC)  # lápices: ritmo roto (19 meses)
        assert (r.producto, r.licito_en, r.cuando) == ("Guantes", "dic 2024 y dic 2025", "noviembre de 2026")
        assert calendario_organismo(s, "Otro organismo", UTC) == []


def test_pagina_del_organismo_muestra_el_calendario(Sesion):
    from fastapi.testclient import TestClient

    from licita.servidor import crear_app
    from licita.web import crear_router_web

    with Sesion() as s:
        s.add(Licitacion(codigo="100-5-LE26", nombre="Guantes de látex", estado_codigo=6, organismo="HOSPITAL DE SAN CARLOS",
                         fecha_publicacion=datetime.now() - timedelta(days=20), fecha_cierre=datetime.now() - timedelta(days=5),
                         items=[], raw={}, descripcion="", tipo="", region="", comuna="", moneda="CLP"))
        hace = datetime.now() - timedelta(days=300)  # relativo a hoy: el test no vence con el tiempo
        _oc(s, "OC-1", "100-1-LE24", hace - timedelta(days=365))
        _oc(s, "OC-2", "100-9-LE25", hace)
        s.commit()
    cliente = TestClient(crear_app(Sesion, router_web=crear_router_web(Sesion, mp=None, ia=None, url_publica="https://calza.cl")))
    r = cliente.get("/licitaciones/organismo/hospital-de-san-carlos")
    assert r.status_code == 200
    assert "¿Qué suele licitar y cuándo?" in r.text and "Guantes" in r.text
    assert "¿Cuándo licita Hospital de San Carlos?" in r.text and "de que el Hospital de San Carlos vuelva a licitar" in r.text
