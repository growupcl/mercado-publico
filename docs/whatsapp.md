# Integración con WhatsApp

Licita usa la **WhatsApp Cloud API de Meta** directamente: sin intermediarios y sin recargo por mensaje.

## Cómo se controla el costo

| Situación | Qué envía Licita | Costo WhatsApp |
|---|---|---|
| Resumen diario, el usuario no ha escrito en 24 h | 1 plantilla *utility* con 2 botones | ~US$0,02 |
| Resumen diario, el usuario escribió hace menos de 24 h | Resumen completo como texto | Gratis |
| El usuario toca un botón o responde un número | Detalle de la licitación | Gratis (ventana abierta) |

Resultado: como máximo **una plantilla pagada al día por usuario**. Todo lo demás ocurre dentro de la ventana gratuita.

## Conversación

- Botón **Ver todas** → el resumen completo con las licitaciones numeradas.
- Botón **Ver la mejor** → el detalle de la licitación con mayor calce.
- Responder **1, 2, 3…** → el detalle de esa licitación del último resumen.
- **TODAS** → repite el último resumen.
- **BAJA** / **ALTA** → deja de recibir o vuelve a recibir resúmenes (Meta exige permitir la baja).
- Cualquier otro texto → mensaje de ayuda.

## Configuración en Meta (una vez)

1. Crear una app de tipo **Business** en [developers.facebook.com](https://developers.facebook.com) y agregar el producto **WhatsApp**.
2. Registrar el número de teléfono de Licita (no puede estar en uso en la app de WhatsApp normal) y verificar la empresa en el Business Manager de Meta.
3. Crear un **usuario del sistema** con permiso `whatsapp_business_messaging` y generar un **token permanente** → `WHATSAPP_TOKEN`.
4. Copiar el **Phone number ID** → `WHATSAPP_PHONE_NUMBER_ID` y el **App secret** (Configuración de la app → Básica) → `WHATSAPP_APP_SECRET`.
5. Inventar un texto secreto → `WHATSAPP_VERIFY_TOKEN`.
6. Levantar el servidor (`licita servidor`) en una URL pública con HTTPS y configurar el webhook en Meta:
   - URL: `https://<tu-dominio>/webhook/whatsapp`
   - Token de verificación: el mismo de `WHATSAPP_VERIFY_TOKEN`
   - Suscribirse al campo **messages**.
7. Crear y enviar a aprobación la plantilla de abajo.

## Plantilla del resumen diario

- **Nombre:** `resumen_diario_licitaciones`
- **Categoría:** Utility
- **Idioma:** Español (`es`)
- **Cuerpo:**

  > Hola {{1}}, hoy encontramos {{2}} que calzan con tu negocio. La mejor: {{3}} ({{4}}% de calce), cierra el {{5}}. Toca un botón para ver el detalle.

  Ejemplos para la revisión de Meta: `Aseo Sur` · `3 licitaciones nuevas` · `Adquisición de insumos de aseo para CESFAM` · `92` · `20-10-2026 15:00`

- **Botones (respuesta rápida):** `Ver todas` · `Ver la mejor`

Importante: la plantilla tiene que ser **informativa** (avisos que el usuario pidió). Si incluye promociones o invitaciones a pagar un plan, Meta la reclasifica como *marketing*, que cuesta unas 4 veces más. Esos mensajes van por correo.

## Envío diario

Programar `licita whatsapp-enviar` una vez al día, por ejemplo a las 8:00, después de `licita sync`, `licita clasificar` y `licita calce` de cada empresa.
