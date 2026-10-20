# Correo de avisos (Google Workspace)

Calza envía por correo, desde **hola@calza.cl** y a través del SMTP de Google Workspace:

- **Avisos de cuenta** (cobro rechazado, fin de la prueba, plan terminado): son transaccionales y llegan aunque el
  cliente haya dado de baja WhatsApp o el resumen por correo.
- **Copia diaria del resumen de licitaciones** (días hábiles a las 8:00, junto al WhatsApp): respaldo por si Meta
  limita la entrega de plantillas de marketing. Usa la misma numeración que el WhatsApp, así el cliente puede
  responder "2" por WhatsApp para ver el detalle. Cada correo trae un enlace de baja (pide confirmación, para que los
  antivirus que abren enlaces no den de baja a nadie) y el encabezado `List-Unsubscribe` de baja en un clic que
  piden Gmail y Outlook. El cliente puede reactivarlo desde su cuenta.

## Configuración (10 minutos)

1. **Elige el usuario que envía.** Debe ser un usuario real de Google Workspace (por ejemplo, tu usuario
   `nombre@calza.cl`). Si `hola@calza.cl` es un alias de ese usuario, sirve igual.
2. **Activa la verificación en dos pasos** en ese usuario: [myaccount.google.com/security](https://myaccount.google.com/security).
3. **Crea una contraseña de aplicación:** [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords),
   nombre "Calza servidor". Copia las 16 letras (sin espacios). Si no aparece la opción, en la consola de
   administración revisa que la verificación en dos pasos esté permitida para tu organización.
4. **Si `hola@calza.cl` es un alias:** en Gmail → Configuración → Cuentas → **Enviar como** → agregar
   `hola@calza.cl` y deja marcada la opción "Tratar como alias". Sin este paso, Gmail cambia el remitente por tu usuario.
5. **En el servidor**, en `/opt/calza/.env`:

   ```
   SMTP_HOST=smtp.gmail.com
   SMTP_PUERTO=587
   SMTP_USUARIO=nombre@calza.cl
   SMTP_CLAVE=abcdefghijklmnop
   CORREO_REMITENTE=hola@calza.cl
   ```

   y reinicia: `docker compose up -d`.
6. **Prueba:**

   ```bash
   docker compose exec app licita correo-prueba tu-correo-personal@gmail.com
   ```

   En Gmail, abre el correo → "Mostrar original" y revisa que SPF, DKIM y DMARC digan **PASS** (los registros DNS
   se configuraron en [despliegue.md](despliegue.md), paso 3).

## Cuándo se envía

- Cuando Mercado Pago avisa un cobro rechazado (al instante) y en la revisión diaria de las 3:15.
- Como máximo un correo cada 3 días por suscripción (Mercado Pago reintenta el cobro varias veces).
- No se envía si la suscripción ya se canceló, si un cobro posterior se aprobó o si el rechazo tiene más de 7 días.
- Si el correo falla (por ejemplo, Google no responde), se reintenta en la revisión siguiente; el aviso por
  WhatsApp no se repite.

## Notas

- Google Workspace permite unos 2.000 correos al día por usuario: de sobra para avisos de cuenta.
- Vultr bloquea el puerto 25 por defecto, pero Calza usa el 587, que no está bloqueado.
- La contraseña de aplicación solo sirve para enviar correo y se puede revocar en cualquier momento desde la misma
  página. Guárdala solo en `.env`.

## Resumen diario por correo

- Lo envía la misma tarea del WhatsApp (`licita whatsapp-enviar`, días hábiles a las 8:00). La salida muestra
  `Correos: N · Errores de correo: M`.
- Las licitaciones quedan como enviadas si salieron por WhatsApp **o** por correo; si fallan los dos canales, se
  reintentan al día siguiente.
- Google Workspace permite unos **2.000 correos al día** por usuario: alcanza de sobra para el piloto. Con más de unos
  1.500 clientes habría que pasar a un servicio de envío masivo (Amazon SES, Postmark, etc.).
- Para que no caigan en spam, SPF, DKIM y DMARC deben estar en **PASS** (ver la prueba del paso 6).
