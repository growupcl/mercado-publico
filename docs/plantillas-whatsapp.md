# Plantillas de WhatsApp

Meta demora cerca de **24 a 36 horas** en aprobar cada plantilla, y cualquier edición vuelve a revisión.
Por eso Calza usa **pocas plantillas fijas**, se envían **todas juntas una sola vez** y no se modifican.
Todo lo demás (detalles, análisis de bases, precios, respuestas) va como texto libre dentro de la
ventana de 24 horas, que no requiere aprobación.

El texto de cada plantilla está definido en `src/licita/plantillas_whatsapp.py`: es el mismo que se envía
a Meta y el que usa el sistema. Las pruebas automáticas revisan las reglas que suelen causar rechazos.

## Las plantillas

| Plantilla | Para qué | Botones | ¿Para el piloto? |
|---|---|---|---|
| `resumen_diario_licitaciones` | Resumen diario cuando la ventana de 24 h está cerrada | Ver todas · Ver la mejor | **Sí** |
| `resumen_diario_simple` | Respaldo con menos variables, por si Meta rechaza la principal | Ver licitaciones | Recomendada |
| `cobro_rechazado` | Mercado Pago no pudo cobrar la suscripción (ya se envía automáticamente) | Ver mi cuenta | Recomendada |
| `alerta_compra_agil` | Aviso urgente de Compra Ágil (plan Pro) | Ver detalle | **Sí**, si vendes el plan Pro (alertas urgentes) |

### `resumen_diario_licitaciones`
> Hola {{1}}, hoy encontramos {{2}} que calzan con tu negocio. La mejor: {{3}} ({{4}}% de calce), cierra el {{5}}. Toca un botón para ver el detalle.

Ejemplos: `Aseo Sur` · `3 licitaciones nuevas` · `Adquisición de insumos de aseo para CESFAM` · `92` · `20-10-2026 15:00`

### `resumen_diario_simple`
> Hola {{1}}, tu resumen de hoy está listo: encontramos {{2}} que calzan con tu negocio en Mercado Público. Toca el botón para verlas.

Ejemplos: `Aseo Sur` · `3 licitaciones nuevas`

### `cobro_rechazado`
> Hola {{1}}, no pudimos cobrar tu plan {{2}} de Calza con la tarjeta registrada en Mercado Pago. Para seguir recibiendo tus licitaciones, revisa tu medio de pago antes del {{3}}. Toca el botón y te enviamos el enlace a tu cuenta.

Ejemplos: `Aseo Sur` · `Pyme mensual` · `23 de octubre de 2026`

### `alerta_compra_agil`
> Hola {{1}}, hay una Compra Ágil que calza con tu negocio: {{2}} ({{3}}% de calce). El plazo para cotizar cierra el {{4}}. Toca el botón para ver el detalle.

Ejemplos: `Aseo Sur` · `Compra de guantes de nitrilo para CESFAM` · `95` · `09-10-2026 18:00`

Todas son de categoría **Utility** e idioma **Español (`es`)**.

## Enviarlas a revisión (todas juntas)

Con `WHATSAPP_TOKEN` y `WHATSAPP_WABA_ID` en el `.env` del servidor:

```bash
docker compose exec app licita whatsapp-plantillas           # ver exactamente lo que se enviará
docker compose exec app licita whatsapp-plantillas --crear   # enviarlas todas a revisión
```

También se pueden crear a mano en WhatsApp Manager → Plantillas de mensajes, copiando el texto, los ejemplos
y los botones de arriba.

Hazlo **el mismo día que conectas el número**, antes de invitar a los clientes del piloto: la espera corre en
paralelo con el resto de la configuración.

## Mientras se aprueban

- Escríbele "Hola" al número de Calza: con la ventana abierta, Calza responde y envía el resumen como texto libre.
- Si el resumen diario se intenta enviar con una plantilla aún no aprobada, el envío falla, el resumen **no** se
  marca como enviado y se reintenta al día siguiente. A quienes tengan la ventana abierta les llega igual como texto.

## Si Meta rechaza una plantilla

1. Lee el motivo en WhatsApp Manager (o en la salida del comando `--crear`).
2. Si es la del resumen diario, usa la de respaldo mientras tanto: `WHATSAPP_PLANTILLA_RESUMEN=resumen_diario_simple`
   en el `.env` y `docker compose up -d`.
3. Ajusta el texto en `src/licita/plantillas_whatsapp.py`, publica la nueva versión y vuelve a enviarla.

## Para que no las rechacen ni las recategoricen

- Nada de promociones, descuentos ni invitaciones a pagar un plan: eso las convierte en *marketing* (unas 4 veces más caras).
- El cuerpo no empieza ni termina con una variable y tiene suficiente texto fijo alrededor de ellas.
- Ejemplos realistas en todas las variables.
- No editar una plantilla aprobada: crear una nueva con otro nombre si hay que cambiarla.
