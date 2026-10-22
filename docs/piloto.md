# Mensajes del piloto

Borradores para invitar pymes al piloto de Calza y acompañar a quienes ya están en la prueba.
Usan solo las condiciones vigentes en el sitio: 7 días gratis con funciones Pro (se activan registrando la tarjeta en Mercado Pago; el primer cobro es al terminar y se puede cancelar antes sin costo);
precio fundador Pro de $39.990 al mes de por vida para los primeros 100 clientes; plan Pyme a $19.990 al mes;
10 % de descuento anual. Si cambian (`src/licita/planes.py`), actualiza también estos textos.

Reemplaza lo que está entre corchetes antes de enviar.

## Antes de enviar

- Envía todo desde tu WhatsApp personal, no desde el número de Calza. Fuera de las 24 horas desde el último
  mensaje del cliente, Meta solo deja escribir desde el número de Calza con plantillas aprobadas, y escribir en frío
  a gente que no lo pidió aumenta el riesgo de bloqueos (más aún sin el negocio verificado en Meta).
- Invita solo a personas que conoces o que te dieron permiso.
- Personaliza con un ejemplo real. Una línea como "hoy vi 3 licitaciones de aseo en tu región" pesa más que todo el
  resto del mensaje. En el panel, **Invitar al piloto** (https://calza.cl/admin/invitar) lo hace por ti: escribes qué
  vende la pyme y su región, busca lo que tiene abierto hoy y arma la invitación 1 con el ejemplo, lista para copiar o
  abrir en WhatsApp. Ahí mismo queda anotada: la lista de invitaciones muestra quién se registró y, a los 3 días sin
  registrarse, marca "Toca seguimiento" con el botón que abre el mensaje 3 ya escrito.
- A quien escriba BAJA, Calza deja de enviarle mensajes automáticamente. Tú tampoco le vuelvas a escribir.

## Invitación

### 1. WhatsApp (primer contacto)

> Hola [Nombre], soy [tu nombre]. Estamos lanzando **Calza**, un servicio que cada día hábil a las 8:00 te manda por WhatsApp las licitaciones y Compras Ágiles de Mercado Público que calzan con lo que vende [Empresa].
>
> Te invito a ser parte del piloto: son 7 días gratis: registras tu tarjeta, pero no se cobra nada si cancelas antes de que terminen. Solo te pido que me cuentes qué te sirvió y qué no.
>
> Te registras en 2 minutos aquí: https://calza.cl/registro
>
> ¿Te animas?

### 2. Correo

**Asunto:** Las licitaciones que calzan con [Empresa], cada mañana en tu WhatsApp

> Hola [Nombre]:
>
> Revisar Mercado Público todos los días quita tiempo, y es fácil que se pase una licitación buena o una Compra Ágil que cierra en pocas horas.
>
> Por eso armamos **Calza**. Le dices qué vendes, en qué regiones trabajas y qué montos te interesan, y cada día hábil a las 8:00 te llega por WhatsApp solo lo nuevo que calza contigo. Si algo te interesa, respondes con su número y te enviamos el detalle: qué piden, los requisitos a los que hay que poner ojo, plazos, precios de referencia de compras anteriores y el link a la ficha oficial.
>
> Te invito a probarlo como parte de nuestro piloto:
> - **7 días gratis** con todas las funciones. Registras tu tarjeta en Mercado Pago, pero el primer cobro es al terminar la prueba y si cancelas antes no pagas nada.
> - Si después decides seguir, los primeros 100 clientes del plan Pro quedan con **precio fundador de $39.990 al mes, de por vida**. El plan Pyme cuesta $19.990 al mes.
> - A cambio, te pido 10 minutos de conversación al final de la prueba para saber qué te sirvió y qué mejorarías.
>
> Puedes ver ahora mismo qué licitaciones abiertas calzan con lo que vendes, sin registrarte, en https://calza.cl, y activarlo en https://calza.cl/registro.
>
> Cualquier duda, respóndeme este correo o escríbeme al [tu WhatsApp].
>
> Saludos,
> [tu nombre]
> Calza — calza.cl

### 3. Seguimiento (3 a 4 días después, si no respondió)

> Hola [Nombre], te escribo de nuevo por Calza. Hoy hay [N] licitaciones abiertas en Mercado Público, y varias podrían calzar con [Empresa]. Si quieres, te muestro cuáles en 2 minutos o te registras directo en https://calza.cl/registro (7 días gratis).

Para [N] usa el número de licitaciones abiertas que muestra la portada de calza.cl ese día.

## Durante la prueba

La prueba dura 7 días y empieza cuando la empresa registra su tarjeta en Mercado Pago. Algunos avisos ya salen solos
(`licita avisos-prueba`, todos los días a las 10:05, por WhatsApp y por correo): 2 días antes del primer cobro, a quienes
registraron su tarjeta, y el día en que una cuenta pasa al plan gratis. Los mensajes de abajo los escribes tú, en persona,
además de esos avisos.

Para ver en qué está cada empresa, abre el panel del piloto en https://calza.cl/admin (usuario `admin`, la clave es
`ADMIN_CLAVE` del `.env` del servidor). El filtro **Sin tarjeta** muestra a quienes se registraron pero no activaron la
prueba. Para ver qué pruebas terminan pronto, ejecuta en el servidor (como usuario `calza`, en `/opt/calza`):

```
docker compose exec -T app licita suscripciones --dias 3
```

Calendario de la prueba:

| Día | Qué pasa | Qué haces tú |
|---|---|---|
| 0 | Se registra en calza.cl | Si no registra la tarjeta ese mismo día, mensaje 0 al día siguiente |
| 1 | Registra la tarjeta: empieza la prueba | Mensaje 1 (bienvenida) |
| 2 | Primer resumen a las 8:00 (días hábiles) | Nada: deja que lo use |
| 3 o 4 | | Mensaje 2 (revisión) y ajustas rubros si hace falta |
| 5 | Le llega solo el aviso de primer cobro | Mensaje 3 (conversación de cierre) |
| 8 | Primer cobro, o pasa al plan gratis si canceló | Si canceló, mensaje 4 |

### 0. Se registró pero no activó la prueba (día siguiente)

> Hola [Nombre], soy [tu nombre], de Calza. Vi que creaste tu cuenta, ¡gracias! Te falta un paso para que empiecen a llegarte las licitaciones: registrar tu tarjeta en Mercado Pago. Hoy no se cobra nada: tienes 7 días gratis y, si cancelas antes, no pagas.
>
> Lo haces desde el enlace de tu cuenta (te llegó al registrarte; si no lo tienes, escribe **CUENTA** al WhatsApp de Calza). ¿Te quedó alguna duda o te complicó algo?

Si la respuesta es que no quieren dejar la tarjeta, anótalo: es la información más importante para decidir si se
mantiene la prueba con tarjeta.

### 1. Bienvenida (día 1, después de que registra la tarjeta)

> Hola [Nombre], soy [tu nombre], de Calza. ¡Gracias por sumarte al piloto! 🙌
>
> Desde mañana, cada día hábil a las 8:00, te llega al WhatsApp el resumen con las licitaciones nuevas que calzan con [Empresa]. Para sacarle el jugo:
> • Marca 👍 o 👎 en cada licitación: con eso afinamos lo que te mandamos.
> • Responde con el **número** de una licitación y te mando el detalle.
> • Después escribe **PRECIOS** para ver cuánto ha pagado el Estado por lo mismo.
> • Envíale el **PDF de las bases** y te resume qué piden, plazos, garantías y cómo se evalúa.
>
> Si el primer resumen trae cosas que no tienen nada que ver contigo, avísame y ajustamos tus rubros. Esa es la información que más nos sirve.

### 2. Revisión (día 3 o 4)

> Hola [Nombre], ¿cómo te han llegado los resúmenes de Calza? Cuéntame en una línea:
> 1. ¿Te llegaron licitaciones que sí te interesan?
> 2. ¿Alguna que no tenía nada que ver?
>
> Con eso afino lo que te mandamos. Y si viste una que te interesa, mándame el PDF de las bases y lo revisamos juntos.

Antes de escribir, mira en el panel sus 👍 y 👎: si marcó varios 👎, pregúntale por esas licitaciones en concreto.
Anota cada respuesta: son la mejor información del piloto. Si varias empresas reciben licitaciones que no les sirven,
eso apunta a mejorar el calce. Para ajustar rubros, la empresa puede escribir **PERFIL** a Calza o editarlo en su cuenta.

### 3. Antes del primer cobro (día 5)

Ese mismo día le llega el aviso automático de que en 2 días se hace el primer cobro. Este mensaje es para conversar,
no para cobrar:

> Hola [Nombre], tu prueba de Calza termina el [fecha]. Antes de eso, ¿me regalas 10 minutos para contarme qué te sirvió y qué mejorarías? ¿Te acomoda [día] a las [hora]?
>
> Si te ha servido, no tienes que hacer nada: sigues con tu plan y el precio fundador de $39.990 al mes queda fijo de por vida (solo para los primeros 100 en el plan Pro). Si no, puedes cancelar desde tu cuenta antes del [fecha] y no se cobra nada.

En la conversación pregunta, en este orden: qué licitaciones le sirvieron, cuáles sobraban, si postuló o piensa
postular a alguna, y si el precio le parece razonable para lo que recibe.

### 4. Si canceló (1 o 2 días después de que termina)

> Hola [Nombre], vi que cancelaste Calza. Sin problema, y gracias por probarlo. Me ayudaría mucho saber por qué: ¿te llegaban licitaciones que no te servían, eran pocas o fue el precio? Con una línea basta.
>
> Si más adelante quieres volver, entra a tu cuenta y te suscribes de nuevo. El precio fundador sigue disponible mientras queden cupos.

No insistas después de este mensaje.

## Qué medir

Al terminar cada semana del piloto, revisa en el panel:

- **Activación:** de los que se registraron, cuántos registraron la tarjeta. Si es menos de la mitad, la tarjeta al
  inicio está frenando el piloto.
- **Calce:** porcentaje de 👍 sobre el total de 👍 y 👎. Con menos de 60 %, hay que mejorar el calce antes de sumar más
  empresas.
- **Uso:** cuántas empresas pidieron detalle, PRECIOS o enviaron bases. Quien no interactúa en la primera semana
  probablemente cancela.
- **Conversión:** de las pruebas que terminaron, cuántas pasaron al primer cobro.
