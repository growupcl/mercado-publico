# Compra Ágil

Las alertas urgentes de Compra Ágil son parte del plan Pro (y de la prueba gratuita, que es Pro).

## Cómo funciona

Cada 20 minutos en días hábiles, de 8:00 a 20:40 (`licita compra-agil`, ver `deploy/crontab`):

1. **Publicaciones nuevas.** Calza consulta la API Compra Ágil v2 de ChileCompra (`api2.mercadopublico.cl`) por las
   Compras Ágiles abiertas publicadas desde la última vez. El listado es rápido y trae nombre, organismo, región,
   presupuesto y cierre.
2. **Detalle solo de lo que importa.** El detalle (descripción, productos y cantidades, plazo de entrega) demora 20 a
   25 segundos por proceso, así que solo se pide para las que comparten palabras clave con algún cliente Pro, primero
   las que cierran antes. Máximo 25 por vuelta (`--max-detalles`).
3. **Calce con IA.** Igual que con las licitaciones: prefiltro sin IA (región, monto, palabras clave) y Claude
   evalúa las mejores de 0 a 100.
4. **Alerta urgente** si el calce es de 70 o más y queda al menos una hora para el cierre:
   - con la conversación abierta (el cliente escribió en las últimas 24 h): un texto con las Compras Ágiles, gratis;
   - si no: la plantilla `alerta_compra_agil` con la mejor y el botón *Ver detalle*.

   Máximo 3 alertas por cliente al día y nunca entre las 21:00 y las 8:00. Lo que no alcance a avisarse aparece en
   el resumen diario siguiente si sigue abierto.

El cliente puede pedir *PRECIOS* sobre una Compra Ágil (usa los códigos de producto, igual que en licitaciones) o
reenviar sus documentos para que la IA los analice: el código `…-COT26` se reconoce automáticamente.

## Qué hay que tener listo

- **Ticket:** es el mismo de Mercado Público (`MERCADOPUBLICO_TICKET`). En esta API viaja en un header, no en la URL,
  así que no queda en ningún registro. Si la API responde que el ticket no tiene permiso, pide en
  [chilecompra.cl/api](https://www.chilecompra.cl/api/) que lo habiliten para Compra Ágil.
- **Plantilla aprobada:** `alerta_compra_agil` (ver [plantillas-whatsapp.md](plantillas-whatsapp.md)). Mientras no esté
  aprobada, solo llegan las alertas a quienes tengan la conversación abierta.
- **Cron:** `crontab deploy/crontab` ya incluye la tarea.

## Primera prueba con datos reales

En el servidor, sin enviar WhatsApp:

```bash
docker compose exec app licita compra-agil --sin-alertas
```

Debe mostrar cuántas Compras Ágiles llegaron, cuántos detalles pidió y cuántos calces encontró. Si no hay clientes
con plan Pro (o en prueba), no consulta la API.

## Consumo del ticket

Con 39 vueltas al día: una a tres consultas de listado por vuelta más hasta 25 detalles, en la práctica unas pocas
centenas de consultas al día. La guía oficial indica que la cuota es diaria por ticket; si se agota (HTTP 429), la
vuelta se detiene y se retoma en la siguiente sin perder publicaciones, porque cada consulta parte desde la última
Compra Ágil guardada.

## Notas de la API (guía oficial v3.0, mayo 2026, y pruebas de la comunidad)

- Las fechas vienen en hora de Chile, a veces con una "Z" que no corresponde. Calza las guarda en hora de Chile y
  compara los cierres contra la hora de Chile.
- El estado `oc_emitida` no se usa en la práctica y la API no informa adjudicaciones de forma confiable; por eso hoy
  Calza no saca precios de Compra Ágil (es la siguiente mejora: usar las cotizaciones de los procesos cerrados).
- La ficha pública de cada proceso es `https://buscador.mercadopublico.cl/ficha?code=CODIGO`.
