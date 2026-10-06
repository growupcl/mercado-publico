# Licita

Copiloto con IA para pymes que le venden al Estado a través de [Mercado Público](https://www.mercadopublico.cl).
Encuentra las licitaciones que calzan con lo que vende cada empresa y se las envía en un resumen diario.

> Estado: **MVP, fase 1** (motor de datos y calce). WhatsApp, web y cobro vienen en las fases siguientes.

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
5. **Resumen diario** (`licita resumen`): arma el mensaje con las mejores oportunidades, listo para enviar por WhatsApp.

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

## Uso

```bash
licita sync --fecha 2026-10-06 --max-detalles 200
licita clasificar
licita empresa-agregar --nombre "Aseo Sur" \
  --descripcion "Vendemos insumos de aseo, guantes y bolsas de basura a instituciones" \
  --regiones "Biobío,Ñuble" --monto-max 30000000
licita calce --empresa 1
licita resumen --empresa 1 --marcar
```

## Pruebas

```bash
pytest
```

Las pruebas usan respuestas de ejemplo con el formato de la API (`tests/fixtures/`) y un cliente de Claude simulado,
así que no necesitan ticket ni clave.

## Estructura

| Archivo | Qué hace |
|---|---|
| `src/licita/mercadopublico.py` | Cliente de la API de Mercado Público (reintentos, pausa entre consultas) y normalización de datos |
| `src/licita/db.py` | Modelos de base de datos: licitaciones, órdenes de compra, empresas y calces |
| `src/licita/sync.py` | Sincronización diaria hacia la base de datos propia |
| `src/licita/ia.py` | Clasificación, extracción de perfil y evaluación de calce con Claude (salidas estructuradas) |
| `src/licita/calce.py` | Prefiltro sin IA y búsqueda de calces |
| `src/licita/resumen.py` | Resumen diario en formato WhatsApp |
| `src/licita/cli.py` | Línea de comandos |
| `docs/modelo_financiero_licita.xlsx` | Modelo financiero (escenarios base y conservador) |

## Próximas fases

- Probar con datos reales: validar formato de respuesta, volumen diario y límites del ticket.
- Envío por WhatsApp (Cloud API de Meta) con plantillas *utility* y botones.
- Análisis de bases (PDF) con Claude Sonnet y caché compartido entre usuarios.
- Inteligencia de precios a partir de las órdenes de compra.
- Web de registro, suscripción (Flow o Mercado Pago) y factura electrónica.
