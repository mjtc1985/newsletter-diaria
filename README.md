# newsletter-diaria

A daily technical newsletter generator built from curated RSS/Atom feeds and selected HTML sources.

The project collects recent stories, deduplicates them, ranks them with AI or heuristics, generates summaries, and produces output ready for console, Markdown, and email delivery.

---

## What it does

- Ingests news from configurable technical sources
- Filters content within a recent time window
- Deduplicates and caps candidates before ranking
- Orders stories by importance
- Generates editorial summaries
- Exports to:
  - console
  - `output/daily.md`
  - `output/latest.json`
  - HTML/plain-text email

---

## Who this is for

This project is a good fit for:

- personal daily reading
- internal technical newsletters
- automated curation of software, AI, and infrastructure news
- experimentation with interchangeable LLM backends

---

## Architecture in one line

`sources -> ingest -> filter/dedupe -> rank -> summarize -> render -> cache/email`

---

## Project structure

```text
src/newsletter_diaria/
├── app.py           # Main orchestration
├── cli.py           # CLI and argument parsing
├── config.py        # Environment loading
├── models.py        # Domain dataclasses
├── sources.py       # Default sources + JSON loading
├── ingest.py        # Ingestion, fetch, and base parsing
├── parsers/         # Per-source pluggable parsers
├── ranking.py       # Ranking and draft assembly
├── llm.py           # LLM backends (local CLI / OpenAI-compatible)
├── renderers.py     # Console, Markdown, and email rendering
├── emailing.py      # SMTP delivery
└── cache.py         # Last-draft persistence
```

Important files outside `src/`:

- `sources.json`: editable source catalog
- `.smtpgmail.env`: local email configuration
- `.newsletter.env.example`: example environment variables
- `.opencode/agent/`: OpenCode agents used by the project
- `systemd/`: units for scheduled daily execution

---

## Requirements

- Python **3.11+**
- `pip`
- network access to the configured sources
  - optionally:
   - **Antigravity CLI** installed locally,
   - **OpenCode** installed locally,
   - **Gemini CLI** installed locally, or
   - an **OpenAI-compatible API**

---

## Quick install

### 1. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -e .
```

If you do not want editable mode:

```bash
pip install .
```

---

## First run

Minimal execution:

```bash
PYTHONPATH=src python -m newsletter_diaria.main
```

Or with the installed entrypoint:

```bash
newsletter-diaria
```

If you want to validate only the pipeline without AI:

```bash
PYTHONPATH=src python -m newsletter_diaria.main --ai-mode off
```

---

## Most useful commands

### Generate the newsletter

```bash
make newsletter
```

### Generate and send by email

```bash
make newsletter-email
```

### Re-send the latest cached newsletter

```bash
make email-latest
```

### Test SMTP without sending a newsletter

```bash
make email-test
```

### Limit the number of stories

```bash
PYTHONPATH=src python -m newsletter_diaria.main --limit 5
```

### Force a specific LLM backend

```bash
PYTHONPATH=src python -m newsletter_diaria.main --ai-mode required --llm-backend local-cli
```

```bash
PYTHONPATH=src python -m newsletter_diaria.main \
  --ai-mode required \
  --llm-backend openai-compatible \
  --llm-model gpt-4.1-mini \
  --llm-base-url https://api.openai.com/v1
```

---

## Generated output

After a normal run, the project produces:

- `output/daily.md`: Markdown version of the newsletter
- `output/latest.json`: structured cache of the latest newsletter

`latest.json` is also used by `--send-latest`, so it is part of the operational flow, not just a debug artifact.

---

## Source configuration

`sources.json` defines which sources are queried.

### Supported source fields

- `name`: display name
- `url`: feed or page URL
- `topic`: logical category
- `priority`: `high`, `medium`, `low`
- `kind`: `feed` or `html`
- `max_items`: maximum number of articles to extract
- `parser`: optional specific parser

### Example

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

If `parser` is not set, the system falls back to the default parser for that `kind`.

### Current special parsers

- `anthropic`
- `uber_engineering`

### Recommended source already included

The project already includes `GitHub Changelog`, which is especially useful for catching product, pricing, and deprecation changes such as GitHub Copilot updates.

---

## AI backends

The project supports two LLM backends:

1. **Local CLI** (`opencode` or `gemini`)
2. **OpenAI-compatible API**

### Option A — Local CLI

Default mode uses the **OpenCode CLI** locally with Google auth.

You can switch between:

- `NEWSLETTER_LLM_CLI_COMMAND=opencode`
- `NEWSLETTER_LLM_CLI_COMMAND=gemini`

Useful variables:

- `NEWSLETTER_LLM_BACKEND=local-cli`
- `NEWSLETTER_LOCAL_CLI_MODEL`

Examples:

```bash
PYTHONPATH=src python -m newsletter_diaria.main \
  --ai-mode required \
  --llm-backend local-cli \
  --llm-cli-command opencode
```

```bash
PYTHONPATH=src python -m newsletter_diaria.main \
  --ai-mode required \
  --llm-backend local-cli \
  --llm-cli-command opencode
```

> If you use `opencode` and change agents in `.opencode/agent/`, restart OpenCode if you already have an active session.

### Quick config reference

| Variable | Meaning | Default |
| --- | --- | --- |
| `NEWSLETTER_LLM_BACKEND` | LLM family | `local-cli` |
| `NEWSLETTER_LLM_CLI_COMMAND` | Local CLI to use | `opencode` |
| `NEWSLETTER_LOCAL_CLI_MODEL` | Model name for the local CLI | unset for OpenCode; `gemini-3.1-pro-preview` for Gemini CLI |
| `NEWSLETTER_LLM_MODEL` | Model for OpenAI-compatible mode | unset |
| `NEWSLETTER_LLM_BASE_URL` | OpenAI-compatible base URL | `https://api.openai.com/v1` |
| `NEWSLETTER_LLM_API_KEY` | OpenAI-compatible API key | unset |

Morning run (the one used by systemd):

```bash
NEWSLETTER_LLM_BACKEND=local-cli NEWSLETTER_LLM_CLI_COMMAND=opencode PYTHONPATH=src python -m newsletter_diaria.main --send-email --ai-mode required
```

That run uses OpenCode with the Antigravity auth plugin.

### Option B — OpenAI-compatible API

Useful when you want to use an API key directly instead of OpenCode.

Useful variables:

- `NEWSLETTER_LLM_BACKEND=openai-compatible`
- `NEWSLETTER_LLM_MODEL`
- `NEWSLETTER_LLM_BASE_URL`
- `NEWSLETTER_LLM_API_KEY`
- `NEWSLETTER_LLM_API_KEY_ENV` (defaults to `OPENAI_API_KEY`)
- `NEWSLETTER_LLM_JSON_MODE` (`1` by default)

Example:

```bash
PYTHONPATH=src python -m newsletter_diaria.main \
  --ai-mode required \
  --llm-backend openai-compatible \
  --llm-model gpt-4.1-mini \
  --llm-base-url https://api.openai.com/v1
```

---

## Email configuration

Delivery is done via generic SMTP.

The project automatically loads SMTP configuration from:

1. `.smtp.env`

### Required variables

- `NEWSLETTER_EMAIL_TO`
- `NEWSLETTER_EMAIL_FROM`
- `NEWSLETTER_SMTP_USERNAME`
- `NEWSLETTER_SMTP_PASSWORD`
- `NEWSLETTER_SMTP_HOST`
- `NEWSLETTER_SMTP_PORT`

### Optional variables

- `NEWSLETTER_SMTP_SSL` (`1` for implicit SSL, `0` for STARTTLS)

### Example

```bash
cp .smtp.env.example .smtp.env
# edit .smtp.env with your values
PYTHONPATH=src python -m newsletter_diaria.main --send-email
```

### Gmail still works

Gmail remains a valid SMTP provider.

Typical Gmail setup:

- `NEWSLETTER_SMTP_HOST=smtp.gmail.com`
- `NEWSLETTER_SMTP_PORT=465`
- `NEWSLETTER_SMTP_SSL=1`

With Gmail, you will usually need an **app password** if 2FA is enabled.

---

## Useful environment variables

### Email

- `NEWSLETTER_EMAIL_TO`
- `NEWSLETTER_EMAIL_FROM`
- `NEWSLETTER_SMTP_USERNAME`
- `NEWSLETTER_SMTP_PASSWORD`
- `NEWSLETTER_SMTP_HOST`
- `NEWSLETTER_SMTP_PORT`
- `NEWSLETTER_SMTP_SSL`

### LLM

- `NEWSLETTER_LLM_BACKEND`
- `NEWSLETTER_LLM_CLI_COMMAND`
- `NEWSLETTER_LLM_MODEL`
- `NEWSLETTER_LLM_BASE_URL`
- `NEWSLETTER_LLM_API_KEY`
- `NEWSLETTER_LLM_API_KEY_ENV`
- `NEWSLETTER_LLM_JSON_MODE`
- `NEWSLETTER_LOCAL_CLI_MODEL`

---

## Recommended operating modes

### Fast local development

```bash
PYTHONPATH=src python -m newsletter_diaria.main --ai-mode off --limit 5
```

### Normal AI-backed run

```bash
make newsletter
```

### Automated daily execution

Use the `systemd/` units included in the repository.

---

## systemd automation

The repository includes:

- `systemd/newsletter-diaria.service`
- `systemd/newsletter-diaria.timer`

### Typical installation

```bash
sudo cp systemd/newsletter-diaria.service /etc/systemd/system/
sudo cp systemd/newsletter-diaria.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now newsletter-diaria.timer
sudo systemctl list-timers | grep newsletter-diaria
```

If you change either unit later, run:

```bash
sudo systemctl daemon-reload
sudo systemctl restart newsletter-diaria.timer
```

If the local Antigravity binary cannot start on this CPU, the app falls back to OpenCode automatically.
If it fails with `ENOSPC` during startup, the service now uses `/var/lib/newsletter-diaria` via `StateDirectory=` for writable temp space.

If you previously created a drop-in override for the service, remove it so it does not shadow the repo unit:

```bash
sudo rm -rf /etc/systemd/system/newsletter-diaria.service.d
```

The service runs directly with:

```bash
PYTHONPATH=src python3 -m newsletter_diaria.main --send-email --ai-mode required
```

### Logs

```bash
journalctl -u newsletter-diaria.service -f
```

---

## Quick troubleshooting

### Email is not being delivered

- run `make email-test`
- check `.smtpgmail.env`
- confirm your app password and 2FA setup

### The LLM backend fails

- run with `--ai-mode off` to isolate ingestion from AI
- Antigravity CLI on Raspberry Pi / legacy CPUs may crash with `go/sigill-fail-fast`; the app now falls back to OpenCode automatically
- if you use OpenCode, run `opencode auth login` and verify the Antigravity plugin is enabled
- if you use an OpenAI-compatible API, validate `base_url`, `model`, and `api_key`

### An HTML source is not parsing correctly

- review `sources.json`
- check whether the source needs a specific `parser`
- if no parser exists yet, add one under `src/newsletter_diaria/parsers/`

### Re-send from cache is not working

- make sure `output/latest.json` exists
- regenerate a newsletter before using `make email-latest`

---

## Current project state

The project is already modularized and ready to grow in these directions:

- more sources and specialized parsers
- additional LLM backends
- better ranking heuristics
- prioritization rules for pricing / deprecations / changelogs / security
- better LLM observability

---

## What this project is not trying to be

- a general-purpose crawler
- a distributed queueing system
- a full editorial platform

It is meant to be practical, hackable, and production-useful enough to generate a daily technical newsletter without turning into an infrastructure monster.

---

## Sensible next improvements

- automated pipeline tests
- special prioritization for changelogs / pricing / deprecations / security
- LLM backend observability
- more output formats
- a lightweight editorial review step

---

## License and housekeeping

The repository does not define a license yet.

If this project is going to be shared more broadly, it would be worth adding soon:

- a license
- a local configuration policy
- a secrets strategy
