# Sismo Venezuela Monitor

Monitor automatico para seguir sismos y noticias relacionadas con Venezuela, pensado para ejecutarse con GitHub Actions y enviar avisos a Discord y/o correo electronico.

El proyecto consulta fuentes publicas, arma un resumen compacto para Discord, envia un resumen completo por email cuando corresponde, evita repetir noticias ya enviadas y guarda estado en el repositorio para recordar que contenido ya fue notificado.

> Este monitor no reemplaza fuentes oficiales, servicios de emergencia ni criterio humano. Es una herramienta de seguimiento y reenvio responsable.

## Que hace

- Consulta eventos sismicos de USGS dentro de una zona que cubre Venezuela y areas cercanas.
- Busca noticias relevantes sobre sismos, replicas, terremotos y reportes oficiales.
- Prioriza fuentes conocidas y permite bloquear republicadores ruidosos.
- Deduplica noticias similares aunque Google News entregue enlaces o titulares ligeramente distintos.
- Evita enviar una notificacion si el contenido no cambio desde el ultimo envio.
- Publica un mensaje compacto en Discord mediante webhook.
- Puede enviar un correo completo por SMTP.
- Incluye un bloque corto listo para reenviar por WhatsApp.
- Puede enviar un resumen diario una vez al dia.
- Actualiza `state/state.json` para mantener historial entre ejecuciones.

## Estructura

```txt
.github/workflows/sismo-monitor.yml
src/sismo_monitor.py
requirements.txt
README.md
.gitignore
state/.gitkeep
state/state.json
```

`state/state.json` se crea o actualiza automaticamente. No lo borres si ya esta en uso, porque ahi se guarda que eventos, noticias y resumenes ya fueron enviados.

## Como funciona

Cada ejecucion hace lo siguiente:

1. Lee el estado anterior desde `state/state.json`.
2. Consulta USGS para eventos sismicos recientes.
3. Consulta fuentes RSS/Google News configuradas para noticias relevantes.
4. Filtra contenido no relacionado con Venezuela o sismos.
5. Deduplica eventos y noticias.
6. Compara el resultado actual con lo enviado anteriormente.
7. Si hay cambios, envia Discord/correo segun configuracion.
8. Si el envio fue exitoso por al menos un canal, actualiza el estado.
9. GitHub Actions commitea el nuevo `state/state.json`.

Por defecto el workflow corre cada hora:

```yaml
schedule:
  - cron: "7 * * * *"
```

GitHub interpreta ese cron en UTC.

## Fuentes

Eventos sismicos:

```txt
USGS Earthquake Catalog API
```

Noticias:

```txt
Google News RSS general para Venezuela
Google News RSS dirigido a FUNVISIS
Google News RSS dirigido a Proteccion Civil
Consultas dirigidas a ReliefWeb, GDACS, Reuters, AP, BBC Mundo y medios venezolanos
```

Tambien se pueden reemplazar las fuentes de noticias por RSS propios con `NEWS_RSS_URLS`.

## Configuracion rapida

1. Crea o clona un repositorio con estos archivos.
2. Activa GitHub Actions en el repositorio.
3. Agrega los secrets necesarios en `Settings -> Secrets and variables -> Actions`.
4. Configura el webhook de Discord y/o SMTP.
5. Ejecuta manualmente el workflow desde `Actions -> Monitor Sismo Venezuela -> Run workflow`.

El workflow ya incluye la configuracion base para Venezuela:

```yaml
DIGEST_TZ: America/Caracas
DIGEST_TZ_LABEL: VET
LOOKBACK_HOURS: "72"
MIN_MAGNITUDE: "2.5"
MAX_NEWS: "12"
MAX_QUAKES: "8"
NOTIFY_ONLY_ON_CHANGE: "true"
SEND_EMPTY_DIGEST: "false"
```

## Secrets

### Discord

Para enviar mensajes a Discord necesitas un webhook:

```txt
DISCORD_WEBHOOK_URL
```

En Discord:

1. Abre el servidor y canal donde quieres recibir alertas.
2. Entra a `Edit Channel -> Integrations -> Webhooks`.
3. Crea un webhook.
4. Copia la URL.
5. Guardala como secret `DISCORD_WEBHOOK_URL`.

### Correo SMTP

Para enviar correo configura estos secrets:

```txt
SMTP_HOST
SMTP_PORT
SMTP_USERNAME
SMTP_PASSWORD
EMAIL_FROM
EMAIL_TO
```

Opcional:

```txt
EMAIL_REPLY_TO
```

`EMAIL_TO` acepta varios destinatarios separados por coma o punto y coma.

Ejemplo con Gmail:

```txt
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=tu_correo@gmail.com
SMTP_PASSWORD=tu_app_password_de_google
EMAIL_FROM=tu_correo@gmail.com
EMAIL_TO=destino1@gmail.com,destino2@gmail.com
```

Para Gmail debes usar una app password, no la clave normal de la cuenta.

## Variables principales

### Canales

```yaml
SEND_DISCORD: "auto"
SEND_EMAIL: "auto"
DISCORD_STRICT: "false"
EMAIL_STRICT: "false"
```

`auto` activa el canal solo si existen los secrets requeridos.

Con `DISCORD_STRICT=false` o `EMAIL_STRICT=false`, si un canal falla pero el otro funciona, el workflow no falla. Si ambos canales fallan, la ejecucion falla y no marca las novedades como enviadas.

### Discord

```yaml
DISCORD_MESSAGE_MODE: "compact"
DISCORD_MAX_CHARS: "1800"
SHORTEN_URLS_FOR_DISCORD: "true"
URL_SHORTENER: "isgd"
```

`compact` manda a Discord un resumen corto con enlaces. El correo, si esta activo, recibe el detalle completo.

Para mandar todo a Discord:

```yaml
DISCORD_MESSAGE_MODE: "full"
```

Para desactivar enlaces cortos:

```yaml
URL_SHORTENER: "none"
SHORTEN_URLS_FOR_DISCORD: "false"
SHORTEN_URLS_FOR_FORWARD: "false"
```

### Monitoreo

```yaml
LOOKBACK_HOURS: "72"
MIN_MAGNITUDE: "2.5"
MAX_NEWS: "12"
MAX_QUAKES: "8"
NEWS_FEED_TIMEOUT: "30"
```

- `LOOKBACK_HOURS`: ventana de busqueda hacia atras.
- `MIN_MAGNITUDE`: magnitud minima para eventos USGS.
- `MAX_NEWS`: maximo de noticias incluidas.
- `MAX_QUAKES`: maximo de sismos incluidos.
- `NEWS_FEED_TIMEOUT`: timeout por fuente RSS.

### Fuentes de noticias

Para reemplazar las busquedas por tus propios RSS:

```yaml
NEWS_RSS_URLS: "https://example.com/feed.xml,https://example.org/rss"
```

Para bloquear fuentes ruidosas:

```yaml
NEWS_SOURCE_BLOCKLIST: "\\b(vietnam\\.vn)\\b"
```

El valor es una expresion regular.

### Deduplicacion y cambios

```yaml
NOTIFY_ONLY_ON_CHANGE: "true"
SEND_EMPTY_DIGEST: "false"
```

El monitor no envia nada si:

- No hay eventos o noticias nuevas.
- El conjunto de contenido detectado coincide con el ultimo digest enviado.
- Ya se envio el resumen diario de ese dia y no se forzo manualmente.

La deduplicacion usa:

```txt
ID del evento sismico
Huella estable del titulo de noticia
Huella del digest completo enviado
```

Esto ayuda cuando Google News entrega el mismo articulo con otro enlace o cuando varios medios publican titulares casi iguales.

### Resumen diario

```yaml
DAILY_SUMMARY_ENABLED: "true"
DAILY_SUMMARY_HOUR: "9"
DAILY_SUMMARY_TO_DISCORD: "true"
DAILY_SUMMARY_TO_EMAIL: "true"
```

El resumen diario se envia una vez al dia cuando la hora local sea igual o posterior a `DAILY_SUMMARY_HOUR`.

Si el contenido no cambio desde el ultimo resumen y `NOTIFY_ONLY_ON_CHANGE=true`, el resumen se salta y queda registrado en el estado.

### Bloque para WhatsApp

```yaml
FORWARD_BLOCK_ENABLED: "true"
SHORTEN_URLS_FOR_FORWARD: "true"
```

El correo completo incluye un bloque corto para reenviar:

```txt
📲 Texto corto para reenviar:

🚨 Actualizacion sismo Venezuela - 25/06 09:07 VET
- Prioridad: 🟡 Media
- M4.6 - Venezuela / zona cercana - 25/06 08:55 VET
  Fuente: https://is.gd/xxxxx
- Titulo de noticia relevante (Medio)
  Fuente: https://is.gd/yyyyy
- Verificar fuentes oficiales antes de compartir.
```

## Ejecucion manual

Desde GitHub:

1. Entra al repositorio.
2. Abre `Actions`.
3. Selecciona `Monitor Sismo Venezuela`.
4. Presiona `Run workflow`.

Opciones disponibles:

```txt
force_send
send_daily_summary
```

`force_send` reenvia lo detectado aunque ya este marcado como visto.

`send_daily_summary` fuerza el resumen diario en ese momento.

## Probar localmente

Instala dependencias:

```bash
python -m pip install -r requirements.txt
```

Ejecuta sin enviar nada real:

```bash
DRY_RUN=true \
SEND_DISCORD=true \
SEND_EMAIL=false \
DISCORD_WEBHOOK_URL=https://discord.invalid/webhook \
STATE_FILE=state/local-test-state.json \
python src/sismo_monitor.py
```

En PowerShell:

```powershell
$env:DRY_RUN = "true"
$env:SEND_DISCORD = "true"
$env:SEND_EMAIL = "false"
$env:DISCORD_WEBHOOK_URL = "https://discord.invalid/webhook"
$env:STATE_FILE = "state/local-test-state.json"
$env:PYTHONIOENCODING = "utf-8"
python src\sismo_monitor.py
```

`DRY_RUN=true` imprime el preview en consola y no envia Discord ni correo.

## Estado persistente

El archivo `state/state.json` guarda:

```txt
seen_ids
seen_signatures
url_cache
last_sent_at
last_sent_count
last_sent_digest_fingerprint
last_daily_summary_date
last_daily_summary_digest_fingerprint
```

El workflow commitea ese archivo al final de cada ejecucion si hubo cambios:

```yaml
permissions:
  contents: write
```

Si el repositorio no permite a GitHub Actions escribir contenido, el monitor podra enviar mensajes pero no podra guardar estado correctamente.

## Personalizar para otro pais o region

Cambia estas variables:

```yaml
DIGEST_TZ: America/Caracas
DIGEST_TZ_LABEL: VET
MIN_LAT: "0.0"
MAX_LAT: "13.8"
MIN_LON: "-74.8"
MAX_LON: "-58.8"
NEWS_RSS_URLS: "..."
```

Tambien conviene ajustar las consultas por defecto en `DEFAULT_NEWS_QUERIES` dentro de `src/sismo_monitor.py`.

## Seguridad

- No pegues webhooks ni passwords en archivos del repositorio.
- Usa siempre GitHub Actions Secrets.
- Si usas Gmail, crea una app password.
- No publiques `state/state.json` si contiene informacion que no quieres compartir.
- Revisa manualmente fuentes oficiales antes de reenviar mensajes sensibles.

## Limitaciones

- Google News RSS puede cambiar resultados, ordenar distinto o entregar enlaces agregados.
- Algunos medios republican notas muy parecidas; la deduplicacion reduce ruido, pero no es perfecta.
- USGS puede actualizar magnitud, profundidad o ubicacion despues del primer reporte.
- El monitor informa novedades; no valida danos ni cifras humanas como una fuente oficial.

