# Publicar Calza en un VPS de Vultr (Santiago)

Al terminar esta guía tendrás:

```
Nebox (cPanel)                      VPS Vultr Santiago (Ubuntu 24.04)
├── DNS de calza.cl ───────────────►  Caddy (HTTPS automático)
│                                       └── Calza (sitio web + webhooks de WhatsApp y Mercado Pago)
├── Correo hola@calza.cl                      └── Postgres (base de datos)
└── Redirección licitainteligente.cl  Cron: ciclo cada 2 h · resumen 8:00 · órdenes 23:30 ·
                                       suscripciones 3:15 · respaldo 2:45
```

Tiempo estimado: 45 a 60 minutos, más la espera de DNS. Costo: unos US$10–12 al mes por el VPS más
~US$2 por los respaldos automáticos de Vultr (revisa los precios vigentes al contratar).

## 1. Crear el VPS en Vultr

1. Si no tienes una llave SSH en tu computador, créala: `ssh-keygen -t ed25519` (Enter a todo) y copia el
   contenido de `~/.ssh/id_ed25519.pub`.
2. En [vultr.com](https://www.vultr.com) → **Deploy New Server**:
   - **Tipo:** Cloud Compute, Shared CPU (Regular Performance).
   - **Ubicación:** **Santiago**.
   - **Imagen:** Ubuntu 24.04 LTS x64.
   - **Plan:** 2 GB de RAM / 1 vCPU (suficiente para el piloto).
   - **Auto Backups:** activado (respaldo completo del servidor, además del respaldo diario de la base).
   - **SSH Keys:** agrega la llave del paso 1.
   - **Hostname:** `calza`.
3. Anota la **IP pública** del servidor.

## 2. Apuntar el dominio desde Nebox

En el cPanel de Nebox → **Zone Editor** de `calza.cl`:

| Tipo | Nombre | Valor |
|---|---|---|
| A | `calza.cl.` | IP del VPS |
| A | `www.calza.cl.` | IP del VPS |

- **No toques los registros MX** ni los del correo: `hola@calza.cl` sigue funcionando en Nebox.
- Si existen registros A anteriores para `calza.cl` o `www`, reemplázalos.
- Para `licitainteligente.cl`: en cPanel → **Redirects**, redirige (301) a `https://calza.cl`.
- Revisa que el cambio se propagó con [dnschecker.org](https://dnschecker.org) (puede tardar de minutos a unas horas).

## 3. Preparar el servidor

Conéctate como root: `ssh root@IP_DEL_VPS`

1. Crea la llave de despliegue para que el servidor pueda bajar el código (solo lectura):

```bash
ssh-keygen -t ed25519 -f /root/.ssh/github_calza -N "" -C "calza-vps"
printf 'Host github.com\n  IdentityFile ~/.ssh/github_calza\n  IdentitiesOnly yes\n' >> /root/.ssh/config
ssh-keyscan github.com >> /root/.ssh/known_hosts
cat /root/.ssh/github_calza.pub
```

2. En GitHub → repositorio `growupcl/mercado-publico` → **Settings → Deploy keys → Add deploy key**: pega la
   llave pública, nombre "VPS Calza", **sin** marcar "Allow write access".
3. Baja el código y prepara el servidor:

```bash
git clone git@github.com:growupcl/mercado-publico.git /opt/calza
bash /opt/calza/deploy/preparar-servidor.sh
```

El script deja la hora de Chile, parches de seguridad automáticos, Docker, cortafuegos (solo SSH, HTTP y HTTPS),
fail2ban y el usuario `calza`, que es el que corre la aplicación.

## 4. Configurar las claves

Desde aquí, todo como usuario `calza`:

```bash
su - calza
cd /opt/calza
cp .env.example .env
chmod 600 .env
nano .env
```

Completa al menos:

| Variable | Valor |
|---|---|
| `POSTGRES_PASSWORD` | El resultado de `openssl rand -base64 24` |
| `DOMINIO` | `calza.cl` |
| `LICITA_URL_PUBLICA` | `https://calza.cl` |
| `LICITA_URL_REGISTRO` | `https://calza.cl/registro` |
| `ANTHROPIC_API_KEY` | Clave de Claude |
| `MERCADOPUBLICO_TICKET` | Ticket de la API de Mercado Público (cuando llegue) |
| `MERCADOPAGO_ACCESS_TOKEN`, `MERCADOPAGO_WEBHOOK_SECRET` | Ver [mercadopago.md](mercadopago.md) |
| `LICITA_PRESTADOR` | `Virtus SpA` |
| `WHATSAPP_*`, `LICITA_WHATSAPP_PUBLICO` | Ver [whatsapp-masivo-app.md](whatsapp-masivo-app.md) |

Lo que aún no tengas (ticket, WhatsApp, Mercado Pago) puede quedar vacío: el sitio funciona igual y esas partes
se activan al completar la variable y reiniciar (`docker compose up -d`).

## 5. Levantar Calza

```bash
docker compose up -d --build
docker compose ps
```

Los tres servicios (`app`, `db`, `caddy`) deben aparecer en `running`, y `app` y `db` en `healthy`.
Abre **https://calza.cl**: Caddy obtiene el certificado HTTPS solo, apenas el DNS apunta al servidor.

Si no carga: `docker compose logs caddy` (problemas de DNS o certificado) y `docker compose logs app`.

## 6. Activar las tareas programadas

```bash
crontab /opt/calza/deploy/crontab
crontab -l
```

| Cuándo | Qué hace |
|---|---|
| Cada 2 horas, de 7:00 a 21:00 | Licitaciones nuevas, clasificación con IA y calces |
| Días hábiles, 8:00 | Resumen diario por WhatsApp |
| 23:30 | Órdenes de compra del día (precios de referencia) |
| 3:15 | Vencimiento de suscripciones impagas |
| 2:45 | Respaldo de la base de datos (se guardan 14 días en `/var/backups/calza`) |

El registro de cada tarea queda en `/opt/calza/logs/tareas.log`.

Los límites `--max-detalles` del crontab cuidan el límite diario de consultas del ticket de Mercado Público.
Ajústalos cuando veamos el volumen real.

## 7. Conectar WhatsApp y Mercado Pago

- **WhatsApp:** URL del webhook `https://calza.cl/webhook/whatsapp` ([whatsapp-masivo-app.md](whatsapp-masivo-app.md), paso 5).
- **Mercado Pago:** URL del webhook `https://calza.cl/pagos/mercadopago/webhook` ([mercadopago.md](mercadopago.md)).

## Operación del día a día

| Necesito… | Comando (como `calza`, en `/opt/calza`) |
|---|---|
| Publicar una versión nueva | `deploy/actualizar.sh` |
| Ver qué pasa en el sitio | `docker compose logs -f app` |
| Ver las tareas programadas | `tail -f logs/tareas.log` |
| Correr una tarea a mano | `deploy/tareas.sh ciclo` (o `whatsapp-enviar`, `suscripciones`…) |
| Usar cualquier comando de Calza | `docker compose exec app licita --help` |
| Respaldar ahora | `deploy/respaldo.sh` |
| Restaurar un respaldo | `gunzip -c /var/backups/calza/calza-AAAA-MM-DD.sql.gz \| docker compose exec -T db psql -U calza -d calza` |

**Respaldos fuera del servidor:** además de los automáticos de Vultr, copia de vez en cuando
`/var/backups/calza` a otro lugar, por ejemplo con `scp calza@IP:/var/backups/calza/*.gz .`.

## Seguridad

- [ ] `.env` con permisos `600` y nunca en el repositorio (ya está en `.gitignore`).
- [ ] Entrar solo con llave SSH: cuando confirmes que entras con tu llave como `calza`, en
      `/etc/ssh/sshd_config` deja `PasswordAuthentication no` y reinicia con `systemctl restart ssh`.
- [ ] La llave de GitHub del servidor es de **solo lectura**.
- [ ] Postgres no queda expuesto a internet: solo la aplicación lo ve, dentro de Docker.

## Antes de tener datos reales importantes

Hoy la base de datos se crea automáticamente al iniciar. Antes del próximo cambio de estructura con clientes reales
agregaremos migraciones (Alembic), para actualizar sin perder datos.
