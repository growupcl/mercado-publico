# Suscripciones con Mercado Pago

Calza cobra con **suscripciones de Mercado Pago**: el cliente ingresa su tarjeta de crédito o débito una vez y
Mercado Pago cobra solo cada mes o cada año. Durante el piloto se usa la cuenta de **Virtus SpA**, que emite las
facturas (`LICITA_PRESTADOR=Virtus SpA`). Cuando exista Calza SpA, basta cambiar las credenciales y ese valor.

## Cómo funciona

1. En "Mi cuenta", el cliente elige plan y periodicidad y toca **Suscribirme**.
2. Calza crea la suscripción en Mercado Pago (pendiente) y lo envía a la página de Mercado Pago para ingresar la tarjeta.
   - Si todavía le quedan días de prueba o de un período pagado, el **primer cobro se programa para cuando terminen**.
3. Mercado Pago devuelve al cliente a `/pagos/mercadopago/retorno` y además avisa por webhook:
   - `subscription_preapproval`: la suscripción se autorizó, pausó o canceló.
   - `subscription_authorized_payment`: se hizo un cobro (aprobado o rechazado).
4. Calza **verifica la firma** del aviso y **vuelve a consultar a Mercado Pago** antes de cambiar nada. Cada cobro
   aprobado extiende el período 1 o 12 meses. Los avisos repetidos no extienden dos veces.
5. Cambio de plan: al autorizarse la suscripción nueva, Calza cancela la anterior.
6. Cancelación: desde "Mi cuenta". El cliente mantiene su plan hasta el fin del período pagado.
7. Si el período termina sin cobro, `licita suscripciones` pasa la cuenta al plan gratis. Con renovación automática
   espera 3 días, porque el cobro y su aviso pueden llegar con atraso.

## Configuración

1. En [Tus integraciones](https://www.mercadopago.cl/developers/panel/app) de la cuenta de Virtus SpA, crea una aplicación
   (por ejemplo "Calza") con el producto *Suscripciones*.
2. **Credenciales:** copia el *Access Token* en `MERCADOPAGO_ACCESS_TOKEN`. Para probar, usa primero las
   credenciales de prueba (empiezan con `TEST-`).
3. **Webhooks:** en la aplicación → Webhooks, configura:
   - URL: `https://calza.cl/pagos/mercadopago/webhook` (en pruebas, la URL pública de tu servidor de prueba).
   - Eventos: **Planes y suscripciones** (suscripciones y pagos autorizados).
   - Copia la **clave secreta** que muestra Mercado Pago en `MERCADOPAGO_WEBHOOK_SECRET`.
4. `LICITA_URL_PUBLICA=https://calza.cl` (Mercado Pago devuelve al cliente a esa dirección).

## Prueba de punta a punta (con credenciales de prueba)

1. Crea en el panel de Mercado Pago dos **usuarios de prueba**: uno vendedor y uno comprador.
2. Regístrate en el sitio, entra a "Mi cuenta" y suscríbete a un plan.
3. En Mercado Pago, entra con el **usuario comprador de prueba** y paga con una tarjeta de prueba.
4. Revisa en "Mi cuenta":
   - [ ] Aparece la renovación automática con el plan y el monto correctos.
   - [ ] Si estabas en la prueba, dice que el primer cobro será al terminar la prueba.
5. Revisa que el aviso llegue: en el panel de Mercado Pago (Webhooks → historial) debe responder `200`.

## Puntos a confirmar en la prueba

- Mercado Pago puede pedirle al cliente **iniciar sesión o crear una cuenta** para suscribirse, y exigir que el correo
  de esa cuenta coincida con el informado. Si eso genera fricción, se puede agregar un pago único (Checkout Pro) como alternativa.
- La comisión de la cuenta: actualizarla en el modelo financiero (hoy supone 3,5%).
