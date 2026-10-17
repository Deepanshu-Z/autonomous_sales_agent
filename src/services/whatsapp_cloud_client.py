"""
whatsapp_cloud_client.py — Official Meta WhatsApp Cloud API Client.

Sends direct text messages and interactive quick-reply buttons via
the Meta Graph API (https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages).
Zero hardcoding: reads credentials, API base, and version from environment variables.
"""

import os
import httpx
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()

_async_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    """Singleton async HTTP client with connection pooling."""
    global _async_client
    if _async_client is None or _async_client.is_closed:
        _async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
        )
    return _async_client


def _get_config():
    """Retrieves WhatsApp Cloud API settings from environment."""
    phone_number_id = (
        os.getenv("PHONE_NUMBER_ID", "").strip()
        or os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    )
    token = (
        os.getenv("WHATSAPP_TOKEN", "").strip()
        or os.getenv("META_PAGE_ACCESS_TOKEN", "").strip()
    )
    base_url = os.getenv("META_GRAPH_API_BASE", "https://graph.facebook.com").rstrip("/")
    version = os.getenv("META_GRAPH_API_VERSION", "v21.0").strip("/")
    return phone_number_id, token, f"{base_url}/{version}"


async def send_whatsapp_cloud_text_async(recipient: str, message: str) -> dict:
    """
    Sends a plain text message to a WhatsApp recipient using Meta WhatsApp Cloud API.
    Recipient must be the clean phone number (with country code, no + sign, e.g. '919876543210').
    """
    phone_number_id, token, base_url = _get_config()
    clean_recipient = str(recipient).replace("+", "").strip()

    if not phone_number_id or not token:
        print(f"[whatsapp_cloud] ⚠️ Missing PHONE_NUMBER_ID or WHATSAPP_TOKEN in .env. Outbound message to {clean_recipient} logged but not sent via Cloud API.")
        return {"status": "unconfigured_credentials", "recipient": clean_recipient, "message": message}

    url = f"{base_url}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": clean_recipient,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": message,
        },
    }

    try:
        client = _get_client()
        resp = await client.post(url, json=payload, headers=headers)
        data = resp.json()
        if resp.status_code >= 400:
            print(f"[whatsapp_cloud] ❌ Meta Cloud API error ({resp.status_code}) sending to {clean_recipient}: {data}")
        else:
            print(f"[whatsapp_cloud] 📤 Message sent successfully to {clean_recipient} via Meta Cloud API.")
        return data
    except Exception as e:
        print(f"[whatsapp_cloud] ❌ Network/HTTP error sending to {clean_recipient}: {e}")
        return {"error": str(e)}


async def send_whatsapp_cloud_buttons_async(recipient: str, message: str, buttons: List[str]) -> dict:
    """
    Sends interactive quick-reply buttons (up to 3 buttons) to a WhatsApp recipient.
    """
    phone_number_id, token, base_url = _get_config()
    clean_recipient = str(recipient).replace("+", "").strip()

    valid_buttons = [str(b).strip()[:20] for b in (buttons or []) if str(b).strip()][:3]
    if not valid_buttons:
        return await send_whatsapp_cloud_text_async(clean_recipient, message)

    if not phone_number_id or not token:
        print(f"[whatsapp_cloud] ⚠️ Missing PHONE_NUMBER_ID or WHATSAPP_TOKEN in .env. Falling back to text logging for {clean_recipient}.")
        return {"status": "unconfigured_credentials", "recipient": clean_recipient, "buttons": valid_buttons}

    url = f"{base_url}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": clean_recipient,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": message,
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": f"btn_{i+1}",
                            "title": title,
                        },
                    }
                    for i, title in enumerate(valid_buttons)
                ],
            },
        },
    }

    try:
        client = _get_client()
        resp = await client.post(url, json=payload, headers=headers)
        data = resp.json()
        if resp.status_code >= 400:
            print(f"[whatsapp_cloud] ❌ Meta Cloud API interactive buttons error ({resp.status_code}) to {clean_recipient}: {data}")
            # Fallback to plain text on error
            return await send_whatsapp_cloud_text_async(clean_recipient, message)
        else:
            print(f"[whatsapp_cloud] 📤 Interactive buttons sent to {clean_recipient} via Meta Cloud API.")
        return data
    except Exception as e:
        print(f"[whatsapp_cloud] ❌ Network/HTTP error sending buttons to {clean_recipient}: {e}")
        return await send_whatsapp_cloud_text_async(clean_recipient, message)
