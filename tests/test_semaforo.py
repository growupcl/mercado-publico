from datetime import datetime

from licita.db import Licitacion, Precio
from licita.semaforo import ROJO, VERDE, senales, texto_semaforo

UTC = datetime(2026, 11, 18, 11, 40)  # 8:40 en Chile
GUANTES = "46181504"


def _lic(cierre="2026-11-28T15:00:00", **kw):
    return Licitacion(codigo="9-9-LE26", nombre="Guantes para CESFAM", descripcion="", tipo=kw.pop("tipo", "LE"),
                      estado_codigo=5, organismo="Municipalidad de Chillán", region="", comuna="", moneda="CLP",
                      monto_estimado=kw.pop("monto", None), fecha_cierre=datetime.fromisoformat(cierre),
                      items=kw.pop("items", [{"codigo_producto": GUANTES, "producto": "Guantes de nitrilo", "cantidad": 100}]),
                      clasificacion=kw.pop("clasificacion", None), raw={})


def _compra(s, ref, proveedor, precio=5000, organismo="MUNICIPALIDAD DE CHILLÁN"):
    s.add(Precio(fuente="orden_de_compra", referencia=ref, posicion=0, codigo_producto=GUANTES, producto="Guantes",
                 precio_unitario=precio, cantidad=100, proveedor_nombre=proveedor, organismo=organismo,
                 fecha=datetime(2026, 6, 1)))


def test_tiempo_y_exigencias(Sesion):
    with Sesion() as s:
        lic = _lic(cierre="2026-11-18T15:00:00",
                   clasificacion={"requisitos_destacados": ["Boleta de garantía de seriedad", "Visita a terreno obligatoria"]})
        texto = texto_semaforo(s, lic, con_precios=False, momento=UTC)
    assert texto.startswith("🚦 *¿Te conviene?* 🔴 Difícil")
    assert "🔴 Cierra hoy a las 15:00" in texto
    assert "🟡 Pide boleta de garantía y visita a terreno" in texto


def test_competencia_con_un_proveedor_que_casi_siempre_gana(Sesion):
    with Sesion() as s:
        for i in range(4):
            _compra(s, f"OC-{i}", "Limpia SpA")
        _compra(s, "OC-9", "Otra SpA")
        _compra(s, "OC-x", "Lejana SpA", organismo="Hospital de Arica")  # otro organismo: no cuenta
        s.commit()
        lista = senales(s, _lic(), con_precios=False, momento=UTC)
    assert lista[0].color == VERDE and "Quedan 10 días" in lista[0].texto
    assert "Limpia SpA le ha vendido esto 4 de las últimas 5 veces" in lista[1].texto


def test_competencia_abierta_y_presupuesto_bajo_mercado_solo_con_precios(Sesion):
    with Sesion() as s:
        for i, prov in enumerate(["A SpA", "B SpA", "C SpA", "D SpA"]):
            _compra(s, f"OC-{i}", prov, precio=5000)
        s.commit()
        lic = _lic(monto=300_000)  # 100 guantes a $5.000 = $500.000 de mercado
        sin = senales(s, lic, con_precios=False, momento=UTC)
        con = senales(s, lic, con_precios=True, momento=UTC)
    assert "ganaron 4 proveedores distintos" in sin[1].texto and len(sin) == 2
    assert con[2].color == ROJO and "40% bajo el precio de mercado" in con[2].texto


def test_sin_datos_no_inventa(Sesion):
    with Sesion() as s:
        lic = _lic(items=[], cierre="2026-12-20T15:00:00")
        assert texto_semaforo(s, lic, con_precios=True, momento=UTC) == "🚦 *¿Te conviene?* 🟢 Buena oportunidad\n🟢 Quedan 32 días para preparar la oferta."
