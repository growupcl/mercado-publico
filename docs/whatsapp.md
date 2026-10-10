# Integración con WhatsApp

Calza usa la **WhatsApp Cloud API de Meta** directamente: sin intermediarios y sin recargo por mensaje.

## Cómo se controla el costo

| Situación | Qué envía Calza | Costo WhatsApp |
|---|---|---|
| Resumen diario, el usuario no ha escrito en 24 h | 1 plantilla *utility* con 2 botones | ~US$0,02 |
| Resumen diario, el usuario escribió hace menos de 24 h | Resumen completo como texto | Gratis |
| El usuario toca un botón o responde un número | Detalle de la licitación | Gratis (ventana abierta) |
| El usuario envía el PDF de las bases o pregunta | Análisis y respuestas | Gratis en WhatsApp (el costo es de IA) |

Resultado: como máximo **una plantilla pagada al día por usuario**. Todo lo demás ocurre dentro de la ventana gratuita.

## Conversación

- Botón **Ver todas** → el resumen completo con las licitaciones numeradas.
- Botón **Ver la mejor** → el detalle de la licitación con mayor calce.
- Responder **1, 2, 3…** → el detalle de esa licitación del último resumen.
- **TODAS** → repite el último resumen.
- **PRECIOS** (después de abrir una licitación) → precios de referencia por ítem, rango competitivo y quién suele
  ganar. Es del plan Pro; en los demás planes se muestra una invitación con cuántos ítems tienen referencia.
- **Enviar el PDF de las bases** → análisis completo: requisitos que dejan fuera, documentos, garantías, criterios
  de evaluación, plazos, multas y preguntas para el foro. Si el usuario venía de ver el detalle de una licitación,
  el análisis queda asociado a esa licitación.
- **Preguntas en texto libre** durante las 24 horas siguientes al análisis → respuesta basada en esas bases, con páginas.
- **BAJA** / **ALTA** → deja de recibir o vuelve a recibir resúmenes (Meta exige permitir la baja).
- Cualquier otro texto → mensaje de ayuda.

## Configuración en Meta (cómo está hoy)

| Pieza | Valor |
|---|---|
| Portafolio de Meta | **Calza** (ID 28242079118797984), razón social Virtus SpA, dominio `calza.cl` verificado |
| Cuenta de WhatsApp Business | ID **1089829487170220** (nombre interno "Masivo App", conviene renombrarla "Calza") |
| Número | **+56 9 5787 5125**, nombre visible "Calza", ID **1302990632906843** |
| App de Meta | **Calza** (ID 2210923413125483), publicada, del portafolio Calza. Acceso estándar: no requiere revisión |
| Token | usuario del sistema `calza-servidor` (administrador), token sin vencimiento con `whatsapp_business_messaging` y `whatsapp_business_management` |
| Webhook | `https://calza.cl/webhook/whatsapp`, campo **messages** suscrito |
| Pago a Meta | tarjeta de Virtus SpA en la cuenta de WhatsApp (IVA autoliquidado, ver el F29) |

El número se creó con el registro integrado de Masivo App ([whatsapp-masivo-app.md](whatsapp-masivo-app.md)), pero
Calza usa **su propia app de Meta**: así no comparte el token ni la clave secreta de la app de Masivo. La app de Masivo
también quedó suscrita a la cuenta y recibe los mismos mensajes. No debe responderlos: su bot tiene que estar apagado
para el cliente Calza.

### Variables del `.env`

| Variable | De dónde sale |
|---|---|
| `WHATSAPP_TOKEN` | business.facebook.com/settings → Usuarios del sistema → `calza-servidor` → Generar token (app Calza, sin vencimiento) |
| `WHATSAPP_PHONE_NUMBER_ID` | `1302990632906843` |
| `WHATSAPP_WABA_ID` | `1089829487170220` |
| `WHATSAPP_APP_SECRET` | developers.facebook.com → app **Calza** → Configuración → Básica → Clave secreta (32 caracteres hexadecimales) |
| `WHATSAPP_VERIFY_TOKEN` | texto aleatorio (`openssl rand -hex 24`), el mismo que se puso al configurar el webhook en Meta |
| `LICITA_WHATSAPP_PUBLICO` | `56957875125` (botón "Abrir WhatsApp" del sitio) |

Hay que cargar todas a la vez: con `WHATSAPP_TOKEN` pero sin `WHATSAPP_APP_SECRET` o `WHATSAPP_VERIFY_TOKEN`, la
app no arranca. Después: `docker compose up -d app` (`restart` no vuelve a leer el `.env`).

### Si algo falla

- **El log muestra `POST /webhook/whatsapp ... 401`**: `WHATSAPP_APP_SECRET` no es la clave secreta de la app
  Calza (por ejemplo, quedó el token, que mide unos 200 caracteres, o la clave de otra app). Meta reintenta los
  mensajes rechazados, así que llegan todos juntos al corregirla.
- **No llega nada al webhook**: revisa que la app esté publicada y suscrita a la cuenta
  (`GET /1089829487170220/subscribed_apps` con el token debe listar la app Calza).
- **Llegan dos respuestas distintas**: el bot de Masivo App está respondiendo. Apágalo para el cliente Calza.
- **Rotar el token** (por ejemplo, si se filtró): genera uno nuevo para `calza-servidor`, cámbialo en el `.env` y
  aplica con `docker compose up -d app`. Para invalidar el anterior, en el usuario del sistema → Revocar tokens.

### Desde cero (para otra instalación)

1. App de Meta tipo empresa en developers.facebook.com, en el portafolio de la empresa, con el caso de uso
   "Conectarte con los clientes a través de WhatsApp".
2. Número registrado en la cuenta de WhatsApp Business (no puede estar en la app de WhatsApp) y método de pago.
3. Usuario del sistema administrador con la app y la cuenta de WhatsApp asignadas (control total), y token sin
   vencimiento con `whatsapp_business_messaging` y `whatsapp_business_management`.
4. Cargar las variables de arriba y levantar el servidor.
5. Webhook en la app: URL `https://<dominio>/webhook/whatsapp`, el `WHATSAPP_VERIFY_TOKEN` y el campo **messages**.
6. Suscribir la app a la cuenta: `POST /<WABA_ID>/subscribed_apps` con el token.
7. Publicar la app (privacidad `/privacidad`, términos `/terminos`, eliminación de datos `/privacidad`, ícono).
8. Enviar las plantillas a revisión (abajo).

## Plantillas

Calza usa cuatro plantillas fijas que se envían a revisión todas juntas, una sola vez:
ver [plantillas-whatsapp.md](plantillas-whatsapp.md) (`licita whatsapp-plantillas --crear`).

## Envío diario

Programar `licita whatsapp-enviar` una vez al día, por ejemplo a las 8:00, después de `licita sync`, `licita clasificar` y `licita calce` de cada empresa.
