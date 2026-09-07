# AGENTS.md

Proyecto: newsletter diaria técnica.

## Objetivo
- Ingerir RSS/Atom de las últimas 24h.
- Usar OpenCode para ranking y resúmenes.
- Generar salida por consola/Markdown.

## Convenciones
- Código en `src/newsletter_diaria/`.
- Fuentes en `sources.json`.
- Salida generada en `output/`.

## Ejecución
```bash
PYTHONPATH=src python -m newsletter_diaria.main
```

## IA / OpenCode
- Agentes:
- `.opencode/agent/newsletter-ranker.md` -> `google/gemini-3-pro-preview`
  - `.opencode/agent/newsletter-summarizer.md` -> `google/gemini-3-flash`
- El backend local se llama `local-cli` y puede usar `opencode` o `gemini`.
- Por defecto se usa `opencode`.
- Si cambias agentes en `.opencode/agent/` y usas `opencode`, reinicia OpenCode.

## Politica editorial
- Ventana de 72h (`--hours`) mas registro de enviados en `seen.json`: una fuente
  que publica una vez al mes compite varios dias sin repetirse.
- El dedupe usa el enlace normalizado, no el uid (el uid incluye el nombre de la
  fuente, asi que el mismo articulo en dos feeds se colaba dos veces).
- Tras resumir, `editorial.py` aplica: descartes de la IA, umbral de importancia,
  cuota por fuente y por grupo, huecos reservados para opinion/research/security
  y limite duro de la edicion. Cada rechazo se registra con su motivo.
- El summarizer puede marcar `descartar: true` en vez de inventar relevancia, y
  `why`/`takeaway` son opcionales. Si cambias ese contrato, toca los dos sitios:
  `llm.py` (SUMMARY_RULES) y `.opencode/agent/newsletter-summarizer.md`.
- `seen.json` solo se escribe si el envio tuvo exito: una ejecucion sin
  `--send-email` no consume candidatos.
