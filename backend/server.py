"""
Chatbot Backend — servidor FastAPI genérico para chatbots de negocio.
Configurable por cliente mediante variables de entorno y ficheros externos.
"""

import asyncio
import csv
import html
import io
import json
import logging
import os
import re
import secrets
import smtplib
import threading
import time
import unicodedata
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

import whatsapp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("chatbot")

# --- Configuración base ---
# Todas las rutas por defecto cuelgan del propio código, no de un $HOME concreto:
# así el motor arranca igual desde un clon recién hecho o dentro del contenedor.
_BASE_DIR = Path(__file__).parent
_ROOT_DIR = _BASE_DIR.parent

# Fichero de secretos compartido por las instancias. Cada servicio pasa además
# su propio fichero de entorno, que manda: load_dotenv no pisa lo que ya está
# definido en el entorno del proceso.
ENV_FILE = Path(os.getenv("ENV_FILE", str(_BASE_DIR / ".env")))
load_dotenv(ENV_FILE)

# Identidad del cliente
COMPANY_NAME    = os.getenv("COMPANY_NAME", "Mi Empresa")
BOT_NAME        = os.getenv("BOT_NAME", "Asistente")
PANEL_TOKEN     = os.getenv("PANEL_TOKEN", "")
WIDGET_TITLE    = os.getenv("WIDGET_TITLE", "Asistente comercial con IA")
WIDGET_WELCOME  = os.getenv("WIDGET_WELCOME", "")
BUSINESS_HOURS  = os.getenv("BUSINESS_HOURS_MSG", "")
PRIVACY_URL     = os.getenv("PRIVACY_URL", "")
POWERED_BY      = os.getenv("POWERED_BY_LABEL", "Asistente IA")
LEAD_EXTRACT    = os.getenv("LEAD_EXTRACT_ENABLED", "true").lower() in ("1", "true", "yes")

# Credenciales
GROQ_API_KEY       = os.getenv("GROQ_API_KEY")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
# `or` en vez de default de getenv: una variable definida pero VACÍA en el .env
# (EMAIL_FROM=) devolvía "" y el login SMTP fallaba con 535 BadCredentials.
# Sin EMAIL_FROM se usa EMAIL_TO: el caso normal es que el buzón que recibe los
# leads sea la misma cuenta que los envía. Sin ninguno de los dos, el aviso por
# email queda desactivado y el lead se guarda igual en disco y en el webhook.
EMAIL_TO           = os.getenv("EMAIL_TO") or ""
EMAIL_FROM         = os.getenv("EMAIL_FROM") or EMAIL_TO
SMTP_HOST          = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT          = int(os.getenv("SMTP_PORT", "587"))
SMTP_TIMEOUT       = 15

# API de IA
GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
LEAD_EXTRACT_MODEL = os.getenv("LEAD_EXTRACT_MODEL", GROQ_MODEL)
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.7"))
MAX_TOKENS  = int(os.getenv("MAX_TOKENS", "500"))
LEAD_EXTRACT_MAX_TOKENS = int(os.getenv("LEAD_EXTRACT_MAX_TOKENS", "400"))

# Webhook opcional para leads (ej: n8n)
LEAD_WEBHOOK_URL = os.getenv("LEAD_WEBHOOK_URL")

# WhatsApp (Meta Cloud API) — el canal se activa solo si hay phone_number_id + token
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
WHATSAPP_ACCESS_TOKEN    = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_VERIFY_TOKEN    = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
META_APP_SECRET          = os.getenv("META_APP_SECRET")
META_GRAPH_VERSION       = os.getenv("META_GRAPH_VERSION", "v21.0")

# CORS
_raw_origins  = os.getenv("ALLOWED_ORIGINS", "*")
CORS_ORIGINS  = ["*"] if _raw_origins == "*" else [o.strip() for o in _raw_origins.split(",")]

# Logs y persistencia
LOG_DIR       = Path(os.getenv("LOG_DIR", str(_ROOT_DIR / "data")))
LEADS_LOG     = LOG_DIR / "leads.jsonl"
CHAT_LOG      = LOG_DIR / "conversations.jsonl"
SESSIONS_FILE = LOG_DIR / "sessions.json"

# Límites
MAX_CONTENT_LEN      = 4_000
MAX_MESSAGES         = 50
MAX_ACTIVE_SESSIONS  = 1_000
SESSION_ID_MAX_LEN   = 128
INACTIVITY_TIMEOUT_MIN = 5
RATE_LIMIT_MAX       = 30
RATE_LIMIT_WINDOW    = 60
MAX_EMAIL_MESSAGES   = 30
MAX_EMAIL_BODY_CHARS = 15_000
MAX_RETRY_DELAY      = 30
GROQ_RETRIABLE_CODES = {429, 500, 502, 503}
LEAD_MIN_KEYWORDS    = int(os.getenv("LEAD_MIN_KEYWORDS", "1"))
LEAD_AUTO_SEND       = os.getenv("LEAD_AUTO_SEND", "true").lower() in ("1", "true", "yes")
ALLOW_PUBLIC_PRICES  = os.getenv("ALLOW_PUBLIC_PRICES", "false").lower() in ("1", "true", "yes")
SIMPLE_CHAT          = os.getenv("SIMPLE_CHAT", "false").lower() in ("1", "true", "yes")

# --- Carga de configuración por cliente ---
PROMPT_FILE  = Path(os.getenv("PROMPT_FILE",   str(_BASE_DIR / "prompt.txt")))
KEYWORDS_FILE = Path(os.getenv("KEYWORDS_FILE", str(_BASE_DIR / "keywords.txt")))


def _load_prompt() -> str:
    if not PROMPT_FILE.exists():
        raise RuntimeError(f"Fichero de prompt no encontrado: {PROMPT_FILE}")
    return PROMPT_FILE.read_text(encoding="utf-8").strip()


def _load_keywords() -> list[str]:
    """Una palabra clave por línea. Se ignoran las vacías y las que empiezan por #."""
    if not KEYWORDS_FILE.exists():
        return []
    lineas = (line.strip() for line in KEYWORDS_FILE.read_text(encoding="utf-8").splitlines())
    return [line.lower() for line in lineas if line and not line.startswith("#")]


SYSTEM_PROMPT       = _load_prompt()
COMMERCIAL_KEYWORDS = _load_keywords()

# --- Textos del asistente, parametrizables por instancia ---
# Los valores por defecto están escritos para un vertical industrial (pedidos,
# medidas, equipo comercial). Una instancia de otro sector (p. ej. una clínica)
# los sobreescribe con TEXTS_FILE=/ruta/textos.json — un JSON plano
# {"clave": "texto"} con las claves que quiera cambiar. Sin TEXTS_FILE el
# comportamiento es exactamente el de siempre.
_TEXTS_FILE = os.getenv("TEXTS_FILE") or ""


def _load_texts() -> dict:
    if not _TEXTS_FILE:
        return {}
    path = Path(_TEXTS_FILE)
    if not path.is_file():
        raise RuntimeError(f"TEXTS_FILE no encontrado: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"TEXTS_FILE debe ser un objeto JSON plano: {path}")
    return data


_TEXTS = _load_texts()


def _text(key: str, default: str) -> str:
    return _TEXTS.get(key, default)


CONTACT_JUST_RECEIVED_HINT = _text("contact_just_received_hint", f"""

[MODO CIERRE — contacto recibido ahora]
La consulta se registra automáticamente para el equipo comercial.
Responde en máximo 2 frases: agradece, resume el pedido (producto + medidas).
Indica que le contactarán pronto. El botón sirve solo para reenviar si lo desea.
NO te presentes como {BOT_NAME}. NO hagas más preguntas.""")

NO_INTRO_HINT = _text("no_intro_hint", f"""

[SIN PRESENTACIÓN]
NO digas "Soy {BOT_NAME}", "Soy el asistente" ni "asistente de {COMPANY_NAME}". Ve directo al contenido.""")

CONTACT_COLLECTED_HINT = _text("contact_collected_hint", """

[MODO POST-CONTACTO — ya hay email o teléfono]
NO hagas preguntas de cualificación nuevas.
Responde solo a lo que pregunte el cliente. Si es un simple ok/gracias, recuerda brevemente «Enviar consulta».""")

NO_CONTACT_HINT = _text("no_contact_hint", """

[SIN EMAIL NI TELÉFONO — REGLA ESTRICTA]
El cliente AÚN NO ha dado email ni teléfono. Nombre, empresa o cargo NO cuentan como contacto.
PROHIBIDO decir «Enviar consulta», «pulsa el botón» o que el equipo comercial le contactará.
Si falta contacto, pídelo en una sola frase corta o sigue asesorando el producto.""")

NAME_ONLY_HINT = _text("name_only_hint", """

[SOLO NOMBRE/EMPRESA — SIN CONTACTO]
El último mensaje del cliente NO incluye email ni teléfono.
Agradece el nombre y pide explícitamente email o teléfono. NO menciones «Enviar consulta».""")

# Si el modelo devuelve una respuesta vacía (ver call_groq), esto es lo que ve
# el usuario en vez de una burbuja en blanco.
EMPTY_REPLY_FALLBACK = _text(
    "empty_reply_fallback",
    "Perdona, se me ha cortado la respuesta. ¿Me lo repites?",
)

EARLY_CONTACT_FORBIDDEN_HINT = """

[DEMASIADO PRONTO — NO PIDAS CONTACTO]
Aún estás en FASE A: cualificar el pedido.
NO pidas email ni teléfono en esta respuesta.
Haz UNA sola pregunta técnica (cantidad, medidas o aplicación que falte)."""

CONTACT_READY_HINT = """

[FASE B — YA PUEDES PEDIR CONTACTO]
Tienes producto y cantidad/medidas. NO hagas más preguntas técnicas de material.
Confirma el perfil en una frase y pide email o teléfono (solo una vez).
No repitas literalmente la misma frase si ya lo pediste antes."""

CONTACT_ALREADY_ASKED_HINT = """

[YA PEDISTE CONTACTO]
El cliente añadió más datos técnicos: confírmalos en una frase y vuelve a pedir email/teléfono sin copiar el mensaje anterior."""

QUANTITY_MEASURE_RE = re.compile(
    r"\d+\s*(?:metros?|m\b|mm|cm|kg|unid|piezas?|rollos?)|\d+\s*x\s*\d+",
    re.IGNORECASE,
)

CONTACT_ASK_RE = re.compile(
    r"\b(email|tel[eé]fono|teléfono|móvil|movil|whatsapp)\b",
    re.IGNORECASE,
)

MIN_USER_MSGS_BEFORE_CONTACT = int(os.getenv("MIN_USER_MSGS_BEFORE_CONTACT", "2"))

_FINALIZE_PHRASES = (
    "enviar consulta",
    "pulsa",
    "pulsar",
    "botón verde",
    "boton verde",
    "botón «enviar",
    "te contactará",
    "te contactaran",
    "se pondrá en contacto",
    "equipo comercial te",
)

PRICE_ASK_RE = re.compile(
    r"\b(precio|precios|cu[aá]nto cuesta|cu[aá]nto vale|cu[aá]nto sale|"
    r"cotizaci[oó]n|tarifa|tarifas|coste|costos?|presupuesto|"
    r"cu[aá]nto cobr|cu[aá]nto ser[ií]a|valor del)\b|€",
    re.IGNORECASE,
)

PRICE_IN_REPLY_RE = re.compile(
    r"(?:"
    r"€\s*\d[\d.,]*|\d[\d.,]*\s*€|"
    r"\d[\d.,]+\s*(?:euros?|eur)\b|"
    r"(?:cuesta|valen?|vale|sale|salen|rondan?|cobra)\s+"
    r"(?:unos?|unas?|aprox\.?|sobre)?\s*\d[\d.,]*|"
    r"precio\s+(?:de|por|unitario|aprox\.?|desde|entre|es)\s*[:.]?\s*\d|"
    r"desde\s+\d[\d.,]*\s*(?:€|euros?)|"
    r"entre\s+\d[\d.,]*\s*y\s+\d[\d.,]*\s*(?:€|euros?)"
    r")",
    re.IGNORECASE,
)

NO_PRICE_REPLY = _text("no_price_reply", (
    "Los precios los gestiona nuestro equipo comercial con un presupuesto personalizado. "
    "Si me indicas email o teléfono, te contactan con la oferta adaptada a tu pedido."
))

PRICE_QUESTION_HINT = _text("price_question_hint", """

[CLIENTE PREGUNTA POR PRECIO O PRESUPUESTO]
PROHIBIDO dar cifras, €, euros/metro, rangos o estimaciones económicas.
Responde que comercial envía presupuesto personalizado. Pide email/teléfono si faltan.""")


# --- Validación de credenciales al arranque ---
def _check_required_env():
    missing = [k for k, v in {"GROQ_API_KEY": GROQ_API_KEY, "GMAIL_APP_PASSWORD": GMAIL_APP_PASSWORD}.items() if not v]
    if missing:
        raise RuntimeError(f"Faltan variables de entorno en {ENV_FILE}: {', '.join(missing)}")


# --- Rate limiting por IP (sliding window) ---
_rate_data: dict[str, list[float]] = {}
TRUSTED_PROXIES = {"127.0.0.1", "::1"}


def _client_ip(request: Request) -> str:
    direct_ip = request.client.host if request.client else "unknown"
    if direct_ip in TRUSTED_PROXIES:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return direct_ip


def _is_rate_limited(ip: str) -> bool:
    now = time.monotonic()
    cutoff = now - RATE_LIMIT_WINDOW
    timestamps = [t for t in _rate_data.get(ip, []) if t > cutoff]
    if len(timestamps) >= RATE_LIMIT_MAX:
        return True
    _rate_data[ip] = timestamps + [now]
    return False


# --- Locks ---
_log_chat_lock  = threading.Lock()
_log_leads_lock = threading.Lock()
_sessions_lock  = threading.Lock()

# --- Estado global inicializado en lifespan ---
_http_client: httpx.AsyncClient | None = None
_start_time: datetime | None = None
_session_locks: dict[str, asyncio.Lock] = {}


def _get_session_lock(session_id: str) -> asyncio.Lock:
    if session_id not in _session_locks:
        _session_locks[session_id] = asyncio.Lock()
    return _session_locks[session_id]


# --- Persistencia de sesiones ---
def _sessions_to_dict(sessions: dict) -> dict:
    result = {}
    for sid, sess in sessions.items():
        result[sid] = {
            "sent":          sess.get("sent", False),
            "finalized":     sess.get("finalized", False),
            "channel":       sess.get("channel", "web"),
            "lead_data":     sess.get("lead_data"),
            "history":       [{"role": m.role, "content": m.content} for m in sess.get("history", [])],
            "last_activity": sess["last_activity"].isoformat() if sess.get("last_activity") else None,
        }
    return result


def _sessions_from_dict(data: dict) -> dict:
    result = {}
    for sid, sess in data.items():
        result[sid] = {
            "sent":          sess.get("sent", False),
            "finalized":     sess.get("finalized", False),
            "channel":       sess.get("channel", "web"),
            "lead_data":     sess.get("lead_data"),
            "history":       [Message.model_construct(role=m["role"], content=m["content"]) for m in sess.get("history", [])],
            "last_activity": datetime.fromisoformat(sess["last_activity"]) if sess.get("last_activity") else None,
        }
    return result


def _save_sessions_sync():
    try:
        data = _sessions_to_dict(active_sessions)
        tmp = SESSIONS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(SESSIONS_FILE)
    except Exception as e:
        logger.error("Error guardando sesiones: %s", e)


def _load_sessions() -> dict:
    if not SESSIONS_FILE.exists():
        return {}
    try:
        data = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
        sessions = _sessions_from_dict(data)
        logger.info("Sesiones restauradas: %d", len(sessions))
        return sessions
    except Exception as e:
        logger.warning("No se pudo cargar sessions.json: %s", e)
        return {}


# --- App con lifespan ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client, _start_time
    _check_required_env()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _start_time = datetime.now()
    _http_client = httpx.AsyncClient(timeout=30)
    active_sessions.update(_load_sessions())
    task = asyncio.create_task(_inactivity_watcher())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    _save_sessions_sync()
    await _http_client.aclose()


app = FastAPI(title=f"{COMPANY_NAME} Chat Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


# --- Modelos ---
_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _validate_session_id(v: str) -> str:
    if len(v) > SESSION_ID_MAX_LEN:
        raise ValueError(f"session_id demasiado largo (máx {SESSION_ID_MAX_LEN})")
    if not _SESSION_ID_RE.match(v):
        raise ValueError("session_id contiene caracteres no permitidos (solo letras, números, - y _)")
    return v


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str

    @field_validator("content")
    @classmethod
    def check_content_length(cls, v: str) -> str:
        if len(v) > MAX_CONTENT_LEN:
            raise ValueError(f"Mensaje demasiado largo (máx {MAX_CONTENT_LEN} caracteres)")
        return v


class ChatRequest(BaseModel):
    session_id: str
    messages: list[Message]

    @field_validator("session_id")
    @classmethod
    def check_session_id(cls, v: str) -> str:
        return _validate_session_id(v)

    @field_validator("messages")
    @classmethod
    def check_messages(cls, v: list) -> list:
        if not v:
            raise ValueError("La lista de mensajes no puede estar vacía")
        if len(v) > MAX_MESSAGES:
            raise ValueError(f"Demasiados mensajes en el historial (máx {MAX_MESSAGES})")
        if v[-1].role != "user":
            raise ValueError("El último mensaje debe ser del usuario")
        return v


class ChatResponse(BaseModel):
    reply: str
    lead_detected: bool
    lead_ready: bool = False
    lead_sent: bool = False
    lead_auto_sent: bool = False


class FinalizeRequest(BaseModel):
    session_id: str
    resend: bool = False

    @field_validator("session_id")
    @classmethod
    def check_session_id(cls, v: str) -> str:
        return _validate_session_id(v)


class PublicConfig(BaseModel):
    company: str
    bot: str
    title: str
    welcome: str
    business_hours: str
    privacy_url: str
    powered_by: str


# --- Lógica de negocio ---
EMAIL_RE = re.compile(r"[\w\.-]+@[\w\.-]+\.\w+")
# 9 dígitos empezando por 6-9, con separadores libres: la versión anterior sólo
# aceptaba el agrupado 3-3-3 y se perdía "655 33 21 09" o "655.33.21.09", que es
# como escribe el móvil media España. Los lookarounds evitan enganchar cifras
# largas (números de pedido, importes).
PHONE_RE = re.compile(r"(?<!\d)(?:\+?34[\s.-]?)?[6-9](?:[\s.-]?\d){8}(?!\d)")


def _has_contact(lead: dict | None) -> bool:
    return bool(lead and (lead.get("emails") or lead.get("phones")))


def _text_has_contact(text: str) -> bool:
    return bool(EMAIL_RE.search(text) or PHONE_RE.search(text))


def _reply_implies_finalize(reply: str) -> bool:
    low = reply.lower()
    return any(phrase in low for phrase in _FINALIZE_PHRASES)


def _user_asks_price(messages: list[Message]) -> bool:
    if ALLOW_PUBLIC_PRICES:
        return False
    user_text = " ".join(m.content for m in messages if m.role == "user")
    return bool(PRICE_ASK_RE.search(user_text))


def _reply_contains_price(reply: str) -> bool:
    return bool(PRICE_IN_REPLY_RE.search(reply))


def _reply_asks_contact(reply: str) -> bool:
    low = reply.lower()
    if not CONTACT_ASK_RE.search(reply):
        return False
    return "?" in reply or any(w in low for w in (
        "proporciona", "facilita", "indica", "deja", "dame", "necesito tu",
    ))


def _assistant_already_asked_contact(messages: list[Message]) -> bool:
    for m in messages:
        if m.role == "assistant" and _reply_asks_contact(m.content):
            return True
    return False


def _user_wants_contact_early(messages: list[Message]) -> bool:
    user_text = " ".join(m.content for m in messages if m.role == "user").lower()
    return bool(re.search(
        r"\b(presupuesto|que me contact|contacten|llamad|llam[aáe]\w*|"
        r"escribid|mi email es|mi tel[eé]fono|mi m[oó]vil|mi n[uú]mero|"
        r"me pongan en contacto|ponerse en contacto)\b",
        user_text,
    ))


def _conversation_ready_for_contact(messages: list[Message]) -> bool:
    user_msgs = [m for m in messages if m.role == "user"]
    if not user_msgs:
        return False
    if _user_wants_contact_early(messages) and len(user_msgs) >= 1:
        return True
    if len(user_msgs) < MIN_USER_MSGS_BEFORE_CONTACT:
        return False
    user_text = " ".join(m.content for m in user_msgs)
    if QUANTITY_MEASURE_RE.search(user_text):
        return True
    return len(user_msgs) >= 3


def _early_contact_fallback(messages: list[Message]) -> str:
    user_text = " ".join(m.content for m in messages if m.role == "user").lower()
    if QUANTITY_MEASURE_RE.search(user_text) and _conversation_ready_for_contact(messages):
        return _text("fallback_measures_ready", (
            "Perfecto, con esas medidas podemos prepararte el perfil adecuado. "
            "¿Me facilitas un email o teléfono para que comercial te envíe presupuesto?"
        ))
    if QUANTITY_MEASURE_RE.search(user_text):
        return _text("fallback_measures", (
            "Perfecto, con esas medidas te orientamos bien. "
            "¿Para qué aplicación concreta lo necesitas (entorno, exposición exterior, etc.)?"
        ))
    return _text("fallback_generic", (
        "Entendido. Para poder orientarte mejor, ¿puedes contarme qué necesitas exactamente?"
    ))


def _reply_reopens_material_choice(reply: str, messages: list[Message]) -> bool:
    if not _conversation_ready_for_contact(messages):
        return False
    low = reply.lower()
    return any(
        p in low for p in (
            "epdm o", "otros materiales", "neopreno o", "considerar otros",
            "caucho natural", "podrías considerar",
        )
    )


def _sanitize_reply(reply: str, messages: list[Message]) -> str:
    """Filtros de seguridad: sin precios, contacto prematuro ni cierre sin datos."""
    if SIMPLE_CHAT:
        # En modo simple solo bloqueamos el cierre sin datos de contacto.
        if not _has_contact(detect_lead(messages)) and _reply_implies_finalize(reply):
            reply = _text("ask_contact_simple", (
                "Gracias por la información. Para que podamos contactarte, "
                "¿me facilitas un email o un teléfono?"
            ))
        return reply

    if _reply_contains_price(reply):
        reply = NO_PRICE_REPLY

    if (
        not _has_contact(detect_lead(messages))
        and _conversation_ready_for_contact(messages)
        and _reply_reopens_material_choice(reply, messages)
    ):
        reply = _early_contact_fallback(messages)

    if (
        not _has_contact(detect_lead(messages))
        and _reply_asks_contact(reply)
        and not _conversation_ready_for_contact(messages)
    ):
        reply = _early_contact_fallback(messages)

    if not _has_contact(detect_lead(messages)) and _reply_implies_finalize(reply):
        reply = _text("ask_contact_before_close", (
            "Gracias por la información. Para que nuestro equipo comercial te envíe "
            "un presupuesto, ¿me facilitas un email o un teléfono de contacto?"
        ))
    return reply


_INTRO_STRIP_RE = re.compile(
    rf"^[\s¡]*(?:Soy\s+{re.escape(BOT_NAME)}(?:,\s*el\s+asistente\s+de\s+\w+)?\.?|"
    r"[^.!?]{0,100}asistente\s+de\s+\w+\.?)\s*",
    re.IGNORECASE,
)


def _strip_repeated_intro(reply: str, messages: list[Message]) -> str:
    """Quita el "Soy <BOT_NAME>…" del principio si ya hubo mensajes del asistente.

    La bienvenida ya salió en pantalla: volver a presentarse en cada respuesta
    suena a robot.
    """
    if sum(1 for m in messages if m.role == "assistant") < 1:
        return reply
    cleaned = _INTRO_STRIP_RE.sub("", reply, count=1).strip()
    return cleaned if cleaned else reply


def _can_auto_send_lead(messages: list[Message], lead_data: dict | None) -> bool:
    return _has_contact(lead_data) and _conversation_ready_for_contact(messages)


def _append_registration_notice(reply: str) -> str:
    low = reply.lower()
    if any(p in low for p in ("registrad", "contactarán", "contactaran", "recibido tu consulta")):
        return reply
    return reply.rstrip() + "\n\n" + _text(
        "registration_notice",
        "✓ Consulta registrada. Nuestro equipo comercial te contactará en breve.",
    )


def _chat_system_extra(messages: list[Message]) -> str:
    parts: list[str] = []
    if sum(1 for m in messages if m.role == "assistant") >= 1:
        parts.append(NO_INTRO_HINT)
    if _user_asks_price(messages):
        parts.append(PRICE_QUESTION_HINT)

    lead_now = detect_lead(messages)
    if _has_contact(lead_now):
        prior = detect_lead(messages[:-1]) if len(messages) > 1 else None
        if not _has_contact(prior):
            parts.append(CONTACT_JUST_RECEIVED_HINT)
        else:
            parts.append(CONTACT_COLLECTED_HINT)
        return "".join(parts)

    if SIMPLE_CHAT:
        # En modo simple no hay fases: el bot puede responder libremente y pedir
        # contacto cuando lo considere natural según el prompt.
        parts.append(NO_CONTACT_HINT)
        return "".join(parts)

    parts.append(NO_CONTACT_HINT)
    if _conversation_ready_for_contact(messages):
        if _assistant_already_asked_contact(messages):
            parts.append(CONTACT_ALREADY_ASKED_HINT)
        else:
            parts.append(CONTACT_READY_HINT)
    else:
        parts.append(EARLY_CONTACT_FORBIDDEN_HINT)

    last = messages[-1] if messages else None
    if last and last.role == "user" and not _text_has_contact(last.content):
        low = last.content.lower()
        if any(t in low for t in ("soy ", "somos ", " s.l", " sl", "empresa", "s.a.")):
            parts.append(NAME_ONLY_HINT)
    return "".join(parts)


def detect_lead(messages: list[Message]) -> dict | None:
    """Lead válido solo si hay email o teléfono (para mostrar 'Enviar consulta')."""
    user_text = " ".join(m.content for m in messages if m.role == "user").lower()
    if not user_text.strip():
        return None
    emails = list(dict.fromkeys(EMAIL_RE.findall(user_text)))
    phones = list(dict.fromkeys(PHONE_RE.findall(user_text)))
    if not emails and not phones:
        return None
    keywords_found = [kw for kw in COMMERCIAL_KEYWORDS if kw in user_text]
    return {"emails": emails, "phones": phones, "keywords": keywords_found}


def _lead_is_sendable(lead: dict, *, manual: bool = False) -> bool:
    if manual:
        return True
    if lead.get("emails") or lead.get("phones"):
        return True
    summary = lead.get("summary") or {}
    if isinstance(summary, dict) and any(summary.get(k) for k in (
        "nombre", "empresa", "producto", "email", "telefono", "resumen"
    )):
        return True
    return len(lead.get("keywords", [])) >= LEAD_MIN_KEYWORDS + 1


def _parse_json_from_llm(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return {}


LEAD_EXTRACT_PROMPT = """Eres un analista comercial B2B. Extrae datos de la conversación de chat.
Responde SOLO con JSON válido (sin markdown) usando estas claves:
nombre, empresa, producto, cantidad, aplicacion, plazo, email, telefono, prioridad, resumen
- prioridad: "alta", "media" o "baja" según urgencia comercial
- resumen: 2-3 frases en español para el equipo comercial (qué necesita el cliente)
- null en campos desconocidos"""


async def extract_lead_summary(messages: list[Message]) -> dict:
    if not LEAD_EXTRACT or not messages:
        return {}
    conv = "\n".join(
        f"{'Cliente' if m.role == 'user' else 'Asistente'}: {m.content}"
        for m in messages[-24:]
    )
    try:
        raw = await call_groq(
            [{"role": "user", "content": f"Conversación:\n\n{conv}"}],
            system_prompt=LEAD_EXTRACT_PROMPT,
            model=LEAD_EXTRACT_MODEL,
            temperature=0.2,
            max_tokens=LEAD_EXTRACT_MAX_TOKENS,
        )
        return _parse_json_from_llm(raw)
    except Exception as e:
        logger.warning("No se pudo extraer resumen de lead: %s", e)
        return {}


async def _prepare_lead(sess: dict, *, enrich: bool = True) -> dict | None:
    history = sess.get("history") or []
    if not history:
        return None
    lead = dict(sess.get("lead_data") or detect_lead(history) or {
        "emails": [], "phones": [], "keywords": [],
    })
    if enrich and LEAD_EXTRACT:
        summary = await extract_lead_summary(history)
        if summary:
            lead["summary"] = summary
            s_email = summary.get("email")
            s_phone = summary.get("telefono")
            if s_email and isinstance(s_email, str):
                lead.setdefault("emails", [])
                if s_email not in lead["emails"]:
                    lead["emails"].append(s_email)
            if s_phone and isinstance(s_phone, str):
                lead.setdefault("phones", [])
                if s_phone not in lead["phones"]:
                    lead["phones"].append(s_phone)
    return lead


async def call_groq(
    messages: list[dict],
    *,
    system_prompt: str | None = None,
    extra_system: str = "",
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    system_content = (system_prompt or SYSTEM_PROMPT) + (extra_system or "")
    payload = {
        "model":       model or GROQ_MODEL,
        "messages":    [{"role": "system", "content": system_content}] + messages,
        "temperature": TEMPERATURE if temperature is None else temperature,
        "max_tokens":  MAX_TOKENS if max_tokens is None else max_tokens,
    }
    last_exc: Exception | None = None

    for attempt in range(3):
        try:
            r = await _http_client.post(GROQ_URL, headers=headers, json=payload)
            r.raise_for_status()
            try:
                data = r.json()
                choice = data["choices"][0]
                content = choice["message"]["content"]
            except (KeyError, IndexError, json.JSONDecodeError) as exc:
                logger.error("Respuesta inesperada de Groq: %s — %s", exc, r.text[:200])
                raise ValueError("Respuesta malformada de Groq") from exc
            if not (content or "").strip():
                # Los modelos de razonamiento (gpt-oss…) gastan tokens pensando antes
                # de escribir: si el presupuesto se agota, content llega vacío con
                # finish_reason="length". Logueamos el porqué y reintentamos.
                logger.warning(
                    "Groq devolvió contenido vacío (finish_reason=%s, completion_tokens=%s "
                    "de max_tokens=%s) — intento %d/3",
                    choice.get("finish_reason"),
                    (data.get("usage") or {}).get("completion_tokens"),
                    payload["max_tokens"], attempt + 1,
                )
                if attempt < 2:
                    await asyncio.sleep(0.5)
                    continue
                return ""
            return content
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            last_exc = e
            is_retriable = isinstance(e, httpx.RequestError) or e.response.status_code in GROQ_RETRIABLE_CODES
            if not is_retriable:
                raise
            if attempt < 2:
                retry_after = None
                if isinstance(e, httpx.HTTPStatusError):
                    retry_after_hdr = e.response.headers.get("Retry-After")
                    if retry_after_hdr and retry_after_hdr.isdigit():
                        retry_after = int(retry_after_hdr)
                delay = min(max(2 ** attempt, retry_after or 0), MAX_RETRY_DELAY) if retry_after else 2 ** attempt
                await asyncio.sleep(delay)

    raise last_exc


def _format_summary_block(summary: dict) -> list[str]:
    if not summary:
        return []
    labels = [
        ("nombre", "Nombre"),
        ("empresa", "Empresa"),
        ("producto", "Producto / necesidad"),
        ("cantidad", "Cantidad / medidas"),
        ("aplicacion", "Aplicación"),
        ("plazo", "Plazo"),
        ("email", "Email"),
        ("telefono", "Teléfono"),
        ("prioridad", "Prioridad"),
        ("resumen", "Resumen"),
    ]
    lines = ["RESUMEN COMERCIAL (IA)", "-" * 50]
    for key, label in labels:
        val = summary.get(key)
        if val and str(val).strip().lower() != "null":
            lines.append(f"{label}: {val}")
    return lines + [""] if len(lines) > 2 else []


_PRIORITY_BADGE_STYLES = {
    "alta":  "background:#fee2e2;color:#991b1b;",
    "media": "background:#fef3c7;color:#92400e;",
    "baja":  "background:#dcfce7;color:#166534;",
}


def _channel_label(channel: str) -> str:
    return "WhatsApp" if channel == "whatsapp" else "Chat web"


def _build_html_email(session_id: str, messages: list[Message], lead_data: dict,
                       summary: dict, subject_tag: str, channel: str = "web") -> str:
    e = html.escape

    summary_rows = ""
    if summary:
        labels = [
            ("nombre", "Nombre"),
            ("empresa", "Empresa"),
            ("producto", "Producto / necesidad"),
            ("cantidad", "Cantidad / medidas"),
            ("aplicacion", "Aplicación"),
            ("plazo", "Plazo"),
            ("email", "Email"),
            ("telefono", "Teléfono"),
            ("prioridad", "Prioridad"),
        ]
        for key, label in labels:
            val = summary.get(key)
            if not val or str(val).strip().lower() == "null":
                continue
            if key == "email":
                value_html = f'<a href="mailto:{e(str(val))}" style="color:#1A2841;">{e(str(val))}</a>'
            elif key == "prioridad":
                style = _PRIORITY_BADGE_STYLES.get(str(val).lower(), "background:#e2e8f0;color:#334155;")
                value_html = (f'<span style="display:inline-block;padding:3px 10px;border-radius:99px;'
                               f'font-size:12px;font-weight:700;{style}">{e(str(val).capitalize())}</span>')
            else:
                value_html = e(str(val))
            summary_rows += (
                f'<tr><td style="padding:6px 0;color:#64748b;width:160px;vertical-align:top;">{e(label)}</td>'
                f'<td style="padding:6px 0;color:#1e293b;font-weight:600;vertical-align:top;">{value_html}</td></tr>'
            )

    resumen_html = ""
    resumen_txt = summary.get("resumen") if summary else None
    if resumen_txt and str(resumen_txt).strip().lower() != "null":
        resumen_html = (f'<p style="font-size:13px;color:#475569;margin:12px 0 0;">{e(str(resumen_txt))}</p>')

    summary_section = ""
    if summary_rows:
        summary_section = f"""
    <div style="padding:18px 24px;border-bottom:1px solid #e2e8f0;">
      <h2 style="font-size:13px;text-transform:uppercase;letter-spacing:.5px;color:#94a3b8;margin:0 0 12px;">Resumen comercial (IA)</h2>
      <table style="width:100%;border-collapse:collapse;font-size:14px;">{summary_rows}</table>
      {resumen_html}
    </div>"""

    contact_lines = []
    if lead_data.get("emails"):
        links = ", ".join(f'<a href="mailto:{e(addr)}" style="color:#1A2841;text-decoration:none;">{e(addr)}</a>' for addr in lead_data["emails"])
        contact_lines.append(f"Email: {links}")
    if lead_data.get("phones"):
        contact_lines.append(f"Teléfono: {e(', '.join(lead_data['phones']))}")
    keywords_html = ""
    if lead_data.get("keywords"):
        keywords_html = '<div style="margin-top:8px;">' + "".join(
            f'<span style="display:inline-block;background:#fef3c7;color:#92400e;border-radius:6px;'
            f'padding:2px 8px;font-size:12px;margin:2px 4px 2px 0;">{e(k)}</span>'
            for k in lead_data["keywords"]
        ) + "</div>"

    contact_section = ""
    if contact_lines or keywords_html:
        contact_section = f"""
    <div style="padding:18px 24px;border-bottom:1px solid #e2e8f0;">
      <h2 style="font-size:13px;text-transform:uppercase;letter-spacing:.5px;color:#94a3b8;margin:0 0 12px;">Contacto detectado</h2>
      <div style="background:#f8fafc;border-radius:8px;padding:12px 14px;font-size:14px;">
        {"<br>".join(contact_lines)}
        {keywords_html}
      </div>
    </div>"""

    total  = len(messages)
    recent = messages[-MAX_EMAIL_MESSAGES:] if total > MAX_EMAIL_MESSAGES else messages
    bubbles = ""
    if total > MAX_EMAIL_MESSAGES:
        bubbles += (f'<p style="font-size:12px;color:#94a3b8;">'
                     f'[Se muestran los últimos {MAX_EMAIL_MESSAGES} de {total} mensajes]</p>')
    for m in recent:
        is_user = m.role == "user"
        role_label = "Cliente" if is_user else BOT_NAME
        align = "flex-end" if is_user else "flex-start"
        bg = "#1A2841" if is_user else "#f1f5f9"
        fg = "#ffffff" if is_user else "#1e293b"
        radius_corner = "border-bottom-right-radius:2px;" if is_user else "border-bottom-left-radius:2px;"
        bubbles += f"""
      <div style="margin-bottom:10px;display:flex;justify-content:{align};">
        <div style="max-width:80%;padding:10px 14px;border-radius:12px;font-size:14px;line-height:1.4;background:{bg};color:{fg};{radius_corner}">
          <span style="display:block;font-size:11px;font-weight:700;opacity:.65;margin-bottom:3px;">{e(role_label)}</span>
          {e(m.content).replace(chr(10), "<br>")}
        </div>
      </div>"""

    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"></head>
<body style="margin:0;padding:24px;background:#eef1f4;font-family:-apple-system,'Segoe UI',Arial,sans-serif;">
  <div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08);">
    <div style="background:#1A2841;color:#ffffff;padding:18px 24px;">
      <div style="font-size:18px;font-weight:700;letter-spacing:.5px;">{e(COMPANY_NAME)}</div>
      <div style="font-size:13px;opacity:.9;margin-top:2px;">{e(subject_tag)} — {e(_channel_label(channel))}</div>
    </div>
    <div style="padding:14px 24px;font-size:12px;color:#64748b;border-bottom:1px solid #e2e8f0;">
      Sesión: {e(session_id)} &nbsp;·&nbsp; {datetime.now().strftime('%d/%m/%Y %H:%M')}
    </div>{summary_section}{contact_section}
    <div style="padding:18px 24px;">
      <h2 style="font-size:13px;text-transform:uppercase;letter-spacing:.5px;color:#94a3b8;margin:0 0 12px;">Conversación</h2>{bubbles}
    </div>
    <div style="padding:16px 24px;font-size:12px;color:#94a3b8;text-align:center;">
      Generado automáticamente desde el chat web de {e(COMPANY_NAME.lower())}
    </div>
  </div>
</body></html>"""


def _send_email_sync(session_id: str, messages: list[Message], lead_data: dict, channel: str = "web"):
    if not EMAIL_TO or not EMAIL_FROM:
        logger.warning("Lead %s sin aviso por email: falta EMAIL_TO en la configuración", session_id[:8])
        return
    summary = lead_data.get("summary") or {}
    priority = (summary.get("prioridad") or "").lower() if isinstance(summary, dict) else ""
    subject_tag = "URGENTE" if priority == "alta" else "Nuevo lead"
    channel_label = _channel_label(channel)

    msg = MIMEMultipart("alternative")
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO
    msg["Subject"] = f"[{COMPANY_NAME}] {subject_tag} — {channel_label} ({session_id[:8]})"

    body_lines = [
        f"NUEVO LEAD — {channel_label.upper()} {COMPANY_NAME.upper()}",
        "=" * 50,
        "",
        f"Sesión: {session_id}",
        f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "",
    ]
    body_lines.extend(_format_summary_block(summary if isinstance(summary, dict) else {}))

    body_lines.append("CONTACTO DETECTADO")
    body_lines.append("-" * 50)
    if lead_data.get("emails"):
        body_lines.append(f"Emails: {', '.join(lead_data['emails'])}")
    if lead_data.get("phones"):
        body_lines.append(f"Teléfonos: {', '.join(lead_data['phones'])}")
    if lead_data.get("keywords"):
        body_lines.append(f"Interés detectado: {', '.join(lead_data['keywords'])}")
    body_lines.append("")

    total  = len(messages)
    recent = messages[-MAX_EMAIL_MESSAGES:] if total > MAX_EMAIL_MESSAGES else messages
    body_lines.extend(["", "CONVERSACIÓN", "-" * 50, ""])
    if total > MAX_EMAIL_MESSAGES:
        body_lines.append(f"[Se muestran los últimos {MAX_EMAIL_MESSAGES} de {total} mensajes]")
        body_lines.append("")
    for m in recent:
        role = "Cliente" if m.role == "user" else BOT_NAME
        body_lines.append(f"{role}:")
        body_lines.append(m.content)
        body_lines.append("")

    body = "\n".join(body_lines)
    if len(body) > MAX_EMAIL_BODY_CHARS:
        body = body[:MAX_EMAIL_BODY_CHARS] + f"\n\n[... Truncado en {MAX_EMAIL_BODY_CHARS} caracteres ...]"
    msg.attach(MIMEText(body, "plain", "utf-8"))

    html_body = _build_html_email(session_id, messages, lead_data,
                                   summary if isinstance(summary, dict) else {}, subject_tag, channel)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    password = GMAIL_APP_PASSWORD.replace(" ", "")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as server:
        server.starttls()
        server.login(EMAIL_FROM, password)
        server.send_message(msg)


async def _send_webhook(session_id: str, sess: dict):
    if not LEAD_WEBHOOK_URL:
        return
    payload = {
        "event":        "lead_detected",
        "company":      COMPANY_NAME,
        "bot":          BOT_NAME,
        "channel":      sess.get("channel", "web"),
        "session_id":   session_id,
        "timestamp":    datetime.now().isoformat(),
        "lead":         sess["lead_data"],
        "conversation": [{"role": m.role, "content": m.content} for m in sess.get("history", [])],
    }
    try:
        r = await _http_client.post(LEAD_WEBHOOK_URL, json=payload, timeout=10)
        r.raise_for_status()
        logger.info("Webhook enviado para sesión %s", session_id[:8])
    except Exception as e:
        logger.error("Error enviando webhook: %s", e)


def _log_lead_sync(session_id: str, lead_data: dict, channel: str = "web"):
    entry = {
        "session_id": session_id,
        "company":    COMPANY_NAME,
        "channel":    channel,
        "timestamp":  datetime.now().isoformat(),
        "data":       lead_data,
    }
    with _log_leads_lock:
        with open(LEADS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _log_exchange_sync(session_id: str, user_msg: Message, assistant_msg: Message):
    entry = {
        "session_id": session_id,
        "company":    COMPANY_NAME,
        "timestamp":  datetime.now().isoformat(),
        "user":       user_msg.content,
        "assistant":  assistant_msg.content,
    }
    with _log_chat_lock:
        with open(CHAT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# --- Estado de sesiones activas (en memoria + persistencia) ---
active_sessions: dict[str, dict] = {}


def _panel_auth(token: str):
    if not PANEL_TOKEN:
        raise HTTPException(status_code=403, detail="Panel no habilitado en este servidor")
    if not token or not secrets.compare_digest(token, PANEL_TOKEN):
        raise HTTPException(status_code=401, detail="Token de acceso inválido")


def _read_leads() -> list[dict]:
    leads = []
    if not LEADS_LOG.exists():
        return leads
    try:
        for line in LEADS_LOG.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    leads.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        logger.error("Error leyendo leads: %s", e)
    return sorted(leads, key=lambda x: x.get("timestamp", ""), reverse=True)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/config", response_model=PublicConfig)
def public_config():
    welcome = WIDGET_WELCOME or f"¡Hola! Soy {BOT_NAME}, asistente de {COMPANY_NAME}. ¿En qué puedo ayudarte?"
    return PublicConfig(
        company=COMPANY_NAME,
        bot=BOT_NAME,
        title=WIDGET_TITLE,
        welcome=welcome,
        business_hours=BUSINESS_HOURS,
        privacy_url=PRIVACY_URL,
        powered_by=POWERED_BY,
    )


@app.get("/api/status")
def status():
    uptime_s = int((datetime.now() - _start_time).total_seconds()) if _start_time else 0
    return {
        "status":          "ok",
        "company":         COMPANY_NAME,
        "bot":             BOT_NAME,
        "model":           GROQ_MODEL,
        "active_sessions": len(active_sessions),
        "uptime_seconds":  uptime_s,
    }


@app.get("/api/stats")
def stats():
    def _count_lines(path: Path) -> int:
        if not path.exists():
            return 0
        try:
            with open(path, encoding="utf-8") as f:
                return sum(1 for _ in f)
        except Exception:
            return 0

    def _count_unique_sessions(path: Path) -> int:
        if not path.exists():
            return 0
        try:
            sessions = set()
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        sessions.add(json.loads(line).get("session_id"))
                    except Exception:
                        pass
            return len(sessions)
        except Exception:
            return 0

    uptime_s = int((datetime.now() - _start_time).total_seconds()) if _start_time else 0
    return {
        "company":              COMPANY_NAME,
        "total_conversations":  _count_unique_sessions(CHAT_LOG),
        "total_exchanges":      _count_lines(CHAT_LOG),
        "total_leads":          _count_lines(LEADS_LOG),
        "active_sessions":      len(active_sessions),
        "uptime_seconds":       uptime_s,
    }


@app.get("/api/leads")
def get_leads(token: str = Query(default="")):
    _panel_auth(token)
    leads = _read_leads()
    return {"company": COMPANY_NAME, "total": len(leads), "leads": leads}


def _slug(texto: str) -> str:
    """Trozo seguro para un nombre de fichero: sin acentos, espacios ni puntuación.

    `COMPANY_NAME` lo pone cada instancia y trae de todo: acentos, puntos y
    paréntesis. Metido tal cual en la cabecera Content-Disposition, un acento es
    un byte no-ASCII que la RFC 6266 no admite sin codificar.
    """
    plano = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", plano.lower()).strip("_") or "instancia"


@app.get("/api/leads/export")
def export_leads_csv(token: str = Query(default="")):
    _panel_auth(token)
    leads = _read_leads()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Fecha", "Sesion", "Canal", "Nombre", "Empresa", "Producto", "Prioridad",
        "Emails", "Telefonos", "Resumen", "Palabras clave",
    ])
    for lead in leads:
        data = lead.get("data", {})
        summary = data.get("summary") or {}
        if not isinstance(summary, dict):
            summary = {}
        writer.writerow([
            lead.get("timestamp", ""),
            lead.get("session_id", "")[:12],
            _channel_label(lead.get("channel", "web")),
            summary.get("nombre", ""),
            summary.get("empresa", ""),
            summary.get("producto", ""),
            summary.get("prioridad", ""),
            ", ".join(data.get("emails", [])),
            ", ".join(data.get("phones", [])),
            summary.get("resumen", ""),
            ", ".join(data.get("keywords", [])),
        ])
    output.seek(0)
    filename = f"leads_{_slug(COMPANY_NAME)}_{datetime.now().strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _run_turn(session_id: str, messages: list[Message], channel: str = "web") -> dict:
    """Un turno completo: llama a la IA, sanea la respuesta, gestiona sesión y auto-envío de lead.

    `messages` es el historial completo (incluye el último mensaje del usuario).
    Usado tanto por /api/chat (web) como por el webhook de WhatsApp — misma lógica, un solo sitio.
    """
    try:
        groq_messages = [{"role": m.role, "content": m.content} for m in messages]
        extra_system = _chat_system_extra(messages)
        reply = await call_groq(groq_messages, extra_system=extra_system)
        reply = _sanitize_reply(reply, messages)
        reply = _strip_repeated_intro(reply, messages)
        if not reply.strip():
            # Nunca devolver una burbuja en blanco al usuario.
            logger.warning("Respuesta vacía tras sanear; usando texto de reserva")
            reply = EMPTY_REPLY_FALLBACK
    except httpx.HTTPStatusError as e:
        logger.error("Error HTTP de Groq: %s", e.response.status_code)
        raise HTTPException(status_code=502, detail=f"Error en el servicio de IA (HTTP {e.response.status_code})") from e
    except httpx.RequestError as e:
        logger.error("Error de red conectando a Groq: %s", type(e).__name__)
        raise HTTPException(status_code=502, detail="No se pudo conectar con el servicio de IA") from e
    except Exception as e:
        logger.error("Error inesperado en call_groq: %s", e)
        raise HTTPException(status_code=502, detail="Error interno al procesar la respuesta") from e

    assistant_msg = Message.model_construct(role="assistant", content=reply)
    full_history = messages + [assistant_msg]

    sess = active_sessions.get(session_id, {"sent": False, "finalized": False, "channel": channel})
    if not sess.get("finalized"):
        lead_data = detect_lead(full_history) or sess.get("lead_data")
        sess["history"] = full_history
        sess["lead_data"] = lead_data
        sess["channel"] = channel
        sess["last_activity"] = datetime.now()
        active_sessions[session_id] = sess
    else:
        lead_data = sess.get("lead_data")

    has_contact = bool(
        lead_data and (lead_data.get("emails") or lead_data.get("phones"))
    )
    # En SIMPLE_CHAT no hay máquina de fases, así que tampoco se exige haber
    # "cualificado" antes de dar el lead por bueno: con un teléfono encima de la
    # mesa basta. Si no, un paciente que pide cita y se va en 2 mensajes se pierde.
    lead_ready = has_contact and (SIMPLE_CHAT or _conversation_ready_for_contact(full_history))
    lead_sent = bool(sess.get("sent"))
    lead_auto_sent = False

    if LEAD_AUTO_SEND and lead_ready and not lead_sent and not sess.get("finalized"):
        lead_auto_sent = await _send_and_mark(session_id, sess, "auto")
        lead_sent = bool(sess.get("sent"))
        if lead_auto_sent:
            reply = _append_registration_notice(reply)
            assistant_msg = Message.model_construct(role="assistant", content=reply)
            full_history = messages + [assistant_msg]
            sess["history"] = full_history

    await asyncio.to_thread(_log_exchange_sync, session_id, messages[-1], assistant_msg)

    return {
        "reply": reply,
        "lead_detected": has_contact,
        "lead_ready": lead_ready,
        "lead_sent": lead_sent,
        "lead_auto_sent": lead_auto_sent,
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request):
    client_ip = _client_ip(request)
    if _is_rate_limited(client_ip):
        raise HTTPException(
            status_code=429,
            detail="Demasiadas peticiones. Espera un momento.",
            headers={"Retry-After": str(RATE_LIMIT_WINDOW)},
        )

    if len(active_sessions) >= MAX_ACTIVE_SESSIONS and req.session_id not in active_sessions:
        raise HTTPException(status_code=503, detail="Servidor ocupado, inténtalo en unos minutos")

    result = await _run_turn(req.session_id, req.messages, channel="web")
    return ChatResponse(**result)


@app.get("/webhook/whatsapp", include_in_schema=False)
async def whatsapp_verify(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
):
    challenge = whatsapp.verify_subscription(hub_mode, hub_verify_token, hub_challenge, WHATSAPP_VERIFY_TOKEN)
    if challenge is None:
        raise HTTPException(status_code=403, detail="Verificación de webhook fallida")
    return PlainTextResponse(challenge)


@app.post("/webhook/whatsapp", include_in_schema=False)
async def whatsapp_incoming(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not whatsapp.verify_signature(META_APP_SECRET, raw_body, signature):
        logger.warning("Firma de WhatsApp inválida, mensaje descartado")
        raise HTTPException(status_code=401, detail="Firma inválida")

    if not WHATSAPP_PHONE_NUMBER_ID or not WHATSAPP_ACCESS_TOKEN:
        raise HTTPException(status_code=503, detail="WhatsApp no configurado en esta instancia")

    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail="JSON inválido") from e

    for incoming in whatsapp.parse_incoming_messages(body):
        if not incoming["from"] or not incoming["text"]:
            continue
        session_id = f"wa_{incoming['from']}"
        try:
            prior = active_sessions.get(session_id, {})
            history = (prior.get("history") or [])[-(MAX_MESSAGES - 1):]
            text = incoming["text"][:MAX_CONTENT_LEN]
            user_msg = Message.model_construct(role="user", content=text)
            messages = history + [user_msg]

            result = await _run_turn(session_id, messages, channel="whatsapp")

            await whatsapp.send_whatsapp_text(
                _http_client,
                phone_number_id=incoming["phone_number_id"] or WHATSAPP_PHONE_NUMBER_ID,
                access_token=WHATSAPP_ACCESS_TOKEN,
                to=incoming["from"],
                text=result["reply"],
                graph_version=META_GRAPH_VERSION,
            )
        except Exception as e:
            # Nunca dejar que un mensaje fallido tumbe la respuesta al webhook:
            # Meta reintenta agresivamente si no recibe 200, duplicando el procesado.
            logger.error("Error procesando mensaje WhatsApp de %s: %s", incoming["from"], e)

    return PlainTextResponse("OK")


async def _send_and_mark(session_id: str, sess: dict, reason: str) -> bool:
    manual = reason in ("manual", "resend")
    async with _get_session_lock(session_id):
        if sess.get("sent") and reason != "resend":
            return False
        if reason == "resend":
            sess["sent"] = False
        if not sess.get("history"):
            return False
        if manual and not any(m.role == "user" for m in sess["history"]):
            return False

        lead_data = await _prepare_lead(sess, enrich=True)
        if not lead_data or not _has_contact(lead_data):
            return False
        if not _lead_is_sendable(lead_data, manual=manual):
            return False

        sess["lead_data"] = lead_data
        channel = sess.get("channel", "web")
        try:
            await asyncio.to_thread(
                _send_email_sync, session_id, sess["history"], lead_data, channel,
            )
            sess["sent"] = True
            logger.info("Email enviado (%s) sesion %s canal %s", reason, session_id[:8], channel)
            await asyncio.to_thread(_log_lead_sync, session_id, lead_data, channel)
            asyncio.create_task(_send_webhook(session_id, sess))
            return True
        except Exception as e:
            logger.error("Error enviando email del lead: %s", e)
            return False


@app.post("/api/finalize")
async def finalize(req: FinalizeRequest):
    sess = active_sessions.get(req.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Sesion no encontrada")

    if sess.get("sent") and not req.resend:
        return {
            "status":       "already_sent",
            "email_sent":   True,
            "lead_sent":    True,
            "lead_ready":   True,
        }

    reason = "resend" if req.resend else "manual"
    sent = await _send_and_mark(req.session_id, sess, reason)
    sess["finalized"] = True
    return {
        "status":     "ok" if sent else "no_contact",
        "email_sent": sent,
        "lead_sent":  bool(sess.get("sent")),
        "resent":     req.resend and sent,
    }


async def _inactivity_watcher():
    cycle = 0
    while True:
        await asyncio.sleep(30)
        cycle += 1
        try:
            cutoff    = datetime.now() - timedelta(minutes=INACTIVITY_TIMEOUT_MIN)
            to_delete = []
            for sid, sess in list(active_sessions.items()):
                if sess.get("finalized"):
                    to_delete.append(sid)
                    continue
                if sess.get("last_activity") and sess["last_activity"] < cutoff:
                    if sess.get("lead_data"):
                        await _send_and_mark(sid, sess, "timeout")
                    sess["finalized"] = True
                    to_delete.append(sid)
            for sid in to_delete:
                active_sessions.pop(sid, None)
                _session_locks.pop(sid, None)

            # Persistir sesiones activas cada 5 ciclos (~2.5 min)
            if cycle % 5 == 0:
                await asyncio.to_thread(_save_sessions_sync)

            # Limpiar rate limiter cada 10 ciclos (~5 min)
            if cycle % 10 == 0:
                rate_cutoff = time.monotonic() - RATE_LIMIT_WINDOW
                stale = [ip for ip, ts in _rate_data.items() if not any(t > rate_cutoff for t in ts)]
                for ip in stale:
                    _rate_data.pop(ip, None)

        except Exception as e:
            logger.error("_inactivity_watcher: %s", e)


# --- Servir frontend ---
FRONTEND_DIR = Path(os.getenv("FRONTEND_DIR", str(_ROOT_DIR / "frontend")))

# Una instancia puede responder a varios dominios y querer una portada distinta
# en cada uno (p. ej. el chat en uno y una página de propuesta en otro). En vez
# de tocar el código, basta con dejar el fichero en `frontend/hosts/<dominio>.html`;
# si no existe, se sirve el `index.html` de siempre.
_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")


def _host_landing(host: str) -> Path | None:
    """Portada propia del dominio, o None si no hay ninguna.

    El nombre viene de una cabecera que pone el cliente, así que se valida como
    hostname antes de tocar el disco: sin barras ni `..` no hay forma de salir
    del directorio de portadas.
    """
    host = host.split(":")[0].strip().lower()
    if not host or ".." in host or not _HOSTNAME_RE.match(host):
        return None
    candidate = FRONTEND_DIR / "hosts" / f"{host}.html"
    return candidate if candidate.is_file() else None


@app.get("/index.html", include_in_schema=False)
@app.get("/", include_in_schema=False)
async def serve_index(request: Request):
    landing = _host_landing(request.headers.get("host", ""))
    return FileResponse(landing or FRONTEND_DIR / "index.html")


@app.get("/panel", include_in_schema=False)
@app.get("/panel.html", include_in_schema=False)
async def serve_panel():
    return FileResponse(FRONTEND_DIR / "panel.html")


# Debe ir al FINAL, después de todas las rutas /api/*
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True, follow_symlink=True), name="frontend")
