"""Pruebas con respuestas reales de la API de Mercado Público (octubre 2026, datos de funcionarios borrados).

Si ChileCompra cambia el formato, estas pruebas lo detectan.
"""

from datetime import date, datetime

import httpx

from conftest import cargar
from licita.analisis import detectar_codigo
from licita.calce import interes_de_clientes, region_compatible
from licita.db import Empresa, Licitacion, OrdenCompra
from licita.mercadopublico import MercadoPublicoClient, normalizar_licitacion
from licita.sync import sincronizar_licitaciones, sincronizar_ordenes_de_compra


def test_licitacion_publicada_real():
    datos = normalizar_licitacion(cargar("real_licitacion_publicada.json")["Listado"][0])
    assert datos["codigo"] == "1056854-11-LE26"
    assert datos["estado_codigo"] == 5 and datos["estado"] == "Publicada"
    assert datos["organismo"] == "I MUNICIPALIDAD DE PROVIDENCIA"
    assert datos["region"] == "Región Metropolitana de Santiago"
    assert datos["monto_estimado"] == 29832000.0
    assert datos["fecha_cierre"] == datetime(2026, 10, 20, 10, 0)  # viene en Fechas, no en la raíz
    assert len(datos["items"]) == 5 and datos["items"][0]["codigo_producto"] == 81111811


def test_licitacion_adjudicada_real_trae_precios_ganadores():
    datos = normalizar_licitacion(cargar("real_licitacion_adjudicada.json")["Listado"][0])
    assert datos["estado_codigo"] == 8
    assert datos["monto_estimado"] is None  # monto no publicado
    adj = datos["items"][0]["adjudicacion"]
    assert adj == {"proveedor_rut": "81.378.300-2", "proveedor_nombre": "ABBOTT LABORATORIES DE CHILE LTDA",
                   "cantidad": 1.0, "precio_unitario": 600.0}


def test_codigos_reales():
    for codigo in ("1056854-11-LE26", "1511-62-L126", "1002772-108-LP26", "1057049-373-LE26", "2345-12-O126"):
        assert detectar_codigo(f"Bases {codigo} final.pdf") == codigo


def test_regiones_con_nombre_oficial():
    lic = Licitacion(codigo="x", region="Región del Libertador General Bernardo O´Higgins")
    assert region_compatible(Empresa(nombre="a", descripcion="", regiones=["O'Higgins"], palabras_clave=[]), lic)
    assert not region_compatible(Empresa(nombre="a", descripcion="", regiones=["Ñuble"], palabras_clave=[]), lic)
    assert region_compatible(Empresa(nombre="a", descripcion="", regiones=["Ñuble"], palabras_clave=[]),
                             Licitacion(codigo="y", region="Región del Ñuble"))


class ClienteContador(MercadoPublicoClient):
    def __init__(self, listado, detalles):
        super().__init__("T", http=httpx.Client(), pausa=0, dormir=lambda s: None)
        self.listado, self.detalles, self.pedidos = listado, detalles, []

    def licitaciones_por_fecha(self, fecha):
        return self.listado

    def licitacion(self, codigo):
        self.pedidos.append(codigo)
        return self.detalles[codigo]

    def ordenes_de_compra_por_fecha(self, fecha):
        return self.listado

    def orden_de_compra(self, codigo):
        self.pedidos.append(codigo)
        return self.detalles[codigo]


def _detalle(codigo, estado):
    return {"CodigoExterno": codigo, "Nombre": codigo, "CodigoEstado": estado, "Comprador": {}, "Fechas": {}, "Items": {}}


def test_sync_prioriza_publicadas_y_no_gasta_consultas_en_cerradas(Sesion):
    listado = [{"CodigoExterno": "C-1", "CodigoEstado": 6}, {"CodigoExterno": "A-1", "CodigoEstado": 8},
               {"CodigoExterno": "P-1", "CodigoEstado": 5}, {"CodigoExterno": "D-1", "CodigoEstado": 7}]
    cliente = ClienteContador(listado, {c: _detalle(c, e) for c, e in [("A-1", 8), ("P-1", 5)]})
    with Sesion() as s:
        r = sincronizar_licitaciones(s, cliente, date(2026, 10, 7), max_detalles=1)
        assert cliente.pedidos == ["P-1"]  # primero la publicada
        assert (r.nuevas, r.omitidas, r.pendientes) == (1, 2, 1)

        # Al día siguiente la publicada cerró: se actualiza sin pedir el detalle.
        cliente.listado = [{"CodigoExterno": "P-1", "CodigoEstado": 6}]
        r = sincronizar_licitaciones(s, cliente, date(2026, 10, 8))
        assert r.actualizadas == 1 and cliente.pedidos == ["P-1"]
        assert s.get(Licitacion, "P-1").estado == "Cerrada"


def test_ordenes_de_compra_solo_las_de_interes(Sesion):
    with Sesion() as s:
        s.add_all([Empresa(nombre="Aseo", descripcion="", regiones=[], palabras_clave=["detergente", "guantes"], plan="pro"),
                   Empresa(nombre="Gratis", descripcion="", regiones=[], palabras_clave=["neumáticos"], plan="gratis")])
        s.commit()
        listado = [{"Codigo": "OC-1", "Nombre": "ADQ. GUANTES DE LATEX PARA CESFAM"},
                   {"Codigo": "OC-2", "Nombre": "COMPRA DE NEUMATICOS CAMIONETA"},
                   {"Codigo": "OC-3", "Nombre": "SERVICIO DE BANQUETERIA"}]
        oc = {"Codigo": "OC-1", "Nombre": "x", "Estado": "Aceptada", "Comprador": {}, "Proveedor": {}, "Fechas": {}, "Items": {}}
        cliente = ClienteContador(listado, {"OC-1": oc})
        r = sincronizar_ordenes_de_compra(s, cliente, date(2026, 10, 7), interes=interes_de_clientes(s))
        assert cliente.pedidos == ["OC-1"] and r.omitidas == 2
        assert s.get(OrdenCompra, "OC-1") is not None
