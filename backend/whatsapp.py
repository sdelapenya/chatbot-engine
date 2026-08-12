"""
Integración WhatsApp Business Platform (Meta Cloud API).
Reutiliza la misma lógica de chat que la web; este módulo solo habla con Graph API.
"""

import hashlib
import hmac
import logging

import httpx

logger = logging.getLogger("chatbot.whatsapp")


def verify_subscription(mode: str | None, token: str | None, challenge: str | None,
                         expected_token: str) -> str | None:
    """GET /webhook/whatsapp — hub.challenge de Meta. Devuelve el challenge si es válido."""
    if mode == "subscribe" and token and expected_token and hmac.compare_digest(token, expected_token):
        return challenge
    return None


def verify_signature(app_secret: str | None, raw_body: bytes, signature_header: str | None) -> bool:
    """Valida X-Hub-Signature-256. Si no hay app_secret configurado, no bloquea (modo pruebas)."""
    if not app_secret:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    received = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, received)


def parse_incoming_messages(body: dict) -> list[dict]:
    """Extrae mensajes de texto entrantes del payload de Meta.

    Devuelve lista de dicts: {from, text, message_id, phone_number_id, contact_name}.
    Ignora eventos que no sean mensajes de texto (status updates, imágenes, etc. — v1).
    """
    out = []
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            phone_number_id = value.get("metadata", {}).get("phone_number_id")
            contacts = {c.get("wa_id"): c.get("profile", {}).get("name") for c in value.get("contacts", [])}
            for msg in value.get("messages", []):
                if msg.get("type") != "text":
                    continue
                wa_from = msg.get("from")
                out.append({
                    "from": wa_from,
                    "text": msg.get("text", {}).get("body", ""),
                    "message_id": msg.get("id"),
                    "phone_number_id": phone_number_id,
                    "contact_name": contacts.get(wa_from),
                })
    return out


async def send_whatsapp_text(http_client: httpx.AsyncClient, *, phone_number_id: str,
                              access_token: str, to: str, text: str,
                              graph_version: str = "v21.0") -> None:
    url = f"https://graph.facebook.com/{graph_version}/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    r = await http_client.post(url, headers=headers, json=payload, timeout=15)
    if r.is_error:
        logger.error("Error enviando WhatsApp a %s: %s %s", to, r.status_code, r.text[:300])
        r.raise_for_status()
