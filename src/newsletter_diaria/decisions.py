from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib import error, request

from newsletter_diaria.models import Item

logger = logging.getLogger("newsletter_diaria")

DEFAULT_BASE_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
# La Raspberry no resuelve DNS con doce peticiones simultaneas: con doce fallaban
# quince de doscientas con "Temporary failure in name resolution", y cada fallo
# saca un articulo de la edicion por red y no por criterio.
MAX_WORKERS = 4
RETRY_ATTEMPTS = 3
RETRY_PAUSE_SECONDS = 1.5
TIMEOUT_SECONDS = 25

# El texto que se le pasa por articulo. La decision se toma sobre el cuerpo
# descargado cuando existe; si no, sobre el resumen del feed.
STATE_MAX_CHARS = 2200

# Niveles de las dos escalas. Son las anclas del rubro: el modelo devuelve una
# nota continua ponderada por su probabilidad en cada nivel, asi que las
# definiciones son el criterio, no una sugerencia del prompt.
CONSEQUENCE_LEVELS = [
    "Nada: marketing, hoja de ruta sin fecha, evento, inscripcion o refrito",
    "Iteracion: version N+1 algo mejor, 'ya disponible en X', una integracion",
    "Contexto util que no cambia ninguna decision",
    "Cambia como se entiende o se hace algo, con evidencia: analisis que explica un mecanismo, post mortem con causa raiz, investigacion replicable con resultado no obvio",
    "Cambia las opciones disponibles: capacidad que antes no existia, cambio de licencia, una herramienta sustituye a otra, lanzamiento que cambia como se construye",
    "Obliga a actuar ya: vulnerabilidad grave ya explotada, cambio de precios de una API, fin de un servicio, ruptura de compatibilidad con fecha",
]
VERIFIABILITY_LEVELS = [
    "Rumor, agregador sin fuente o contenido SEO",
    "Medio citando anonimos, o una cifra espectacular que solo afirma el vendedor",
    "Medio o autor fiable citando fuentes con nombre",
    "Fuente primaria autoinformada pero comprobable: documentacion, precios, notas de version",
    "Fuente primaria mas verificacion o reproduccion independiente",
    "Documento primario comprobable: codigo, paper con repositorio, CVE con detalle, texto legal",
]

SUBJECT_INSTRUCTIONS = (
    "El ASUNTO del articulo es la inteligencia artificial: modelos, agentes, herramientas de IA, "
    "construir software con IA o sobre IA, evaluar modelos, seguridad de sistemas de IA, su coste, "
    "sus limites, su regulacion o su negocio, y el analisis y la critica de todo eso. "
    "Que un articulo solo MENCIONE la IA no cuenta: una infraestructura o una herramienta que "
    "ademas sirve para cargas de trabajo de IA sigue teniendo como asunto la infraestructura."
)
COMMERCIAL_INSTRUCTIONS = (
    "Es material comercial: una entrada de changelog, una nota de producto del tipo 'ya disponible "
    "en X', un caso de cliente o testimonial, una ronda de financiacion, o promocion de producto. "
    "Un analisis, un tutorial, un ensayo o un post mortem NO son material comercial aunque te "
    "parezcan flojos."
)
WORTH_READING_INSTRUCTIONS = (
    "Merece que leamos el articulo entero para decidir si entra en un boletin diario sobre IA. "
    "Es una criba amplia: ante la duda, di que si. "
    "Merece leerse aunque el titular no parezca de IA cuando alguien cambia una decision tecnica y "
    "dice por que, cuando algo ocurre por primera vez, cuando alguien verifica o desmiente lo que "
    "afirmo otro, o cuando hay una cifra medida. "
    "No merece leerse un titular de gadgets, consumo, hardware, deporte o politica general, ni una "
    "novedad interna del ecosistema de un lenguaje, ni un numero de version, ni un evento, una "
    "inscripcion, una ronda o una contratacion."
)


@dataclass(frozen=True)
class Judgement:
    """Lo que el modelo de decisiones dice de un articulo."""

    consequence: float      # 0 a 5
    verifiability: float    # 0 a 5
    is_ai: float            # probabilidad 0 a 1
    commercial: float       # probabilidad 0 a 1

    @property
    def importance(self) -> int:
        """Nota de 1 a 100, para que el resto de la tuberia no cambie.

        La consecuencia manda y la verificabilidad corrige: un numero
        espectacular que solo afirma el vendedor no puede encabezar la edicion."""
        blended = 0.7 * self.consequence + 0.3 * self.verifiability
        return max(1, min(100, round(blended / 5 * 100)))

    @property
    def subject(self) -> str:
        return "ia" if self.is_ai >= 0.5 else "otro"

    @property
    def discarded(self) -> bool:
        return self.commercial >= 0.6


def state_of(item: Item) -> dict:
    return {
        "fuente": item.source,
        "titular": item.title,
        "texto": (item.body or item.summary or "")[:STATE_MAX_CHARS],
    }


class TypeSafeDecider:
    """Las decisiones (criba, tema, notas, descarte) contra un modelo que solo
    devuelve valores tipados. Las escribe en el hueco que define el esquema, asi
    que no puede saltarse una escala como si fuese una instruccion del prompt."""

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    def ask(self, state: dict, questions: dict) -> dict:
        last: Exception | None = None
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                return self.ask_once(state, questions)
            except RuntimeError as exc:
                last = exc
                if attempt < RETRY_ATTEMPTS:
                    time.sleep(RETRY_PAUSE_SECONDS * attempt)
        raise last or RuntimeError("TypeSafe failed")

    def ask_once(self, state: dict, questions: dict) -> dict:
        body = json.dumps({"model": self.model, "state": state, "questions": questions}).encode("utf-8")
        http_request = request.Request(
            self.base_url,
            data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")[:200]
            raise RuntimeError(f"TypeSafe failed with HTTP {exc.code}: {detail}") from exc
        except (error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not reach TypeSafe: {exc}") from exc
        answers = payload.get("answers")
        if not isinstance(answers, dict):
            raise RuntimeError("TypeSafe returned no answers")
        return answers

    def worth_reading(self, items: list[Item]) -> dict[str, float]:
        """Una pregunta por titular. A diferencia de pedir 'elige 60 de estos
        161', cada articulo se juzga por si mismo y no compite por un hueco en
        una lista que el modelo tiene que mantener entera en la cabeza."""
        def one(item: Item) -> tuple[str, float]:
            state = {"fuente": item.source, "titular": item.title}
            questions = {"merece": {"type": "noul", "instructions": WORTH_READING_INSTRUCTIONS}}
            try:
                answers = self.ask(state, questions)
                return item.uid, float(answers["merece"]["noul"])
            except Exception as exc:
                logger.info("Preselection call failed for '%s': %s", item.title[:50], exc)
                return item.uid, -1.0

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            results = list(executor.map(one, items))
        return {uid: score for uid, score in results if score >= 0}

    def judge(self, items: list[Item]) -> dict[str, Judgement]:
        def one(item: Item) -> tuple[str, Judgement | None]:
            questions = {
                "es_ia": {"type": "noul", "instructions": SUBJECT_INSTRUCTIONS},
                "comercial": {"type": "noul", "instructions": COMMERCIAL_INSTRUCTIONS},
                "consecuencia": {
                    "type": "score",
                    "instructions": "Cuanto altera lo que alguien va a hacer manana, si desarrolla software, opera infraestructura o trabaja con IA.",
                    "criteria": CONSEQUENCE_LEVELS,
                },
                "verificabilidad": {
                    "type": "score",
                    "instructions": "Quien afirma lo que cuenta el articulo y hasta que punto se puede comprobar.",
                    "criteria": VERIFIABILITY_LEVELS,
                },
            }
            try:
                answers = self.ask(state_of(item), questions)
                return item.uid, Judgement(
                    consequence=float(answers["consecuencia"]["score"]),
                    verifiability=float(answers["verificabilidad"]["score"]),
                    is_ai=float(answers["es_ia"]["noul"]),
                    commercial=float(answers["comercial"]["noul"]),
                )
            except Exception as exc:
                logger.info("Judgement call failed for '%s': %s", item.title[:50], exc)
                return item.uid, None

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            results = list(executor.map(one, items))
        return {uid: judgement for uid, judgement in results if judgement is not None}


def build_decider(api_key_env: str = "TYPESAFE_API_KEY") -> TypeSafeDecider | None:
    api_key = os.getenv(api_key_env)
    if not api_key:
        logger.info("No %s set: decisions stay with the language model", api_key_env)
        return None
    return TypeSafeDecider(api_key)
