"""Plantillas de WhatsApp de Calza: fuente única del texto que se envía a aprobación y del que se usa.

Meta demora cerca de 24 a 36 horas en aprobar cada plantilla y cualquier edición vuelve a revisión,
por eso se envían todas juntas una vez (`licita whatsapp-plantillas --crear`) y no se modifican.

Reglas que siguen para evitar rechazos: categoría UTILITY (información que el cliente pidió, sin
promociones), el cuerpo no empieza ni termina con una variable, hay texto suficiente alrededor de
las variables y cada una trae un ejemplo realista.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

IDIOMA = "es"


@dataclass(frozen=True)
class Plantilla:
    nombre: str
    cuerpo: str
    ejemplos: tuple[str, ...]  # un ejemplo por variable {{1}}, {{2}}…
    botones: tuple[str, ...]  # textos de los botones de respuesta rápida (máx. 20 caracteres)
    uso: str

    @property
    def variables(self) -> int:
        return len(self.ejemplos)

    def definicion_meta(self, idioma: str = IDIOMA) -> dict[str, Any]:
        """Cuerpo de la solicitud POST /{WABA_ID}/message_templates."""
        componentes: list[dict[str, Any]] = [
            {"type": "BODY", "text": self.cuerpo, "example": {"body_text": [list(self.ejemplos)]}},
        ]
        if self.botones:
            componentes.append({"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": b} for b in self.botones]})
        return {"name": self.nombre, "language": idioma, "category": "UTILITY", "components": componentes}


RESUMEN_DIARIO = Plantilla(
    nombre="resumen_diario_licitaciones",
    cuerpo=("Hola {{1}}, hoy encontramos {{2}} que calzan con tu negocio. La mejor: {{3}} ({{4}}% de calce), "
            "cierra el {{5}}. Toca un botón para ver el detalle."),
    ejemplos=("Aseo Sur", "3 licitaciones nuevas", "Adquisición de insumos de aseo para CESFAM", "92", "20-10-2026 15:00"),
    botones=("Ver todas", "Ver la mejor"),
    uso="Resumen diario cuando la ventana de 24 h está cerrada.",
)

RESUMEN_DIARIO_SIMPLE = Plantilla(
    nombre="resumen_diario_simple",
    cuerpo=("Hola {{1}}, tu resumen de hoy está listo: encontramos {{2}} que calzan con tu negocio en "
            "Mercado Público. Toca el botón para verlas."),
    ejemplos=("Aseo Sur", "3 licitaciones nuevas"),
    botones=("Ver licitaciones",),
    uso="Respaldo del resumen diario, con menos variables, por si Meta rechaza la plantilla principal.",
)

COBRO_RECHAZADO = Plantilla(
    nombre="cobro_rechazado",
    cuerpo=("Hola {{1}}, no pudimos cobrar tu plan {{2}} de Calza con la tarjeta registrada en Mercado Pago. "
            "Para seguir recibiendo tus licitaciones, revisa tu medio de pago antes del {{3}}. "
            "Toca el botón y te enviamos el enlace a tu cuenta."),
    ejemplos=("Aseo Sur", "Pyme mensual", "23 de octubre de 2026"),
    botones=("Ver mi cuenta",),
    uso="Aviso cuando Mercado Pago rechaza un cobro de la suscripción.",
)

ALERTA_COMPRA_AGIL = Plantilla(
    nombre="alerta_compra_agil",
    cuerpo=("Hola {{1}}, hay una Compra Ágil que calza con tu negocio: {{2}} ({{3}}% de calce). "
            "El plazo para cotizar cierra el {{4}}. Toca el botón para ver el detalle."),
    ejemplos=("Aseo Sur", "Compra de guantes de nitrilo para CESFAM", "95", "09-10-2026 18:00"),
    botones=("Ver detalle",),
    uso="Aviso urgente de Compra Ágil (plan Pro), apenas se confirma el calce.",
)

PLANTILLAS = {p.nombre: p for p in (RESUMEN_DIARIO, RESUMEN_DIARIO_SIMPLE, COBRO_RECHAZADO, ALERTA_COMPRA_AGIL)}

# Qué responde Calza cuando el usuario toca cada botón (el payload se define al enviar).
PAYLOADS_RESUMEN = {
    "resumen_diario_licitaciones": ("VER_TODAS", "DETALLE:{codigo}"),
    "resumen_diario_simple": ("VER_TODAS",),
    "alerta_compra_agil": ("DETALLE:{codigo}",),
}
