"""
interakt.py — Interakt WhatsApp API integration service.
Handles message sending, agent assignment, and webhook signature verification.
"""

import os
import hmac
import time
from hashlib import sha256
import httpx
from dotenv import load_dotenv

load_dotenv()

INTERAKT_API_KEY = os.getenv("INTERAKT_API_KEY")
INTERAKT_WEBHOOK_SECRET = os.getenv("INTERAKT_WEBHOOK_SECRET")

BASE_URL = "https://api.interakt.ai/v1/public"

_async_client = None

def _get_async_client() -> httpx.AsyncClient:
    """Returns a singleton async HTTP client with connection pooling."""
    global _async_client
    if _async_client is None or _async_client.is_closed:
        _async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
        )
    return _async_client


def _headers():
    return {
        "Authorization": f"Basic {INTERAKT_API_KEY}",
        "Content-Type": "application/json",
    }


def _split_phone(phone_with_country_code: str):
    clean_phone = str(phone_with_country_code).replace("+", "").strip()
    if len(clean_phone) == 10:
        return "+91", clean_phone
    if len(clean_phone) == 12 and clean_phone.startswith("91"):
        return "+91", clean_phone[2:]
    for cc3 in ("966", "971", "974", "965", "968", "973", "256", "353", "358"):
        if clean_phone.startswith(cc3) and len(clean_phone) > len(cc3):
            return "+" + cc3, clean_phone[len(cc3):]
    if clean_phone.startswith("1") and len(clean_phone) == 11:
        return "+1", clean_phone[1:]
    country_code = "+" + clean_phone[:2]
    number = clean_phone[2:]
    return country_code, number


async def send_text_message_async(phone: str, message: str):
    clean_phone = str(phone or "").strip()
    if clean_phone.startswith("ig_") or clean_phone.startswith("fb_"):
        from src.services.meta_client import send_direct_message_async
        from src.core.meta_loader import format_for_meta
        channel = "facebook" if clean_phone.startswith("fb_") else "instagram"
        return await send_direct_message_async(clean_phone.split("_", 1)[1], format_for_meta(message), channel=channel)

    if clean_phone.startswith("wa_"):
        clean_phone = clean_phone[3:]

    country_code, number = _split_phone(clean_phone)
    payload = {
        "countryCode": country_code,
        "phoneNumber": number,
        "type": "Text",
        "data": {"message": message},
    }
    t0 = time.perf_counter()
    try:
        client = _get_async_client()
        response = await client.post(
            f"{BASE_URL}/message/",
            headers=_headers(),
            json=payload,
        )
        elapsed = time.perf_counter() - t0
        return response
    except httpx.TimeoutException:
        elapsed = time.perf_counter() - t0
        return None
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"[interakt] send_text_message_async error ({elapsed:.2f}s): {e}")
        return None


import re


def assign_chat_to_agent(phone: str, agent_email: str) -> bool:
    clean_phone = str(phone or "").strip()
    if clean_phone.startswith("wa_"):
        clean_phone = clean_phone[3:]
    if clean_phone.startswith("ig_") or clean_phone.startswith("fb_"):
        return True

    digits_only = re.sub(r"\D", "", clean_phone)
    if len(digits_only) < 10:
        return True

    payload = {"user_phone_number": clean_phone, "agent_email": agent_email}
    t0 = time.perf_counter()
    try:
        response = httpx.post(
            f"{BASE_URL}/assignment/",
            headers=_headers(),
            json=payload,
            timeout=10.0,
        )
        elapsed = time.perf_counter() - t0
        print(f"[interakt] assign_chat_to_agent: {response.status_code} ({elapsed:.2f}s)")

        if response.status_code == 200:
            return True
        if response.status_code == 400 and "already assigned to same agent" in response.text.lower():
            print(f"[interakt] {agent_email} was already assigned -- treating as success.")
            return True

        print(f"[interakt] Assignment genuinely failed: {response.status_code} {response.text}")
        return False
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"[interakt] assign_chat_to_agent error ({elapsed:.2f}s): {e}")
        return False


# Outcomes of an Interakt chat assignment (see assign_chat_to_agent_detailed_async).
ASSIGNED = "assigned"                      # chat now belongs to the agent
NOT_INTERAKT_CHANNEL = "not_interakt"      # Instagram / Facebook: no Interakt assignment exists
AGENT_NOT_FOUND = "agent_not_found"        # this agent email doesn't exist in Interakt -> try another agent
CUSTOMER_NOT_FOUND = "customer_not_found"  # Interakt doesn't know this customer -> no agent will work
ASSIGN_FAILED = "failed"                   # timeout / 5xx / unknown error


async def assign_chat_to_agent_detailed_async(phone: str, agent_email: str) -> str:
    """Assign the chat in Interakt and say WHY it failed, so callers can pick another agent."""
    clean_phone = str(phone or "").strip()
    if clean_phone.startswith("wa_"):
        clean_phone = clean_phone[3:]
    if clean_phone.startswith("ig_") or clean_phone.startswith("fb_"):
        return NOT_INTERAKT_CHANNEL

    if len(re.sub(r"\D", "", clean_phone)) < 10:
        return CUSTOMER_NOT_FOUND

    payload = {"user_phone_number": clean_phone, "agent_email": agent_email}
    t0 = time.perf_counter()
    try:
        response = await _get_async_client().post(f"{BASE_URL}/assignment/", headers=_headers(), json=payload)
        elapsed = time.perf_counter() - t0
        print(f"[interakt] assign_chat_to_agent_async({agent_email}): {response.status_code} ({elapsed:.2f}s)")

        if response.status_code == 200:
            return ASSIGNED
        body = response.text.lower()
        if response.status_code == 400:
            if "already assigned to same agent" in body:
                return ASSIGNED
            if "agent with email not found" in body:
                print(f"[interakt] Agent {agent_email} does not exist in Interakt")
                return AGENT_NOT_FOUND
            if "customer matching query does not exist" in body:
                print(f"[interakt] Customer {phone} not yet in Interakt -- cannot assign.")
                return CUSTOMER_NOT_FOUND
            print(f"[interakt] Assignment 400 error: {response.text}")
            return ASSIGN_FAILED
        print(f"[interakt] Assignment failed: {response.status_code} {response.text}")
        return ASSIGN_FAILED
    except httpx.TimeoutException:
        print(f"[interakt] assign_chat_to_agent_async TIMEOUT after {time.perf_counter() - t0:.2f}s")
        return ASSIGN_FAILED
    except Exception as e:
        print(f"[interakt] assign_chat_to_agent_async error ({time.perf_counter() - t0:.2f}s): {e}")
        return ASSIGN_FAILED


async def assign_chat_to_agent_async(phone: str, agent_email: str) -> bool:
    """Boolean wrapper kept for existing callers (owner monitoring assignment, Meta workers).
    For handoffs use agent_handoff.assign_lead_to_available_agent_async, which skips bad agents."""
    result = await assign_chat_to_agent_detailed_async(phone, agent_email)
    return result in (ASSIGNED, NOT_INTERAKT_CHANNEL)


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    if not INTERAKT_WEBHOOK_SECRET:
        return True
    computed = "sha256=" + hmac.HMAC(
        INTERAKT_WEBHOOK_SECRET.encode(), payload, sha256
    ).hexdigest()
    return hmac.compare_digest(computed, signature)


def send_text_message(phone: str, message: str):
    clean_phone = str(phone or "").strip()
    if clean_phone.startswith("ig_") or clean_phone.startswith("fb_") or clean_phone.startswith("wa_"):
        return None

    country_code, number = _split_phone(clean_phone)
    payload = {
        "countryCode": country_code,
        "phoneNumber": number,
        "type": "Text",
        "data": {"message": message},
    }
    t0 = time.perf_counter()
    try:
        response = httpx.post(
            f"{BASE_URL}/message/",
            headers=_headers(),
            json=payload,
            timeout=10.0,
        )
        elapsed = time.perf_counter() - t0
        print(f"[interakt] send_text_message: {response.status_code} ({elapsed:.2f}s)")
        return response
    except httpx.TimeoutException:
        elapsed = time.perf_counter() - t0
        print(f"[interakt] send_text_message TIMEOUT after {elapsed:.2f}s")
        return None
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"[interakt] send_text_message error ({elapsed:.2f}s): {e}")
        return None


def _ensure_whatsapp_compatible_video_url(url: str) -> str:
    """
    Guarantees raw video URLs stay strictly under Meta's 16MB WhatsApp file limit (Error 131053).
    For Cloudinary video URLs, dynamically injects 720p / auto compression / H.264 transcoding
    if not already transformed.
    """
    if not url or "res.cloudinary.com" not in url:
        return url
    if any(param in url for param in ["q_auto", "vc_h264", "w_"]):
        return url
    if "/video/upload/" in url:
        return url.replace("/video/upload/", "/video/upload/w_720,q_auto:eco,vc_h264/")
    if "/upload/" in url:
        return url.replace("/upload/", "/upload/w_720,q_auto:eco,vc_h264/")
    return url


async def send_video_message_async(phone: str, video_url: str, caption: str = "") -> bool:
    """
    Sends a native playable video file (MP4) to WhatsApp via Interakt or to Meta DMs via Graph API.
    Returns True if successfully delivered, False on timeout or failure.
    """
    if not video_url:
        return False
    video_url = _ensure_whatsapp_compatible_video_url(video_url)
    clean_phone = str(phone or "").strip()
    if clean_phone.startswith("ig_") or clean_phone.startswith("fb_"):
        from src.services.meta_client import send_media_message_async
        recipient_id = clean_phone.split("_", 1)[1]
        channel = "facebook" if clean_phone.startswith("fb_") else "instagram"
        try:
            res = await send_media_message_async(recipient_id, "video", video_url, channel=channel)
            if isinstance(res, dict) and "error" not in res:
                return True
        except Exception as e:
            print(f"[interakt] send_video_message_async Meta error for {clean_phone}: {e}")
        return False

    if clean_phone.startswith("wa_"):
        clean_phone = clean_phone[3:]

    country_code, number = _split_phone(clean_phone)
    payload = {
        "countryCode": country_code,
        "phoneNumber": number,
        "type": "Video",
        "data": {
            "mediaUrl": video_url,
            "caption": caption or "",
        },
    }
    t0 = time.perf_counter()
    try:
        client = _get_async_client()
        response = await client.post(
            f"{BASE_URL}/message/",
            headers=_headers(),
            json=payload,
        )
        elapsed = time.perf_counter() - t0
        if response.status_code in (200, 201):
            print(f"[interakt] send_video_message_async SUCCESS ({elapsed:.2f}s)")
            return True
        else:
            print(f"[interakt] send_video_message_async failed: {response.status_code} ({response.text})")
            return False
    except httpx.TimeoutException:
        elapsed = time.perf_counter() - t0
        print(f"[interakt] send_video_message_async TIMEOUT after {elapsed:.2f}s")
        return False
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"[interakt] send_video_message_async error ({elapsed:.2f}s): {e}")
        return False


async def send_image_message_async(phone: str, image_url: str, caption: str = "") -> bool:
    """
    Sends an image file (PNG/JPEG) to WhatsApp via Interakt or to Meta DMs via Graph API.
    Returns True if successfully delivered, False on timeout or failure.
    """
    if not image_url:
        return False
    clean_phone = str(phone or "").strip()
    if clean_phone.startswith("ig_") or clean_phone.startswith("fb_"):
        from src.services.meta_client import send_media_message_async
        recipient_id = clean_phone.split("_", 1)[1]
        channel = "facebook" if clean_phone.startswith("fb_") else "instagram"
        try:
            res = await send_media_message_async(recipient_id, "image", image_url, channel=channel)
            if isinstance(res, dict) and "error" not in res:
                return True
        except Exception as e:
            print(f"[interakt] send_image_message_async Meta error for {clean_phone}: {e}")
        return False

    if clean_phone.startswith("wa_"):
        clean_phone = clean_phone[3:]

    country_code, number = _split_phone(clean_phone)
    payload = {
        "countryCode": country_code,
        "phoneNumber": number,
        "type": "Image",
        "data": {
            "mediaUrl": image_url,
            "caption": caption or "",
            "message": caption or "",
        },
    }
    t0 = time.perf_counter()
    try:
        client = _get_async_client()
        response = await client.post(
            f"{BASE_URL}/message/",
            headers=_headers(),
            json=payload,
        )
        elapsed = time.perf_counter() - t0
        if response.status_code in (200, 201):
            print(f"[interakt] send_image_message_async SUCCESS ({elapsed:.2f}s)")
            return True
        else:
            print(f"[interakt] send_image_message_async failed: {response.status_code} ({response.text})")
            return False
    except httpx.TimeoutException:
        elapsed = time.perf_counter() - t0
        print(f"[interakt] send_image_message_async TIMEOUT after {elapsed:.2f}s")
        return False
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"[interakt] send_image_message_async error ({elapsed:.2f}s): {e}")
        return False

