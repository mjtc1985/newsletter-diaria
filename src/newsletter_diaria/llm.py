from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import textwrap
import time
from pathlib import Path
from shutil import which as shutil_which
from typing import Protocol
from urllib import error, request

from newsletter_diaria.models import Item, LLMConfig, OpenAICompatibleConfig, OpenCodeConfig

logger = logging.getLogger("newsletter_diaria")

# Reintentos ante errores transitorios (429 rate-limit, 5xx) del backend HTTP.
HTTP_RETRY_STATUS = {429, 500, 502, 503, 504}
HTTP_MAX_ATTEMPTS = 3
HTTP_RETRY_CAP_SECONDS = 35.0
# Modelo de reserva (free tier más amplio) al que caer si el bueno agota cuota.
FALLBACK_MODELS = ["gemini-flash-lite-latest"]


class LLMProvider(Protocol):
    def rank(self, items: list[Item]) -> dict:
        raise NotImplementedError

    def summarize_batch(self, ranked_ids: list[tuple[Item, int, int]]) -> dict:
        raise NotImplementedError

    def summarize_one(self, item: Item) -> dict:
        raise NotImplementedError


class OpenCodeProvider:
    def __init__(self, config: OpenCodeConfig):
        self.config = config

    def rank(self, items: list[Item]) -> dict:
        payload = {
            "items": [
                {
                    "uid": item.uid,
                    "source": item.source,
                    "title": item.title,
                    "link": item.link,
                    "published_at": item.published_at.isoformat() if item.published_at else None,
                    "summary_excerpt": textwrap.shorten(item.summary or item.title, width=180, placeholder="..."),
                }
                for item in items
            ]
        }
        prompt = (
            "Responde en español y SOLO con JSON válido. "
            "Ordena estas noticias por importancia real. "
            "Los campos rank e importance deben ser enteros; rank empieza en 1 y nunca es 0. "
            "importance va de 1 a 100. "
            "Devuelve TODOS los uid exactamente una vez; no omitas ninguno. "
            "Si dos noticias tienen importancia similar, reparte mejor entre fuentes, pero sin forzar cuotas. "
            "Usa solo los campos dados. "
            "Devuelve exactamente {\"headline\":string,\"trends\":[string],\"items\":[{\"uid\":string,\"rank\":number,\"importance\":number}]}. "
            f"Datos: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
        )
        logger.info("Ranking prompt length: %d characters", len(prompt))
        logger.info("Calling %s to rank %d items", self._cli_label(), len(items))
        return self._run_json(agent=self.config.ranker_agent, prompt=prompt)

    def summarize_batch(self, ranked_ids: list[tuple[Item, int, int]]) -> dict:
        payload = {
            "items": [
                {
                    "uid": item.uid,
                    "source": item.source,
                    "title": item.title,
                    "link": item.link,
                    "published_at": item.published_at.isoformat() if item.published_at else None,
                    "summary": item.summary,
                    "rank": rank,
                    "importance": importance,
                }
                for item, rank, importance in ranked_ids
            ]
        }
        prompt = (
            "Responde en español y SOLO con JSON válido.\n"
            "Resume cada uno de los artículos de la lista.\n"
            "REGLA OBLIGATORIA PARA CADA ARTÍCULO:\n"
            "- 'title': DEBE ser el título traducido a un español natural y fluido (conserva únicamente nombres propios de personas, empresas y tecnologías/marcas sin traducir).\n"
            "- 'summary': Abstract breve y natural de 2 a 4 frases, sin listas.\n"
            "- 'why': Explicación de por qué importa al sector tecnológico/IA/desarrollo.\n"
            "- 'takeaway': Conclusión clave o lección práctica.\n"
            "Todos los campos (uid, title, summary, why, takeaway) son estrictamente obligatorios en cada elemento de 'items'.\n"
            "Devuelve exactamente: {\"items\":[{\"uid\":string,\"title\":string,\"summary\":string,\"why\":string,\"takeaway\":string}]}.\n"
            f"Datos: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
        )
        logger.info("Calling %s to summarize %d items in batch", self._cli_label(), len(ranked_ids))
        return self._run_json(agent=self.config.summarizer_agent, prompt=prompt)

    def summarize_one(self, item: Item) -> dict:
        payload = {
            "uid": item.uid,
            "source": item.source,
            "title": item.title,
            "link": item.link,
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "summary": item.summary,
        }
        prompt = (
            "Responde en español y SOLO con JSON válido.\n"
            "Resume el artículo sin inventar nada.\n"
            "REGLAS OBLIGATORIAS:\n"
            "- 'title': DEBE ser el título traducido a un español natural y fluido (conserva únicamente nombres propios y marcas).\n"
            "- 'summary': Abstract breve y natural de 2 a 4 frases, sin listas.\n"
            "- 'why': Explicación de por qué importa.\n"
            "- 'takeaway': Conclusión clave.\n"
            "Devuelve exactamente: {\"title\":string,\"summary\":string,\"why\":string,\"takeaway\":string}.\n"
            f"Datos: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
        )
        return self._run_json(agent=self.config.summarizer_agent, prompt=prompt)

    def _cli_label(self) -> str:
        return "Gemini CLI" if self.config.cli_command == "gemini" else "OpenCode"

    def _run_json(self, agent: str, prompt: str) -> dict:
        if self.config.cli_command == "opencode":
            return self._run_opencode_json(agent, prompt)

        if self.config.cli_command == "gemini":
            return self._run_gemini_json(prompt)

        raise RuntimeError(f"Unsupported local LLM CLI: {self.config.cli_command}")

    def _run_opencode_json(self, agent: str, prompt: str) -> dict:
        cli_bin = resolve_cli_bin("opencode")
        command = [cli_bin, "run", "--agent", agent, "--format", "default", "--dir", str(self.config.cwd)]
        if self.config.model:
            command.extend(["--model", self.config.model])
        command.append(prompt)
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "opencode failed").strip())
        return extract_json_object(completed.stdout)

    def _run_gemini_json(self, prompt: str) -> dict:
        cli_bin = resolve_cli_bin("gemini")
        command = [cli_bin, "--output-format", "json"]
        if self.config.model:
            command.extend(["-m", self.config.model])
        command.extend(["-p", prompt])

        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "gemini failed").strip())
        data = extract_json_object(completed.stdout)
        if isinstance(data, dict) and isinstance(data.get("response"), str):
            return extract_json_object(data["response"])
        return data


class OpenAICompatibleProvider:
    def __init__(self, config: OpenAICompatibleConfig):
        self.config = config
        self.base_url = (config.base_url or "https://api.openai.com/v1").rstrip("/")
        self.model = config.model
        self.api_key = config.api_key or os.getenv(config.api_key_env)

    def rank(self, items: list[Item]) -> dict:
        payload = {
            "items": [
                {
                    "uid": item.uid,
                    "source": item.source,
                    "title": item.title,
                    "link": item.link,
                    "published_at": item.published_at.isoformat() if item.published_at else None,
                    "summary_excerpt": textwrap.shorten(item.summary or item.title, width=180, placeholder="..."),
                }
                for item in items
            ]
        }
        prompt = (
            "Responde en español y SOLO con JSON válido. "
            "Ordena estas noticias por importancia real. "
            "Los campos rank e importance deben ser enteros; rank empieza en 1 y nunca es 0. "
            "importance va de 1 a 100. "
            "Devuelve TODOS los uid exactamente una vez; no omitas ninguno. "
            "Si dos noticias tienen importancia similar, reparte mejor entre fuentes, pero sin forzar cuotas. "
            "Usa solo los campos dados. "
            "Devuelve exactamente {\"headline\":string,\"trends\":[string],\"items\":[{\"uid\":string,\"rank\":number,\"importance\":number}]}. "
            f"Datos: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
        )
        logger.info("Calling OpenAI-compatible backend to rank %d items", len(items))
        return self._chat_json(prompt)

    def summarize_batch(self, ranked_ids: list[tuple[Item, int, int]]) -> dict:
        payload = {
            "items": [
                {
                    "uid": item.uid,
                    "source": item.source,
                    "title": item.title,
                    "link": item.link,
                    "published_at": item.published_at.isoformat() if item.published_at else None,
                    "summary": item.summary,
                    "rank": rank,
                    "importance": importance,
                }
                for item, rank, importance in ranked_ids
            ]
        }
        prompt = (
            "Responde en español y SOLO con JSON válido.\n"
            "Resume cada uno de los artículos de la lista.\n"
            "REGLA OBLIGATORIA PARA CADA ARTÍCULO:\n"
            "- 'title': DEBE ser el título traducido a un español natural y fluido (conserva únicamente nombres propios de personas, empresas y tecnologías/marcas sin traducir).\n"
            "- 'summary': Abstract breve y natural de 2 a 4 frases, sin listas.\n"
            "- 'why': Explicación de por qué importa al sector tecnológico/IA/desarrollo.\n"
            "- 'takeaway': Conclusión clave o lección práctica.\n"
            "Todos los campos (uid, title, summary, why, takeaway) son estrictamente obligatorios en cada elemento de 'items'.\n"
            "Devuelve exactamente: {\"items\":[{\"uid\":string,\"title\":string,\"summary\":string,\"why\":string,\"takeaway\":string}]}.\n"
            f"Datos: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
        )
        logger.info("Calling OpenAI-compatible backend to summarize %d items in batch", len(ranked_ids))
        return self._chat_json(prompt)

    def summarize_one(self, item: Item) -> dict:
        payload = {
            "uid": item.uid,
            "source": item.source,
            "title": item.title,
            "link": item.link,
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "summary": item.summary,
        }
        prompt = (
            "Responde en español y SOLO con JSON válido.\n"
            "Resume el artículo sin inventar nada.\n"
            "REGLAS OBLIGATORIAS:\n"
            "- 'title': DEBE ser el título traducido a un español natural y fluido (conserva únicamente nombres propios y marcas).\n"
            "- 'summary': Abstract breve y natural de 2 a 4 frases, sin listas.\n"
            "- 'why': Explicación de por qué importa.\n"
            "- 'takeaway': Conclusión clave.\n"
            "Devuelve exactamente: {\"title\":string,\"summary\":string,\"why\":string,\"takeaway\":string}.\n"
            f"Datos: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
        )
        return self._chat_json(prompt)

    def _chat_json(self, prompt: str) -> dict:
        if not self.model:
            raise RuntimeError("Missing model for OpenAI-compatible backend")
        if not self.api_key:
            raise RuntimeError(
                f"Missing API key for OpenAI-compatible backend. Set --llm-api-key or {self.config.api_key_env}"
            )

        # Intentamos primero el modelo bueno; si agota cuota (429), caemos rápido al
        # de reserva (free tier más amplio). El backoff con esperas solo se usa en el
        # último modelo de la lista, para no ralentizar cuando hay alternativa lista.
        models: list[str] = []
        for candidate in [self.model, *FALLBACK_MODELS]:
            if candidate and candidate not in models:
                models.append(candidate)

        last_exc: Exception | None = None
        for index, model in enumerate(models):
            is_last = index == len(models) - 1
            try:
                data = self._post_chat(prompt, model, allow_backoff=is_last)
            except Exception as exc:
                last_exc = exc
                if not is_last:
                    logger.warning("Modelo '%s' no disponible (%s); probando reserva '%s'",
                                   model, str(exc)[:80], models[index + 1])
                    continue
                raise
            if index > 0:
                logger.warning("Generado con modelo de reserva: %s", model)
            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise RuntimeError("OpenAI-compatible backend returned an unexpected response shape") from exc
            return extract_json_object(normalize_content(content))
        raise last_exc or RuntimeError("OpenAI-compatible backend failed")

    def _post_chat(self, prompt: str, model: str, allow_backoff: bool) -> dict:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a precise JSON-only assistant. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        if self.config.json_mode:
            payload["response_format"] = {"type": "json_object"}
        body = json.dumps(payload).encode("utf-8")

        attempts = HTTP_MAX_ATTEMPTS if allow_backoff else 1
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            http_request = request.Request(
                f"{self.base_url}/chat/completions",
                data=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with request.urlopen(http_request, timeout=90) as response:
                    return json.loads(response.read().decode("utf-8"))
            except error.HTTPError as exc:
                details = exc.read().decode("utf-8", errors="ignore")
                last_exc = RuntimeError(f"OpenAI-compatible backend failed with HTTP {exc.code}: {details}")
                if exc.code in HTTP_RETRY_STATUS and attempt < attempts:
                    delay = min(parse_retry_delay(details, default=5.0 * attempt), HTTP_RETRY_CAP_SECONDS)
                    logger.warning("Backend HTTP %s (attempt %d/%d); retrying in %.0fs",
                                   exc.code, attempt, attempts, delay)
                    time.sleep(delay)
                    continue
                raise last_exc from exc
            except (error.URLError, TimeoutError, OSError) as exc:
                last_exc = RuntimeError(f"Could not reach OpenAI-compatible backend: {exc}")
                if attempt < attempts:
                    time.sleep(5.0 * attempt)
                    continue
                raise last_exc from exc
            except Exception as exc:
                last_exc = RuntimeError(f"Unexpected error communicating with OpenAI-compatible backend: {exc}")
                raise last_exc from exc
        raise last_exc or RuntimeError("OpenAI-compatible backend failed after retries")


def build_provider(config: LLMConfig) -> LLMProvider:
    if config.backend == "openai-compatible":
        return OpenAICompatibleProvider(config.openai_compatible)
    return OpenCodeProvider(config.opencode)


def resolve_cli_bin(command_name: str) -> str:
    if shutil_which(command_name):
        return command_name
    for candidate in (
        Path.home() / ".opencode" / "bin" / command_name,
        Path.home() / ".local" / "bin" / command_name,
        Path.home() / ".bun" / "bin" / command_name,
        Path(f"/usr/local/bin/{command_name}"),
        Path(f"/usr/bin/{command_name}"),
    ):
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise RuntimeError(f"Could not find the {command_name} binary")


def parse_retry_delay(text: str, default: float) -> float:
    """Extrae el retardo sugerido por el servidor (p. ej. 'retry in 48.1s' o
    '"retryDelay": "48s"') para respetar el rate-limit; si no lo encuentra, usa default."""
    for pattern in (r'retry in ([0-9]+(?:\.[0-9]+)?)s', r'retryDelay"\s*:\s*"([0-9]+(?:\.[0-9]+)?)s'):
        match = re.search(pattern, text, re.I)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass
    return default


def extract_json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("The AI did not return valid JSON")
    return json.loads(cleaned[start : end + 1])


def normalize_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for entry in content:
            if isinstance(entry, dict):
                text = entry.get("text") or entry.get("content")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(entry, str):
                parts.append(entry)
        if parts:
            return "\n".join(parts)
    raise RuntimeError("OpenAI-compatible backend returned non-text content")
