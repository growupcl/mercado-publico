"""Migraciones de la base de datos con Alembic.

Cada cambio en los modelos (db.py) va acompañado de una migración en migraciones/versions, que
actualiza la base sin perder datos. Para crear una nueva:

    licita migrar --nueva "agrega campo X"

y revisar el archivo generado antes de publicarlo. Al iniciar, Calza aplica las pendientes.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config as ConfigAlembic
from sqlalchemy import create_engine, inspect, text

DIRECTORIO = Path(__file__).parent / "migraciones"
CANDADO_POSTGRES = 727274  # evita que dos procesos migren a la vez


def _config(conexion=None, url: str | None = None) -> ConfigAlembic:
    cfg = ConfigAlembic()
    cfg.set_main_option("script_location", str(DIRECTORIO))
    cfg.attributes["connection"] = conexion
    cfg.attributes["url"] = url
    return cfg


def migrar(database_url: str) -> None:
    """Deja la base en la última versión. Si fue creada antes de existir las migraciones, la marca como actual."""
    motor = create_engine(database_url)
    try:
        with motor.connect() as conexion:
            es_postgres = conexion.dialect.name == "postgresql"
            if es_postgres:
                conexion.execute(text(f"SELECT pg_advisory_lock({CANDADO_POSTGRES})"))
            try:
                tablas = set(inspect(conexion).get_table_names())
                cfg = _config(conexion)
                if "empresas" in tablas and "alembic_version" not in tablas:
                    command.stamp(cfg, "head")
                else:
                    command.upgrade(cfg, "head")
                conexion.commit()
            finally:
                if es_postgres:
                    conexion.execute(text(f"SELECT pg_advisory_unlock({CANDADO_POSTGRES})"))
                    conexion.commit()
    finally:
        motor.dispose()


def nueva_migracion(database_url: str, mensaje: str) -> None:
    """Genera una migración comparando los modelos con la base (que debe estar en la última versión)."""
    migrar(database_url)
    command.revision(_config(url=database_url), message=mensaje, autogenerate=True)
