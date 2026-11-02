"""
hono_staging_client.py — Asynchronous Client for Sensationz Backend AI Staging API.

Dispatches staging requests to sensationz-hono (/api/external/stage-enrollment)
guarded by the cryptographically secure AUTOMATION_SECRET header.
Supports both:
- Paid course enrollments ('COURSE')
- Free trial classes ('TRIAL')
"""

import os
import re
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("hono_staging_client")

HONO_API_URL = os.getenv("HONO_API_URL", "http://localhost:5000").rstrip("/")
AUTOMATION_SECRET = os.getenv("AUTOMATION_SECRET", "")

# ── Shared Connection Pool (Keep-Alive) ──────────────────────────────────────
_HTTP_CLIENT: Optional[httpx.AsyncClient] = None

def get_hono_client() -> httpx.AsyncClient:
    """Returns a shared, pooled AsyncClient with persistent Keep-Alive connections."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=3.0),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
        )
    return _HTTP_CLIENT

CANONICAL_DB_COURSE_MAP = {
    "yoga": "Yoga",
    "kathak": "Kathak",
    "ladies_dance": "Ladies Bollywood Dance",
    "western_dance": "Western Dance",
    "fitness": "Fitness",
    "music": "Vocal",
    "guitar": "Guitar",
    "keyboard": "Keyboard",
    "face_yoga": "Face Yoga",
    "online live interactive yoga": "Yoga",
    "kathak classical dance": "Kathak",
    "online live interactive ladies bollywood dance": "Ladies Bollywood Dance",
    "fitness, zumba & aerobics": "Fitness",
    "vocal singing & music": "Vocal",
    "guitar classes": "Guitar",
    "keyboard & piano": "Keyboard",
    "face yoga & facial fitness": "Face Yoga",
}


def clean_phone_for_contact(phone: str) -> str:
    """Extracts pure digits from phone string, guaranteeing country code representation."""
    digits = re.sub(r"\D", "", str(phone or ""))
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"91{digits}"
    return digits


def clean_email(email_str: str) -> Optional[str]:
    """Extracts and normalizes email string (lowercased, stripped of surrounding punctuation)."""
    if not email_str:
        return None
    m = re.search(r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", str(email_str))
    if m:
        return m.group(1).lower().strip().rstrip(".")
    return None


async def stage_ai_enrollment_async(
    email: str,
    contact: str,
    course: str,
    timing: Optional[str] = None,
    teacher: Optional[str] = None,
    days: Optional[str] = None,
    days_code: Optional[str] = None,
    enrollment_type: str = "COURSE",
    package_duration: Optional[str] = None,
    payment_id: Optional[str] = None,
    amount: Optional[float] = None,
    currency: str = "INR",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    timeout_seconds: float = 15.0,
) -> Dict[str, Any]:
    """
    Submits student enrollment payload to sensationz-hono (/api/external/stage-enrollment).

    Returns:
        {
            "success": bool,
            "action": "STAGED" | "ENROLLED_DIRECTLY" | "ERROR",
            "staged_id": str | None,
            "profile_id": str | None,
            "message": str,
            "error": str | None,
        }
    """
    normalized_email = clean_email(email)
    if not normalized_email:
        logger.warning(f"[hono_staging_client] Invalid email provided: '{email}'")
        return {
            "success": False,
            "action": "ERROR",
            "staged_id": None,
            "profile_id": None,
            "message": "Invalid email address format.",
            "error": f"Invalid email: '{email}'",
        }

    normalized_contact = clean_phone_for_contact(contact)
    if len(normalized_contact) < 10:
        logger.warning(f"[hono_staging_client] Invalid contact provided: '{contact}'")
        return {
            "success": False,
            "action": "ERROR",
            "staged_id": None,
            "profile_id": None,
            "message": "Contact number must contain at least 10 digits.",
            "error": f"Invalid contact: '{contact}'",
        }

    if not AUTOMATION_SECRET:
        logger.error("[hono_staging_client] AUTOMATION_SECRET is not configured in environment!")
        return {
            "success": False,
            "action": "ERROR",
            "staged_id": None,
            "profile_id": None,
            "message": "Automation secret unconfigured.",
            "error": "AUTOMATION_SECRET missing in .env",
        }

    # Resolve course to canonical DB course title if possible
    clean_course_input = str(course or "").strip()
    canonical_course = CANONICAL_DB_COURSE_MAP.get(
        clean_course_input.lower(), clean_course_input
    )

    # Calculate dates based on package duration if omitted
    if not start_date:
        start_date = datetime.now().strftime("%Y-%m-%d")
    if not end_date:
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            dur_lower = str(package_duration or "").lower()
            if "year" in dur_lower or "12 month" in dur_lower or "annual" in dur_lower:
                days_add = 365
            elif "6 month" in dur_lower:
                days_add = 180
            elif "3 month" in dur_lower:
                days_add = 90
            elif "2 month" in dur_lower:
                days_add = 60
            elif "trial" in dur_lower or enrollment_type == "TRIAL":
                days_add = 7
            else:
                days_add = 30
            end_date = (start_dt + timedelta(days=days_add)).strftime("%Y-%m-%d")
        except Exception:
            end_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

    endpoint = f"{HONO_API_URL}/api/external/stage-enrollment"
    headers = {
        "Content-Type": "application/json",
        "x-automation-secret": AUTOMATION_SECRET,
    }

    payload: Dict[str, Any] = {
        "email": normalized_email,
        "contact": normalized_contact,
        "course": canonical_course,
        "enrollment_type": enrollment_type,
        "currency": currency or "INR",
        "start_date": start_date,
        "end_date": end_date,
    }
    if package_duration:
        payload["package_duration"] = package_duration
    if timing:
        payload["timing"] = timing
    if teacher:
        payload["teacher"] = teacher
    if days:
        payload["days"] = days
    if days_code:
        payload["days_code"] = days_code
    if payment_id:
        payload["payment_id"] = payment_id
    if amount is not None:
        payload["amount"] = float(amount)

    try:
        client = get_hono_client()
        resp = await client.post(endpoint, headers=headers, json=payload, timeout=timeout_seconds)

        if resp.status_code in (200, 201):
            res_data = resp.json()
            inner = res_data.get("data") or {}
            action = inner.get("action", "STAGED")
            staged_id = inner.get("staged_id")
            profile_id = inner.get("profile_id")

            logger.info(
                f"[hono_staging_client] ✅ Successfully staged/enrolled {normalized_email} "
                f"({action}, ID: {staged_id or profile_id})"
            )
            return {
                "success": True,
                "action": action,
                "staged_id": staged_id,
                "profile_id": profile_id,
                "message": res_data.get("message", "Success"),
                "error": None,
            }
        else:
            try:
                res_json = resp.json()
                err_msg = res_json.get("error") or res_json.get("message") or resp.text[:300]
            except Exception:
                err_msg = resp.text[:300]

            logger.error(
                f"[hono_staging_client] ❌ Staging failed HTTP {resp.status_code}: {err_msg}"
            )
            return {
                "success": False,
                "action": "ERROR",
                "staged_id": None,
                "profile_id": None,
                "message": f"Server returned HTTP {resp.status_code}: {err_msg}",
                "error": err_msg,
            }
    except httpx.ConnectError as conn_err:
        logger.warning(
            f"[hono_staging_client] Connection error reaching {endpoint}: {conn_err}"
        )
        return {
            "success": False,
            "action": "ERROR",
            "staged_id": None,
            "profile_id": None,
            "message": "Could not connect to backend server.",
            "error": str(conn_err),
        }
    except Exception as exc:
        logger.error(f"[hono_staging_client] Unexpected error staging enrollment: {exc}")
        return {
            "success": False,
            "action": "ERROR",
            "staged_id": None,
            "profile_id": None,
            "message": "Unexpected error occurred.",
            "error": str(exc),
        }


# ── Non-Blocking Async Background Runner with Exponential Backoff ─────────────
async def _execute_staging_with_retry(payload: Dict[str, Any], phone: str, max_retries: int = 3):
    """
    Executes stage_ai_enrollment_async in the background with exponential retries.
    Updates in-memory and Redis state on success so the user turn is never blocked.
    """
    from src.state.chat_state import get_user_state_async, save_user_state_async

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"[staging_worker] Attempt {attempt}/{max_retries} for {payload.get('email')} ({phone})")
            res = await stage_ai_enrollment_async(**payload)
            if res.get("success"):
                logger.info(
                    f"[staging_worker] ✅ Staged successfully on attempt {attempt}: "
                    f"{payload.get('email')} -> ID: {res.get('staged_id') or res.get('profile_id')}"
                )
                try:
                    state = await get_user_state_async(phone)
                    state["staged_enrollment"] = True
                    state["staged_id"] = res.get("staged_id") or res.get("profile_id")
                    state["awaiting_email_fulfillment"] = False
                    await save_user_state_async(phone, state)
                except Exception as state_err:
                    logger.warning(f"[staging_worker] Could not update local state after success: {state_err}")
                return res
            else:
                logger.warning(
                    f"[staging_worker] Staging attempt {attempt} returned non-success: {res.get('error') or res.get('message')}"
                )
        except Exception as attempt_err:
            logger.error(f"[staging_worker] Staging attempt {attempt} threw exception: {attempt_err}")

        if attempt < max_retries:
            backoff = attempt * 1.5
            logger.info(f"[staging_worker] Retrying staging for {phone} in {backoff}s...")
            await asyncio.sleep(backoff)

    logger.critical(f"[staging_worker] ❌ All {max_retries} staging attempts failed for {payload.get('email')} ({phone})")
    return {"success": False, "error": f"Failed after {max_retries} retries"}


def trigger_async_staging(payload: Dict[str, Any], phone: str):
    """
    Fire-and-forget non-blocking staging runner.
    Immediately dispatches an async background task so WhatsApp webhooks respond in <30ms.
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_execute_staging_with_retry(payload, phone))
        logger.info(f"[staging_worker] Dispatched background staging task for {payload.get('email')} ({phone})")
    except RuntimeError:
        # Fallback if called outside an active event loop
        asyncio.create_task(_execute_staging_with_retry(payload, phone))

