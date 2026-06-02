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
  - `.opencode/agent/newsletter-ranker.md` -> `openai/gpt-5.5`
  - `.opencode/agent/newsletter-summarizer.md` -> `openai/gpt-5.4-mini`
- El backend local se llama `local-cli` y puede usar `opencode` o `gemini`.
- Por defecto se usa `gemini`.
- Si cambias agentes en `.opencode/agent/` y usas `opencode`, reinicia OpenCode.
