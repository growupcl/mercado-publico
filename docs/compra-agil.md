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

El cliente puede reenviar los documentos de una Compra Ágil para que la IA los analice: el código `…-COT26` se
reconoce automáticamente.

## Precios: a cuánto cotiza la competencia

Todas las noches a las 22:00 (`licita compra-agil-precios`), Calza revisa las Compras Ágiles que cerraron en el
último día (cerradas, desiertas o con proveedor seleccionado). De las que comparten palabras clave con algún cliente
Pro pide el detalle y guarda **las cotizaciones admisibles de cada proveedor**, producto por producto. Máximo 100
detalles por noche (unos 40 minutos); las que no alcanzan quedan para la noche siguiente.

Cuando el cliente escribe *PRECIOS* sobre una licitación o una Compra Ágil, cada producto muestra, además de lo que
efectivamente pagó el Estado (órdenes de compra y adjudicaciones):

```
⚡ En Compra Ágil cotizan $4.725 – $5.050 c/u (4 cotizaciones en 2 procesos)
🏁 La más baja de cada proceso suele ser $4.575 – $4.725 (mediana $4.650)
```

- La segunda línea es la más útil: en Compra Ágil normalmente gana la cotización más baja que cumple, así que es una
  buena referencia del precio para ganar.
- Las cotizaciones se muestran **aparte** de los precios pagados: son ofertas, no compras. No cuentan para "suelen
  ganar" ni para el precio competitivo.
- Se descartan las cotizaciones inadmisibles y los precios atípicos (por ejemplo, cajas contra unidades). Hacen falta
  al menos 3 cotizaciones de 2 procesos distintos para mostrar la referencia.
- Si la API muestra cotizaciones nuevas (un segundo llamado), se reemplazan las anteriores del mismo proceso.

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

Con 39 vueltas al día: una a tres consultas de listado por vuelta más hasta 25 detalles, y de noche hasta 40 páginas
de cerradas más 100 detalles para precios; en la práctica unas pocas centenas de consultas al día. La guía oficial indica que la cuota es diaria por ticket; si se agota (HTTP 429), la
vuelta se detiene y se retoma en la siguiente sin perder publicaciones, porque cada consulta parte desde la última
Compra Ágil guardada.

## Notas de la API (guía oficial v3.0, mayo 2026, y pruebas de la comunidad)

- Las fechas vienen en hora de Chile, a veces con una "Z" que no corresponde. Calza las guarda en hora de Chile y
  compara los cierres contra la hora de Chile.
- El estado `oc_emitida` no se usa en la práctica y la API no informa adjudicaciones de forma confiable (quién ganó);
  por eso Calza usa las cotizaciones y no precios ganadores.
- Según la guía, el detalle completo de las cotizaciones aparece desde que el proceso cierra; si al revisarlo aún no
  aparecen, se vuelve a revisar cuando cambie de estado (desierta o proveedor seleccionado).
- La ficha pública de cada proceso es `https://buscador.mercadopublico.cl/ficha?code=CODIGO`.
