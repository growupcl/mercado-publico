# Histórico de órdenes de compra (precios desde el primer día)

La inteligencia de precios necesita compras anteriores de cada producto. La API de Mercado Público entrega las
órdenes de compra día a día y con un límite de consultas, así que para tener **24 meses de historia** desde el
primer día Calza usa los archivos mensuales de **datos abiertos** de ChileCompra.

## De dónde vienen

`https://transparenciachc.blob.core.windows.net/oc-da/AAAA-M.zip` (mes sin cero: `2026-9.zip`). Cada archivo trae
un CSV con una fila por producto de cada orden de compra (unas 1,5 millones de filas al mes). Es el mismo que se baja
a mano desde [datosabiertos.chilecompra.cl](http://datosabiertos.chilecompra.cl/Home/DescargaHistorico).

## Qué se guarda

- Solo el **precio unitario neto** de cada línea, con producto, código ONU, unidad, cantidad, proveedor, organismo,
  región y fecha (tabla `precios`). No se guardan las órdenes completas: ocuparían decenas de GB.
- **Solo los productos que tienen que ver con los clientes** (comparten palabras clave con alguno). Cuando entra un
  cliente de un rubro nuevo, vuelve a correr la carga: agrega solo lo nuevo, sin duplicar.
- Se descartan órdenes canceladas, en otra moneda y líneas sin precio.
- Volver a cargar un mes no duplica nada, ni lo que ya llegó por la API.

## Comandos

```bash
# Carga inicial: 24 meses (unos 60 a 90 minutos; ~2 minutos por mes más la descarga)
deploy/tareas.sh historico-oc --meses 24

# Semanal (ya está en el crontab, lunes 1:30): mes actual y anterior, que ChileCompra sigue completando
deploy/tareas.sh historico-oc --meses 2

# Cliente de un rubro nuevo: repetir la carga para traer sus productos
deploy/tareas.sh historico-oc --meses 24

# Si el servidor no puede descargar, baja los .zip a mano y súbelos
docker compose exec app licita historico-oc --archivo /ruta/2026-9.zip /ruta/2026-8.zip
```

`--todo` guarda todos los rubros (útil para estudiar el mercado), pero ocupa mucho espacio: evítalo en el VPS del piloto.

## Espacio

Con el filtro por clientes se guarda del orden del 5 % al 15 % de las líneas: unos cientos de miles de precios al mes
y del orden de 1 a 2 GB para 24 meses. Revisa el disco con `df -h` después de la carga inicial.

## Antes de la primera carga

El formato (columnas, separador `;`, Latin-1) se tomó de cargadores públicos que ya procesan estos archivos: no pude
descargar uno de verdad desde aquí. Si ChileCompra cambió los encabezados, el comando se detiene con un mensaje que
muestra las columnas que encontró; mándamelo y lo ajusto.
