# Facturar los pagos en el portal gratuito del SII

Durante el piloto factura **Virtus SpA** (variable `LICITA_PRESTADOR`) con el sistema de facturación gratuito del
SII. Calza no emite la factura: deja cada una lista para copiar y guarda el folio cuando ya la emitiste.

## Qué datos tiene Calza

En el registro el cliente entrega RUT, razón social, giro, dirección comercial, comuna y correo, que es lo que exige
la factura. Cada pago aprobado por Mercado Pago queda como **factura pendiente** hasta que registres su folio.

## Rutina (una vez por semana basta; la factura debe emitirse a más tardar el mes del pago)

1. En el servidor, lista las pendientes:

   ```bash
   docker compose exec app licita facturas
   ```

   ```
   Pago #12 · pagado el 08-10-2026
     RUT receptor:   76.123.456-0
     Razón social:   Aseo Sur SpA
     Giro:           Venta de artículos de aseo
     Dirección:      Av. Colón 1234
     Comuna:         Concepción
     Correo:         contacto@aseosur.cl
     Detalle:        Suscripción Calza plan Pro mensual (1 mes) · cantidad 1 · precio $33.605
     Neto $33.605 · IVA $6.385 · Total $39.990
   ```

   Para tenerlas en planilla: `docker compose exec -T app licita facturas --csv > facturas.csv`.

2. Emite cada una en [sii.cl](https://www.sii.cl) → **Servicios online → Factura electrónica → Sistema de
   facturación gratuito del SII → Emitir documento → Factura electrónica**, con la clave de Virtus SpA:
   - **Receptor:** escribe el RUT; el SII completa razón social y dirección si las conoce. Revisa que coincidan con
     las de Calza (si no, usa las de Calza: son las que el cliente declaró).
   - **Detalle:** la glosa de Calza, cantidad 1 y como precio el **neto**. El SII calcula el IVA y el total: deben
     dar lo mismo que muestra Calza.
   - **Forma de pago:** contado (ya se pagó con Mercado Pago).
3. Envía el PDF al correo del cliente (el portal ofrece enviarlo al emitir, o descárgalo y mándalo desde
   hola@calza.cl).
4. Registra el folio en Calza:

   ```bash
   docker compose exec app licita facturas --emitida 12 --folio 125
   ```

   El cliente ve el número de factura en su cuenta (columna **Factura** de sus pagos).

## Casos especiales

- **"⚠ Faltan datos":** cuentas antiguas sin dirección o giro. Pídeselos al cliente antes de emitir.
- **"⚠ diferencia de $1":** el SII calcula el IVA desde el neto y hay montos con IVA incluido que no se pueden
  reproducir exactos. Los precios actuales cuadran todos (el Pyme anual se dejó en $215.890 por esto); si cambias
  un precio, revisa que no aparezca este aviso.
- **Reembolsos:** emite una nota de crédito en el mismo portal, referenciando la factura.

## Cuándo dejar el portal

Con más de ~30 facturas al mes conviene un proveedor con API (OpenFactura, Bsale, LibreDTE u otro) y que Calza
emita sola al recibir cada pago. También es el momento de pasar la facturación de Virtus SpA a Calza SpA.
