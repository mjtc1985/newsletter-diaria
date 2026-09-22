from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http.client import HTTPException, HTTPSConnection
from urllib.parse import urlsplit

from newsletter_diaria.models import Item

logger = logging.getLogger("newsletter_diaria")

DEFAULT_BASE_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
# La Raspberry es su propio servidor DNS y una rafaga de doscientas consultas la
# tumbaba, porque abriamos conexion nueva por llamada. Con la conexion
# reutilizada por hilo son diez consultas en toda la ejecucion. Medido sobre 40
# articulos: 4.3 s con cuatro hilos, 2.9 s con diez, 7.2 s con dieciseis, que ya
# se degrada.
MAX_WORKERS = 10
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
    "Cambia como se entiende o se hace algo, con evidencia: alguien ha medido algo y da la cifra, un analisis explica un mecanismo, un post mortem da la causa raiz, o hay investigacion replicable con un resultado no obvio",
    "Cambia las opciones disponibles, o alguien verifica o desmiente lo que otro afirmo, o es la primera vez que ocurre algo, o un laboratorio lanza un modelo aunque no te suene el nombre, o una empresa cambia una decision tecnica y explica por que",
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
    "La inteligencia artificial es el asunto del articulo, o es la causa de lo que cuenta.\n"
    "Cuenta como IA: modelos, agentes, herramientas de IA, construir software con IA o sobre IA, "
    "evaluar modelos, seguridad de sistemas de IA, su coste, sus limites, su regulacion o su "
    "negocio, y el analisis y la critica de todo eso.\n"
    "Cuenta tambien cuando la IA es la CAUSA de la noticia aunque el objeto sea otra cosa: "
    "'la programacion con IA nos ha convertido el CI en un cuello de botella' cuenta, porque sin "
    "la IA no habria noticia, y lo mismo una empresa que cambia de arquitectura porque la IA le "
    "cambia los costes.\n"
    "NO cuenta cuando la IA solo aparece mencionada o como una carga de trabajo mas: un "
    "orquestador que anade planificacion para cargas de IA sigue teniendo como asunto el "
    "orquestador, porque la noticia existiria igual sin la IA.\n"
    "La pregunta que lo decide es: si quitas la IA de la historia, ¿queda noticia?"
)
COMMERCIAL_INSTRUCTIONS = (
    "Es material comercial: una entrada de changelog, una nota de producto del tipo 'ya disponible "
    "en X', un caso de cliente o testimonial, o promocion de producto. Tambien lo es cuando el "
    "sujeto de la noticia es la propia empresa contando lo bien que le va o como alguien usa su "
    "producto. "
    "Un analisis, un tutorial, un ensayo o un post mortem NO son material comercial aunque te "
    "parezcan flojos."
)
VERSION_INSTRUCTIONS = (
    "El asunto es una version: notas de una release, 'la version N anade tal cosa', una encuesta "
    "de la comunidad de un lenguaje, o novedades internas del ecosistema de un lenguaje o "
    "framework. No cuenta si rompe compatibilidad, depreca algo con fecha, o es una "
    "vulnerabilidad del propio lenguaje o runtime."
)
EXPLAINER_INSTRUCTIONS = (
    "Es divulgacion estandar de un concepto ya conocido, del tipo 'que es X explicado', que no "
    "ensena nada no obvio a quien ya trabaja en esto. Un analisis con un hallazgo propio, un post "
    "mortem o una investigacion con resultados NO son divulgacion estandar."
)
NEWS_INSTRUCTIONS = (
    "El articulo cuenta algo que ha pasado: un lanzamiento, un incidente, una medicion, una "
    "decision tomada, una vulnerabilidad, un cambio con fecha. Un analisis, un ensayo, una "
    "opinion, un tutorial o una guia NO son noticia aunque sean excelentes."
)
TOOL_INSTRUCTIONS = (
    "Lo que presenta el articulo es una herramienta, una libreria o un framework empaquetado, en "
    "vez de un hallazgo, un resultado medido o algo que ha ocurrido. Un paper que publica una "
    "libreria es herramienta; un paper que publica un resultado no lo es."
)
EVENT_INSTRUCTIONS = (
    "Es un evento, un congreso, una inscripcion, una ronda de financiacion o una contratacion."
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


# Topes de los criterios negativos, los mismos que tenia escritos el prompt.
VERSION_CAP = 20
EVENT_CAP = 20
EXPLAINER_CAP = 40
# Una libreria empaquetada no encabeza: "solo si es un resultado".
TOOL_CAP = 45
FLAG_THRESHOLD = 0.6


@dataclass(frozen=True)
class Judgement:
    """Lo que el modelo de decisiones dice de un articulo."""

    consequence: float      # 0 a 5
    verifiability: float    # 0 a 5
    is_ai: float            # probabilidad 0 a 1
    commercial: float       # probabilidad 0 a 1
    version: float = 0.0    # probabilidad 0 a 1
    explainer: float = 0.0
    event: float = 0.0
    is_news: float = 1.0    # cuenta algo que ha pasado, frente a analisis u opinion
    is_tool: float = 0.0    # presenta una herramienta, no un resultado

    @property
    def importance(self) -> int:
        """Nota de 1 a 100, para que el resto de la tuberia no cambie.

        Manda la consecuencia. La verificabilidad entra de forma asimetrica:
        hunde lo que solo afirma quien lo vende, pero no penaliza a un analisis
        bueno por venir de un agregador. Ponderarla al 30% mandaba del 90 al 57
        un post agudo de Hacker News, que es justo lo que no queremos."""
        base = self.consequence / 5
        if self.verifiability < 2:
            base *= 0.5 + 0.25 * self.verifiability
        base += 0.05 * max(0.0, self.verifiability - 3) / 2

        score = max(1, min(100, round(base * 100)))
        if self.version >= FLAG_THRESHOLD:
            score = min(score, VERSION_CAP)
        if self.event >= FLAG_THRESHOLD:
            score = min(score, EVENT_CAP)
        if self.explainer >= FLAG_THRESHOLD:
            score = min(score, EXPLAINER_CAP)
        if self.is_tool >= FLAG_THRESHOLD:
            score = min(score, TOOL_CAP)
        return score

    @property
    def bucket(self) -> int:
        """Orden dentro de la edicion, por encima de la nota.

        Primero lo que pasa, luego lo que se opina, y lo ajeno a la IA al final:
        entra, pero no encabeza."""
        if self.subject != "ia":
            return 2
        return 0 if self.is_news >= 0.5 else 1

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
        parts = urlsplit(base_url)
        self.host = parts.netloc
        self.path = parts.path or "/"
        self.local = threading.local()

    def connection(self) -> HTTPSConnection:
        existing = getattr(self.local, "conn", None)
        if existing is None:
            existing = HTTPSConnection(self.host, timeout=TIMEOUT_SECONDS)
            self.local.conn = existing
        return existing

    def drop_connection(self) -> None:
        existing = getattr(self.local, "conn", None)
        if existing is not None:
            try:
                existing.close()
            except Exception:  # pragma: no cover - cierre defensivo
                pass
            self.local.conn = None

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
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        connection = self.connection()
        try:
            connection.request("POST", self.path, body=body, headers=headers)
            response = connection.getresponse()
            status, raw = response.status, response.read()
        except (HTTPException, OSError) as exc:
            self.drop_connection()
            raise RuntimeError(f"Could not reach TypeSafe: {exc}") from exc

        if status != 200:
            self.drop_connection()
            raise RuntimeError(f"TypeSafe failed with HTTP {status}: {raw[:200].decode('utf-8', errors='ignore')}")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError(f"TypeSafe returned invalid JSON: {exc}") from exc
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
                "version": {"type": "noul", "instructions": VERSION_INSTRUCTIONS},
                "divulgacion": {"type": "noul", "instructions": EXPLAINER_INSTRUCTIONS},
                "evento": {"type": "noul", "instructions": EVENT_INSTRUCTIONS},
                "noticia": {"type": "noul", "instructions": NEWS_INSTRUCTIONS},
                "herramienta": {"type": "noul", "instructions": TOOL_INSTRUCTIONS},
            }
            try:
                answers = self.ask(state_of(item), questions)
                return item.uid, Judgement(
                    consequence=float(answers["consecuencia"]["score"]),
                    verifiability=float(answers["verificabilidad"]["score"]),
                    is_ai=float(answers["es_ia"]["noul"]),
                    commercial=float(answers["comercial"]["noul"]),
                    version=float(answers["version"]["noul"]),
                    explainer=float(answers["divulgacion"]["noul"]),
                    event=float(answers["evento"]["noul"]),
                    is_news=float(answers["noticia"]["noul"]),
                    is_tool=float(answers["herramienta"]["noul"]),
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
