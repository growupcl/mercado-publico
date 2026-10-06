import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    ticket: str
    database_url: str
    modelo_clasificacion: str

    @classmethod
    def desde_entorno(cls) -> "Config":
        return cls(
            ticket=os.environ.get("MERCADOPUBLICO_TICKET", ""),
            database_url=os.environ.get("DATABASE_URL", "sqlite:///licita.db"),
            modelo_clasificacion=os.environ.get("LICITA_MODELO_CLASIFICACION", "claude-haiku-4-5"),
        )
