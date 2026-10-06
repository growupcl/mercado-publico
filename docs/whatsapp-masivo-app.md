# Configurar WhatsApp de Licita con la app de Meta de Masivo App

Esta guía corresponde a la **opción A**: Licita usa la app de Meta que Masivo App ya tiene certificada
(Tech Provider), con un **número y una cuenta de WhatsApp Business propios de Licita**.
Licita se conecta directo a la Cloud API de Meta, así que no hay cambios de código: solo configuración.

Para la descripción general de la integración y del cobro, ver [whatsapp.md](whatsapp.md).

```
Portafolio de negocio de Masivo App (ya verificado)
└── App de Meta de Masivo App (Tech Provider)
    ├── Cuentas de WhatsApp Business de Masivo y sus clientes (sin cambios)
    └── Cuenta de WhatsApp Business "Licita"          ← nueva
        └── Número de Licita                          ← nuevo
            └── Webhook → https://<dominio-de-licita>/webhook/whatsapp
```

## Antes de empezar

- [ ] Acceso de **administrador** al portafolio de negocio de Masivo App y a su app en developers.facebook.com.
- [ ] Un **número de teléfono nuevo** para Licita que reciba SMS o llamadas y que **no esté registrado en WhatsApp**
      (ni en la app normal ni en WhatsApp Business). Si lo estaba, primero elimina esa cuenta desde el teléfono.
- [ ] Un **sitio web de Licita**, aunque sea una página simple, donde se vea quién está detrás. Meta lo usa para aprobar el nombre visible.
- [ ] Una **URL pública con HTTPS** donde correrá `licita servidor`. Para pruebas sirve un túnel como `cloudflared` o `ngrok`.

## Paso 1. Crear la cuenta de WhatsApp Business de Licita

1. Entra a **WhatsApp Manager** del portafolio de Masivo App (business.facebook.com → Cuentas → Cuentas de WhatsApp).
2. Crea una **cuenta nueva** llamada `Licita`. No reutilices una cuenta de Masivo ni de sus clientes: así las plantillas,
   la calidad del número y los costos de Licita quedan separados.
3. Completa el perfil: nombre visible **Licita**, categoría (por ejemplo "Servicios profesionales"), descripción corta y sitio web.
4. Anota el **ID de la cuenta de WhatsApp Business** (`WABA_ID`).

## Paso 2. Agregar y registrar el número

1. En la cuenta de Licita, agrega el número y verifícalo con el código que llega por SMS o llamada.
2. Anota el **ID del número de teléfono** (`PHONE_NUMBER_ID`). Va en `WHATSAPP_PHONE_NUMBER_ID`.
3. Registra el número en la Cloud API, definiendo un PIN de 6 dígitos para la verificación en dos pasos (guárdalo en tu gestor de contraseñas):

```bash
curl -X POST "https://graph.facebook.com/v23.0/$PHONE_NUMBER_ID/register" \
  -H "Authorization: Bearer $WHATSAPP_TOKEN" -H "Content-Type: application/json" \
  -d '{"messaging_product": "whatsapp", "pin": "123456"}'
```

4. El nombre visible "Licita" queda **en revisión**. Mientras tanto se puede probar, pero conviene esperar la aprobación antes del piloto.

## Paso 3. Crear un usuario del sistema solo para Licita

Por seguridad, Licita no debe usar el mismo token que Masivo App: si se filtrara, quedarían expuestos los números de tus clientes.

1. En el portafolio: **Configuración del negocio → Usuarios → Usuarios del sistema → Agregar**, con el nombre `licita-backend` y el rol Empleado.
2. **Asignar activos:**
   - La app de Meta de Masivo App, con control total.
   - **Solo** la cuenta de WhatsApp Business de Licita, con control total.
3. **Generar token:** elige la app, vencimiento **Nunca** y los permisos `whatsapp_business_messaging` y `whatsapp_business_management`.
4. Guarda el token en `WHATSAPP_TOKEN`. No lo subas al repositorio: el archivo `.env` ya está excluido en `.gitignore`.

## Paso 4. Método de pago

1. En **WhatsApp Manager → Configuración de pagos** de la cuenta de Licita, asigna la tarjeta o línea de crédito del portafolio.
2. Así Meta le cobra directo a Licita (unos US$0,02 por plantilla utility en Chile), sin recargo de intermediarios.

## Paso 5. Webhook: que los mensajes de Licita lleguen a Licita

La app de Masivo App ya tiene una URL de webhook para sus propios números. Para Licita hay dos caminos.

### Camino recomendado: URL propia para la cuenta de Licita

Meta permite que una cuenta de WhatsApp Business use una URL de webhook distinta de la principal de la app.
Revisa la sección *Webhooks overrides* de la documentación de Meta por si cambió el formato.

1. Levanta el servidor de Licita en su URL pública:

```bash
licita servidor --puerto 8000
```

2. Suscribe la app a la cuenta de Licita, indicando la URL y el token de verificación de Licita:

```bash
curl -X POST "https://graph.facebook.com/v23.0/$WABA_ID/subscribed_apps" \
  -H "Authorization: Bearer $WHATSAPP_TOKEN" -H "Content-Type: application/json" \
  -d '{"override_callback_uri": "https://<dominio-de-licita>/webhook/whatsapp",
       "verify_token": "'"$WHATSAPP_VERIFY_TOKEN"'"}'
```

3. Meta llama a la URL para verificarla. Si `licita servidor` está corriendo, responde solo.

### Alternativa: que Masivo App reenvíe los eventos

Si prefieres no tocar la suscripción, el webhook de Masivo App puede reenviar a Licita los eventos cuyo
`entry[].changes[].value.metadata.phone_number_id` sea el número de Licita. Para eso:

- Reenvía el **cuerpo original sin modificar** y el encabezado **`X-Hub-Signature-256` original**. La firma
  se calcula con el *app secret* de la app de Masivo, que es el mismo que usa Licita, así que la verificación sigue funcionando.
- Licita además ignora los mensajes dirigidos a otros números (filtra por `WHATSAPP_PHONE_NUMBER_ID`).

## Paso 6. Crear la plantilla del resumen diario

Desde WhatsApp Manager (Plantillas de mensajes → Crear) o por la API:

```bash
curl -X POST "https://graph.facebook.com/v23.0/$WABA_ID/message_templates" \
  -H "Authorization: Bearer $WHATSAPP_TOKEN" -H "Content-Type: application/json" \
  -d '{
    "name": "resumen_diario_licitaciones",
    "language": "es",
    "category": "UTILITY",
    "components": [
      {
        "type": "BODY",
        "text": "Hola {{1}}, hoy encontramos {{2}} que calzan con tu negocio. La mejor: {{3}} ({{4}}% de calce), cierra el {{5}}. Toca un botón para ver el detalle.",
        "example": {"body_text": [["Aseo Sur", "3 licitaciones nuevas", "Adquisición de insumos de aseo para CESFAM", "92", "20-10-2026 15:00"]]}
      },
      {
        "type": "BUTTONS",
        "buttons": [
          {"type": "QUICK_REPLY", "text": "Ver todas"},
          {"type": "QUICK_REPLY", "text": "Ver la mejor"}
        ]
      }
    ]
  }'
```

Mantén la plantilla **informativa**, sin promociones ni invitaciones a pagar. Si Meta la reclasifica como *marketing*, cuesta unas 4 veces más.

## Paso 7. Variables de entorno de Licita

| Variable | De dónde sale |
|---|---|
| `WHATSAPP_TOKEN` | Token del usuario del sistema `licita-backend` (paso 3) |
| `WHATSAPP_PHONE_NUMBER_ID` | ID del número de Licita (paso 2) |
| `WHATSAPP_APP_SECRET` | App secret de la app de Masivo App (Configuración de la app → Básica) |
| `WHATSAPP_VERIFY_TOKEN` | Un texto secreto que tú inventas, el mismo que usaste en el paso 5 |
| `WHATSAPP_PLANTILLA_RESUMEN` | `resumen_diario_licitaciones` |
| `WHATSAPP_IDIOMA` | `es` |
| `LICITA_URL_REGISTRO` | Página de registro de Licita (se envía a números no registrados) |

## Paso 8. Prueba de punta a punta

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
   - [ ] Llega la plantilla con los botones **Ver todas** y **Ver la mejor**.
   - [ ] Al tocar **Ver la mejor**, llega el detalle de la licitación.
   - [ ] Al responder **1**, llega el detalle de la primera licitación.
   - [ ] Al escribir **BAJA**, se confirma la baja; con **ALTA**, se reactiva.
4. Revisa los registros del servidor: cada mensaje entrante y saliente queda en la tabla `mensajes_whatsapp`.

## Cuidados para mantener el número sano

- **Consentimiento:** solo envía resúmenes a empresas que aceptaron recibir mensajes de Licita por WhatsApp.
  En la web de registro habrá una casilla de consentimiento; durante el piloto, pide la confirmación por escrito.
- **Calidad del número:** si muchos usuarios bloquean o reportan, Meta baja la calificación y limita los envíos.
  Por eso existe la palabra **BAJA** y por eso el resumen se envía solo cuando hay calces de al menos 60%.
- **Límite de mensajes:** revisa en WhatsApp Manager el límite diario de conversaciones iniciadas por la empresa
  que tiene el número. Para un piloto de 20 a 30 empresas sobra.
- **Separación de Masivo App:** las plantillas, la calidad y los costos de Licita viven en su propia cuenta.
  Si más adelante Licita se separa en otra empresa, la cuenta se puede migrar a un portafolio propio y conectarla con
  el registro integrado (*Embedded Signup*) de Masivo App como un cliente más.
