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
    country_code = "+" + clean_phone[:2]
    number = clean_phone[2:]
    return country_code, number


async def send_text_message_async(phone: str, message: str):
    country_code, number = _split_phone(phone)
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
        print(f"[interakt] send_text_message_async: {response.status_code} ({elapsed:.2f}s)")
        return response
    except httpx.TimeoutException:
        elapsed = time.perf_counter() - t0
        print(f"[interakt] send_text_message_async TIMEOUT after {elapsed:.2f}s")
        return None
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"[interakt] send_text_message_async error ({elapsed:.2f}s): {e}")
        return None


def assign_chat_to_agent(phone: str, agent_email: str) -> bool:
    payload = {"user_phone_number": phone, "agent_email": agent_email}
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


async def assign_chat_to_agent_async(phone: str, agent_email: str) -> bool:
    payload = {"user_phone_number": phone, "agent_email": agent_email}
    t0 = time.perf_counter()
    try:
        client = _get_async_client()
        response = await client.post(
            f"{BASE_URL}/assignment/",
            headers=_headers(),
            json=payload,
        )
        elapsed = time.perf_counter() - t0
        print(f"[interakt] assign_chat_to_agent_async({agent_email}): {response.status_code} ({elapsed:.2f}s)")

        if response.status_code == 200:
            return True
        if response.status_code == 400:
            body = response.text.lower()
            if "already assigned to same agent" in body:
                print(f"[interakt] {agent_email} was already assigned -- treating as success.")
                return True
            if "customer matching query does not exist" in body:
                print(f"[interakt] Customer {phone} not yet in Interakt -- assignment skipped (non-fatal).")
                return False
            print(f"[interakt] Assignment 400 error: {response.text}")
            return False

        print(f"[interakt] Assignment failed: {response.status_code} {response.text}")
        return False
    except httpx.TimeoutException:
        elapsed = time.perf_counter() - t0
        print(f"[interakt] assign_chat_to_agent_async TIMEOUT after {elapsed:.2f}s")
        return False
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"[interakt] assign_chat_to_agent_async error ({elapsed:.2f}s): {e}")
        return False


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    if not INTERAKT_WEBHOOK_SECRET:
        return True
    computed = "sha256=" + hmac.HMAC(
        INTERAKT_WEBHOOK_SECRET.encode(), payload, sha256
    ).hexdigest()
    return hmac.compare_digest(computed, signature)


def send_text_message(phone: str, message: str):
    country_code, number = _split_phone(phone)
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
