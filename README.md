# newsletter-diaria

Newsletter diaria de noticias de software, IA y dev blogs.

## MVP
- Ingesta RSS/APIs
- Ranking por importancia
- Resúmenes con IA
- Envío por email
- Fuentes configurables en `sources.json`

## Dev
```bash
PYTHONPATH=src python -m newsletter_diaria.main
```

## One-liner
```bash
make newsletter
```

## One-liner con email
```bash
make newsletter-email
```
Por defecto no limita el número de noticias; si quieres caparlo, usa `--limit N`.

## Reenviar lo último sin rehacer feeds
```bash
make email-latest
```
Usa el último boletín cacheado en `output/latest.json`.

## Email test
```bash
make email-test
```
Prueba la conexión/autenticación SMTP sin mandar la newsletter.

## Config
Edita `sources.json` para añadir/quitar feeds.

Campos soportados por fuente:
- `name`
- `url`
- `topic`
- `priority`
- `kind` (`feed` o `html`)
- `max_items`
- `parser` (opcional, para fuentes especiales)

Ejemplo de fuente con parser específico:
```json
{
  "name": "Anthropic Blog",
  "url": "https://www.anthropic.com/news",
  "topic": "ai",
  "priority": "high",
  "kind": "html",
  "max_items": 5,
  "parser": "anthropic"
}
```

Si no defines `parser`, el sistema usa uno genérico según `kind`.

### Email (Gmail SMTP)
El script carga automáticamente `.smtpgmail.env` desde la raíz del repo.

La ventana temporal por defecto es de **las últimas 24 horas** (`--hours 24`).

Usa variables de entorno:
- `NEWSLETTER_EMAIL_TO` (puede ser una lista separada por comas/`;`/saltos de línea)
- `NEWSLETTER_EMAIL_FROM`
- `NEWSLETTER_GMAIL_USER`
- `NEWSLETTER_GMAIL_PASSWORD`
- `NEWSLETTER_SMTP_HOST` (opcional, default `smtp.gmail.com`)
- `NEWSLETTER_SMTP_PORT` (opcional, default `465`)

Ejemplo:
```bash
cp .smtpgmail.env.example .smtpgmail.env
# edita .smtpgmail.env con tus datos
PYTHONPATH=src python -m newsletter_diaria.main --send-email
```

Nota: con Gmail suele hacer falta un **app password** si tienes 2FA.

OpenCode no necesita configuración extra para esto: el binario Python lee `.smtpgmail.env` al arrancar.

## IA
La IA corre vía **OpenCode instalado en el sistema**.

Por defecto el script hace `opencode run ...` usando el binario local.

Variables útiles:
- `NEWSLETTER_OPENCODE_MODEL`

Ejemplo:
```bash
PYTHONPATH=src python -m newsletter_diaria.main --ai-mode required
```

Nota: si añades o cambias agentes en `.opencode/agent/`, reinicia OpenCode si usas una sesión ya abierta.

## Ejecución diaria a las 09:00
Hay dos unidades systemd listas en `systemd/`:
- `systemd/newsletter-diaria.service`
- `systemd/newsletter-diaria.timer`

Instalación típica:
```bash
sudo cp systemd/newsletter-diaria.service /etc/systemd/system/
sudo cp systemd/newsletter-diaria.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now newsletter-diaria.timer
sudo systemctl list-timers | grep newsletter-diaria
```

Logs:
```bash
journalctl -u newsletter-diaria.service -f
```

## Docker
No aplica para OpenCode en este proyecto.
