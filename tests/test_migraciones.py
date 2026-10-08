from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from licita.db import Base, Empresa, crear_sesiones
from licita.migrar import _config, migrar


def _ultima_version() -> str:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(_config()).get_current_head()


def test_las_migraciones_crean_exactamente_los_modelos(tmp_path):
    """Si cambias un modelo en db.py sin crear su migración, esta prueba falla.

    Solución: licita migrar --nueva "descripción del cambio" y revisar el archivo generado.
    """
    url = f"sqlite:///{tmp_path}/calza.db"
    migrar(url)
    motor = create_engine(url)
    with motor.connect() as conexion:
        diferencias = compare_metadata(MigrationContext.configure(conexion, opts={"compare_type": True}), Base.metadata)
    assert diferencias == []


def test_migrar_desde_la_version_inicial_conserva_los_datos(tmp_path):
    from alembic import command
    from sqlalchemy import text

    url = f"sqlite:///{tmp_path}/calza.db"
    motor = create_engine(url)
    with motor.connect() as conexion:
        command.upgrade(_config(conexion), "0001")
        conexion.execute(text("INSERT INTO empresas (nombre, descripcion, regiones, monto_min, monto_max, palabras_clave, "
                              "whatsapp, whatsapp_activo, plan, rut, razon_social, giro, email, token_cuenta_hash, creada_en) "
                              "VALUES ('Aseo Sur', '', '[]', NULL, NULL, '[]', '', 1, 'pyme', '', '', '', '', '', '2026-10-01')"))
        conexion.commit()
    migrar(url)
    with motor.connect() as conexion:
        assert MigrationContext.configure(conexion).get_current_revision() == _ultima_version()
        assert conexion.execute(text("SELECT nombre FROM empresas")).scalar() == "Aseo Sur"
        assert "aviso_enviado_en" in {c["name"] for c in inspect(conexion).get_columns("pagos")}


def test_migrar_dos_veces_no_hace_nada(tmp_path):
    url = f"sqlite:///{tmp_path}/calza.db"
    migrar(url)
    migrar(url)
    assert "alembic_version" in inspect(create_engine(url)).get_table_names()


def test_base_creada_antes_de_las_migraciones_se_marca_como_actual(tmp_path):
    url = f"sqlite:///{tmp_path}/antigua.db"
    motor = create_engine(url)
    Base.metadata.create_all(motor)
    migrar(url)
    with motor.connect() as conexion:
        assert MigrationContext.configure(conexion).get_current_revision() == _ultima_version()


def test_crear_sesiones_en_archivo_usa_migraciones(tmp_path):
    Sesion = crear_sesiones(f"sqlite:///{tmp_path}/calza.db")
    with Sesion() as s:
        s.add(Empresa(nombre="Aseo Sur", descripcion="", regiones=[], palabras_clave=[]))
        s.commit()
        assert s.query(Empresa).count() == 1
