import json
from pathlib import Path

import httpx
import pytest

from licita.db import crear_sesiones
from licita.mercadopublico import MercadoPublicoClient

FIXTURES = Path(__file__).parent / "fixtures"


def cargar(nombre: str) -> dict:
    return json.loads((FIXTURES / nombre).read_text(encoding="utf-8"))


def respuesta_falsa(request: httpx.Request) -> httpx.Response:
    """Imita la API de Mercado Público a partir de los archivos de ejemplo."""
    recurso = request.url.path.rsplit("/", 1)[-1]
    params = request.url.params
    if recurso == "licitaciones.json":
        if "codigo" in params:
            return httpx.Response(200, json=cargar(f"licitacion_{params['codigo']}.json"))
        return httpx.Response(200, json=cargar("licitaciones_fecha.json"))
    if recurso == "ordenesdecompra.json":
        if "codigo" in params:
            return httpx.Response(200, json=cargar(f"orden_{params['codigo']}.json"))
        return httpx.Response(200, json=cargar("ordenes_fecha.json"))
    return httpx.Response(404, json={"Codigo": 404, "Mensaje": "No encontrado"})


@pytest.fixture
def Sesion():
    return crear_sesiones("sqlite://")


@pytest.fixture
def cliente_mp():
    http = httpx.Client(transport=httpx.MockTransport(respuesta_falsa))
    return MercadoPublicoClient("TICKET-PRUEBA", http=http, pausa=0, dormir=lambda s: None)
