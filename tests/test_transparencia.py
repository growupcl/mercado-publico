import re
from datetime import datetime, timedelta

from licita.db import Licitacion, Precio
from licita.semaforo import senales
from licita.transparencia import comparar_precio, indicadores_organismo, pago_caro

UTC = datetime(2026, 11, 21, 12, 0)
GUANTES = "46181504"
ORG = "I MUNICIPALIDAD DE LAS TORRES"


def _precio(s, ref, precio, *, organismo="OTRO ORGANISMO", unidad="Caja", origen="licitacion", proveedor="Prov SpA",
            cantidad=10, codigo=GUANTES):
    s.add(Precio(fuente="orden_de_compra", referencia=ref, posicion=0, codigo_producto=codigo, producto="Guantes de nitrilo",
                 unidad=unidad, precio_unitario=precio, cantidad=cantidad, proveedor_nombre=proveedor, organismo=organismo,
                 fecha=datetime(2026, 5, 1), origen=origen, codigo_licitacion=ref if origen == "licitacion" else ""))


def _mercado(s, n=12, precio=5000):
    for i in range(n):
        _precio(s, f"M-{i}", precio + i * 50, organismo=f"HOSPITAL {i}")


def test_compara_en_la_misma_unidad_y_sin_atipicos(Sesion):
    with Sesion() as s:
        _mercado(s)
        _precio(s, "M-x", 900_000, organismo="HOSPITAL X")  # atípico: no mueve la mediana
        _precio(s, "M-u", 50, organismo="HOSPITAL U", unidad="Unidad")  # otra unidad: no cuenta
        _precio(s, "O-1", 14_000, organismo=ORG)
        _precio(s, "O-2", 15_000, organismo=ORG)
        s.commit()
        c = comparar_precio(s, "I Municipalidad de Las Torres", GUANTES, momento=UTC)
    assert c.unidad == "caja" and c.mediana_organismo == 14_500 and 5_200 < c.mediana_estado < 5_400
    assert c.compras_organismo == 2 and c.compras_estado == 12 and c.diferencia.startswith("+17")


def test_sin_referencia_suficiente_no_compara(Sesion):
    with Sesion() as s:
        _mercado(s, n=5)
        _precio(s, "O-1", 14_000, organismo=ORG)
        _precio(s, "O-2", 15_000, organismo=ORG)
        s.commit()
        assert comparar_precio(s, ORG, GUANTES, momento=UTC) is None


def test_semaforo_avisa_la_oportunidad_si_paga_mucho_mas(Sesion):
    with Sesion() as s:
        _mercado(s)
        _precio(s, "O-1", 14_000, organismo=ORG)
        _precio(s, "O-2", 15_000, organismo=ORG)
        lic = Licitacion(codigo="9-9-LE26", nombre="Guantes", descripcion="", tipo="LE", estado_codigo=5, organismo=ORG,
                         region="", comuna="", moneda="CLP", fecha_cierre=datetime(2026, 12, 15),
                         items=[{"codigo_producto": GUANTES, "producto": "Guantes"}], raw={})
        s.commit()
        assert pago_caro(s, lic, UTC).veces > 2.5
        textos = [x.texto for x in senales(s, lic, con_precios=True, momento=UTC)]
        assert any(t.startswith("Este organismo ha pagado 2,") and "Si ofertas a precio de mercado" in t for t in textos)
        assert not any("ha pagado" in x.texto for x in senales(s, lic, con_precios=False, momento=UTC))


def test_indicadores_del_organismo_y_su_pagina(Sesion):
    from fastapi.testclient import TestClient

    from licita.servidor import crear_app
    from licita.web import crear_router_web

    with Sesion() as s:
        _mercado(s)
        _precio(s, "O-1", 14_000, organismo=ORG, proveedor="Aseo Total SpA", cantidad=100)
        _precio(s, "O-2", 15_000, organismo=ORG, proveedor="Aseo Total SpA", cantidad=100)
        _precio(s, "O-3", 1_000, organismo=ORG, origen="trato_directo", proveedor="Otra SpA", cantidad=100, codigo="44121700")
        for i, n in enumerate([1, 1, 3, 1]):
            s.add(Licitacion(codigo=f"L-{i}", nombre="x", estado_codigo=8, organismo=ORG, items=[],
                             raw={"Adjudicacion": {"NumeroOferentes": n}}))
        s.add(Licitacion(codigo="L-pub", nombre="Guantes nuevos", estado_codigo=6, organismo=ORG, items=[], raw={},
                         descripcion="", tipo="", region="", comuna="", moneda="CLP",
                         fecha_publicacion=datetime.now() - timedelta(days=10), fecha_cierre=datetime.now() - timedelta(days=2)))
        s.commit()
        ind = indicadores_organismo(s, ORG, UTC)
    assert ind.como_compra == [("Licitación", 97), ("Trato directo", 3)]
    assert ind.proveedor_principal == ("Aseo Total SpA", 97) and ind.un_oferente == (3, 4)
    assert [p.codigo_producto for p in ind.precios] == [GUANTES]

    cliente = TestClient(crear_app(Sesion, router_web=crear_router_web(Sesion, mp=None, ia=None, url_publica="https://calza.cl")))
    r = cliente.get("/licitaciones/organismo/i-municipalidad-de-las-torres")
    assert r.status_code == 200
    assert "¿Cómo compra la I Municipalidad de las Torres?" in r.text
    assert "3 de sus últimas 4</strong> licitaciones adjudicadas tuvieron un solo oferente" in r.text
    assert "Lo que paga frente al resto del Estado" in r.text and "+1" in r.text
    assert "úsalo como referencia, no como conclusión" in r.text


def _mercado_amplio(s, n=25):
    from datetime import datetime as dt
    for i in range(n):
        s.add(Precio(fuente="orden_de_compra", referencia=f"G-{i}", posicion=0, codigo_producto=GUANTES,
                     producto="Guantes de nitrilo" if i else "Guantes de nitrilo talla M caja 100 unidades",
                     unidad="Caja", precio_unitario=5000 + i * 40, cantidad=10, proveedor_nombre=f"Prov {i % 3}",
                     organismo=f"HOSPITAL {i % 7}", region="Región del Ñuble" if i % 2 else "Región de Valparaíso",
                     fecha=dt.now() - timedelta(days=30 + i * 10), origen="licitacion", codigo_licitacion=f"G-{i}"))


def test_guia_de_precios(Sesion):
    from licita.transparencia import guia_producto, productos_con_guia

    with Sesion() as s:
        _mercado_amplio(s)
        _precio(s, "pocas", 100, codigo="99999999")  # pocas compras: sin guía
        s.commit()
        [p] = productos_con_guia(s)
        g = guia_producto(s, GUANTES)
    assert p.codigo == GUANTES and p.slug.startswith(f"{GUANTES}-guantes-de-nitrilo")
    assert g.nombre == "Guantes de nitrilo" and g.unidad == "caja" and g.compras == 25 and g.organismos == 7
    assert 5_400 < g.mediana < 5_600 and g.p25 < g.mediana < g.p75
    assert {r for r, _, _ in g.por_region} == {"Región del Ñuble", "Región de Valparaíso"}
    assert g.proveedores[0][1] >= 8


def test_paginas_de_la_guia(Sesion):
    from fastapi.testclient import TestClient

    from licita.servidor import crear_app
    from licita.web import crear_router_web

    with Sesion() as s:
        _mercado_amplio(s)
        s.commit()
    cliente = TestClient(crear_app(Sesion, router_web=crear_router_web(Sesion, mp=None, ia=None, url_publica="https://calza.cl")))
    r = cliente.get("/precios")
    assert r.status_code == 200 and "¿Cuánto paga el Estado? Guía de precios" in r.text
    [enlace] = re.findall(r'href="(/precios/46181504-[^"]+)"', r.text)
    r = cliente.get(enlace)
    assert r.status_code == 200
    assert "¿Cuánto paga el Estado por guantes de nitrilo?" in r.text and "Precio por región" in r.text
    assert "¿Cuánto cuesta guantes de nitrilo en Mercado Público?" in r.text and '"FAQPage"' in r.text
    r = cliente.get("/precios/46181504-otro-nombre", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == enlace
    assert cliente.get("/precios/12345678-nada").status_code == 404
    sitemap = cliente.get("/sitemap.xml").text
    assert "https://calza.cl/precios<" in sitemap and f"https://calza.cl{enlace}<" in sitemap
    assert 'href="/precios"' in cliente.get("/").text  # enlazada desde el pie de página


def test_no_compara_servicios_ni_diferencias_imposibles(Sesion):
    """Casos reales: FONASA contrata diálisis por contrato anual y el resto por sesión; CENABAST compra un remedio de
    $25 millones con el mismo código que unas cápsulas de $1.000. Ninguno es un sobreprecio."""
    nefrologia = "85121608"
    with Sesion() as s:
        _mercado(s)
        for i in range(12):
            _precio(s, f"N-{i}", 370_000 + i * 1000, organismo=f"HOSPITAL {i}", codigo=nefrologia)
        _precio(s, "F-1", 2_568_384_000, organismo=ORG, codigo=nefrologia)
        _precio(s, "F-2", 2_600_000_000, organismo=ORG, codigo=nefrologia)
        _precio(s, "C-1", 24_900_000, organismo=ORG)
        _precio(s, "C-2", 23_896_000, organismo=ORG)
        s.commit()
        assert comparar_precio(s, ORG, nefrologia, momento=UTC) is None  # servicio: no se compara
        assert comparar_precio(s, ORG, GUANTES, momento=UTC) is None  # más de 10 veces: no es el mismo producto
        assert indicadores_organismo(s, ORG, momento=UTC).precios == []


def test_no_compara_contratos_completos_ni_unidades_de_servicio(Sesion):
    """Casos reales: la PDI registró un lote de computadores de $211 millones como "1 unidad"; una municipalidad
    arrienda software por mes. No son precios por unidad."""
    computadores, software = "43211507", "43231500"
    with Sesion() as s:
        for i in range(12):
            _precio(s, f"PC-{i}", 550_000 + i * 1000, organismo=f"HOSPITAL {i}", codigo=computadores, unidad="Unidad")
            _precio(s, f"SW-{i}", 680_000 + i * 1000, organismo=f"HOSPITAL {i}", codigo=software, unidad="Mes")
        _precio(s, "PDI-1", 211_276_124, organismo=ORG, codigo=computadores, unidad="Unidad", cantidad=1)
        _precio(s, "PDI-2", 33_391_505, organismo=ORG, codigo=computadores, unidad="Unidad", cantidad=1)
        _precio(s, "M-1", 3_459_387, organismo=ORG, codigo=software, unidad="Mes")
        _precio(s, "M-2", 3_500_000, organismo=ORG, codigo=software, unidad="Mes")
        s.commit()
        assert comparar_precio(s, ORG, computadores, momento=UTC) is None
        assert comparar_precio(s, ORG, software, momento=UTC) is None
        # Si además compró computadores por unidad, esos sí se comparan.
        _precio(s, "PDI-3", 1_200_000, organismo=ORG, codigo=computadores, unidad="Unidad", cantidad=20)
        _precio(s, "PDI-4", 1_300_000, organismo=ORG, codigo=computadores, unidad="Unidad", cantidad=10)
        s.commit()
        c = comparar_precio(s, ORG, computadores, momento=UTC)
        assert c.compras_organismo == 2 and c.mediana_organismo == 1_250_000
