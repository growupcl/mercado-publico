from datetime import date, datetime

import httpx
import pytest

from licita.mercadopublico import MercadoPublicoClient, MercadoPublicoError, normalizar_licitacion, normalizar_orden_de_compra


def test_listado_por_fecha_envia_ticket_y_fecha():
    vistos = []

    def handler(request):
        vistos.append(request.url)
        return httpx.Response(200, json={"Cantidad": 0, "Listado": []})

    c = MercadoPublicoClient("T1", http=httpx.Client(transport=httpx.MockTransport(handler)), pausa=0, dormir=lambda s: None)
    assert c.licitaciones_por_fecha(date(2026, 10, 6)) == []
    assert vistos[0].params["fecha"] == "06102026"
    assert vistos[0].params["ticket"] == "T1"


def test_reintenta_ante_peticiones_simultaneas():
    respuestas = [
        httpx.Response(200, json={"Codigo": 10500, "Mensaje": "Peticiones simultáneas"}),
        httpx.Response(503),
        httpx.Response(200, json={"Cantidad": 1, "Listado": [{"CodigoExterno": "X"}]}),
    ]
    c = MercadoPublicoClient(
        "T", http=httpx.Client(transport=httpx.MockTransport(lambda r: respuestas.pop(0))), pausa=0, dormir=lambda s: None
    )
    assert c.licitacion("X") == {"CodigoExterno": "X"}


def test_error_de_ticket_no_se_reintenta():
    llamadas = []

    def handler(request):
        llamadas.append(1)
        return httpx.Response(200, json={"Codigo": 203, "Mensaje": "Ticket no válido"})

    c = MercadoPublicoClient("T", http=httpx.Client(transport=httpx.MockTransport(handler)), pausa=0, dormir=lambda s: None)
    with pytest.raises(MercadoPublicoError, match="Ticket no válido"):
        c.licitaciones_activas()
    assert len(llamadas) == 1


def test_sin_ticket_falla_con_mensaje_claro():
    with pytest.raises(MercadoPublicoError, match="MERCADOPUBLICO_TICKET"):
        MercadoPublicoClient("")


def test_normalizar_licitacion(cliente_mp):
    datos = normalizar_licitacion(cliente_mp.licitacion("1234-56-LE26"))
    assert datos["codigo"] == "1234-56-LE26"
    assert datos["organismo"] == "Ilustre Municipalidad de Concepción"
    assert datos["region"] == "Región del Biobío"
    assert datos["monto_estimado"] == 8500000
    assert datos["fecha_cierre"] == datetime(2026, 10, 20, 15, 0)
    assert [i["producto"] for i in datos["items"]] == ["Detergente", "Guantes de látex", "Bolsas de basura"]


def test_normalizar_orden_de_compra(cliente_mp):
    datos = normalizar_orden_de_compra(cliente_mp.orden_de_compra("1234-567-SE26"))
    assert datos["proveedor_nombre"] == "Aseo Sur SpA"
    assert datos["region"] == "Región del Biobío"
    assert datos["items"][0]["precio_neto"] == 10000
