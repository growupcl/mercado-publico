from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def ahora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Licitacion(Base):
    __tablename__ = "licitaciones"

    codigo: Mapped[str] = mapped_column(String(40), primary_key=True)
    nombre: Mapped[str] = mapped_column(Text, default="")
    descripcion: Mapped[str] = mapped_column(Text, default="")
    estado_codigo: Mapped[int | None] = mapped_column(Integer, index=True)
    estado: Mapped[str] = mapped_column(String(40), default="")
    tipo: Mapped[str] = mapped_column(String(10), default="")
    organismo: Mapped[str] = mapped_column(Text, default="")
    unidad: Mapped[str] = mapped_column(Text, default="")
    region: Mapped[str] = mapped_column(String(120), default="")
    comuna: Mapped[str] = mapped_column(String(120), default="")
    monto_estimado: Mapped[float | None] = mapped_column(Float)
    moneda: Mapped[str] = mapped_column(String(10), default="")
    fecha_publicacion: Mapped[datetime | None] = mapped_column(DateTime)
    fecha_cierre: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Clasificación con IA: se hace una sola vez por licitación y se comparte entre usuarios.
    clasificacion: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    clasificada_en: Mapped[datetime | None] = mapped_column(DateTime)
    actualizada_en: Mapped[datetime] = mapped_column(DateTime, default=ahora, onupdate=ahora)


class OrdenCompra(Base):
    __tablename__ = "ordenes_compra"

    codigo: Mapped[str] = mapped_column(String(40), primary_key=True)
    nombre: Mapped[str] = mapped_column(Text, default="")
    estado: Mapped[str] = mapped_column(String(60), default="")
    codigo_licitacion: Mapped[str | None] = mapped_column(String(40), index=True)
    organismo: Mapped[str] = mapped_column(Text, default="")
    region: Mapped[str] = mapped_column(String(120), default="")
    proveedor_rut: Mapped[str] = mapped_column(String(20), default="", index=True)
    proveedor_nombre: Mapped[str] = mapped_column(Text, default="")
    total: Mapped[float | None] = mapped_column(Float)
    total_neto: Mapped[float | None] = mapped_column(Float)
    moneda: Mapped[str] = mapped_column(String(10), default="")
    fecha_envio: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actualizada_en: Mapped[datetime] = mapped_column(DateTime, default=ahora, onupdate=ahora)


class Empresa(Base):
    """Cliente de Licita Inteligente: una pyme proveedora y su perfil de búsqueda."""

    __tablename__ = "empresas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(200))
    descripcion: Mapped[str] = mapped_column(Text)
    regiones: Mapped[list[str]] = mapped_column(JSON, default=list)
    monto_min: Mapped[float | None] = mapped_column(Float)
    monto_max: Mapped[float | None] = mapped_column(Float)
    palabras_clave: Mapped[list[str]] = mapped_column(JSON, default=list)
    whatsapp: Mapped[str] = mapped_column(String(20), default="", index=True)
    whatsapp_activo: Mapped[bool] = mapped_column(Boolean, default=True)
    # Último mensaje recibido del usuario: abre la ventana de 24 h en que responder es gratis.
    ultimo_mensaje_entrante: Mapped[datetime | None] = mapped_column(DateTime)
    # Contexto de la conversación: última licitación vista y último análisis de bases enviado.
    licitacion_activa: Mapped[str | None] = mapped_column(String(40))
    analisis_activo_id: Mapped[int | None] = mapped_column(Integer)
    contexto_actualizado_en: Mapped[datetime | None] = mapped_column(DateTime)
    creada_en: Mapped[datetime] = mapped_column(DateTime, default=ahora)


class Calce(Base):
    """Resultado de evaluar una licitación para una empresa."""

    __tablename__ = "calces"
    __table_args__ = (UniqueConstraint("empresa_id", "licitacion_codigo"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    licitacion_codigo: Mapped[str] = mapped_column(ForeignKey("licitaciones.codigo"))
    puntaje: Mapped[int] = mapped_column(Integer)
    razon: Mapped[str] = mapped_column(Text, default="")
    puntaje_prefiltro: Mapped[float] = mapped_column(Float, default=0)
    notificado: Mapped[bool] = mapped_column(Boolean, default=False)
    notificado_en: Mapped[datetime | None] = mapped_column(DateTime)
    # Número con que apareció en el último resumen ("responde 1, 2, 3...").
    posicion_resumen: Mapped[int | None] = mapped_column(Integer)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora)


class MensajeWhatsApp(Base):
    """Registro de mensajes enviados y recibidos (auditoría y control de costos)."""

    __tablename__ = "mensajes_whatsapp"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa_id: Mapped[int | None] = mapped_column(ForeignKey("empresas.id"), index=True)
    telefono: Mapped[str] = mapped_column(String(20))
    direccion: Mapped[str] = mapped_column(String(10))  # "entrante" o "saliente"
    tipo: Mapped[str] = mapped_column(String(20))  # "texto", "plantilla", "boton"
    contenido: Mapped[str] = mapped_column(Text, default="")
    wamid: Mapped[str] = mapped_column(String(120), default="")
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora)


class Documento(Base):
    """PDF de bases. Se identifica por su hash: el mismo archivo se analiza una sola vez."""

    __tablename__ = "documentos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    nombre_archivo: Mapped[str] = mapped_column(String(255), default="")
    ruta: Mapped[str] = mapped_column(Text)
    tamano: Mapped[int] = mapped_column(Integer)
    licitacion_codigo: Mapped[str | None] = mapped_column(String(40), index=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora)


class AnalisisBases(Base):
    """Resultado del análisis con IA de un documento de bases, compartido entre usuarios."""

    __tablename__ = "analisis_bases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    documento_id: Mapped[int] = mapped_column(ForeignKey("documentos.id"), unique=True)
    licitacion_codigo: Mapped[str | None] = mapped_column(String(40), index=True)
    modelo: Mapped[str] = mapped_column(String(60))
    resultado: Mapped[dict[str, Any]] = mapped_column(JSON)
    tokens_entrada: Mapped[int] = mapped_column(Integer, default=0)
    tokens_salida: Mapped[int] = mapped_column(Integer, default=0)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora)


class SolicitudAnalisis(Base):
    """Cada análisis entregado a una empresa (para el límite mensual de su plan)."""

    __tablename__ = "solicitudes_analisis"
    __table_args__ = (UniqueConstraint("empresa_id", "analisis_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    analisis_id: Mapped[int] = mapped_column(ForeignKey("analisis_bases.id"))
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora)


def crear_sesiones(database_url: str) -> sessionmaker:
    if database_url in ("sqlite://", "sqlite:///:memory:"):
        # Base en memoria (pruebas): una sola conexión compartida entre hilos.
        engine = create_engine(database_url, poolclass=StaticPool, connect_args={"check_same_thread": False})
    else:
        engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
