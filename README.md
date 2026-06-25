# Sismo Venezuela -> Discord monitor

Automatizacion para GitHub Actions que revisa cada hora fuentes publicas sobre sismos/noticias relacionadas con Venezuela y publica un resumen por puntos en Discord cuando encuentra novedades.

## Que hace

- Consulta eventos sismicos en USGS dentro de una caja geografica que cubre Venezuela y zonas cercanas.
- Consulta RSS publicos de Google News con busquedas sobre sismo/terremoto/replicas/FUNVISIS/Proteccion Civil Venezuela.
- Filtra duplicados con `state/state.json`.
- Envia a Discord solo si hay novedades, para no repetir el mismo mensaje cada hora.
- Permite ejecucion manual desde la pestaña Actions de GitHub.

## Lo que tenes que configurar

### 1. Crear el webhook en Discord

1. Crea un servidor privado o usa uno existente.
2. Crea un canal, por ejemplo `#sismo-venezuela`.
3. En el canal: **Edit Channel** -> **Integrations** -> **Webhooks**.
4. Crea un webhook nuevo.
5. Copia la URL del webhook.

No pegues esa URL en chats ni en archivos del repo. Es una credencial.

### 2. Crear el repo en GitHub

1. Crea un repo privado, por ejemplo `sismo-venezuela-monitor`.
2. Sube estos archivos respetando la estructura:

```txt
.github/workflows/sismo-monitor.yml
src/sismo_monitor.py
requirements.txt
README.md
.gitignore
state/.gitkeep
```

### 3. Guardar el webhook como secret

En GitHub:

1. Repo -> **Settings**.
2. **Secrets and variables** -> **Actions**.
3. **New repository secret**.
4. Name: `DISCORD_WEBHOOK_URL`.
5. Secret: pega la URL del webhook de Discord.

### 4. Permitir escritura del workflow

El workflow necesita actualizar `state/state.json` para recordar que ya envio una noticia.

En GitHub:

1. Repo -> **Settings** -> **Actions** -> **General**.
2. En **Workflow permissions**, deja habilitado permiso de escritura si tu cuenta/organizacion lo requiere.

El archivo YAML ya incluye:

```yaml
permissions:
  contents: write
```

### 5. Probar manualmente

1. Ve a la pestaña **Actions**.
2. Selecciona **Monitor Sismo Venezuela**.
3. Click en **Run workflow**.
4. Para la primera prueba podes activar `force_send`.

El primer envio puede incluir varias novedades de las ultimas 24 horas. Despues solo deberia enviar items nuevos.

## Configuracion rapida

En `.github/workflows/sismo-monitor.yml` podes ajustar:

```yaml
LOOKBACK_HOURS: "24"
MIN_MAGNITUDE: "2.5"
MAX_NEWS: "8"
MAX_QUAKES: "8"
SEND_EMPTY_DIGEST: "false"
```

- `LOOKBACK_HOURS`: ventana de busqueda.
- `MIN_MAGNITUDE`: magnitud minima USGS.
- `SEND_EMPTY_DIGEST`: si lo pones en `true`, enviara tambien mensajes de "sin novedades".

## Cambiar busquedas de noticias

Por defecto usa estas consultas:

- `(sismo OR terremoto OR temblor OR replicas OR replica) Venezuela when:1d`
- `FUNVISIS sismo Venezuela when:1d`
- `Proteccion Civil Venezuela sismo when:1d`

Tambien podes pasar URLs RSS propias con la variable `NEWS_RSS_URLS`, separadas por coma.

## Formato de mensaje

Ejemplo:

```txt
Actualizacion sismo Venezuela - 25/06/2026 16:07 ART

Resumen por puntos:
- Prioridad: Alta
- Novedades detectadas: 4 (1 sismos / 3 noticias)

Sismos nuevos:
- M4.8 - 20 km al norte de ... - 25/06 15:41 ART - profundidad 10.0 km
  Fuente: https://earthquake.usgs.gov/...

Noticias nuevas:
- [Medio] Titulo de la noticia...
  https://news.google.com/...

Verificar comunicados oficiales antes de reenviar datos sensibles.
```

## Nota importante

Esto no reemplaza fuentes oficiales ni servicios de emergencia. Es un digest automatico para ayudarte a monitorear novedades y reenviarlas con criterio.
