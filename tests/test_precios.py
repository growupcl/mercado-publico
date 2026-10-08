import itertools
from datetime import date, datetime

import pytest

from licita.conversacion import procesar_webhook
from licita.db import Calce, Empresa, Licitacion, OrdenCompra, Precio
from licita.precios import informe_precios, referencia_item, registrar_precios_orden, texto_precios
from licita.sync import sincronizar_licitaciones, sincronizar_ordenes_de_compra

MOMENTO = datetime(2026, 10, 7, 9, 0)
TEL = "56989299524"
DETERGENTE = "47131604"


def _precio(s, ref, precio, *, codigo=DETERGENTE, producto="Detergente líquido 5 litros", proveedor="Aseo Sur SpA",
            region="Región del Biobío", fecha=datetime(2026, 5, 1), cantidad=100):
    s.add(Precio(fuente="orden_de_compra", referencia=ref, posicion=0, codigo_producto=codigo, producto=producto,
                 precio_unitario=precio, cantidad=cantidad, proveedor_nombre=proveedor, region=region, fecha=fecha))


@pytest.fixture
def s(Sesion, cliente_mp):
    sesion = Sesion()
    sincronizar_licitaciones(sesion, cliente_mp, date(2026, 10, 6))
    return sesion


def test_sync_guarda_precios_de_adjudicaciones_y_ordenes(s, cliente_mp):
    adj = s.query(Precio).filter_by(fuente="adjudicacion").one()
    assert (adj.referencia, adj.precio_unitario, adj.proveedor_nombre) == ("3456-7-LP26", 142000000, "Constructora Ñuble SpA")
    sincronizar_ordenes_de_compra(s, cliente_mp, date(2026, 10, 6))
    oc = s.query(Precio).filter_by(fuente="orden_de_compra").one()
    assert (oc.codigo_producto, oc.precio_unitario, oc.proveedor_nombre) == (DETERGENTE, 10000, "Aseo Sur SpA")


def test_ignora_ordenes_canceladas_otra_moneda_y_repetidas(s):
    item = {"codigo_producto": DETERGENTE, "producto": "Detergente", "precio_neto": 5000, "cantidad": 1}
    cancelada = OrdenCompra(codigo="OC-1", estado="Cancelada", moneda="CLP", items=[item])
    dolares = OrdenCompra(codigo="OC-2", estado="Aceptada", moneda="USD", items=[item])
    buena = OrdenCompra(codigo="OC-3", estado="Aceptada", moneda="CLP", items=[item])
    assert registrar_precios_orden(s, cancelada) == 0
    assert registrar_precios_orden(s, dolares) == 0
    assert registrar_precios_orden(s, buena) == 1
    s.flush()
    assert registrar_precios_orden(s, buena) == 0


def test_referencia_por_codigo_con_cuartiles_atipicos_y_proveedores(s):
    for i, valor in enumerate([4000, 4200, 4500, 4800, 5000, 5200, 5500, 60000]):  # 60000 = caja, atípico
        _precio(s, f"OC-{i}", valor, proveedor="Aseo Sur SpA" if i % 2 else "Limpia Ya Ltda")
    _precio(s, "OC-viejo", 1000, fecha=datetime(2023, 1, 1))  # fuera de los 24 meses
    s.commit()
    ref = referencia_item(s, {"codigo_producto": DETERGENTE, "producto": "Detergente", "cantidad": 200},
                          region="Región del Biobío", momento=MOMENTO)
    assert ref.observaciones == 7
    assert ref.minimo == 4000 and ref.maximo == 5500
    assert ref.mediana == 4800
    assert ref.rango_competitivo == (ref.p25, 4800) and ref.p25 < 4800
    assert ref.en_region == 8
    assert {n for n, _ in ref.proveedores_frecuentes} == {"Aseo Sur SpA", "Limpia Ya Ltda"}
    assert not ref.por_similitud


def test_sin_datos_suficientes_no_inventa(s):
    _precio(s, "OC-1", 4000)
    _precio(s, "OC-2", 4100)
    s.commit()
    assert referencia_item(s, {"codigo_producto": DETERGENTE, "producto": "Detergente"}, momento=MOMENTO) is None


def test_usa_productos_parecidos_de_la_misma_familia(s):
    for i, valor in enumerate([300, 320, 350]):
        _precio(s, f"OC-{i}", valor, codigo="53102199", producto="Guantes desechables de látex talla M")
    _precio(s, "OC-x", 9000, codigo="53102150", producto="Botas de seguridad")
    s.commit()
    ref = referencia_item(s, {"codigo_producto": "53102104", "producto": "Guantes de látex", "descripcion": "desechables"}, momento=MOMENTO)
    assert ref.por_similitud and ref.observaciones == 3 and ref.mediana == 320


def test_informe_y_texto_con_comparacion_de_presupuesto(s):
    lic = s.get(Licitacion, "1234-56-LE26")  # detergente ×200, guantes ×500, bolsas ×1000; presupuesto $8.500.000
    for i, (codigo, valores) in enumerate({DETERGENTE: [9000, 10000, 11000], "53102104": [3000, 3200, 3400], "47121701": [2000, 2100, 2200]}.items()):
        for j, v in enumerate(valores):
            _precio(s, f"OC-{i}-{j}", v, codigo=codigo)
    s.commit()
    informe = informe_precios(s, lic, momento=MOMENTO)
    assert informe.cobertura == (3, 3)
    assert informe.total_mediano == 10000 * 200 + 3200 * 500 + 2100 * 1000  # $5.700.000
    texto = texto_precios(informe)
    assert "💲 *Precios de referencia: Adquisición de insumos de aseo para CESFAM*" in texto
    assert "Mediana: $10.000 c/u · habitual $9.500 – $10.500 (3 compras)" in texto
    assert "👉 Precio competitivo: $9.500 – $10.000" in texto
    assert "Total a precio mediano: $5.700.000" in texto
    assert "está 49% sobre el precio de mercado" in texto


def test_texto_sin_referencias(s):
    texto = texto_precios(informe_precios(s, s.get(Licitacion, "1234-56-LE26"), momento=MOMENTO))
    assert "Todavía no tengo suficientes compras anteriores" in texto


# --- WhatsApp ---

_ids = itertools.count(1)


def _texto(body):
    return {"entry": [{"changes": [{"value": {"messages": [
        {"from": TEL, "id": f"wamid.p{next(_ids)}", "type": "text", "text": {"body": body}}]}}]}]}


class WA:
    def __init__(self):
        self.enviados = []

    def enviar_texto(self, tel, texto):
        self.enviados.append(texto)
        return "w"


def _empresa(s, plan):
    e = Empresa(nombre="Aseo Sur", descripcion="", regiones=[], palabras_clave=[], whatsapp=TEL, plan=plan)
    s.add(e)
    s.commit()
    s.add(Calce(empresa_id=e.id, licitacion_codigo="1234-56-LE26", puntaje=92, razon="Es tu rubro.",
                notificado=True, notificado_en=MOMENTO, posicion_resumen=1))
    for i, v in enumerate([9000, 10000, 11000]):
        _precio(s, f"OC-{i}", v)
    s.commit()
    return e


def test_precios_por_whatsapp_plan_pro(s):
    _empresa(s, "pro")
    wa = WA()
    procesar_webhook(s, wa, _texto("PRECIOS"), momento=MOMENTO)
    assert "Primero abre una licitación" in wa.enviados[-1]
    procesar_webhook(s, wa, _texto("1"), momento=MOMENTO)
    assert "Escribe *PRECIOS*" in wa.enviados[-1]
    procesar_webhook(s, wa, _texto("precios"), momento=MOMENTO)
    assert wa.enviados[-1].startswith("💲 *Precios de referencia")
    assert "👉 Precio competitivo: $9.500 – $10.000" in wa.enviados[-1]


def test_precios_en_plan_pyme_muestra_invitacion_al_pro(s):
    _empresa(s, "pyme")
    wa = WA()
    procesar_webhook(s, wa, _texto("1"), momento=MOMENTO)
    procesar_webhook(s, wa, _texto("PRECIOS"), momento=MOMENTO)
    assert "parte del *plan Pro*" in wa.enviados[-1]
    assert "referencias de 1 de 3 ítems" in wa.enviados[-1]
