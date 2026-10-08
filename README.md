# Calza

Copiloto con IA para pymes que le venden al Estado a través de [Mercado Público](https://www.mercadopublico.cl).
Encuentra las licitaciones que calzan con lo que vende cada empresa y se las envía en un resumen diario.

Sitio: [calza.cl](https://calza.cl) · El nombre interno del proyecto y del comando es `licita`.

> Estado: **MVP, fase 5** (motor de datos, calce con IA, WhatsApp, análisis de bases, inteligencia de precios, y web de registro con pagos).

## Cómo funciona

1. **Sincronización** (`licita sync`): descarga las licitaciones del día (y opcionalmente las órdenes de compra)
   desde la [API de Mercado Público](https://api.mercadopublico.cl) y las guarda en una base de datos propia.
   Solo pide el detalle de las licitaciones nuevas o que cambiaron de estado, para cuidar el límite diario del ticket.
2. **Clasificación con IA** (`licita clasificar`): Claude Haiku resume cada licitación y extrae rubros y palabras clave.
   Se hace una sola vez por licitación y se reutiliza para todos los usuarios.
3. **Perfil de la empresa** (`licita empresa-agregar`): la pyme describe su negocio en lenguaje natural y la IA
   extrae sus palabras clave.
4. **Calce** (`licita calce`): un prefiltro sin IA (estado, cierre, región, monto, palabras clave) elige las mejores
   candidatas y la IA les asigna un puntaje de 0 a 100 con una explicación.
5. **Resumen diario** (`licita resumen`): arma el mensaje con las mejores oportunidades.
6. **WhatsApp** (`licita whatsapp-enviar` y `licita servidor`): envía el resumen diario como una plantilla con botones
   y responde los mensajes del usuario (detalle de cada licitación, baja/alta). Ver [docs/whatsapp.md](docs/whatsapp.md).
7. **Análisis de bases** (`licita analizar` o enviando el PDF por WhatsApp): Claude Sonnet lee el PDF de las bases y
   entrega requisitos que dejan fuera, documentos a presentar, garantías, criterios de evaluación, plazos, multas y
   preguntas sugeridas para el foro. Después, el usuario puede hacer preguntas sobre esas bases por 24 horas.
   Cada PDF se analiza una sola vez y el resultado se reutiliza para todos (el mismo archivo no vuelve a costar IA).
8. **Inteligencia de precios** (`licita precios` o escribiendo *PRECIOS* en WhatsApp, plan Pro): para cada ítem de una
   licitación muestra cuánto ha pagado el Estado (mediana y rango habitual de los últimos 24 meses), un rango de precio
   competitivo, quién suele ganar y si el presupuesto alcanza a precios de mercado. Se alimenta de las órdenes de compra
   y de las licitaciones adjudicadas que trae `licita sync --ordenes`, y del **histórico de órdenes de compra** de los
   datos abiertos de ChileCompra (`licita historico-oc`, ver [docs/historico-oc.md](docs/historico-oc.md)); no usa IA,
   así que no tiene costo variable.
9. **Sitio web, registro y pagos** (`licita servidor`): página de inicio con los planes, registro con 14 días de prueba
   del plan Pro sin tarjeta, cuenta con enlace privado (sin contraseñas; por WhatsApp se pide con *CUENTA*) y
   **suscripción con Mercado Pago** (tarjeta de crédito o débito) que cobra sola cada mes o cada año. Si el cliente se
   suscribe durante la prueba, el primer cobro es al terminar la prueba. Puede cambiar de plan o cancelar la
   renovación desde su cuenta. Precio fundador del plan Pro para los primeros 100 clientes, congelado de por vida.
   Al vencer sin pago, la cuenta pasa al plan gratis (`licita suscripciones`, una vez al día; con renovación
   automática hay 3 días de gracia para que llegue el cobro). Si un cobro es rechazado, Calza avisa al cliente
   por WhatsApp. Durante el piloto factura **Virtus SpA** (`LICITA_PRESTADOR`).
10. **Compra Ágil** (`licita compra-agil`, cada 20 minutos, plan Pro): trae las Compras Ágiles recién publicadas desde
    la [API Compra Ágil v2](docs/compra-agil.md), pide el detalle solo de las que comparten palabras clave con un
    cliente Pro, las evalúa con IA y avisa **de inmediato** por WhatsApp las que calzan, porque en Compra Ágil suele
    ganar quien cotiza primero. Máximo 3 alertas al día por cliente, de 8:00 a 21:00. De noche
    (`licita compra-agil-precios`) guarda las cotizaciones de las que cerraron, y *PRECIOS* muestra a cuánto cotiza la
    competencia y cuál suele ser la cotización más baja (la que gana).

Las órdenes de compra quedan guardadas para construir más adelante la inteligencia de precios.

## Instalación

Requiere Python 3.10 o superior.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # completar MERCADOPUBLICO_TICKET y ANTHROPIC_API_KEY
set -a && source .env && set +a
```

- **Ticket de Mercado Público**: se solicita con Clave Única en [chilecompra.cl/api](https://www.chilecompra.cl/api) y llega por correo.
- **Clave de Claude**: en [console.anthropic.com](https://console.anthropic.com).
- **WhatsApp** (opcional): ver [docs/whatsapp.md](docs/whatsapp.md), y la guía de conexión a través de Masivo App: [docs/whatsapp-masivo-app.md](docs/whatsapp-masivo-app.md).

Nota: si ya tenías una base `licita.db` de la fase 1, bórrala para que se cree con las tablas nuevas
(todavía no hay migraciones; se agregarán antes de pasar a producción).

## Uso

```bash
licita sync --fecha 2026-10-06 --max-detalles 200
licita clasificar
licita empresa-agregar --nombre "Aseo Sur" \
  --descripcion "Vendemos insumos de aseo, guantes y bolsas de basura a instituciones" \
  --regiones "Biobío,Ñuble" --monto-max 30000000
licita calce --empresa 1
licita resumen --empresa 1 --marcar
licita analizar --pdf bases.pdf --codigo 1234-56-LE26
licita precios --codigo 1234-56-LE26
licita compra-agil                 # Compras Ágiles nuevas, calce y alertas urgentes (plan Pro)
licita historico-oc --meses 24     # precios del histórico de órdenes de compra (carga inicial; luego cada semana)
licita compra-agil-precios         # cotizaciones de las Compras Ágiles que cerraron (una vez al día)
licita ciclo                       # sincroniza, clasifica y busca calces para todas las empresas activas
licita servidor --puerto 8000      # sitio web en http://localhost:8000 y webhook de WhatsApp
licita suscripciones               # vence las suscripciones impagas (programar una vez al día)
```

Para probar los pagos, usa las **credenciales de prueba** de la cuenta de Mercado Pago (Tus integraciones →
Credenciales de prueba) en `MERCADOPAGO_ACCESS_TOKEN`, configura un webhook hacia
`https://<tu-dominio>/pagos/mercadopago/webhook` con los eventos *Planes y suscripciones*, copia su clave secreta en
`MERCADOPAGO_WEBHOOK_SECRET` y paga con un usuario y tarjetas de prueba. Ver [docs/mercadopago.md](docs/mercadopago.md).

**Facturas:** cada pago aprobado queda pendiente de factura. `licita facturas` muestra los datos listos para emitirla
en el portal gratuito del SII y `licita facturas --emitida PAGO_ID --folio N` registra el folio. Ver
[docs/facturacion.md](docs/facturacion.md).

## Publicar en el servidor

Calza corre en un VPS (Vultr, Santiago) con Docker: aplicación, Postgres y Caddy (HTTPS automático), más tareas
programadas y respaldos diarios. Guía paso a paso en [docs/despliegue.md](docs/despliegue.md).

```bash
docker compose up -d --build      # en el servidor, con .env completo
crontab deploy/crontab            # tareas programadas
deploy/actualizar.sh              # publicar una versión nueva
```

## Base de datos y migraciones

La estructura de la base se maneja con migraciones (Alembic, en `src/licita/migraciones/`). Calza aplica las
pendientes al iniciar cualquier comando. Si cambias un modelo en `db.py`:

```bash
licita migrar --nueva "agrega campo X"    # genera la migración; revísala antes de publicarla
```

Las columnas nuevas obligatorias en tablas con datos necesitan un valor por defecto en la base (`server_default`).
Una prueba automática falla si un modelo cambia sin su migración.

## Pruebas

```bash
pytest
```

Las pruebas usan respuestas de ejemplo con el formato de la API (`tests/fixtures/`) y un cliente de Claude simulado,
así que no necesitan ticket ni clave. Para correrlas contra Postgres:
`LICITA_TEST_DATABASE_URL=postgresql+psycopg://usuario@host/base pytest`.

## Estructura

| Archivo | Qué hace |
|---|---|
| `src/licita/mercadopublico.py` | Cliente de la API de Mercado Público (reintentos, pausa entre consultas) y normalización de datos |
| `src/licita/db.py` | Modelos de base de datos: licitaciones, órdenes de compra, empresas y calces |
| `src/licita/compra_agil.py` | Cliente de la API Compra Ágil v2 y sincronización (se guardan como licitaciones tipo COT) |
| `src/licita/alertas.py` | Alertas urgentes de Compra Ágil por WhatsApp (plan Pro) |
| `src/licita/historico.py` | Carga de precios desde el histórico mensual de órdenes de compra (datos abiertos) |
| `src/licita/sync.py` | Sincronización diaria hacia la base de datos propia |
| `src/licita/ia.py` | Clasificación, extracción de perfil y evaluación de calce con Claude (salidas estructuradas) |
| `src/licita/calce.py` | Prefiltro sin IA y búsqueda de calces |
| `src/licita/resumen.py` | Textos del resumen diario y del detalle de cada licitación |
| `src/licita/whatsapp.py` | Cliente de la WhatsApp Cloud API de Meta y verificación de firma |
| `src/licita/notificaciones.py` | Envío del resumen diario cuidando la ventana gratuita de 24 h |
| `src/licita/conversacion.py` | Respuestas a los mensajes entrantes (botones, números, PDF de bases, preguntas, baja/alta) |
| `src/licita/precios.py` | Inteligencia de precios: referencias por producto, rango competitivo y proveedores frecuentes |
| `src/licita/web.py` y `src/licita/plantillas/` | Sitio web: inicio, registro, cuenta, retorno de pagos, términos y privacidad |
| `src/licita/avisos.py` | Avisos al cliente sobre su cuenta (cobro rechazado) |
| `src/licita/suscripciones.py` | Registro, prueba gratuita, pagos, activación y vencimientos |
| `src/licita/mercadopago.py` | Cliente de suscripciones de Mercado Pago y verificación de la firma de sus avisos |
| `src/licita/planes.py` | Planes y precios (con IVA), descuento anual y precio fundador |
| `src/licita/legal.py` | **Borrador** de términos y política de privacidad: revisar con un abogado antes del lanzamiento |
| `src/licita/analisis.py` | Análisis de bases en PDF con Claude Sonnet, reutilización por archivo y límite mensual |
| `src/licita/servidor.py` | Servidor web que recibe los webhooks de WhatsApp |
| `src/licita/migrar.py` y `src/licita/migraciones/` | Migraciones de la base de datos (Alembic) |
| `src/licita/plantillas_whatsapp.py` | Plantillas de WhatsApp (texto que se envía a aprobación y el que se usa) |
| `src/licita/tareas.py` | Ciclo periódico: sincronizar, clasificar y buscar calces |
| `src/licita/cli.py` | Línea de comandos |
| `Dockerfile`, `compose.yaml`, `deploy/` | Despliegue: imagen, servicios, HTTPS, cron, respaldos y preparación del servidor |
| `docs/modelo_financiero_licita.xlsx` | Modelo financiero (escenarios base y conservador) |

## Próximas fases

- Probar el puntaje con IA y el análisis de bases con datos reales (el formato de la API, el volumen diario y el enlace a la ficha ya están validados).
- Descarga automática de las bases: no hay vía oficial y los términos apuntan en contra; se consultó a ChileCompra
  (ver [docs/descarga-de-bases.md](docs/descarga-de-bases.md)). Hoy el usuario reenvía el PDF.
- Factura electrónica automática al confirmar cada pago (hoy se emite a mano en el portal del SII, ver docs/facturacion.md).
- Aviso por correo (además de WhatsApp) cuando un cobro automático es rechazado.
