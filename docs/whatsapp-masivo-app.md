# WhatsApp de Calza y Masivo App

El número de Calza (**+56 9 5787 5125**) se conectó con el **registro integrado** (*Embedded Signup*) de Masivo App,
registrando a Calza como un cliente más de masivoapp.cl, con el correo hola@calza.cl. Así quedaron creados, dentro del
portafolio de Meta **Calza** (razón social Virtus SpA, separado del portafolio de Masivo App):

- la cuenta de WhatsApp Business (ID 1089829487170220) y el número, con el nombre visible "Calza";
- la tarjeta de Virtus SpA para pagarle a Meta;
- la suscripción de la app de Masivo App a esa cuenta.

Después Calza conectó **su propia app de Meta** y su propio token: ver [whatsapp.md](whatsapp.md#configuración-en-meta-cómo-está-hoy).
Masivo App guarda su token cifrado y Calza no lo necesita, ni tampoco la clave secreta de la app de Masivo.

## Qué rol tiene hoy Masivo App

- **Conserva acceso a la cuenta** de WhatsApp de Calza y recibe sus mensajes. Su bot o respuestas automáticas deben
  estar **apagados** para el cliente Calza: si no, el cliente recibe dos respuestas.
- Sirve para **campañas de marketing de Calza** (plantillas de categoría marketing, a contactos con consentimiento),
  sin tocar el código de Calza. Ojo: esas campañas cuentan para el mismo límite diario y la misma calidad del número
  que el resumen diario.
- Si se quiere cortar el acceso de Masivo App: business.facebook.com/settings → portafolio Calza → Integraciones →
  Apps conectadas → quitar Masivo App. Calza sigue funcionando igual.

## Antes de usarlo en serio

- [ ] Un **acuerdo simple entre Virtus SpA y la empresa de Masivo App** sobre el acceso a los mensajes de Calza
      (tratamiento de datos, Ley 21.719), mientras Masivo mantenga ese acceso.
- [ ] **Verificación del negocio** en Meta (Centro de seguridad del portafolio Calza), con el e-RUT o la escritura de
      Virtus SpA. Sin ella el límite es de 250 clientes iniciados por día.

## Cuando exista Calza SpA

1. **Si el portafolio Calza todavía no está verificado**, basta cambiar la razón social en Información del negocio
   y verificarlo con los documentos de Calza SpA. Número, app y plantillas siguen igual.
2. **Si ya está verificado a nombre de Virtus SpA**, crea y verifica un portafolio nuevo para Calza SpA y **migra el
   número** a una cuenta de WhatsApp Business de ese portafolio. Meta lo permite y el número conserva su calidad.
   - Las **plantillas no se migran**: créalas de nuevo con `licita whatsapp-plantillas --crear`.
   - Crea la app de Meta y el usuario del sistema en el portafolio nuevo ([whatsapp.md](whatsapp.md#desde-cero-para-otra-instalación))
     y actualiza todas las variables `WHATSAPP_*`.
3. En los dos casos, cambia `LICITA_PRESTADOR` y las credenciales de Mercado Pago (ver [mercadopago.md](mercadopago.md)).
