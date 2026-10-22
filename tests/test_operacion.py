import io
from datetime import datetime, timezone

from licita.cli import main
from licita.operacion import LARGO_DETALLE, correo_falla


def test_correo_de_falla_explica_el_impacto_y_como_revisar():
    momento = datetime(2026, 10, 22, 11, 0, tzinfo=timezone.utc)  # 8:00 en Chile
    asunto, texto = correo_falla("whatsapp-enviar", "Error: token vencido\n", momento=momento)
    assert asunto == "⚠️ Calza: falló la tarea whatsapp-enviar"
    assert "falló a las 22-10-2026 08:00" in texto and "no recibieron" in texto and "Error: token vencido" in texto
    assert "tail -n 80 logs/tareas.log" in texto
    asunto, texto = correo_falla("whatsapp-enviar", "", resuelta=True, momento=momento)
    assert asunto == "✅ Calza: la tarea whatsapp-enviar volvió a funcionar" and "No hay que hacer nada" in texto


def test_correo_de_falla_recorta_salidas_largas_y_tareas_desconocidas():
    _, texto = correo_falla("otra", "x" * (LARGO_DETALLE + 500) + "FINAL")
    assert "FINAL" in texto and "Qué significa" not in texto
    assert texto.count("x") <= LARGO_DETALLE


class CorreoFalso:
    enviados = []

    def __init__(self, *args):
        pass

    def enviar(self, para, asunto, texto, html=None, **kw):
        CorreoFalso.enviados.append((para, asunto, texto))


def test_comando_avisar_falla(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("SMTP_USUARIO", "hola@calza.cl")
    monkeypatch.setenv("SMTP_CLAVE", "x")
    monkeypatch.setenv("ALERTAS_EMAIL", "equipo@calza.cl")
    monkeypatch.setattr("licita.correo.ClienteCorreo", CorreoFalso)
    monkeypatch.setattr("sys.stdin", io.StringIO("Error: se cayó la API\n"))
    assert main(["avisar-falla", "ciclo"]) == 0
    para, asunto, texto = CorreoFalso.enviados[-1]
    assert para == "equipo@calza.cl" and "ciclo" in asunto and "se cayó la API" in texto
    assert main(["avisar-falla", "ciclo", "--resuelta"]) == 0
    assert "volvió a funcionar" in CorreoFalso.enviados[-1][1]


def test_resumen_falla_si_no_sale_ni_la_mitad(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    resultado = SimpleNamespace(plantillas=0, textos=1, sin_novedades=0, errores=0, correos=0, errores_correo=0)
    monkeypatch.setattr("licita.notificaciones.enviar_resumenes", lambda *a, **kw: resultado)
    assert main(["whatsapp-enviar"]) == 0
    resultado.errores = 1  # 1 error y 1 enviado: la mitad no salió
    assert main(["whatsapp-enviar"]) == 1
    resultado.textos = 5  # un número con problemas no es una falla
    assert main(["whatsapp-enviar"]) == 0


def test_salud_revisa_la_base_de_datos(Sesion):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from licita.servidor import crear_app

    assert TestClient(crear_app(Sesion)).get("/salud").json() == {"ok": True}
    caida = sessionmaker(create_engine("postgresql+psycopg://x:y@127.0.0.1:1/z", connect_args={"connect_timeout": 1}))
    r = TestClient(crear_app(caida)).get("/salud")
    assert r.status_code == 503 and r.json() == {"ok": False}
