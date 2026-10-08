from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from conftest import cargar
from licita.alertas import enviar_alertas_compra_agil
from licita.analisis import detectar_codigo
from licita.calce import candidatas
from licita.compra_agil import (
    ClienteCompraAgil, CuotaAgotada, actualizar_precios_compra_agil, normalizar_compra_agil, sincronizar_compras_agiles,
)
from licita.conversacion import responder
from licita.db import Calce, Empresa, Licitacion, Precio, hora_chile
from licita.precios import FUENTE_COTIZACION, informe_precios, referencia_item, texto_precios
from licita.ia import AsistenteIA, EvaluacionCalce, EvaluacionesCalce, clasificar_pendientes
from licita.tareas import ciclo_compra_agil
from test_whatsapp import WhatsAppFalso

# 13:00 UTC = 10:00 en Chile (horario de verano, UTC-3).
MOMENTO = datetime(2026, 10, 7, 13, 0)
TEL = "56989299524"
GUANTES, SOPORTE, LICEO = "1057539-228-COT26", "2494-141-COT26", "3333-10-COT26"


class APIFalsa:
    """Imita api2.mercadopublico.cl a partir de los archivos de ejemplo."""

    def __init__(self, estado_detalle=200):
        self.pedidos: list[httpx.Request] = []
        self.estado_detalle = estado_detalle

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.pedidos.append(request)
        if request.headers.get("ticket") != "TICKET-PRUEBA":
            return httpx.Response(401, json={"success": "NOK", "payload": None, "errors": [{"codigo": "401", "mensaje": "Falta el ticket"}]})
        partes = request.url.path.rstrip("/").split("/")
        if partes[-1] == "compra-agil":
            abiertas = request.url.params.get("estado") == "publicada"
            return httpx.Response(200, json=cargar("compra_agil_listado.json" if abiertas else "compra_agil_cerradas.json"))
        if self.estado_detalle != 200:
            return httpx.Response(self.estado_detalle, json={"success": "NOK", "payload": None, "errors": [{"codigo": str(self.estado_detalle)}]})
        try:
            return httpx.Response(200, json=cargar(f"compra_agil_{partes[-1]}.json"))
        except FileNotFoundError:
            return httpx.Response(404, json={"success": "NOK", "payload": None, "errors": [{"codigo": "404"}]})

    @property
    def detalles(self) -> list[str]:
        return [r.url.path.rsplit("/", 1)[-1] for r in self.pedidos if not r.url.path.endswith("compra-agil")]


def _cliente(api: APIFalsa) -> ClienteCompraAgil:
    return ClienteCompraAgil("TICKET-PRUEBA", http=httpx.Client(transport=httpx.MockTransport(api)), pausa=0, dormir=lambda s: None)


def _empresa(s, plan="pro", **extra) -> Empresa:
    e = Empresa(
        nombre="Aseo Sur", descripcion="Insumos de aseo en el Biobío", regiones=["Biobío"], plan=plan, whatsapp=TEL,
        palabras_clave=["guantes", "bolsas de basura", "artículos de aseo"], **extra,
    )
    s.add(e)
    s.commit()
    return e


def _ia(puntajes: dict[str, int]):
    llamadas = []

    def parse(**kwargs):
        llamadas.append(kwargs)
        contenido = kwargs["messages"][0]["content"]
        evaluaciones = [EvaluacionCalce(codigo=c, puntaje=p, razon="Piden guantes y bolsas: es tu rubro.")
                        for c, p in puntajes.items() if c in contenido]
        return SimpleNamespace(stop_reason="end_turn", parsed_output=EvaluacionesCalce(evaluaciones=evaluaciones))

    return AsistenteIA(SimpleNamespace(messages=SimpleNamespace(parse=parse))), llamadas


# --- Formato de la API ---

def test_normaliza_un_detalle_real():
    datos = normalizar_compra_agil(cargar("real_compra_agil_detalle.json")["payload"])
    assert (datos["codigo"], datos["tipo"], datos["estado_codigo"], datos["estado"]) == ("744835-588-COT26", "COT", 7, "Desierta")
    assert datos["organismo"] == "I MUNICIPALIDAD DE LO ESPEJO" and datos["region"] == "Región Metropolitana de Santiago"
    assert datos["monto_estimado"] == 998592
    # Fechas en hora de Chile, con el formato "AAAA-MM-DD HH:MM" que entrega la API.
    assert datos["fecha_cierre"] == datetime(2026, 9, 30, 13, 15)
    assert datos["items"][0]["producto"] == "Carpas" and datos["items"][0]["cantidad"] == 1
    assert datos["raw"]["detalle"] is True and datos["raw"]["plazo_entrega_dias"] == 3
    assert "Solicitud de Materiales" in datos["descripcion"]


def test_codigo_de_compra_agil_se_reconoce_en_documentos():
    assert detectar_codigo("Cotización para la compra 1057539-228-cot26.pdf") == GUANTES
    assert detectar_codigo("Bases 1056854-11-LE26") == "1056854-11-LE26"


def test_el_ticket_va_en_el_header_y_no_en_la_url():
    api = APIFalsa()
    items, paginacion = _cliente(api).listar(estado="publicada", tamano_pagina=10)
    assert len(items) == 3 and paginacion["total_paginas"] == 1
    assert "TICKET" not in str(api.pedidos[0].url) and api.pedidos[0].headers["ticket"] == "TICKET-PRUEBA"


def test_cuota_agotada_y_detalle_inexistente():
    assert _cliente(APIFalsa()).detalle("9-9-COT26") is None
    with pytest.raises(CuotaAgotada):
        _cliente(APIFalsa(estado_detalle=429)).detalle(GUANTES)


# --- Sincronización ---

def test_sincroniza_y_pide_detalle_solo_de_lo_que_interesa(Sesion):
    api = APIFalsa()
    with Sesion() as s:
        r = sincronizar_compras_agiles(s, _cliente(api), interes=lambda t: "basura" in t.lower(), momento=MOMENTO)
        assert (r.nuevas, r.detalles, r.errores) == (3, 2, 0)
        # Primero el detalle de la que cierra antes.
        assert api.detalles == [LICEO, GUANTES]
        guantes = s.get(Licitacion, GUANTES)
        assert guantes.tipo == "COT" and guantes.estado_codigo == 5 and len(guantes.items) == 2
        assert s.get(Licitacion, LICEO).region == "Biobío"  # sin nombre de región: se usa el código
        assert s.get(Licitacion, SOPORTE).items == []  # sin detalle: no calza con nadie

        # La siguiente vez parte desde la última publicación (con 30 min de margen) y no repite detalles.
        r = sincronizar_compras_agiles(s, _cliente(api), interes=lambda t: True, momento=MOMENTO)
        assert (r.nuevas, r.actualizadas) == (0, 3)
        assert api.pedidos[-2].url.params["publicado_desde"] == "2026-10-07T09:10:00Z"
        assert api.detalles == [LICEO, GUANTES, SOPORTE]
        assert len(s.get(Licitacion, GUANTES).items) == 2  # el listado no borra el detalle


def test_compra_agil_es_solo_para_el_plan_pro(Sesion):
    with Sesion() as s:
        sincronizar_compras_agiles(s, _cliente(APIFalsa()), interes=lambda t: True, momento=MOMENTO)
        pro, pyme = _empresa(s), _empresa(s, plan="pyme", whatsapp_activo=True)
        pyme.whatsapp = "56911112222"
        assert [lic.codigo for lic, _ in candidatas(s, pro, momento=MOMENTO)] == [GUANTES, LICEO]
        assert candidatas(s, pyme, momento=MOMENTO) == []
        # Las Compras Ágiles no se clasifican con IA (son muchas y el calce usa el detalle).
        ia, llamadas = _ia({})
        assert clasificar_pendientes(s, ia) == (0, 0) and llamadas == []


def test_el_cierre_se_compara_en_hora_de_chile(Sesion):
    with Sesion() as s:
        sincronizar_compras_agiles(s, _cliente(APIFalsa()), interes=lambda t: True, momento=MOMENTO)
        e = _empresa(s)
        assert hora_chile(MOMENTO) == datetime(2026, 10, 7, 10, 0)
        # La del liceo cierra a las 10:30 de Chile: a las 10:00 sigue abierta, a las 10:31 ya no.
        assert LICEO in [lic.codigo for lic, _ in candidatas(s, e, momento=MOMENTO)]
        assert LICEO not in [lic.codigo for lic, _ in candidatas(s, e, momento=MOMENTO + timedelta(minutes=31))]


# --- Ciclo completo y alertas ---

def test_ciclo_completo_envia_alerta_urgente_con_plantilla(Sesion):
    wa = WhatsAppFalso()
    ia, llamadas = _ia({GUANTES: 92, LICEO: 88})
    with Sesion() as s:
        e = _empresa(s)
        r = ciclo_compra_agil(s, _cliente(APIFalsa()), ia, wa, momento=MOMENTO)
        assert r.errores == [] and r.calces == 2 and r.alertas.alertas == 1
        # Sin ventana de 24 h: plantilla con la mejor. La del liceo cierra en menos de una hora y no se avisa.
        [(tipo, tel, nombre, parametros, payloads)] = wa.enviados
        assert (tipo, nombre) == ("plantilla", "alerta_compra_agil")
        assert parametros == ["Aseo Sur", "Guantes de nitrilo y bolsas de basura para CESFAM", "92", "08-10-2026 15:00"]
        assert payloads == [f"DETALLE:{GUANTES}"]
        assert "<perfil_empresa>" in llamadas[0]["messages"][0]["content"]

        # El botón abre el detalle, con la ficha de Compra Ágil.
        e.ultimo_mensaje_entrante = MOMENTO
        [detalle] = responder(s, e, "boton", f"DETALLE:{GUANTES}", momento=MOMENTO)
        assert "⚡ Compra Ágil: Guantes de nitrilo" in detalle and "Plazo de entrega: 2 días" in detalle
        assert f"https://buscador.mercadopublico.cl/ficha?code={GUANTES}" in detalle
        assert "40 × Guantes de nitrilo" in detalle

        # Una nueva vuelta no repite la alerta.
        r = ciclo_compra_agil(s, _cliente(APIFalsa()), ia, wa, momento=MOMENTO + timedelta(minutes=20))
        assert r.alertas.alertas == 0 and len(wa.enviados) == 1


def test_con_ventana_abierta_va_texto_y_respeta_el_maximo_diario(Sesion):
    wa = WhatsAppFalso()
    with Sesion() as s:
        sincronizar_compras_agiles(s, _cliente(APIFalsa()), interes=lambda t: True, momento=MOMENTO)
        e = _empresa(s, ultimo_mensaje_entrante=MOMENTO - timedelta(hours=2))
        s.add_all([Calce(empresa_id=e.id, licitacion_codigo=c, puntaje=p, razon="Es tu rubro.")
                   for c, p in ((GUANTES, 95), (SOPORTE, 80))])
        s.commit()
        r = enviar_alertas_compra_agil(s, wa, momento=MOMENTO, por_dia=1)
        assert (r.textos, r.alertas) == (1, 1)
        texto = wa.enviados[0][2]
        assert "hay una Compra Ágil que calza contigo" in texto and "Guantes de nitrilo" in texto and "soporte" not in texto
        # Ya usó su alerta del día.
        assert enviar_alertas_compra_agil(s, wa, momento=MOMENTO + timedelta(hours=1), por_dia=1).alertas == 0
        # "1" abre el detalle de lo que se avisó.
        assert "Guantes de nitrilo" in responder(s, e, "texto", "1", momento=MOMENTO)[0]


def test_no_alerta_fuera_de_horario_ni_a_planes_sin_compra_agil(Sesion):
    wa = WhatsAppFalso()
    with Sesion() as s:
        sincronizar_compras_agiles(s, _cliente(APIFalsa()), interes=lambda t: True, momento=MOMENTO)
        pyme = _empresa(s, plan="pyme")
        s.add(Calce(empresa_id=pyme.id, licitacion_codigo=GUANTES, puntaje=95, razon="x"))
        s.commit()
        assert enviar_alertas_compra_agil(s, wa, momento=MOMENTO).alertas == 0
        pyme.plan = "pro"
        noche = datetime(2026, 10, 8, 1, 30)  # 22:30 en Chile
        assert enviar_alertas_compra_agil(s, wa, momento=noche).fuera_de_horario
        assert wa.enviados == []


def test_sin_clientes_pro_no_consulta_la_api(Sesion):
    api = APIFalsa()
    ia, _ = _ia({})
    with Sesion() as s:
        _empresa(s, plan="pyme")
        r = ciclo_compra_agil(s, _cliente(api), ia, None, momento=MOMENTO)
    assert api.pedidos == [] and r.sync is None


# --- Precios: cotizaciones de las Compras Ágiles cerradas ---

def test_guarda_cotizaciones_admisibles_de_las_cerradas_que_interesan(Sesion):
    api = APIFalsa()
    with Sesion() as s:
        r = actualizar_precios_compra_agil(s, _cliente(api), interes=lambda t: "guantes" in t.lower(), momento=MOMENTO)
        assert (r.cerradas, r.detalles, r.con_cotizaciones, r.cotizaciones) == (3, 2, 2, 6)
        params = next(p.url.params for p in api.pedidos if "cambio_desde" in p.url.params)
        assert params["estado"] == "cerrada,desierta,proveedor_seleccionado"
        assert (params["cambio_desde"], params["cambio_hasta"]) == ("2026-10-06T08:00:00Z", "2026-10-07T10:00:00Z")
        assert s.get(Licitacion, "7777-3-COT26") is None  # fumigación: no le interesa a nadie, no se guarda
        assert s.get(Licitacion, "5555-1-COT26").estado_codigo == 7
        precios = s.query(Precio).filter_by(fuente=FUENTE_COTIZACION).all()
        assert "BARATO SPA" not in {p.proveedor_nombre for p in precios}  # inadmisible
        bolsa = next(p for p in precios if p.codigo_producto == "47121701" and p.precio_unitario == 1500)
        assert (bolsa.unidad, bolsa.proveedor_rut, bolsa.referencia) == ("Paquete", "76.111.111-1", "6666-2-COT26")

        # Otra vuelta no repite detalles ni duplica precios.
        r = actualizar_precios_compra_agil(s, _cliente(api), interes=lambda t: True, momento=MOMENTO)
        assert r.detalles == 0 and s.query(Precio).filter_by(fuente=FUENTE_COTIZACION).count() == 6


def test_informe_muestra_a_cuanto_cotiza_la_competencia(Sesion):
    with Sesion() as s:
        actualizar_precios_compra_agil(s, _cliente(APIFalsa()), interes=lambda t: True, momento=MOMENTO)
        sincronizar_compras_agiles(s, _cliente(APIFalsa()), interes=lambda t: True, momento=MOMENTO)
        lic = s.get(Licitacion, GUANTES)
        informe = informe_precios(s, lic, momento=MOMENTO)
        guantes = informe.cotizacion(0)
        assert (guantes.cotizaciones, guantes.procesos) == (4, 2)
        assert (guantes.p25, guantes.p75) == (4725, 5050)
        # La más baja de cada proceso (4.500 y 4.800) es la referencia para ganar.
        assert (guantes.ganadora_p25, guantes.ganadora_mediana, guantes.ganadora_p75) == (4575, 4650, 4725)
        # Las cotizaciones no se mezclan con lo que efectivamente pagó el Estado.
        assert referencia_item(s, lic.items[0], momento=MOMENTO) is None
        texto = texto_precios(informe)
        assert "cotizaciones de Compra Ágil" in texto
        assert "⚡ En Compra Ágil cotizan $4.725 – $5.050 c/u (4 cotizaciones en 2 procesos)" in texto
        assert "🏁 La más baja de cada proceso suele ser $4.575 – $4.725 (mediana $4.650)" in texto
        # Bolsas: un solo proceso con cotizaciones no alcanza para una referencia.
        assert informe.cotizacion(1) is None and informe.cobertura == (1, 2)


def test_el_detalle_de_una_compra_agil_invita_a_ver_precios(Sesion):
    with Sesion() as s:
        sincronizar_compras_agiles(s, _cliente(APIFalsa()), interes=lambda t: True, momento=MOMENTO)
        e = _empresa(s, ultimo_mensaje_entrante=MOMENTO)
        s.add(Calce(empresa_id=e.id, licitacion_codigo=GUANTES, puntaje=90, razon="Es tu rubro."))
        s.commit()
        [detalle] = responder(s, e, "boton", f"DETALLE:{GUANTES}", momento=MOMENTO)
        assert "Escribe *PRECIOS* para ver a cuánto cotiza la competencia" in detalle
        assert "📎 Tiene 2 adjuntos:\n• Especificaciones técnicas CESFAM.pdf\n• Anexo cotización.docx" in detalle
        assert "Descárgalos desde la ficha (PDF o Word) y envíamelos aquí" in detalle and "PDF de las bases" not in detalle
        assert detalle.count("PRECIOS") == 1
