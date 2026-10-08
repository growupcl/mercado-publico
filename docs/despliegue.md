# Publicar Calza en Vultr (Santiago)

Al terminar esta guía tendrás:

```
NIC Chile (registro de calza.cl y licitainteligente.cl)
   └── servidores DNS → Vultr DNS
                          ├── calza.cl, www ─────────────► VPS Vultr Santiago (Ubuntu 24.04)
                          ├── licitainteligente.cl ──────►   Caddy (HTTPS y redirecciones)
                          │                                    └── Calza (sitio + webhooks)
                          │                                          └── Postgres
                          └── correo (MX, SPF, DKIM) ────► Google Workspace (hola@calza.cl)

Cron en el VPS: ciclo cada 2 h · Compra Ágil cada 20 min · resumen 8:00 · órdenes 23:30 · suscripciones 3:15 · respaldo 2:45
```

Calza queda **separada del hosting de tus clientes**: nada depende de Nebox.

Tiempo estimado: 1 a 1,5 horas, más la espera de propagación de DNS. Costos aproximados (revisa los precios
vigentes): VPS ~US$10–12 al mes, respaldos automáticos de Vultr ~US$2, Google Workspace ~US$7 por usuario al mes.
Vultr DNS no tiene costo.

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

## 2. DNS en Vultr

### 2.1 Crear las zonas en Vultr

En Vultr → **Network → DNS → Add Domain**:

1. Dominio `calza.cl`, con la IP del VPS como dirección por defecto.
2. Repite con `licitainteligente.cl` (misma IP).

Vultr crea registros por defecto. Déjalos así:

**Zona `calza.cl`**

| Tipo | Nombre | Valor | Comentario |
|---|---|---|---|
| A | *(vacío, la raíz)* | IP del VPS | Sitio |
| A | `www` | IP del VPS | Redirige a calza.cl |
| MX | *(vacío)* | Los de Google Workspace (paso 3) | **Borra el MX que Vultr crea por defecto** |
| TXT | *(vacío)* | `v=spf1 include:_spf.google.com ~all` | Autoriza a Google a enviar tu correo |
| TXT | `google._domainkey` | La llave DKIM de Google (paso 3.4) | Firma de tus correos |
| TXT | `_dmarc` | `v=DMARC1; p=none; rua=mailto:hola@calza.cl` | Política antifraude (empieza en modo observación) |

**Zona `licitainteligente.cl`:** solo los registros **A** de la raíz y de `www` hacia la IP del VPS. Borra el MX por
defecto: ese dominio no recibe correo. Caddy redirige ambos a `https://calza.cl`.

### 2.2 Cambiar los servidores DNS en NIC Chile

Hazlo **después** de crear las zonas, para que el dominio no quede sin respuesta.

1. Entra a [nic.cl](https://www.nic.cl) → **Mis dominios** → `calza.cl` → **Modificar servidores de nombre (DNS)**.
2. Deja solo:
   - `ns1.vultr.com`
   - `ns2.vultr.com`
3. Repite con `licitainteligente.cl`.
4. Revisa la propagación en [dnschecker.org](https://dnschecker.org) (registros NS y A). En Chile suele tardar
   minutos a pocas horas.

## 3. Correo con Google Workspace

1. Contrata Google Workspace (plan Business Starter) en [workspace.google.com](https://workspace.google.com) con el
   dominio `calza.cl`.
2. **Verifica el dominio:** Google te da un registro TXT (`google-site-verification=…`). Agrégalo en Vultr DNS, en la
   raíz de `calza.cl`, y confirma en Google.
3. **Activa Gmail:** Google indica el registro MX (hoy, `smtp.google.com` con prioridad 1). Agrégalo en Vultr DNS y
   borra cualquier otro MX.
4. **Activa DKIM:** en la consola de administración → Aplicaciones → Google Workspace → Gmail →
   **Autenticar correo electrónico** → generar registro (2048 bits). Copia el TXT `google._domainkey` en Vultr DNS y
   vuelve a la consola a **Iniciar autenticación** (puede pedir esperar unas horas).
5. Crea el usuario o alias **`hola@calza.cl`**. Un alias en tu propio usuario no tiene costo extra.
6. Prueba: envía un correo desde `hola@calza.cl` a una cuenta de Gmail y, en "Mostrar original", revisa que SPF,
   DKIM y DMARC digan **PASS**.

Este correo es el que usarás para Meta, Mercado Pago y el contacto con clientes. Cuando Meta verifique el dominio,
te pedirá agregar otro TXT (`facebook-domain-verification=…`): va en la misma zona de Vultr DNS.

## 4. Preparar el servidor

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

El script `preparar-servidor.sh` deja la hora de Chile, parches de seguridad automáticos, Docker, cortafuegos (solo SSH, HTTP y HTTPS),
fail2ban y el usuario `calza`, que es el que corre la aplicación.

## 5. Configurar las claves

Desde aquí, todo como usuario `calza`. Al terminar el paso 4 el script te indica cómo seguir:

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
| `DOMINIOS_REDIRIGIDOS` | `licitainteligente.cl, www.licitainteligente.cl` |
| `LICITA_URL_PUBLICA` | `https://calza.cl` |
| `LICITA_URL_REGISTRO` | `https://calza.cl/registro` |
| `ANTHROPIC_API_KEY` | Clave de Claude |
| `MERCADOPUBLICO_TICKET` | Ticket de la API de Mercado Público (cuando llegue) |
| `MERCADOPAGO_ACCESS_TOKEN`, `MERCADOPAGO_WEBHOOK_SECRET` | Ver [mercadopago.md](mercadopago.md) |
| `LICITA_PRESTADOR` | `Virtus SpA` |
| `WHATSAPP_*`, `LICITA_WHATSAPP_PUBLICO` | Ver [whatsapp-masivo-app.md](whatsapp-masivo-app.md) |

Lo que aún no tengas (ticket, WhatsApp, Mercado Pago) puede quedar vacío: el sitio funciona igual y esas partes
se activan al completar la variable y reiniciar (`docker compose up -d`).

## 6. Levantar Calza

```bash
docker compose up -d --build
docker compose ps
```

Los tres servicios (`app`, `db`, `caddy`) deben aparecer en `running`, y `app` y `db` en `healthy`.
Abre **https://calza.cl**: Caddy obtiene los certificados HTTPS solo, apenas el DNS apunta al servidor.
Revisa también que `https://www.calza.cl` y `https://licitainteligente.cl` redirijan a `https://calza.cl`.

Si no carga: `docker compose logs caddy` (problemas de DNS o certificado) y `docker compose logs app`.

## 7. Activar las tareas programadas

```bash
crontab /opt/calza/deploy/crontab
crontab -l
```

| Cuándo | Qué hace |
|---|---|
| Cada 2 horas, de 7:00 a 21:00 | Licitaciones nuevas, clasificación con IA y calces |
| Días hábiles, 8:00 | Resumen diario por WhatsApp |
| Días hábiles, cada 20 min de 8:00 a 20:40 | Compras Ágiles nuevas y alertas urgentes del plan Pro ([compra-agil.md](compra-agil.md)) |
| 23:30 | Órdenes de compra del día (precios de referencia) |
| 3:15 | Vencimiento de suscripciones impagas |
| 2:45 | Respaldo de la base de datos (se guardan 14 días en `/var/backups/calza`) |

El registro de cada tarea queda en `/opt/calza/logs/tareas.log`.

Los límites `--max-detalles` del crontab cuidan el límite diario de consultas del ticket de Mercado Público.
Ajústalos cuando veamos el volumen real.

## 8. Conectar WhatsApp y Mercado Pago

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
| Ver y registrar facturas ([facturacion.md](facturacion.md)) | `docker compose exec app licita facturas` |
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

## Cambios en la base de datos

Calza usa migraciones (Alembic). Al publicar una versión nueva con `deploy/actualizar.sh`, la aplicación aplica sola
las migraciones pendientes al iniciar, sin perder datos. Para revisarlo a mano:

```bash
docker compose exec app licita migrar
```

Antes de una actualización que cambie la base, conviene correr `deploy/respaldo.sh`.
