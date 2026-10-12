"""
meta_client.py — Reusable Meta Graph API client for Instagram & Facebook comments and DMs.
Zero hardcoding: dynamically reads token, base url, and API version.
"""

import os
import httpx
from dotenv import load_dotenv
from src.core.meta_loader import get_meta_settings

load_dotenv()

_async_client: httpx.AsyncClient = None


def _get_client() -> httpx.AsyncClient:
    """Singleton async HTTP client with connection pooling."""
    global _async_client
    if _async_client is None or _async_client.is_closed:
        _async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
        )
    return _async_client


async def _meta_post(endpoint: str, payload: dict) -> dict:
    """Unified POST caller for all Meta Graph API endpoints."""
    token = os.getenv("META_PAGE_ACCESS_TOKEN", "").strip()
    if not token:
        print("[meta_client] ⚠️ META_PAGE_ACCESS_TOKEN is not configured.")
        return {"error": "Missing META_PAGE_ACCESS_TOKEN"}

    settings = get_meta_settings()
    base_url = settings["graph_api_base"]
    version = settings["graph_api_version"]
    url = f"{base_url}/{version}/{endpoint.lstrip('/')}"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        client = _get_client()
        resp = await client.post(url, json=payload, headers=headers)
        data = resp.json()
        if resp.status_code >= 400:
            print(f"[meta_client] API error ({resp.status_code}) on {endpoint}: {data}")
        return data
    except Exception as e:
        print(f"[meta_client] Request failed on {endpoint}: {e}")
        return {"error": str(e)}


async def reply_to_comment_async(comment_id: str, message: str) -> dict:
    """Posts a public direct reply to an Instagram or Facebook comment."""
    return await _meta_post(f"{comment_id}/replies", {"message": message})


async def send_private_reply_async(comment_id: str, message: str) -> dict:
    """Sends a private DM to a user from their comment (Private Reply API)."""
    return await _meta_post("me/messages", {"recipient": {"comment_id": comment_id}, "message": {"text": message}})


async def send_direct_message_async(recipient_id: str, message: str) -> dict:
    """Sends a direct message (DM) to an Instagram/Facebook user."""
    return await _meta_post("me/messages", {"recipient": {"id": recipient_id}, "message": {"text": message}})


async def send_quick_replies_async(recipient_id: str, message: str, buttons: list) -> dict:
    """
    Sends interactive quick-reply buttons (up to 10 buttons) to Instagram or Facebook Messenger.
    """
    clean_buttons = [str(b).strip()[:20] for b in (buttons or []) if str(b).strip()][:10]
    if not clean_buttons:
        return await send_direct_message_async(recipient_id, message)

    quick_replies = [
        {
            "content_type": "text",
            "title": title,
            "payload": title,
        }
        for title in clean_buttons
    ]
    payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "text": message,
            "quick_replies": quick_replies,
        },
    }
    return await _meta_post("me/messages", payload)


async def send_media_message_async(recipient_id: str, media_type: str, media_url: str) -> dict:
    """Sends a media attachment (video, image, audio) to an Instagram/Facebook user."""
    payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "attachment": {
                "type": media_type,
                "payload": {"url": media_url, "is_reusable": True},
            }
        },
    }
    return await _meta_post("me/messages", payload)
