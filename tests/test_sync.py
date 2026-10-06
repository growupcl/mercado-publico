from datetime import date

from licita.db import Licitacion, OrdenCompra
from licita.sync import sincronizar_licitaciones, sincronizar_ordenes_de_compra


def test_sincroniza_y_luego_omite_sin_cambios(Sesion, cliente_mp):
    with Sesion() as s:
        r = sincronizar_licitaciones(s, cliente_mp, date(2026, 10, 6))
        assert (r.nuevas, r.actualizadas, r.sin_cambios, r.errores) == (3, 0, 0, 0)
        assert s.get(Licitacion, "2345-12-L126").region == "Región de la Araucanía"

        r = sincronizar_licitaciones(s, cliente_mp, date(2026, 10, 6))
        assert (r.nuevas, r.sin_cambios) == (0, 3)


def test_actualiza_si_cambia_el_estado(Sesion, cliente_mp):
    with Sesion() as s:
        sincronizar_licitaciones(s, cliente_mp, date(2026, 10, 6))
        s.get(Licitacion, "1234-56-LE26").estado_codigo = 6
        s.commit()
        r = sincronizar_licitaciones(s, cliente_mp, date(2026, 10, 6))
        assert r.actualizadas == 1
        assert s.get(Licitacion, "1234-56-LE26").estado_codigo == 5


def test_respeta_max_detalles(Sesion, cliente_mp):
    with Sesion() as s:
        r = sincronizar_licitaciones(s, cliente_mp, date(2026, 10, 6), max_detalles=1)
        assert r.nuevas == 1


def test_sincroniza_ordenes_de_compra(Sesion, cliente_mp):
    with Sesion() as s:
        r = sincronizar_ordenes_de_compra(s, cliente_mp, date(2026, 10, 6))
        assert r.nuevas == 1
        assert s.get(OrdenCompra, "1234-567-SE26").total_neto == 1000000
        assert sincronizar_ordenes_de_compra(s, cliente_mp, date(2026, 10, 6)).sin_cambios == 1
