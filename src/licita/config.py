import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    ticket: str
    database_url: str
    modelo_clasificacion: str
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_waba_id: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_api_version: str = "v23.0"
    whatsapp_plantilla_resumen: str = "resumen_diario_licitaciones"
    whatsapp_idioma: str = "es"
    url_registro: str = ""
    modelo_analisis: str = "claude-sonnet-5-5"
    dir_documentos: str = "documentos"
    limite_analisis_mes: int = 15
    url_publica: str = "http://localhost:8000"
    whatsapp_publico: str = ""
    mercadopago_access_token: str = ""
    mercadopago_webhook_secret: str = ""
    prestador: str = "Virtus SpA"

    @classmethod
    def desde_entorno(cls) -> "Config":
        return cls(
            ticket=os.environ.get("MERCADOPUBLICO_TICKET", ""),
            database_url=os.environ.get("DATABASE_URL", "sqlite:///licita.db"),
            modelo_clasificacion=os.environ.get("LICITA_MODELO_CLASIFICACION", "claude-haiku-4-5"),
            whatsapp_token=os.environ.get("WHATSAPP_TOKEN", ""),
            whatsapp_phone_number_id=os.environ.get("WHATSAPP_PHONE_NUMBER_ID", ""),
            whatsapp_waba_id=os.environ.get("WHATSAPP_WABA_ID", ""),
            whatsapp_verify_token=os.environ.get("WHATSAPP_VERIFY_TOKEN", ""),
            whatsapp_app_secret=os.environ.get("WHATSAPP_APP_SECRET", ""),
            whatsapp_api_version=os.environ.get("WHATSAPP_API_VERSION", "v23.0"),
            whatsapp_plantilla_resumen=os.environ.get("WHATSAPP_PLANTILLA_RESUMEN", "resumen_diario_licitaciones"),
            whatsapp_idioma=os.environ.get("WHATSAPP_IDIOMA", "es"),
            url_registro=os.environ.get("LICITA_URL_REGISTRO", ""),
            modelo_analisis=os.environ.get("LICITA_MODELO_ANALISIS", "claude-sonnet-5-5"),
            dir_documentos=os.environ.get("LICITA_DIR_DOCUMENTOS", "documentos"),
            limite_analisis_mes=int(os.environ.get("LICITA_LIMITE_ANALISIS_MES", "15")),
            url_publica=os.environ.get("LICITA_URL_PUBLICA", "http://localhost:8000").rstrip("/"),
            whatsapp_publico=os.environ.get("LICITA_WHATSAPP_PUBLICO", ""),
            mercadopago_access_token=os.environ.get("MERCADOPAGO_ACCESS_TOKEN", ""),
            mercadopago_webhook_secret=os.environ.get("MERCADOPAGO_WEBHOOK_SECRET", ""),
            prestador=os.environ.get("LICITA_PRESTADOR", "Virtus SpA"),
        )
