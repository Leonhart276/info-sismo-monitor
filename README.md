# Sismo Venezuela -> Discord + correo

Automatizacion para GitHub Actions que revisa cada hora fuentes publicas sobre sismos/noticias relacionadas con Venezuela y envia novedades.

Esta version agrega:

- Discord compacto con enlaces a fuentes/noticias.
- Enlaces cortos para Discord y el bloque de reenvio.
- Correo completo con detalle y links completos.
- Bloque corto listo para copiar y reenviar por WhatsApp.
- Resumen diario automatico una vez al dia.
- `state/state.json` para evitar repetir noticias y para recordar el ultimo resumen diario enviado.

## Estructura

```txt
.github/workflows/sismo-monitor.yml
src/sismo_monitor.py
requirements.txt
README.md
.gitignore
state/.gitkeep
```

## 1. Archivos a reemplazar

En tu repo actual reemplaza:

```txt
.github/workflows/sismo-monitor.yml
src/sismo_monitor.py
README.md
```

No borres `state/state.json` si ya existe, porque ahi esta el historial de lo enviado.

## 2. Secrets necesarios

Si ya te llego el correo y Discord, no tenes que cambiar secrets.

Discord:

```txt
DISCORD_WEBHOOK_URL
```

Correo / SMTP:

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

## 3. Ejemplo con Gmail

```txt
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=tu_correo@gmail.com
SMTP_PASSWORD=tu_app_password_de_google
EMAIL_FROM=tu_correo@gmail.com
EMAIL_TO=tu_correo@gmail.com
```

`EMAIL_TO` puede tener uno o varios destinatarios separados por coma.

## 4. Discord con enlaces cortos

El workflow usa:

```yaml
DISCORD_MESSAGE_MODE: "compact"
DISCORD_MAX_CHARS: "1800"
SHORTEN_URLS_FOR_DISCORD: "true"
SHORTEN_URLS_FOR_FORWARD: "true"
URL_SHORTENER: "isgd"
```

Con eso Discord muestra un resumen corto, pero incluye links de las noticias/fuentes destacadas.

Si algun dia el acortador falla, el script no se rompe: usa el enlace original.

Para desactivar enlaces cortos:

```yaml
SHORTEN_URLS_FOR_DISCORD: "false"
SHORTEN_URLS_FOR_FORWARD: "false"
```

Tambien podes usar `v.gd`:

```yaml
URL_SHORTENER: "vgd"
```

O desactivar completamente el acortador:

```yaml
URL_SHORTENER: "none"
```

## 5. Resumen diario automatico

El workflow corre cada hora, pero el resumen diario se envia una sola vez por dia cuando la hora local sea igual o posterior a:

```yaml
DAILY_SUMMARY_HOUR: "9"
```

La zona horaria esta configurada en:

```yaml
DIGEST_TZ: America/Argentina/Buenos_Aires
```

El resumen diario incluye:

```txt
- Estado general de las ultimas 24 h
- Total de sismos detectados
- Mayor magnitud registrada
- Noticias relevantes detectadas
- Ultima novedad
- Links principales
- Bloque corto listo para WhatsApp
```

Para enviarlo por Discord y correo:

```yaml
DAILY_SUMMARY_TO_DISCORD: "true"
DAILY_SUMMARY_TO_EMAIL: "true"
```

Si queres que el resumen diario llegue solo por correo:

```yaml
DAILY_SUMMARY_TO_DISCORD: "false"
DAILY_SUMMARY_TO_EMAIL: "true"
```

## 6. Bloque corto para WhatsApp

El correo completo agrega un bloque asi:

```txt
📲 Texto corto para reenviar:

🚨 Actualizacion sismo Venezuela - 25/06 09:07 ART
- Prioridad: 🟡 Media
- M4.6 - Venezuela / zona cercana - 25/06 08:55 ART
  Fuente: https://is.gd/xxxxx
- Titulo de noticia relevante (Medio)
  Fuente: https://is.gd/yyyyy
- Verificar fuentes oficiales antes de compartir.
```

Para desactivarlo:

```yaml
FORWARD_BLOCK_ENABLED: "false"
```

## 7. Probar manualmente

En GitHub:

1. Repo -> Actions.
2. Monitor Sismo Venezuela.
3. Run workflow.
4. Opcional: activa `force_send` para reenviar novedades aunque ya esten vistas.
5. Opcional: activa `send_daily_summary` para probar el resumen diario en ese momento.

## 8. Comportamiento esperado

Si hay novedades:

- Discord recibe una alerta compacta con enlaces.
- Correo recibe el detalle completo.
- El correo incluye un texto corto listo para WhatsApp.
- Se actualiza `state/state.json`.

Una vez al dia:

- Envia resumen diario aunque no haya novedades urgentes.
- Si no hubo novedades en 24 h, avisa que el monitoreo sigue activo.

Si no hay novedades y aun no toca resumen diario:

- No envia nada por defecto.

Para enviar tambien cuando no hay novedades cada hora, cambia:

```yaml
SEND_EMPTY_DIGEST: "true"
```

## 9. Ajustes utiles

```yaml
LOOKBACK_HOURS: "24"
MIN_MAGNITUDE: "2.5"
MAX_NEWS: "8"
MAX_QUAKES: "8"
EMAIL_STRICT: "false"
DISCORD_STRICT: "false"
```

- `EMAIL_STRICT: "false"`: si el correo falla pero Discord funciona, el workflow no se rompe.
- `DISCORD_STRICT: "false"`: si Discord falla pero el correo funciona, el workflow no se rompe.
- Si ambos canales fallan al enviar una alerta, el workflow falla y no marca las novedades como enviadas.

## Seguridad

No pegues webhooks, passwords SMTP ni app passwords en archivos del repo. Guardalos solo como GitHub Secrets.

Esto no reemplaza fuentes oficiales ni servicios de emergencia. Es un digest automatico para monitoreo y reenvio con criterio.
