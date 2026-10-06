"""Textos de términos y privacidad.

BORRADOR: deben ser revisados por un abogado antes del lanzamiento (Ley 19.628 y Ley 21.719
de protección de datos personales, Ley 19.496 del consumidor en lo aplicable).
"""

FECHA = "octubre de 2026"

def terminos(prestador: str) -> list:
    return [
    ("Quién presta el servicio", [
        f"Durante el período piloto, el servicio Calza es prestado y facturado por {prestador}.",
    ]),
    ("El servicio", [
        "Calza es un servicio que revisa la información pública de Mercado Público y le envía a cada empresa las "
        "licitaciones que coinciden con su perfil, junto con análisis y referencias generados con apoyo de inteligencia artificial.",
        "Calza es un servicio independiente y no está afiliado a ChileCompra ni a Mercado Público.",
    ]),
    ("Alcance de la información", [
        "Los resúmenes, puntajes de calce, análisis de bases y precios de referencia son una ayuda para decidir. "
        "No reemplazan la lectura de las bases oficiales ni constituyen asesoría legal. Calza no garantiza la adjudicación de ninguna licitación.",
        "Ante cualquier diferencia, prevalece lo publicado en www.mercadopublico.cl.",
    ]),
    ("Prueba, planes y pagos", [
        "Al registrarte tienes 14 días de prueba gratuita con las funciones del plan Pro, sin necesidad de tarjeta.",
        "Los planes se pagan con una suscripción de Mercado Pago (tarjeta de crédito o débito) que se renueva "
        "automáticamente cada mes o cada año. Si te suscribes durante la prueba, el primer cobro es al terminar la prueba. "
        "Los precios publicados incluyen IVA.",
        "Puedes cancelar la renovación cuando quieras desde tu cuenta. Mantienes el plan hasta el final del período pagado "
        "y luego tu cuenta pasa al plan gratis.",
        "El precio fundador del plan Pro se mantiene mientras tu suscripción siga vigente sin interrupciones.",
    ]),
    ("Uso aceptable", [
        "No puedes revender el servicio ni usarlo para enviar comunicaciones no solicitadas.",
    ]),
    ("Contacto", ["hola@calza.cl"]),
    ]


def privacidad(prestador: str) -> list:
    return [
    ("Responsable", [
        f"Durante el período piloto, el responsable del tratamiento de tus datos es {prestador}, que presta el servicio Calza.",
    ]),
    ("Qué datos recopilamos", [
        "Datos de tu empresa (nombre, RUT, razón social, giro), datos de contacto (correo y WhatsApp), la descripción de tu "
        "negocio y tus preferencias, los documentos que nos envías (por ejemplo, bases de licitación) y los mensajes que intercambias con Calza.",
    ]),
    ("Para qué los usamos", [
        "Para seleccionar las licitaciones que te calzan, enviarte el resumen diario, responder tus consultas, emitir tu factura y administrar tu cuenta.",
        "La descripción de tu negocio, los documentos y tus preguntas se procesan con proveedores de inteligencia artificial "
        "y de mensajería (Anthropic y Meta/WhatsApp) solo para prestar el servicio. Los pagos los procesa Mercado Pago: "
        "Calza no ve ni guarda los datos de tu tarjeta.",
    ]),
    ("WhatsApp", [
        "Solo te escribimos si lo autorizaste al registrarte. Puedes dejar de recibir mensajes en cualquier momento escribiendo BAJA.",
    ]),
    ("Tus derechos", [
        "Puedes solicitar acceso, rectificación, eliminación u oposición al tratamiento de tus datos escribiendo a hola@calza.cl.",
    ]),
    ("Conservación", [
        "Conservamos tus datos mientras tengas una cuenta y el tiempo que exijan las obligaciones tributarias.",
    ]),
    ]
