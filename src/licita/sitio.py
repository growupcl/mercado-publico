"""Páginas públicas del sitio pensadas para buscadores e IA.

- /licitaciones, /licitaciones/region/{region}, /licitaciones/rubro/{rubro}: licitaciones abiertas, paginadas.
- /licitacion/{codigo}: ficha de una licitación o Compra Ágil, con enlace a la ficha oficial de Mercado Público.
- /buscar: demo sin registro (no se indexa).
- /robots.txt, /sitemap.xml, /llms.txt, /favicon.svg, /og.png y la clave de IndexNow.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import sessionmaker

from .db import ahora
from .planes import DIAS_PRUEBA, PLANES, PRECIO_FUNDADOR_PRO, formato_pesos, monto
from .publico import (
    POR_PAGINA, REGION_POR_SLUG, REGIONES_PUBLICAS, RUBRO_POR_SLUG, RUBROS, Cache, Ficha, buscar, cargar_abiertas,
    cargar_ficha, ejemplo_resumen, estadisticas, pagina,
)

log = logging.getLogger(__name__)
ESTATICOS = Path(__file__).parent / "estaticos"
CACHE_PUBLICO = {"Cache-Control": "public, max-age=300"}
CACHE_ESTATICO = {"Cache-Control": "public, max-age=604800"}
NO_INDEXAR = {"X-Robots-Tag": "noindex"}
MAX_URLS_INDEXNOW = 10_000


def clave_indexnow(url_publica: str) -> str:
    """Clave pública de IndexNow (Bing, Yandex, Seznam, Naver...): fija por dominio, se publica en /{clave}.txt."""
    return hashlib.sha256(f"calza-indexnow:{url_publica}".encode()).hexdigest()[:32]


def avisar_indexnow(url_publica: str, urls: list[str], *, http: httpx.Client | None = None) -> int:
    """Avisa a los buscadores compatibles con IndexNow que estas URL son nuevas o cambiaron."""
    urls = urls[:MAX_URLS_INDEXNOW]
    if not urls:
        return 0
    clave = clave_indexnow(url_publica)
    cuerpo = {
        "host": url_publica.split("://", 1)[-1].split("/", 1)[0], "key": clave,
        "keyLocation": f"{url_publica}/{clave}.txt", "urlList": urls,
    }
    cliente = http or httpx.Client(timeout=30)
    try:
        r = cliente.post("https://api.indexnow.org/indexnow", json=cuerpo)
    finally:
        if http is None:
            cliente.close()
    if r.status_code not in (200, 202):
        raise RuntimeError(f"IndexNow respondió {r.status_code}: {r.text[:200]}")
    return len(urls)


def faq() -> list[tuple[str, str]]:
    pyme, pro = PLANES["pyme"], PLANES["pro"]
    return [
        ("¿Qué es Calza?",
         "Calza es un copiloto para pymes chilenas que le venden al Estado. Revisa Mercado Público varias veces al día, "
         "te avisa por WhatsApp las licitaciones y Compras Ágiles que calzan con lo que vendes y te explica las bases "
         "en palabras simples."),
        ("¿De dónde salen los datos?",
         "De la API oficial de Mercado Público (ChileCompra), que es pública. Calza es un servicio independiente, no "
         "afiliado a ChileCompra; cada licitación enlaza a su ficha oficial, donde se oferta."),
        ("¿Cuánto cuesta?",
         f"El plan Pyme cuesta {formato_pesos(pyme.mensual)} al mes y el Pro {formato_pesos(pro.mensual)} "
         f"(los primeros clientes Pro pagan {formato_pesos(PRECIO_FUNDADOR_PRO)} de por vida). Pagando un año se "
         f"ahorra un 10%. Precios con IVA. Los primeros {DIAS_PRUEBA} días son gratis y sin tarjeta, con todo lo del plan Pro."),
        ("¿Cómo sabe Calza qué licitaciones me sirven?",
         "Describes en tus palabras lo que vendes, tus regiones y el monto que te interesa. Calza compara cada licitación "
         "nueva con tu perfil y le asigna un porcentaje de calce; solo te llegan las que superan el umbral."),
        ("¿Qué es una Compra Ágil?",
         "Es la modalidad de Mercado Público para compras de menor monto: el organismo pide cotizaciones con plazos muy "
         "cortos, a veces de uno o dos días. En el plan Pro, Calza te avisa apenas se publica una que te calza."),
        ("¿Cómo funciona el análisis de bases?",
         "Envías el PDF o Word de las bases por WhatsApp y recibes un resumen con requisitos, garantías, documentos, "
         "plazos y criterios de evaluación. Es una ayuda para leer más rápido: lo que vale son siempre las bases oficiales."),
        ("¿Necesito estar inscrito en Mercado Público?",
         "Para ofertar necesitas una cuenta de proveedor en mercadopublico.cl. Para firmar contratos con el Estado "
         "normalmente se exige además estar inscrito y hábil en el Registro de Proveedores. Calza te avisa y te ayuda "
         "a preparar la oferta; la postulación la haces tú en Mercado Público."),
        ("¿Puedo cancelar cuando quiera?",
         "Sí. No hay permanencia mínima: desactivas la renovación desde tu cuenta y sigues con el servicio hasta el fin "
         "del período pagado."),
    ]


def ofertas_ld() -> list[dict]:
    return [
        {"@type": "Offer", "name": f"Plan {p.nombre} {periodo}", "price": valor, "priceCurrency": "CLP",
         "category": "subscription"}
        for p in PLANES.values()
        for periodo, valor in (("mensual", p.mensual), ("anual", monto(p.codigo, "anual")))
    ]


def crear_router_publico(
    Sesion: sessionmaker, *, plantillas: Jinja2Templates, url_publica: str, fundador_disponible,
    cache: Cache | None = None,
) -> APIRouter:
    router = APIRouter()
    cache = cache or Cache(segundos=300)
    indexnow = clave_indexnow(url_publica)
    plantillas.env.filters["miles"] = lambda n: f"{n:,}".replace(",", ".")
    plantillas.env.globals["item_list_ld"] = lambda fichas, desde=0: [
        {"@type": "ListItem", "position": desde + i, "url": f"{url_publica}/licitacion/{f.codigo}", "name": f.nombre}
        for i, f in enumerate(fichas, 1)
    ]
    plantillas.env.globals["migas_ld"] = lambda items: {
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i, "name": nombre, "item": url_publica + url}
            for i, (nombre, url) in enumerate([("Inicio", "/"), *items], 1)
        ],
    }

    def abiertas() -> list[Ficha]:
        def calcular():
            with Sesion() as s:
                return cargar_abiertas(s)
        return cache.obtener("abiertas", calcular)

    def fundador() -> bool:
        def calcular():
            with Sesion() as s:
                return fundador_disponible(s)
        return cache.obtener("fundador", calcular)

    def html(request: Request, plantilla: str, contexto: dict, *, estado: int = 200, encabezados: dict | None = None):
        return plantillas.TemplateResponse(request, plantilla, contexto, status_code=estado,
                                           headers=encabezados if encabezados is not None else CACHE_PUBLICO)

    @router.get("/", response_class=HTMLResponse)
    def inicio(request: Request):
        fichas = abiertas()
        return html(request, "inicio.html", {
            "planes": list(PLANES.values()), "anual": {c: monto(c, "anual") for c in PLANES},
            "fundador": fundador(), "precio_fundador": PRECIO_FUNDADOR_PRO, "dias_prueba": DIAS_PRUEBA,
            "stats": estadisticas(fichas), "ejemplos": cache.obtener("ejemplos", lambda: ejemplo_resumen(fichas)),
            "ultimas": [f for f in fichas if not f.es_compra_agil][:6],
            "regiones": REGIONES_PUBLICAS, "rubros": RUBROS, "faq": faq(),
            "faq_ld": [{"@type": "Question", "name": p, "acceptedAnswer": {"@type": "Answer", "text": r}} for p, r in faq()],
            "ofertas_ld": ofertas_ld(),
        })

    def listado(request: Request, fichas: list[Ficha], numero: int, *, titulo: str, h1: str, intro: str,
                ruta: str, migas: list[tuple[str, str]], region=None, rubro=None):
        items, numero, total = pagina(fichas, numero)
        canonical = url_publica + ruta + (f"?pagina={numero}" if numero > 1 else "")
        return html(request, "licitaciones.html", {
            "titulo": titulo + (f" (página {numero})" if numero > 1 else ""), "h1": h1, "intro": intro,
            "fichas": items, "total_fichas": len(fichas), "numero": numero, "total_paginas": total, "ruta": ruta,
            "canonical": canonical, "migas": migas, "regiones": REGIONES_PUBLICAS, "rubros": RUBROS,
            "region_actual": region, "rubro_actual": rubro, "stats": estadisticas(fichas), "por_pagina": POR_PAGINA,
            "dias_prueba": DIAS_PRUEBA,
        })

    @router.get("/licitaciones", response_class=HTMLResponse)
    def licitaciones(request: Request, pagina: int = 1):
        fichas = abiertas()
        return listado(
            request, fichas, pagina, titulo="Licitaciones abiertas de Mercado Público hoy",
            h1="Licitaciones abiertas en Mercado Público",
            intro=(f"{len(fichas)} licitaciones y Compras Ágiles del Estado de Chile abiertas para ofertar, "
                   "actualizadas varias veces al día. Filtra por región o rubro, o deja que Calza te avise "
                   "por WhatsApp solo las que calzan con tu negocio."),
            ruta="/licitaciones", migas=[("Licitaciones", "/licitaciones")],
        )

    @router.get("/licitaciones/region/{region_slug}", response_class=HTMLResponse)
    def por_region(request: Request, region_slug: str, pagina: int = 1):
        region = REGION_POR_SLUG.get(region_slug)
        if region is None:
            raise HTTPException(status_code=404)
        fichas = [f for f in abiertas() if region.contiene(f)]
        return listado(
            request, fichas, pagina, titulo=f"Licitaciones abiertas en la {region.titulo}",
            h1=f"Licitaciones abiertas en la {region.titulo}",
            intro=(f"{len(fichas)} licitaciones y Compras Ágiles de organismos públicos de la {region.titulo} "
                   "abiertas hoy en Mercado Público: municipios, servicios de salud, colegios y más."),
            ruta=f"/licitaciones/region/{region.slug}",
            migas=[("Licitaciones", "/licitaciones"), (region.titulo, f"/licitaciones/region/{region.slug}")],
            region=region,
        )

    @router.get("/licitaciones/rubro/{rubro_slug}", response_class=HTMLResponse)
    def por_rubro(request: Request, rubro_slug: str, pagina: int = 1):
        rubro = RUBRO_POR_SLUG.get(rubro_slug)
        if rubro is None:
            raise HTTPException(status_code=404)
        fichas = [f for f in abiertas() if rubro.contiene(f)]
        nombre = rubro.nombre.lower()
        return listado(
            request, fichas, pagina, titulo=f"Licitaciones de {nombre} abiertas en Mercado Público",
            h1=f"Licitaciones de {nombre}",
            intro=(f"{len(fichas)} licitaciones y Compras Ágiles abiertas hoy en Mercado Público relacionadas con "
                   f"{nombre} ({', '.join(rubro.frases[:4])} y más), en todo Chile."),
            ruta=f"/licitaciones/rubro/{rubro.slug}",
            migas=[("Licitaciones", "/licitaciones"), (rubro.nombre, f"/licitaciones/rubro/{rubro.slug}")],
            rubro=rubro,
        )

    @router.get("/licitacion/{codigo}", response_class=HTMLResponse)
    def ficha(request: Request, codigo: str):
        if len(codigo) > 40:
            raise HTTPException(status_code=404)
        fichas = abiertas()
        f = cache.obtener("por_codigo", lambda: {x.codigo: x for x in fichas}).get(codigo)
        if f is None:  # cerrada o desconocida: no se guarda en el cache (los códigos los elige quien visita)
            with Sesion() as s:
                f = cargar_ficha(s, codigo)
        if f is None:
            raise HTTPException(status_code=404)
        abierta = f.abierta()
        rubro = next((r for r in RUBROS if r.contiene(f)), None)
        region = next((r for r in REGIONES_PUBLICAS if r.contiene(f)), None)
        similares = [
            x for x in fichas
            if x.codigo != f.codigo and ((rubro and rubro.contiene(x)) or (not rubro and region and region.contiene(x)))
        ]
        if region and rubro:  # primero las de la misma región
            similares.sort(key=lambda x: not region.contiene(x))
        migas = [("Licitaciones", "/licitaciones")]
        if region:
            migas.append((region.titulo, f"/licitaciones/region/{region.slug}"))
        migas.append((f.codigo, f"/licitacion/{f.codigo}"))
        return html(request, "licitacion.html", {
            "f": f, "abierta": abierta, "dias": f.dias_para_cierre(), "rubro": rubro, "region": region,
            "similares": similares[:6], "migas": migas, "canonical": f"{url_publica}/licitacion/{f.codigo}",
            "dias_prueba": DIAS_PRUEBA,
        }, encabezados=CACHE_PUBLICO if abierta else {**CACHE_PUBLICO, **NO_INDEXAR})

    @router.get("/buscar", response_class=HTMLResponse)
    def buscar_demo(request: Request, q: str = "", region: str = ""):
        q = q.strip()[:300]
        reg = REGION_POR_SLUG.get(region)
        fichas = abiertas()
        resultados = buscar(fichas, q, reg, limite=20) if q else []
        return html(request, "buscar.html", {
            "q": q, "region_actual": reg, "regiones": REGIONES_PUBLICAS, "resultados": resultados,
            "total_abiertas": len(fichas), "dias_prueba": DIAS_PRUEBA,
        }, encabezados={**NO_INDEXAR, "Cache-Control": "private, max-age=60"})

    @router.get("/robots.txt", response_class=PlainTextResponse)
    def robots():
        return PlainTextResponse(
            "# Calza: bienvenidos los buscadores y los asistentes de IA (GPTBot, ClaudeBot, PerplexityBot, etc.).\n"
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /cuenta/\n"
            "Disallow: /pagos/\n"
            "Disallow: /webhook/\n"
            "Disallow: /buscar\n"
            "\n"
            f"Sitemap: {url_publica}/sitemap.xml\n",
            headers=CACHE_PUBLICO,
        )

    @router.get("/sitemap.xml")
    def sitemap():
        def calcular() -> str:
            fichas = abiertas()
            hoy = ahora().date().isoformat()
            urls = [(f"{url_publica}/", hoy, "1.0"), (f"{url_publica}/licitaciones", hoy, "0.9"),
                    (f"{url_publica}/registro", None, "0.6")]
            urls += [(f"{url_publica}/licitaciones/region/{r.slug}", hoy, "0.8") for r in REGIONES_PUBLICAS]
            urls += [(f"{url_publica}/licitaciones/rubro/{r.slug}", hoy, "0.8") for r in RUBROS]
            urls += [(f"{url_publica}/licitacion/{f.codigo}", _fecha_iso(f.actualizada_en), "0.5") for f in fichas]
            urls += [(f"{url_publica}/terminos", None, "0.2"), (f"{url_publica}/privacidad", None, "0.2")]
            lineas = ['<?xml version="1.0" encoding="UTF-8"?>',
                      '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
            for loc, lastmod, prioridad in urls:
                mod = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
                lineas.append(f"<url><loc>{escape(loc)}</loc>{mod}<priority>{prioridad}</priority></url>")
            lineas.append("</urlset>")
            return "\n".join(lineas)
        return Response(cache.obtener("sitemap", calcular), media_type="application/xml", headers=CACHE_PUBLICO)

    @router.get("/llms.txt", response_class=PlainTextResponse)
    def llms():
        return PlainTextResponse(
            plantillas.get_template("llms.txt").render(
                sitio=url_publica, planes=list(PLANES.values()), anual={c: monto(c, "anual") for c in PLANES},
                precio_fundador=PRECIO_FUNDADOR_PRO, dias_prueba=DIAS_PRUEBA, regiones=REGIONES_PUBLICAS,
                rubros=RUBROS, stats=estadisticas(abiertas()),
            ),
            media_type="text/markdown; charset=utf-8", headers=CACHE_PUBLICO,
        )

    @router.get(f"/{indexnow}.txt", response_class=PlainTextResponse, include_in_schema=False)
    def clave_indexnow_txt():
        return PlainTextResponse(indexnow)

    for nombre, tipo in (("favicon.svg", "image/svg+xml"), ("og.png", "image/png"),
                         ("apple-touch-icon.png", "image/png"), ("favicon.ico", "image/x-icon")):
        def servir(nombre=nombre, tipo=tipo):
            return FileResponse(ESTATICOS / nombre, media_type=tipo, headers=CACHE_ESTATICO)
        router.add_api_route(f"/{nombre}", servir, methods=["GET"], include_in_schema=False)

    return router


def _fecha_iso(fecha: datetime | None) -> str | None:
    return fecha.date().isoformat() if fecha else None
