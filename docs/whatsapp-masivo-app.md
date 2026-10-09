# Configurar WhatsApp de Calza a través de Masivo App (Tech Provider)

Durante el piloto, Calza es un servicio de **Virtus SpA** (la misma empresa que cobra y factura), así que su cuenta de
WhatsApp va en el **portafolio de negocio de Virtus SpA en Meta**, separado del de Masivo App. Se conecta a WhatsApp a
través de la app de Masivo App, que es **Tech Provider**: Calza entra como un cliente más mediante el **registro
integrado** (*Embedded Signup*) de Masivo. Cuando exista Calza SpA, el número se migra a su portafolio (ver el final).

Calza se conecta directo a la Cloud API de Meta con el token que entrega ese registro, así que **no hay cambios de código**,
solo configuración. Para la descripción general de la integración y del cobro, ver [whatsapp.md](whatsapp.md).

```
Portafolio de Masivo App                       Portafolio de Virtus SpA (distinto del de Masivo App)
└── App de Meta de Masivo App  ──acceso──►     └── Cuenta de WhatsApp Business "Calza"
    (Tech Provider)          (registro              └── Número de Calza
                              integrado)                 └── Webhook → https://calza.cl/webhook/whatsapp
```

Qué gana Calza con esto:
- **Riesgos separados:** una sanción de Meta a Calza no toca el portafolio de Masivo App ni a sus clientes, y al revés.
  Por eso el portafolio de Virtus SpA **no debe ser el mismo** que usa Masivo App.
- **Identidad correcta:** la verificación y la facturación de Meta quedan a nombre de Virtus SpA, la empresa que presta
  el servicio, y el nombre visible es **Calza**.
- **Sin esperas de certificación:** Calza aprovecha la app ya aprobada de Masivo App y no necesita la suya.
- **Salida fácil:** si algún día Calza quiere su propia app de Meta, se conecta la misma cuenta a esa app y basta cambiar el token.

## Antes de empezar

- [ ] Los documentos de **Virtus SpA** (razón social, RUT, dirección) y el correo `hola@calza.cl`.
- [ ] El **sitio web** `calza.cl` mostrando que Calza es un servicio de Virtus SpA (está en el pie de página y en los
      términos). Meta lo revisa para aprobar el nombre visible "Calza", distinto de la razón social.
- [ ] Un **número de teléfono nuevo** para Calza que reciba SMS o llamadas y que **no esté registrado en WhatsApp**.
- [ ] Una **URL pública con HTTPS** donde correrá `licita servidor`. Para pruebas sirve un túnel como `cloudflared` o `ngrok`.
- [ ] Un **acuerdo simple entre Virtus SpA y la empresa de Masivo App**: servicio de conexión a WhatsApp y tratamiento de datos.
      La app de Masivo tiene acceso técnico a los mensajes de Calza, y con la Ley 21.719 conviene dejarlo por escrito.

## Paso 1. Crear y verificar el portafolio de negocio de Virtus SpA

1. Con la cuenta de Facebook del representante de Virtus SpA, entra a [business.facebook.com](https://business.facebook.com).
   Si Virtus ya tiene un portafolio que **no** es el de Masivo App, úsalo. Si no, crea **Virtus SpA** con la razón social
   exacta, el sitio web `https://calza.cl` y el correo `hola@calza.cl`.
2. **Verifica el dominio** `calza.cl` (Configuración del negocio → Seguridad de la marca → Dominios) con un registro
   DNS TXT en Vultr (Network → DNS → calza.cl).
3. **Inicia la verificación del negocio** (Centro de seguridad → Verificación del negocio). Para Chile suelen servir:
   - Un documento que acredite la razón social y el RUT: escritura o certificado de constitución, o el e-RUT del SII.
   - Un documento con la dirección: una boleta de servicios o un certificado a nombre de la empresa.
   - El nombre, la dirección y el sitio web deben coincidir **exactamente** con lo que dicen los documentos.
4. La verificación puede tardar desde horas hasta un par de semanas. Puedes avanzar con los pasos siguientes mientras tanto:
   sin verificar se puede probar, pero con límites más bajos de envío y de números.

## Paso 2. Conectar Calza mediante el registro integrado de Masivo App

Es el mismo flujo que usan los clientes de Masivo App:

1. Abre el **enlace o botón de registro integrado** de Masivo App y entra con la cuenta de Facebook del administrador de **Calza**
   (no con la de Masivo).
2. En el asistente:
   - Elige el portafolio **Virtus SpA**.
   - Crea una cuenta de WhatsApp Business nueva llamada **Calza**.
   - Agrega el número nuevo y verifícalo con el código por SMS o llamada.
   - Define el nombre visible **Calza** y la categoría (por ejemplo "Servicios profesionales"). Meta lo revisa: si lo
     rechaza por no coincidir con Virtus SpA, apela indicando que `calza.cl` muestra "Calza es un servicio de Virtus SpA".
3. Al terminar, el backend de Masivo App hace lo mismo que con cualquier cliente:
   - Canjea el código del registro por el **token de integración del negocio**, que da acceso solo a los activos de Calza.
   - **Registra el número** en la Cloud API, con un PIN de 6 dígitos para la verificación en dos pasos.
   - **Suscribe la app** a la cuenta de WhatsApp Business de Calza para recibir eventos (en el paso 5 se define la URL).
4. Obtén y anota:
   - El **token de integración del negocio** de Calza → `WHATSAPP_TOKEN`. Pídelo al backend de Masivo App o al registro del canje.
     Es un secreto: compártelo por un canal seguro, nunca por chat ni correo sin cifrar.
   - El **ID de la cuenta de WhatsApp Business** (`WABA_ID`).
   - El **ID del número de teléfono** → `WHATSAPP_PHONE_NUMBER_ID`.

Si el backend de Masivo App no registró el número, hazlo a mano con el token de Calza:

```bash
curl -X POST "https://graph.facebook.com/v23.0/$PHONE_NUMBER_ID/register" \
  -H "Authorization: Bearer $WHATSAPP_TOKEN" -H "Content-Type: application/json" \
  -d '{"messaging_product": "whatsapp", "pin": "123456"}'
```

## Paso 3. Método de pago a nombre de Calza

Como cliente de un Tech Provider, Virtus SpA le paga **directo a Meta**:

1. En **WhatsApp Manager** del portafolio de Virtus SpA → Configuración de pagos, agrega la tarjeta de Virtus SpA.
2. Los cargos (unos US$0,02 por plantilla utility en Chile) quedan a nombre de Virtus SpA, sin pasar por Masivo App.

## Paso 4. Enviar las plantillas a revisión

Meta demora cerca de 24 a 36 horas en aprobarlas, así que envíalas **hoy mismo**, todas juntas. El detalle de cada
plantilla está en [plantillas-whatsapp.md](plantillas-whatsapp.md). Con `WHATSAPP_TOKEN` y `WHATSAPP_WABA_ID` en el `.env`:

```bash
docker compose exec app licita whatsapp-plantillas --crear
```

## Paso 5. Webhook: que los mensajes de Calza lleguen a Calza

Por defecto, Meta envía los eventos de la cuenta de Calza a la URL de webhook de la app de Masivo App. Hay dos formas de dirigirlos a Calza.

### Opción recomendada: URL propia para la cuenta de Calza

Meta permite que una cuenta de WhatsApp Business use una URL distinta de la principal de la app.
Revisa la sección *Webhooks overrides* de la documentación de Meta por si cambió el formato.

1. El servidor de Calza ya está en producción en `https://calza.cl`. Primero carga en el `.env` `WHATSAPP_TOKEN`,
   `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_WABA_ID`, `WHATSAPP_APP_SECRET` y `WHATSAPP_VERIFY_TOKEN`, y aplica con
   `docker compose up -d app`: sin `WHATSAPP_TOKEN` el webhook de WhatsApp no se activa.

2. Indica la URL y el token de verificación de Calza en la suscripción de la app a la cuenta de Calza:

```bash
curl -X POST "https://graph.facebook.com/v23.0/$WABA_ID/subscribed_apps" \
  -H "Authorization: Bearer $WHATSAPP_TOKEN" -H "Content-Type: application/json" \
  -d '{"override_callback_uri": "https://calza.cl/webhook/whatsapp",
       "verify_token": "'"$WHATSAPP_VERIFY_TOKEN"'"}'
```

3. Meta llama a la URL para verificarla. Si `licita servidor` está corriendo, responde solo.

Ojo con la firma: Meta firma cada evento con el **app secret de la app de Masivo App**, y Calza necesita ese mismo
valor en `WHATSAPP_APP_SECRET` para verificar que los mensajes vienen de Meta. Hoy controlas ambas empresas, así que
es aceptable, pero queda una dependencia:
- Guárdalo solo en el gestor de secretos del servidor de Calza.
- Si Masivo App rota su app secret, hay que actualizarlo también en Calza.
- Si las empresas llegan a tener dueños distintos, cambia a la opción de reenvío de abajo o crea una app de Meta propia para Calza.

### Alternativa: Masivo App reenvía los eventos de Calza

Úsala si no quieres que Calza conozca el app secret de Masivo App:

1. El webhook de Masivo App identifica los eventos de Calza por `entry[].changes[].value.metadata.phone_number_id`.
2. Los reenvía a `https://calza.cl/webhook/whatsapp` con el **cuerpo original sin modificar**
   y con una **firma nueva** calculada con un secreto compartido solo entre Masivo y Calza:
   `X-Hub-Signature-256: sha256=HMAC_SHA256(secreto_compartido, cuerpo)`.
3. En Calza, `WHATSAPP_APP_SECRET` toma ese secreto compartido. El código de Calza verifica la firma igual que si viniera de Meta.

El costo de esta opción: si Masivo App se cae, Calza deja de recibir mensajes.

Con cualquiera de las dos, Calza **ignora los mensajes dirigidos a otros números** (filtra por `WHATSAPP_PHONE_NUMBER_ID`).

## Paso 6. Variables de entorno de Calza

| Variable | De dónde sale |
|---|---|
| `WHATSAPP_TOKEN` | Token de integración del negocio de Calza (paso 2) |
| `WHATSAPP_PHONE_NUMBER_ID` | ID del número de Calza (paso 2) |
| `WHATSAPP_WABA_ID` | ID de la cuenta de WhatsApp Business de Calza (paso 2) |
| `WHATSAPP_APP_SECRET` | App secret de la app de Masivo App (webhook propio) o el secreto compartido (reenvío) |
| `WHATSAPP_VERIFY_TOKEN` | Un texto secreto que tú inventas, el mismo que usaste en el paso 5 |
| `WHATSAPP_PLANTILLA_RESUMEN` | `resumen_diario_licitaciones` |
| `WHATSAPP_IDIOMA` | `es` |
| `LICITA_URL_REGISTRO` | Página de registro de Calza (se envía a números no registrados) |

## Paso 7. Prueba de punta a punta

1. Registra una empresa de prueba con **tu propio celular**:

```bash
licita empresa-agregar --nombre "Empresa de prueba" --whatsapp "+56 9 XXXX XXXX" \
  --descripcion "Vendemos insumos de aseo y limpieza a instituciones"
```

2. Genera calces (`licita sync`, `licita clasificar`, `licita calce --empresa N`) y envía el resumen:

```bash
licita whatsapp-enviar
```

3. Revisa en tu celular:
   - [ ] Llega la plantilla con los botones **Ver todas** y **Ver la mejor**, enviada por **Calza**.
   - [ ] Al tocar **Ver la mejor**, llega el detalle de la licitación.
   - [ ] Al responder **1**, llega el detalle de la primera licitación.
   - [ ] Al escribir **BAJA**, se confirma la baja; con **ALTA**, se reactiva.
4. Revisa los registros del servidor: cada mensaje entrante y saliente queda en la tabla `mensajes_whatsapp`.
5. Confirma que en WhatsApp Manager el cargo aparece en el portafolio de **Virtus SpA**, no en el de Masivo App.

## Cuidados para mantener el número sano

- **Consentimiento:** solo envía resúmenes a empresas que aceptaron recibir mensajes de Calza por WhatsApp.
  En la web de registro habrá una casilla de consentimiento; durante el piloto, pide la confirmación por escrito.
- **Calidad del número:** si muchos usuarios bloquean o reportan, Meta baja la calificación y limita los envíos.
  Por eso existe la palabra **BAJA** y por eso el resumen se envía solo cuando hay calces de al menos 60%.
- **Límite de mensajes:** revisa en WhatsApp Manager el límite diario de conversaciones iniciadas por la empresa.
  Sube con la verificación del negocio y con un buen historial de calidad. Para un piloto de 20 a 30 empresas sobra.

## Cuando exista Calza SpA

1. Crea y verifica el portafolio **Calza SpA** (paso 1, con sus documentos).
2. **Migra el número** desde la cuenta de WhatsApp Business del portafolio de Virtus a una cuenta nueva en el de Calza SpA.
   Meta lo permite entre portafolios, y el número y su calificación de calidad se mantienen.
3. Las **plantillas no se migran**: créalas de nuevo con `licita whatsapp-plantillas --crear` y espera la aprobación.
4. Actualiza `WHATSAPP_TOKEN` y `WHATSAPP_WABA_ID` (y `WHATSAPP_PHONE_NUMBER_ID` si cambia), junto con
   `LICITA_PRESTADOR` y las credenciales de Mercado Pago (ver [mercadopago.md](mercadopago.md)).
