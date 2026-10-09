import json
import re
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from licita.db import Licitacion, hora_chile
from licita.publico import (
    REGION_POR_SLUG, RUBRO_POR_SLUG, Cache, Ficha, buscar, cargar_abiertas, ejemplo_resumen, estadisticas, pagina,
    slug,
)
from licita.servidor import crear_app
from licita.sitio import avisar_indexnow, clave_indexnow
from licita.web import crear_router_web


def _cierre(ahora_cl, dias):
    if dias == 0:  # cierra hoy, más tarde
        fin_del_dia = ahora_cl.replace(hour=23, minute=59, second=59)
        return min(ahora_cl + timedelta(hours=2), fin_del_dia - timedelta(seconds=30))
    return ahora_cl + timedelta(days=dias)


def _lic(codigo, nombre, *, region="Región del Biobío", cierre_dias=5, estado=5, tipo="LE", monto=5_000_000, **kw):
    ahora_cl = hora_chile()
    return Licitacion(
        codigo=codigo, nombre=nombre, descripcion=kw.pop("descripcion", nombre), estado_codigo=estado, tipo=tipo,
        organismo=kw.pop("organismo", "Municipalidad de Concepción"), region=region, monto_estimado=monto,
        moneda="CLP", fecha_publicacion=kw.pop("publicada", ahora_cl - timedelta(hours=2)),
        fecha_cierre=_cierre(ahora_cl, cierre_dias), items=kw.pop("items", []), raw={}, **kw,
    )


@pytest.fixture
def sitio(Sesion):
    with Sesion() as s:
        s.add_all([
            _lic("1234-5-LE26", "Adquisición de artículos de aseo y bolsas de basura para CESFAM",
                 items=[{"producto": "Guantes de nitrilo", "cantidad": 200, "unidad": "caja", "categoria": "", "descripcion": ""}],
                 clasificacion={"resumen": "Compra de insumos de aseo para un centro de salud."}),
            _lic("2222-1-L126", "Compra de notebooks para liceo", region="Región Metropolitana de Santiago", cierre_dias=10),
            _lic("3333-2-LP26", "Servicio de vigilancia y guardias", region="Región de Valparaíso", cierre_dias=1),
            _lic("4444-COT26", "Bolsas de basura", tipo="COT", cierre_dias=0),
            _lic("5555-1-LE26", "Aseo de dependencias municipales", cierre_dias=-1),  # ya cerró
            _lic("6666-1-LE26", "Detergente industrial", estado=6),  # cerrada en Mercado Público
        ])
        s.commit()
    app = crear_app(Sesion, router_web=crear_router_web(Sesion, mp=None, ia=None, url_publica="https://calza.cl"))
    return TestClient(app)


def _jsonld(html):
    return [json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]


def test_slug_y_regiones():
    assert slug("O'Higgins") == "ohiggins"
    assert slug("Arica y Parinacota") == "arica-y-parinacota"
    assert REGION_POR_SLUG["biobio"].titulo == "Región del Biobío"
    assert REGION_POR_SLUG["ohiggins"].contiene(Ficha("x", region="Región del Libertador General Bernardo O´Higgins"))


def test_abiertas_estadisticas_y_rubros(Sesion, sitio):
    with Sesion() as s:
        fichas = cargar_abiertas(s)
    assert {f.codigo for f in fichas} == {"1234-5-LE26", "2222-1-L126", "3333-2-LP26", "4444-COT26"}
    e = estadisticas(fichas)
    assert (e.abiertas, e.compras_agiles, e.cierran_hoy) == (4, 1, 1)
    assert {f.codigo for f in fichas if RUBRO_POR_SLUG["aseo"].contiene(f)} == {"1234-5-LE26", "4444-COT26"}
    assert RUBRO_POR_SLUG["computacion"].contiene(next(f for f in fichas if f.codigo == "2222-1-L126"))
    assert RUBRO_POR_SLUG["salud"].contiene(fichas[[f.codigo for f in fichas].index("1234-5-LE26")])  # por los guantes


def test_buscar_exige_una_frase_completa_y_filtra_region(Sesion, sitio):
    with Sesion() as s:
        fichas = cargar_abiertas(s)
    resultado = buscar(fichas, "bolsas de basura, guantes")
    assert [f.codigo for f, _ in resultado][0] == "1234-5-LE26"
    assert {f.codigo for f, _ in resultado} == {"1234-5-LE26", "4444-COT26"}
    assert buscar(fichas, "bolsas de basura", REGION_POR_SLUG["metropolitana"]) == []
    assert buscar(fichas, "   ") == []
    assert buscar(fichas, "basura plástico") == []  # solo coincidencias parciales


def test_pagina_y_cache():
    assert pagina(list(range(65)), 3, 30) == ([60, 61, 62, 63, 64], 3, 3)
    assert pagina([], 9) == ([], 1, 1)
    reloj = [0.0]
    cache, llamadas = Cache(segundos=10, reloj=lambda: reloj[0]), []
    calcular = lambda: llamadas.append(1) or len(llamadas)
    assert cache.obtener("a", calcular) == 1 and cache.obtener("a", calcular) == 1
    reloj[0] = 11
    assert cache.obtener("a", calcular) == 2


def test_ejemplo_resumen_usa_rubros_distintos():
    ahora_cl = hora_chile()
    fichas = [
        Ficha("a", nombre="Artículos de aseo", monto_estimado=1e6, fecha_cierre=ahora_cl + timedelta(days=5), estado_codigo=5),
        Ficha("b", nombre="Más artículos de aseo", monto_estimado=1e6, fecha_cierre=ahora_cl + timedelta(days=5), estado_codigo=5),
        Ficha("c", nombre="Compra de notebooks", monto_estimado=1e6, fecha_cierre=ahora_cl + timedelta(days=5), estado_codigo=5),
        Ficha("d", nombre="Compra de muebles", monto_estimado=None, fecha_cierre=ahora_cl + timedelta(days=5), estado_codigo=5),
    ]
    fichas = [Ficha(**{**f.__dict__, "raices": __import__("licita.publico").publico.raices(f.nombre)}) for f in fichas]
    assert [f.codigo for f in ejemplo_resumen(fichas)] == ["a", "c"]


def test_inicio_dinamico_con_seo(sitio):
    r = sitio.get("/")
    assert r.status_code == 200
    html = r.text
    assert '<html lang="es-CL">' in html
    assert '<link rel="canonical" href="https://calza.cl/">' in html
    assert 'content="index, follow' in html
    assert 'property="og:image" content="https://calza.cl/og.png"' in html
    assert "Hoy hay 4 abiertas" in html  # la descripción usa los datos del día
    assert "Adquisición de artículos de aseo" in html  # recién publicadas
    assert 'href="/licitaciones/region/biobio"' in html and 'href="/licitaciones/rubro/aseo"' in html
    assert {"SoftwareApplication", "FAQPage"} <= {b.get("@type") for b in _jsonld(html)}
    app = next(b for b in _jsonld(html) if b.get("@type") == "SoftwareApplication")
    assert {"price": 19990, "priceCurrency": "CLP"}.items() <= app["offers"][0].items()
    assert r.headers["cache-control"] == "public, max-age=300"


def test_listados_por_region_y_rubro(sitio):
    r = sitio.get("/licitaciones")
    assert r.status_code == 200 and "4 licitaciones y Compras Ágiles" in r.text
    assert "Aseo de dependencias municipales" not in r.text  # cerrada
    r = sitio.get("/licitaciones/region/biobio")
    assert "Adquisición de artículos de aseo" in r.text and "notebooks" not in r.text
    assert '<link rel="canonical" href="https://calza.cl/licitaciones/region/biobio">' in r.text
    migas = next(b for b in _jsonld(r.text) if b.get("@type") == "BreadcrumbList")
    assert migas["itemListElement"][-1]["item"] == "https://calza.cl/licitaciones/region/biobio"
    r = sitio.get("/licitaciones/rubro/computacion")
    assert "Compra de notebooks para liceo" in r.text and "Adquisición de artículos" not in r.text
    assert sitio.get("/licitaciones/region/atlantida").status_code == 404
    assert sitio.get("/licitaciones/rubro/nada").status_code == 404
    pag = sitio.get("/licitaciones?pagina=99")  # se corrige a la última página
    assert '<link rel="canonical" href="https://calza.cl/licitaciones">' in pag.text


def test_ficha_abierta_cerrada_y_desconocida(sitio):
    r = sitio.get("/licitacion/1234-5-LE26")
    assert r.status_code == 200
    assert "Compra de insumos de aseo para un centro de salud." in r.text
    assert "Guantes de nitrilo" in r.text and "200 caja" in r.text
    assert "https://www.mercadopublico.cl/fichaLicitacion.html?idLicitacion=1234-5-LE26" in r.text
    assert "x-robots-tag" not in r.headers
    r = sitio.get("/licitacion/4444-COT26")
    assert "https://buscador.mercadopublico.cl/ficha?code=4444-COT26" in r.text and "Compra Ágil" in r.text
    r = sitio.get("/licitacion/5555-1-LE26")
    assert r.status_code == 200 and r.headers["x-robots-tag"] == "noindex" and 'content="noindex, follow"' in r.text
    assert "Cerrada" in r.text
    assert sitio.get("/licitacion/NO-EXISTE").status_code == 404


def test_buscar_no_se_indexa_y_escapa(sitio):
    r = sitio.get("/buscar", params={"q": "bolsas de basura", "region": "biobio"})
    assert r.status_code == 200 and r.headers["x-robots-tag"] == "noindex"
    assert "Adquisición de artículos de aseo" in r.text
    r = sitio.get("/buscar", params={"q": "<script>alert(1)</script>"})
    assert "<script>alert(1)</script>" not in r.text and "&lt;script&gt;" in r.text


def test_paginas_privadas_no_se_indexan(sitio):
    r = sitio.get("/cuenta/token-inexistente")
    assert 'content="noindex, nofollow"' in r.text


def test_robots_sitemap_llms_y_estaticos(sitio):
    robots = sitio.get("/robots.txt").text
    assert "Disallow: /cuenta/" in robots and "Sitemap: https://calza.cl/sitemap.xml" in robots
    sitemap = sitio.get("/sitemap.xml")
    assert sitemap.headers["content-type"].startswith("application/xml")
    assert "<loc>https://calza.cl/licitacion/1234-5-LE26</loc>" in sitemap.text
    assert "5555-1-LE26" not in sitemap.text and "6666-1-LE26" not in sitemap.text
    assert "<loc>https://calza.cl/licitaciones/rubro/aseo</loc>" in sitemap.text
    llms = sitio.get("/llms.txt")
    assert llms.headers["content-type"].startswith("text/markdown")
    assert "Pyme: $19.990 al mes" in llms.text and "&#39;" not in llms.text and "(https://calza.cl/licitaciones/region/ohiggins)" in llms.text
    for ruta, tipo in (("/favicon.svg", "image/svg+xml"), ("/og.png", "image/png"), ("/favicon.ico", "image/x-icon"),
                       ("/apple-touch-icon.png", "image/png")):
        r = sitio.get(ruta)
        assert r.status_code == 200 and r.headers["content-type"].startswith(tipo), ruta
    clave = clave_indexnow("https://calza.cl")
    assert sitio.get(f"/{clave}.txt").text == clave


def test_avisar_indexnow():
    enviados = []

    def responder(request):
        enviados.append(json.loads(request.content))
        return httpx.Response(202)

    http = httpx.Client(transport=httpx.MockTransport(responder))
    assert avisar_indexnow("https://calza.cl", ["https://calza.cl/licitacion/1"], http=http) == 1
    assert enviados[0]["host"] == "calza.cl" and enviados[0]["keyLocation"].endswith(".txt")
    assert avisar_indexnow("https://calza.cl", [], http=http) == 0
