# Sismo Venezuela -> Discord + correo

Automatizacion para GitHub Actions que revisa cada hora fuentes publicas sobre sismos/noticias relacionadas con Venezuela y envia novedades.

- Discord recibe un resumen compacto para evitar recortes.
- El correo recibe el resumen completo con links.
- WhatsApp fue eliminado de esta version.
- El archivo `state/state.json` evita repetir noticias ya enviadas.

## Estructura

```txt
.github/workflows/sismo-monitor.yml
src/sismo_monitor.py
requirements.txt
README.md
.gitignore
state/.gitkeep
```

## 1. Mantener Discord

Si ya tenias el webhook funcionando, no cambies el secret:

```txt
DISCORD_WEBHOOK_URL
```

El YAML usa:

```yaml
DISCORD_MESSAGE_MODE: "compact"
DISCORD_MAX_CHARS: "1800"
```

Con esto Discord manda una alerta corta y evita el mensaje de recorte.

Si algun dia queres que Discord mande el resumen completo en varias partes, cambia:

```yaml
DISCORD_MESSAGE_MODE: "full"
```

## 2. Agregar correo

Crea estos secrets en GitHub:

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

`EMAIL_TO` puede tener uno o varios destinatarios separados por coma.

## Ejemplo con Gmail

```txt
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=tu_correo@gmail.com
SMTP_PASSWORD=tu_app_password_de_google
EMAIL_FROM=tu_correo@gmail.com
EMAIL_TO=tu_correo@gmail.com
```

En Gmail conviene usar una app password, no tu clave normal de la cuenta.

## Ejemplo con Microsoft 365 / Outlook

```txt
SMTP_HOST=smtp.office365.com
SMTP_PORT=587
SMTP_USERNAME=tu_correo@tudominio.com
SMTP_PASSWORD=tu_password_o_app_password
EMAIL_FROM=tu_correo@tudominio.com
EMAIL_TO=tu_correo@tudominio.com
```

## 3. Subir cambios al repo

Reemplaza estos archivos en tu repo actual:

```txt
.github/workflows/sismo-monitor.yml
src/sismo_monitor.py
README.md
```

No borres tu `state/state.json` si ya existe, porque ahi esta el historial de lo enviado.

## 4. Probar manualmente

En GitHub:

1. Repo -> Actions.
2. Monitor Sismo Venezuela.
3. Run workflow.
4. Activa `force_send` para forzar una prueba aunque ya haya enviado esas noticias antes.

## Comportamiento esperado

Si hay novedades:

- Discord recibe un mensaje compacto.
- Correo recibe el resumen completo.
- Se actualiza `state/state.json`.

Si no hay novedades:

- No envia nada por defecto.

Para enviar tambien cuando no hay novedades, cambia:

```yaml
SEND_EMPTY_DIGEST: "true"
```

## Ajustes utiles

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
- Si ambos fallan, el workflow falla y no marca las novedades como enviadas.

## Seguridad

No pegues webhooks, passwords SMTP ni app passwords en archivos del repo. Guardalos solo como GitHub Secrets.

Esto no reemplaza fuentes oficiales ni servicios de emergencia. Es un digest automatico para monitoreo y reenvio con criterio.
