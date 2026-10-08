"""Entorno de Alembic. Se ejecuta desde licita.migrar (no hace falta alembic.ini)."""

from alembic import context
from sqlalchemy import create_engine

from licita.db import Base

config = context.config
target_metadata = Base.metadata


def _ejecutar(conexion) -> None:
    # render_as_batch: permite alterar tablas también en SQLite (desarrollo).
    context.configure(connection=conexion, target_metadata=target_metadata, render_as_batch=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


conexion = config.attributes.get("connection")
if conexion is not None:
    _ejecutar(conexion)
else:
    with create_engine(config.attributes["url"]).connect() as conexion:
        _ejecutar(conexion)
