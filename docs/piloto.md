# Mensajes del piloto

Borradores para invitar pymes al piloto de Calza y acompañar a quienes ya están en la prueba.
Usan solo las condiciones vigentes en el sitio: 14 días gratis con funciones Pro y sin tarjeta;
precio fundador Pro de $39.990 al mes de por vida para los primeros 100 clientes; plan Pyme a $19.990 al mes;
10 % de descuento anual. Si cambian (`src/licita/planes.py`), actualiza también estos textos.

Reemplaza lo que está entre corchetes antes de enviar.

## Antes de enviar

- Envía todo desde tu WhatsApp personal, no desde el número de Calza. Fuera de las 24 horas desde el último
  mensaje del cliente, Meta solo deja escribir desde el número de Calza con plantillas aprobadas, y escribir en frío
  a gente que no lo pidió aumenta el riesgo de bloqueos (más aún sin el negocio verificado en Meta).
- Invita solo a personas que conoces o que te dieron permiso.
- Personaliza con un ejemplo real: busca lo que vende la empresa en el buscador de https://calza.cl.
  Una línea como "hoy vi 3 licitaciones de aseo en tu región" pesa más que todo el resto del mensaje.
- A quien escriba BAJA, Calza deja de enviarle mensajes automáticamente. Tú tampoco le vuelvas a escribir.

## Invitación

### 1. WhatsApp (primer contacto)

> Hola [Nombre], soy [tu nombre]. Estamos lanzando **Calza**, un servicio que cada día hábil a las 8:00 te manda por WhatsApp las licitaciones y Compras Ágiles de Mercado Público que calzan con lo que vende [Empresa].
>
> Te invito a ser parte del piloto: son 14 días gratis, sin tarjeta. Solo te pido que me cuentes qué te sirvió y qué no.
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
> - **14 días gratis** con todas las funciones, sin tarjeta.
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

> Hola [Nombre], te escribo de nuevo por Calza. Hoy hay [N] licitaciones abiertas en Mercado Público, y varias podrían calzar con [Empresa]. Si quieres, te muestro cuáles en 2 minutos o te registras directo en https://calza.cl/registro (14 días gratis, sin tarjeta).

Para [N] usa el número de licitaciones abiertas que muestra la portada de calza.cl ese día.

## Durante la prueba

Calza no avisa solo cuando una prueba está por terminar. Para ver a quién le toca el mensaje 3, ejecuta en el servidor
(como usuario `calza`, en `/opt/calza`):

```
docker compose exec -T app licita suscripciones --dias 3
```

Lista las cuentas que vencen en los próximos 3 días.

### 1. Bienvenida (día 1, después de que se registra)

> Hola [Nombre], soy [tu nombre], de Calza. ¡Gracias por sumarte al piloto! 🙌
>
> Desde mañana, cada día hábil a las 8:00, te llega al WhatsApp el resumen con las licitaciones nuevas que calzan con [Empresa]. Para sacarle el jugo:
> • Responde con el **número** de una licitación y te mando el detalle.
> • Después escribe **PRECIOS** para ver cuánto ha pagado el Estado por lo mismo.
> • Envíale el **PDF de las bases** y te resume qué piden, plazos, garantías y cómo se evalúa.
>
> Si el primer resumen trae cosas que no tienen nada que ver contigo, avísame y ajustamos tus rubros. Esa es la información que más nos sirve.

### 2. Revisión (día 5 o 6)

> Hola [Nombre], ¿cómo te han llegado los resúmenes de Calza? Cuéntame en una línea:
> 1. ¿Te llegaron licitaciones que sí te interesan?
> 2. ¿Alguna que no tenía nada que ver?
>
> Con eso afino lo que te mandamos. Y si viste una que te interesa, mándame el PDF de las bases y lo revisamos juntos.

Anota cada respuesta: son la mejor información del piloto. Si varias empresas reciben licitaciones que no les sirven,
eso apunta a mejorar el calce.

### 3. Antes de que termine (día 11 o 12)

> Hola [Nombre], tu prueba de Calza termina el [fecha]. Antes de eso, ¿me regalas 10 minutos esta semana para contarme qué te sirvió y qué mejorarías? ¿Te acomoda [día] a las [hora]?
>
> Si quieres seguir, escríbele **CUENTA** a Calza y te manda el enlace para activar tu plan:
> • **Pro, precio fundador:** $39.990 al mes **de por vida** (solo para los primeros 100). Incluye precios de referencia y alertas urgentes de Compra Ágil.
> • **Pyme:** $19.990 al mes.
> • **Anual:** 10% de descuento.
>
> Si te suscribes ahora, no pierdes días de prueba: el primer cobro es al terminarla.

### 4. Si terminó y no pagó (1 o 2 días después)

> Hola [Nombre], tu prueba de Calza terminó y dejamos de enviarte el resumen. Si no te convenció, me ayudaría mucho saber por qué: ¿te llegaban licitaciones que no te servían, eran pocas o fue el precio? Con una línea basta.
>
> Si quieres volver, escríbele **CUENTA** a Calza y lo reactivas. El precio fundador sigue disponible mientras queden cupos.

No insistas después de este mensaje.
