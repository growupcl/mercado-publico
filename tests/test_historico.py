import io
import zipfile
from datetime import date, datetime

import httpx

from licita.calce import interes_de_clientes
from licita.cli import main
from licita.db import Empresa, OrdenCompra, Precio
from licita.historico import fecha, importar_archivo, importar_csv, importar_meses, meses_hacia_atras, numero
from licita.precios import registrar_precios_orden

ENCABEZADO = ("Codigo;Link;Nombre;codigoEstado;Estado;OrganismoPublico;RegionUnidadCompra;RutSucursal;NombreProveedor;"
              "FechaEnvio;TipoMonedaOC;RubroN3;codigoProductoONU;NombreroductoGenerico;EspecificacionComprador;"
              "UnidadMedida;cantidad;monedaItem;precioNeto")
FILAS = [
    # Dos líneas de una OC de aseo; la especificación trae un salto de línea entre comillas.
    '1234-567-SE26;http://x;Insumos de aseo;6;Aceptada;Municipalidad de Concepción;Región del Biobío;76.123.456-0;'
    'Aseo Sur SpA;2026-09-15 10:30:00;CLP;Artículos de limpieza;47131810;Detergente;"Detergente líquido\n5 litros";'
    'Unidad;10;CLP;"9.990,5"',
    '1234-567-SE26;http://x;Insumos de aseo;6;Aceptada;Municipalidad de Concepción;Región del Biobío;76.123.456-0;'
    'Aseo Sur SpA;2026-09-15 10:30:00;CLP;Artículos de limpieza;42132203;Guantes de nitrilo;Talla M;Caja;4;CLP;12000',
    # Otro rubro: no le interesa a ningún cliente.
    '2222-1-SE26;http://x;Neumáticos;6;Aceptada;Carabineros;Región Metropolitana;77.000.000-1;Gomas SA;'
    '2026-09-16;CLP;Repuestos;25172504;Neumático;Aro 16;Unidad;4;CLP;85000',
    # Cancelada y en dólares: se descartan aunque sean de aseo.
    '3333-1-SE26;http://x;Detergente;9;Cancelada;Hospital;Región del Biobío;76.1-1;X;2026-09-17;CLP;Limpieza;47131810;'
    'Detergente;;Unidad;1;CLP;5000',
    '4444-1-SE26;http://x;Detergente;6;Aceptada;Hospital;Región del Biobío;76.1-1;X;2026-09-17;USD;Limpieza;47131810;'
    'Detergente;;Unidad;1;USD;12,5',
]


def _csv(filas=FILAS) -> str:
    return "\r\n".join([ENCABEZADO, *filas]) + "\r\n"


def _zip(texto: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("lic_2026-9.csv", texto.encode("latin-1"))
    return buf.getvalue()


def _cliente(s):
    s.add(Empresa(nombre="Aseo Sur", descripcion="Aseo", palabras_clave=["detergente", "guantes"]))
    s.commit()
    return interes_de_clientes(s)


def test_formatos_de_numero_y_fecha():
    assert numero("9.990,5") == 9990.5 and numero("1234,5") == 1234.5 and numero("1234.5") == 1234.5
    assert numero("1.234") == 1234 and numero("85000") == 85000 and numero("") is None
    assert fecha("2026-09-15 10:30:00") == datetime(2026, 9, 15, 10, 30) and fecha("15/09/2026") == datetime(2026, 9, 15)


def test_importa_solo_lo_que_interesa_y_es_idempotente(Sesion):
    with Sesion() as s:
        interes = _cliente(s)
        r = importar_csv(s, io.StringIO(_csv(), newline=""), interes=interes)
        assert (r.lineas, r.ordenes, r.guardados, r.filtrados, r.descartados) == (5, 4, 2, 1, 2)
        detergente = s.query(Precio).filter_by(codigo_producto="47131810").one()
        assert detergente.precio_unitario == 9990.5 and detergente.producto == "Detergente Detergente líquido\n5 litros"
        assert (detergente.posicion, detergente.region, detergente.fecha) == (0, "Región del Biobío", datetime(2026, 9, 15, 10, 30))
        guantes = s.query(Precio).filter_by(codigo_producto="42132203").one()
        assert (guantes.posicion, guantes.unidad, guantes.cantidad) == (1, "Caja", 4)

        # Volver a cargar (el mes se sigue completando) no duplica.
        r = importar_csv(s, io.StringIO(_csv(), newline=""), interes=interes)
        assert (r.guardados, r.ya_estaban) == (0, 2) and s.query(Precio).count() == 2


def test_no_duplica_lo_que_ya_trajo_la_api(Sesion):
    with Sesion() as s:
        oc = OrdenCompra(codigo="1234-567-SE26", estado="Aceptada", moneda="CLP", items=[
            {"codigo_producto": 47131810, "producto": "Detergente", "precio_neto": 9990.5, "cantidad": 10},
            {"codigo_producto": 42132203, "producto": "Guantes", "precio_neto": 12000, "cantidad": 4},
        ])
        assert registrar_precios_orden(s, oc) == 2
        s.commit()
        r = importar_csv(s, io.StringIO(_csv(), newline=""), interes=_cliente(s))
        assert r.guardados == 0 and s.query(Precio).count() == 2


def test_descarga_los_meses_y_salta_los_no_publicados(Sesion, tmp_path):
    pedidos = []

    def responder(request):
        pedidos.append(request.url.path)
        if request.url.path.endswith("/2026-9.zip"):
            return httpx.Response(200, content=_zip(_csv()))
        return httpx.Response(404)

    http = httpx.Client(transport=httpx.MockTransport(responder))
    with Sesion() as s:
        vistos = []
        r = importar_meses(s, meses_hacia_atras(date(2026, 10, 8), 2), interes=None, http=http,
                           al_terminar_mes=lambda a, m, res: vistos.append((a, m, res is not None)))
        assert pedidos == ["/oc-da/2026-9.zip", "/oc-da/2026-10.zip"]
        assert vistos == [(2026, 9, True), (2026, 10, False)]
        assert r.guardados == 3  # con --todo también entra el neumático


def test_meses_hacia_atras_cruza_el_anio():
    assert meses_hacia_atras(date(2026, 2, 10), 3) == [(2025, 12), (2026, 1), (2026, 2)]


def test_comando_importa_un_archivo_descargado(Sesion, tmp_path, monkeypatch, capsys):
    ruta = tmp_path / "2026-9.zip"
    ruta.write_bytes(_zip(_csv()))
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    assert main(["historico-oc", "--archivo", str(ruta), "--todo"]) == 0
    assert "3 precios nuevos" in capsys.readouterr().out
    assert main(["historico-oc", "--archivo", str(ruta)]) == 0
    assert "no hay clientes con palabras clave" in capsys.readouterr().out
