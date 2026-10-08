# Descarga automática de las bases: estado y decisión pendiente

## Situación (octubre 2026)

- **No existe una vía oficial.** La API de licitaciones (v1) no entrega los adjuntos. La API de Compra Ágil (v2)
  lista sus nombres (`documentos[]`), pero no tiene un endpoint de descarga, y los enlaces directos responden 404:
  la descarga la hace el JavaScript del portal.
- **Los términos apuntan en contra de bajarlas con un robot desde el portal.** Las condiciones de la API indican
  que el acceso automatizado "debe efectuarse exclusivamente en la forma que se describa en api.mercadopublico.cl",
  con monitoreo por IP. La Resolución 304-B (julio 2024) permite bloquear hasta 12 meses a proveedores que operen
  "mediante robots" que comprometan la plataforma. No se pudo leer el texto completo de las condiciones del portal
  desde el entorno de desarrollo.
- **Riesgo:** perder el ticket de la API (todo Calza depende de él) o que se asocie a los clientes con uso de robots.

Por eso Calza **no descarga las bases sola**. El cliente las descarga desde la ficha y las reenvía por WhatsApp, y
Calza las analiza. En Compra Ágil, el detalle ya muestra los nombres de los adjuntos y pide reenviar los PDF.

## Siguiente paso: preguntar a ChileCompra

Enviar desde hola@calza.cl a **api@chilecompra.cl**:

> **Asunto:** Consulta sobre acceso a documentos adjuntos mediante la API
>
> Estimados:
>
> Somos Calza (calza.cl), un servicio que ayuda a pymes proveedoras del Estado a encontrar licitaciones y Compras
> Ágiles que calzan con su negocio. Usamos la API de Mercado Público y la API Compra Ágil v2 con nuestro ticket,
> respetando los límites de consulta.
>
> Para ayudar a nuestros clientes a preparar sus ofertas, queremos resumir las bases y documentos adjuntos de los
> procesos que les interesan. Al respecto, les consultamos:
>
> 1. ¿Existe o está previsto un servicio de la API para obtener los documentos adjuntos de licitaciones y de Compras
>    Ágiles (por ejemplo, a partir de `documentos[].id` en la API Compra Ágil v2)?
> 2. Si no existe, ¿está permitido descargar de forma automatizada los adjuntos públicos desde la ficha del proceso,
>    con un volumen acotado (decenas de documentos al día) y pausas entre descargas? ¿Qué condiciones deberíamos
>    respetar?
> 3. ¿Hay alguna alternativa recomendada (datos abiertos u otro canal) para acceder a estos documentos?
>
> Quedamos atentos y agradecemos su ayuda.
>
> Saludos,
> [Nombre]
> Calza — hola@calza.cl

## Según la respuesta

- **Si hay un servicio oficial:** se integra al ciclo y el análisis de bases llega solo con cada alerta o resumen.
- **Si autorizan la descarga desde la ficha:** se implementa con las condiciones que indiquen (volumen, pausas,
  identificación) y solo para los procesos que le interesan a un cliente.
- **Si no:** se mantiene el reenvío por WhatsApp. Una mejora posible es aceptar también adjuntos en Word.
