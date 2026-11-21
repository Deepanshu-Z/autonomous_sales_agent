"""
razorpay_service.py — Razorpay Payment Gateway Integration for Dynamic UPI QR Codes.

Single-responsibility component providing:
1. Dynamic, single-use UPI QR code generation via Razorpay QR Code API.
2. Embedded metadata notes for WhatsApp user tracking (course, timing, teacher, phone, duration).
3. QR code lifecycle helpers (fetch, close, verify webhook signatures).

Zero Hardcoding: Reads API credentials dynamically from environment variables
(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET).
"""

import os
import re
import time
import asyncio
import logging
from typing import Dict, Any, Optional, Union, Tuple
import razorpay
from dotenv import load_dotenv

from src.state.chat_state import (
    get_user_state_async,
    save_user_state_async,
    advance_stage,
    phone_concurrency_lock,
    get_sent_qr_ids,
)
from src.state.payment_status import is_payment_verified
from src.services.interakt import send_text_message_async
from src.state.chat_history import save_message_async
from src.state.csv_logger import log_message_async
from src.core.lead_logger import log_lead_action
from src.core.kb_loader import get_catalog_entry

# Ensure environment variables are loaded
load_dotenv()

logger = logging.getLogger(__name__)


class RazorpayServiceError(Exception):
    """Raised when a Razorpay API operation fails."""
    pass


_client_instance: Optional[razorpay.Client] = None


def get_razorpay_client(
    key_id: Optional[str] = None,
    key_secret: Optional[str] = None,
) -> razorpay.Client:
    """
    Returns an initialized Razorpay Client instance.
    Reads RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET from environment if not provided.
    """
    effective_key_id = key_id or os.getenv("RAZORPAY_KEY_ID")
    effective_key_secret = key_secret or os.getenv("RAZORPAY_KEY_SECRET")

    if not effective_key_id or not effective_key_secret:
        raise RazorpayServiceError(
            "Razorpay credentials missing. Ensure RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are set in environment."
        )

    return razorpay.Client(auth=(effective_key_id, effective_key_secret))


def create_dynamic_qr_code(
    amount: Optional[Union[int, float]] = None,
    amount_in_paise: Optional[int] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    notes: Optional[Dict[str, Any]] = None,
    customer_phone: Optional[str] = None,
    course_id: Optional[str] = None,
    duration: Optional[str] = None,
    timing: Optional[str] = None,
    teacher: Optional[str] = None,
    days: Optional[str] = None,
    student_name: Optional[str] = None,
    close_by_minutes: int = 1440,
    client: Optional[razorpay.Client] = None,
) -> Dict[str, Any]:
    """
    Generates a dynamic, single-use UPI QR code via Razorpay's QR Code API.

    API Requirements:
    - type="upi_qr"
    - usage="single_use"
    - fixed_amount=True
    - payment_amount in paise (must be >= 100 paise / Rs 1.00)
    - Embedded metadata notes (phone, course_id, timing, teacher, duration, etc.)

    Args:
        amount: Payable amount in INR (e.g. 826 for Rs. 826.00). Converted to paise.
        amount_in_paise: Exact payable amount in paise (e.g. 82600). Takes precedence over amount.
        name: Display name on the QR code (max 40 chars, default: 'Sensationz Performing Arts').
        description: Description of the payment (max 255 chars).
        notes: Arbitrary key-value metadata dict (up to 15 key-value pairs).
        customer_phone: Customer WhatsApp / phone number.
        course_id: Course ID (e.g. 'kathak', 'yoga').
        duration: Package duration (e.g. '1 Month', '3 Months').
        timing: Selected batch timing (e.g. '5:00 PM to 6:00 PM').
        teacher: Selected instructor name (e.g. 'Ms. Madhuri').
        student_name: Student name if known.
        close_by_minutes: Minutes until QR code expires (default: 1440 mins / 24 hours). Must be >= 2 mins.
        client: Optional pre-configured razorpay.Client instance.

    Returns:
        Dict containing:
        - id / qr_id: Razorpay QR code ID (e.g. 'qr_xxxxx')
        - image_url: Hosted Razorpay QR image / payment page URL
        - status: Current status (e.g. 'active')
        - payment_amount: Amount in paise
        - payment_amount_inr: Amount in INR
        - name: QR code display name
        - description: Payment description
        - close_by: Unix timestamp of expiry
        - notes: Embedded metadata notes
        - raw: Full API response dict
    """
    # 1. Resolve and validate payment amount in paise
    if amount_in_paise is not None:
        try:
            paise = int(amount_in_paise)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid amount_in_paise: {amount_in_paise}") from e
    elif amount is not None:
        try:
            float_amt = float(amount)
            paise = int(round(float_amt * 100))
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid amount: {amount}") from e
    else:
        raise ValueError("Either amount (in INR) or amount_in_paise must be provided.")

    if paise < 100:
        raise ValueError(f"Payment amount ({paise} paise) must be at least 100 paise (Rs. 1.00) — UPI/NPCI bank gateways reject transactions below Re. 1.")

    # 2. Build and sanitize embedded metadata notes (max 15 pairs, values max 256 chars)
    embedded_notes: Dict[str, str] = {}
    if notes:
        for k, v in notes.items():
            if v is not None and str(k).strip():
                embedded_notes[str(k).strip()[:40]] = str(v).strip()[:256]

    # Merge convenience fields into notes if not already present
    convenience_fields = [
        ("phone", customer_phone),
        ("course_id", course_id),
        ("duration", duration),
        ("timing", timing),
        ("teacher", teacher),
        ("days", days),
        ("student_name", student_name),
        ("source", "whatsapp_sales_agent"),
    ]
    for key, val in convenience_fields:
        if val is not None and key not in embedded_notes:
            embedded_notes[key] = str(val).strip()[:256]

    # Limit to Razorpay's maximum of 15 note keys
    if len(embedded_notes) > 15:
        embedded_notes = dict(list(embedded_notes.items())[:15])

    # 3. Construct default name & description if not provided
    qr_name = name or (f"Sensationz - {course_id.title()}" if course_id else "Sensationz Course Enrollment")
    qr_name = str(qr_name).strip()[:40]

    qr_desc = description or (
        f"Enrollment for {course_id.title()} ({duration})"
        if course_id and duration
        else (f"Enrollment for {course_id.title()}" if course_id else "Course Enrollment Fee")
    )
    qr_desc = str(qr_desc).strip()[:255]

    # 4. Build API payload strictly adhering to Razorpay QR Code specifications
    payload: Dict[str, Any] = {
        "type": "upi_qr",
        "usage": "single_use",
        "fixed_amount": True,
        "payment_amount": paise,
        "name": qr_name,
        "description": qr_desc,
        "notes": embedded_notes,
    }

    if close_by_minutes > 0:
        # Razorpay single_use close_by must be between 2 minutes and 45 days
        effective_minutes = max(2, close_by_minutes)
        payload["close_by"] = int(time.time()) + (effective_minutes * 60)

    # 5. Execute API call through Razorpay SDK
    rzp_client = client or get_razorpay_client()
    try:
        response = rzp_client.qrcode.create(payload)
    except Exception as exc:
        logger.error(f"[razorpay_service] Failed to create QR code: {exc}", exc_info=True)
        raise RazorpayServiceError(f"Razorpay QR Code creation failed: {exc}") from exc

    qr_id = response.get("id")
    image_url = response.get("image_url")
    status = response.get("status")

    logger.info(
        f"[razorpay_service] Dynamic QR code created: id={qr_id}, amount_paise={paise}, status={status}"
    )

    return {
        "id": qr_id,
        "qr_id": qr_id,
        "image_url": image_url,
        "status": status,
        "payment_amount": response.get("payment_amount", paise),
        "payment_amount_inr": response.get("payment_amount", paise) / 100.0,
        "name": response.get("name", qr_name),
        "description": response.get("description", qr_desc),
        "close_by": response.get("close_by"),
        "notes": response.get("notes", embedded_notes),
        "raw": response,
    }


async def create_dynamic_qr_code_async(
    amount: Optional[Union[int, float]] = None,
    amount_in_paise: Optional[int] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    notes: Optional[Dict[str, Any]] = None,
    customer_phone: Optional[str] = None,
    course_id: Optional[str] = None,
    duration: Optional[str] = None,
    timing: Optional[str] = None,
    teacher: Optional[str] = None,
    days: Optional[str] = None,
    student_name: Optional[str] = None,
    close_by_minutes: int = 1440,
    client: Optional[razorpay.Client] = None,
) -> Dict[str, Any]:
    """Non-blocking async wrapper to generate dynamic UPI QR code via worker thread pool."""
    return await asyncio.to_thread(
        create_dynamic_qr_code,
        amount=amount,
        amount_in_paise=amount_in_paise,
        name=name,
        description=description,
        notes=notes,
        customer_phone=customer_phone,
        course_id=course_id,
        duration=duration,
        timing=timing,
        teacher=teacher,
        days=days,
        student_name=student_name,
        close_by_minutes=close_by_minutes,
        client=client,
    )


def fetch_qr_code(
    qr_id: str,
    client: Optional[razorpay.Client] = None,
) -> Dict[str, Any]:
    """
    Fetches the details and payment status of an existing Razorpay QR Code.
    """
    if not qr_id:
        raise ValueError("qr_id must be provided.")

    rzp_client = client or get_razorpay_client()
    try:
        return rzp_client.qrcode.fetch(qr_id)
    except Exception as exc:
        logger.error(f"[razorpay_service] Failed to fetch QR code {qr_id}: {exc}")
        raise RazorpayServiceError(f"Failed to fetch Razorpay QR code {qr_id}: {exc}") from exc


async def fetch_qr_code_async(
    qr_id: str,
    client: Optional[razorpay.Client] = None,
) -> Dict[str, Any]:
    """Non-blocking async wrapper to fetch QR code details."""
    return await asyncio.to_thread(fetch_qr_code, qr_id, client=client)


def close_qr_code(
    qr_id: str,
    client: Optional[razorpay.Client] = None,
) -> Dict[str, Any]:
    """
    Closes an active dynamic QR code on demand.
    """
    if not qr_id:
        raise ValueError("qr_id must be provided.")

    rzp_client = client or get_razorpay_client()
    try:
        return rzp_client.qrcode.close(qr_id)
    except Exception as exc:
        logger.error(f"[razorpay_service] Failed to close QR code {qr_id}: {exc}")
        raise RazorpayServiceError(f"Failed to close Razorpay QR code {qr_id}: {exc}") from exc


async def close_qr_code_async(
    qr_id: str,
    client: Optional[razorpay.Client] = None,
) -> Dict[str, Any]:
    """Non-blocking async wrapper to close QR code."""
    return await asyncio.to_thread(close_qr_code, qr_id, client=client)


def verify_webhook_signature(
    webhook_body: Union[str, bytes],
    webhook_signature: str,
    webhook_secret: Optional[str] = None,
    client: Optional[razorpay.Client] = None,
) -> bool:
    """
    Verifies Razorpay webhook signature (X-Razorpay-Signature header)
    against the raw request body string.
    """
    if isinstance(webhook_body, bytes):
        webhook_body = webhook_body.decode("utf-8")

    secret = webhook_secret or os.getenv("RAZORPAY_WEBHOOK_SECRET")
    if not secret:
        raise RazorpayServiceError("RAZORPAY_WEBHOOK_SECRET is not configured.")

    try:
        rzp_client = client or get_razorpay_client()
    except Exception:
        rzp_client = razorpay.Client()

    try:
        rzp_client.utility.verify_webhook_signature(webhook_body, webhook_signature, secret)
        return True
    except Exception as exc:
        logger.warning(f"[razorpay_service] Webhook signature verification failed: {exc}")
        return False


async def translate_text_to_language(text: str, target_language: str) -> str:
    """
    Translates confirmation text to any of 10,000+ languages using the fast LLM.
    Strictly preserves emojis, course names, timing strings, txn IDs, and WhatsApp bolding.
    Falls back safely to base text if translation fails or times out.
    """
    if not target_language or not text:
        return text
    clean_lang = target_language.strip().lower()
    if any(k in clean_lang for k in ["english", "hinglish", "latin"]):
        return text

    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import SystemMessage, HumanMessage

        openai_key = os.getenv("OPENAI_API_KEY")
        model_name = os.getenv("OPENAI_EXTRACTOR_MODEL") or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        llm = ChatOpenAI(model=model_name, api_key=openai_key, temperature=0, timeout=6)

        system_prompt = (
            f"You are a native translator for WhatsApp customer messages. Translate the provided message into {target_language}.\n"
            "STRICT RULES:\n"
            "1. Maintain the exact meaning, polite tone, line breaks, and all emojis.\n"
            "2. Keep course names (e.g. Kathak, Yoga), timing numbers, and transaction IDs (e.g. pay_...) UNTOUCHED.\n"
            "3. Use standard WhatsApp bold formatting (*text*).\n"
            "4. Output ONLY the translated message, nothing else."
        )
        resp = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=text),
        ])
        translated = resp.content.strip()
        if translated:
            return translated
    except Exception as e:
        logger.warning(f"[razorpay_service] Translation to {target_language} failed (falling back to base): {e}")
    return text


def format_onboarding_confirmation(
    course_name: str,
    duration: str,
    timing: Optional[str],
    teacher: Optional[str],
    amount_inr: float,
    payment_id: str,
    language: str = "english",
    student_email: Optional[str] = None,
) -> str:
    """
    Synchronous formatter for onboarding confirmation.
    Delivers a focused, high-conversion message requesting the customer's Gmail ID
    with a clear permanent-linkage warning.
    """
    lang_lower = (language or "english").lower().strip()
    is_hindi = "hindi" in lang_lower and "hinglish" not in lang_lower
    is_hinglish = "hinglish" in lang_lower or "roman" in lang_lower or "latin" in lang_lower
    display_timing = timing or "To be scheduled via Sensationz App"

    # 1. Hindi (Devanagari)
    if is_hindi:
        if student_email:
            return (
                f"🎉 *Payment Received & Enrollment Confirmed!*\n\n"
                f"आपका *{course_name} ({display_timing})* बैच सफलतापूर्वक कन्फर्म हो गया है! (Txn: {payment_id}) 🌟\n\n"
                f"आपका बैच आपकी ईमेल (*{student_email}*) से लिंक हो चुका है। Sensationz ऐप खोलें और **'Continue with Google'** से अपनी क्लास शुरू करें! 🚀✨"
            )
        return (
            f"🎉 *Payment Received & Enrollment Confirmed!*\n\n"
            f"आपका *{course_name} ({display_timing})* बैच सफलतापूर्वक कन्फर्म हो गया है! (Txn: {payment_id}) 🌟\n\n"
            f"अपनी क्लासेस को एक्टिवेट करने के लिए, कृपया अपना **Gmail ID** यहाँ चैट में शेयर करें: 📧👇\n\n"
            f"⚠️ *महत्वपूर्ण:* एक बार शेयर किया गया Gmail बदला या एडिट नहीं किया जा सकेगा और आपकी प्रोफाइल से हमेशा के लिए लिंक हो जाएगा, इसलिए कृपया दोबारा जांच कर ही भेजें।"
        )

    # 2. Hinglish (Latin script)
    if is_hinglish:
        if student_email:
            return (
                f"🎉 *Payment Received & Enrollment Confirmed!*\n\n"
                f"Aapka *{course_name} ({display_timing})* batch successfully confirm ho gaya hai! (Txn: {payment_id}) 🌟\n\n"
                f"Aapka batch aapki email (*{student_email}*) se link ho chuka hai. Sensationz App open karke **'Continue with Google'** se apni classes start karein! 🚀✨"
            )
        return (
            f"🎉 *Payment Received & Enrollment Confirmed!*\n\n"
            f"Aapka *{course_name} ({display_timing})* batch successfully confirm ho gaya hai! (Txn: {payment_id}) 🌟\n\n"
            f"Apni classes ko activate karne ke liye, kripya apna **Gmail ID** yahan chat mein share karein: 📧👇\n\n"
            f"⚠️ *Important:* Ek baar share kiya gaya Gmail edit ya change nahi ho sakta aur aapki profile se permanently link ho jayega, isliye kripya recheck karke hi bhejein."
        )

    # 3. Base English template
    if student_email:
        return (
            f"🎉 *Payment Received & Enrollment Confirmed!*\n\n"
            f"Your *{course_name} ({display_timing})* batch has been successfully confirmed! (Txn: {payment_id}) 🌟\n\n"
            f"Your batch is linked to your email (*{student_email}*). Open the Sensationz App and tap **'Continue with Google'** to access your classes! 🚀✨"
        )
    return (
        f"🎉 *Payment Received & Enrollment Confirmed!*\n\n"
        f"Your *{course_name} ({display_timing})* batch has been successfully confirmed! (Txn: {payment_id}) 🌟\n\n"
        f"To activate your classes, please share your **Gmail ID** right here in this chat: 📧👇\n\n"
        f"⚠️ *Important:* Once shared, this Gmail ID cannot be edited or changed and will be permanently linked to your profile, so please double-check it before sending."
    )


async def format_onboarding_confirmation_async(
    course_name: str,
    duration: str,
    timing: Optional[str],
    teacher: Optional[str],
    amount_inr: float,
    payment_id: str,
    language: str = "english",
    student_email: Optional[str] = None,
) -> str:
    """
    Async formatter supporting 10,000+ native languages dynamically.
    For English, Hinglish, and Hindi: uses zero-latency pre-rendered templates.
    For any other regional or international language: dynamically translates via polyglot LLM.
    """
    base_msg = format_onboarding_confirmation(
        course_name=course_name,
        duration=duration,
        timing=timing,
        teacher=teacher,
        amount_inr=amount_inr,
        payment_id=payment_id,
        language=language,
        student_email=student_email,
    )

    from src.core.kb_loader import format_support_executives_display
    support_contacts = format_support_executives_display()
    lang_lower = (language or "english").lower().strip()
    is_hindi = "hindi" in lang_lower and "hinglish" not in lang_lower
    is_hinglish = "hinglish" in lang_lower or "roman" in lang_lower or "latin" in lang_lower
    if is_hindi:
        support_intro = "Kisi bhi sawaal ke liye aap apni pasand ki language mein hamari support team se baat kar sakte hain:"
    elif is_hinglish:
        support_intro = "Agar aapko koi doubt ho, toh aap apni preferred language mein hamari support team se baat kar sakte hain:"
    else:
        support_intro = "If you have any questions, you can also connect with our support team in your preferred language:"
    if support_contacts:
        base_msg = f"{base_msg}\n\n{support_intro}\n{support_contacts}"

    is_english = "english" in lang_lower and not is_hinglish

    # If already covered by native pre-rendered template, return immediately
    if is_hindi or is_hinglish or is_english:
        return base_msg

    # For 10,000+ other languages (Tamil, Telugu, Bengali, Marathi, Spanish, etc.), translate dynamically
    return await translate_text_to_language(base_msg, language)


async def fulfill_razorpay_payment_async(
    event_payload: Dict[str, Any],
    send_notification: bool = True,
    ledger_source: Optional[str] = "webhook",
) -> Dict[str, Any]:
    """
    `ledger_source` tags the row written to the `payments` ledger ("webhook" / "claim_lookup" /
    "reconciler"); None skips the ledger (developer test endpoint: fake payments never recorded).

    Subtask 6.2: Reconciles verified Razorpay payment webhook events (payment.captured, qr_code.credited).

    1. Extracts payment entity, qr_code entity, amount, and embedded metadata notes.
    2. Identifies customer phone number.
    3. Guarantees idempotency (skips duplicate fulfillment if state is already PAID with same payment_id/order_id).
    4. Updates database state to payment_status='PAID', advances stage to 'ENROLLED', and updates slot metadata.
    5. Dispatches instant WhatsApp confirmation template with onboarding class details and app access links.
    6. Logs lead action and message history.
    """
    event_name = event_payload.get("event", "")
    payload = event_payload.get("payload", {})

    payment_entity = (
        payload.get("payment", {}).get("entity", {})
        if isinstance(payload.get("payment"), dict)
        else (
            payload.get("order", {}).get("entity", {})
            if isinstance(payload.get("order"), dict)
            else (
                payload.get("payment_link", {}).get("entity", {})
                if isinstance(payload.get("payment_link"), dict)
                else {}
            )
        )
    )
    qr_entity = (
        payload.get("qr_code", {}).get("entity", {})
        if isinstance(payload.get("qr_code"), dict)
        else {}
    )

    # Only a CAPTURED payment may make a client PAID (shared guard for webhook + QR lookup).
    payment_state = payment_entity.get("status")
    if payment_state and payment_state != "captured":
        logger.warning(f"[razorpay_service] Ignoring non-captured payment {payment_entity.get('id')} (status={payment_state})")
        return {"status": "ignored", "reason": f"payment_not_captured:{payment_state}", "payment_id": payment_entity.get("id")}

    payment_id = payment_entity.get("id") or qr_entity.get("id") or ""
    amount_paise = payment_entity.get("amount") or qr_entity.get("payment_amount") or 0
    amount_inr = round(float(amount_paise) / 100.0, 2) if amount_paise else 0.0
    payment_method = payment_entity.get("method", "upi")

    payment_notes = payment_entity.get("notes") or {}
    qr_notes = qr_entity.get("notes") or {}
    notes = {**qr_notes, **payment_notes}

    qr_id = qr_entity.get("id") or payment_entity.get("order_id") or notes.get("qr_id") or ""

    raw_phone = str(notes.get("phone") or payment_entity.get("contact") or "").strip()
    if not raw_phone:
        logger.warning(f"[razorpay_service] Missing phone in payment webhook. Payment ID: {payment_id}")
        return {
            "status": "error",
            "reason": "missing_phone",
            "payment_id": payment_id,
        }

    phone = raw_phone
    async with phone_concurrency_lock(phone):
        state = await get_user_state_async(phone)

        # If state appears empty (new session with no course/package), check normalized variations
        if not state.get("course") and not state.get("package") and not state.get("selected_duration"):
            clean_digits = re.sub(r"\D", "", phone)
            candidates = []
            if clean_digits:
                if len(clean_digits) == 10:
                    candidates.extend([f"91{clean_digits}", f"wa_91{clean_digits}", clean_digits])
                elif len(clean_digits) == 12 and clean_digits.startswith("91"):
                    candidates.extend([clean_digits, f"wa_{clean_digits}", clean_digits[2:]])
                elif clean_digits.startswith("91") and len(clean_digits) > 10:
                    candidates.extend([clean_digits, f"wa_{clean_digits}"])
            for cand in candidates:
                if cand != phone:
                    cand_state = await get_user_state_async(cand)
                    if cand_state.get("course") or cand_state.get("package") or cand_state.get("selected_duration"):
                        phone = cand
                        state = cand_state
                        break

        # Idempotency check: prevent duplicate fulfillment or duplicate WhatsApp messaging
        if is_payment_verified(state):
            if state.get("payment_id") == payment_id or (qr_id and state.get("order_id") == qr_id):
                logger.info(
                    f"[razorpay_service] Duplicate webhook for {phone}: already fulfilled with payment_id={state.get('payment_id')}"
                )
                return {
                    "status": "already_fulfilled",
                    "phone": phone,
                    "payment_id": payment_id,
                    "stage": state.get("stage", "ENROLLED"),
                }

        # Filter: Only fulfill transactions generated by or intended for this sales bot
        is_sales_agent_tx = (
            notes.get("source") in ("whatsapp_sales_agent", "sales_agent")
            or notes.get("flow") == "whatsapp_sales_agent"
            or bool(notes.get("course_id"))
            or (qr_id and (state.get("order_id") == qr_id or state.get("last_qr_id") == qr_id))
        )
        if not is_sales_agent_tx:
            logger.info(
                f"[razorpay_service] ℹ️ Ignoring non-sales-agent transaction {payment_id} for phone {phone} (notes: {notes})."
            )
            return {
                "status": "ignored",
                "reason": "unrelated_transaction",
                "payment_id": payment_id,
            }

        course_id = (notes.get("course_id") or state.get("course") or "").lower().strip()
        if not course_id:
            logger.warning(
                f"[razorpay_service] ⚠️ Ignoring payment {payment_id}: No course identified in notes or state."
            )
            return {
                "status": "ignored",
                "reason": "missing_course",
                "payment_id": payment_id,
            }

        duration = notes.get("duration") or state.get("package") or state.get("selected_duration") or "1 Month"
        timing = notes.get("timing") or state.get("timing") or state.get("selected_timing") or "Flexible / To be confirmed"
        teacher = notes.get("teacher") or state.get("selected_teacher") or state.get("teacher") or "Assigned Instructor"

        # Resolve course title from knowledge base catalog
        course_name = course_id.title()
        try:
            catalog = get_catalog_entry(course_id)
            if catalog and catalog.get("name"):
                course_name = catalog["name"]
        except Exception as e:
            logger.debug(f"[razorpay_service] Error reading catalog for {course_id}: {e}")

        # Update database state to PAID and advance stage to ENROLLED
        state["payment_status"] = "PAID"
        state["stage"] = advance_stage(state.get("stage", "NEW"), "ENROLLED")
        state["is_enrolled"] = True
        state["paid_amount"] = amount_inr
        state["payment_id"] = payment_id
        state["payment_method"] = payment_method
        state["payment_timestamp"] = int(time.time())
        if qr_id:
            state["order_id"] = qr_id
        if course_id:
            state["course"] = course_id
        if duration:
            state["package"] = duration
            state["selected_duration"] = duration
        if timing and timing != "Flexible / To be confirmed":
            state["timing"] = timing
            state["selected_timing"] = timing
        if teacher and teacher != "Assigned Instructor":
            state["selected_teacher"] = teacher
            state["teacher"] = teacher

        days = notes.get("days") or state.get("days") or state.get("selected_days")
        days_code = notes.get("days_code") or state.get("days_code")
        if days:
            state["days"] = days
            state["selected_days"] = days
        if days_code:
            state["days_code"] = days_code

        # Extract customer email and auto-stage to backend API if available
        from src.services.hono_staging_client import clean_email, stage_ai_enrollment_async, trigger_async_staging
        raw_email = (
            payment_entity.get("email")
            or notes.get("email")
            or notes.get("customer_email")
            or notes.get("student_email")
            or state.get("student_email")
        )
        customer_email = clean_email(raw_email)

        if customer_email:
            state["student_email"] = customer_email
            stage_payload = {
                "email": customer_email,
                "contact": phone,
                "course": course_name or course_id,
                "timing": timing if timing != "Flexible / To be confirmed" else None,
                "teacher": teacher if teacher != "Assigned Instructor" else None,
                "days": days,
                "days_code": days_code,
                "enrollment_type": "COURSE",
                "package_duration": duration,
                "payment_id": payment_id,
                "amount": amount_inr,
                "currency": "INR",
            }
            # Non-blocking async background dispatch with 3x retry
            trigger_async_staging(stage_payload, phone)
            state["awaiting_email_fulfillment"] = False
            logger.info(
                f"[razorpay_service] ✅ Dispatched non-blocking auto-staging for {phone} ({customer_email})"
            )
        else:
            state["staged_enrollment"] = False
            state["awaiting_email_fulfillment"] = True
            logger.info(
                f"[razorpay_service] ⚠️ No email found in payment {payment_id} for {phone}. Marked awaiting_email_fulfillment=True."
            )

        # Save session state
        state["follow_up_count"] = 0
        state["next_followup_due_at"] = None
        state["next_follow_up_due_at"] = None
        await save_user_state_async(phone, state)

        # Permanent payments ledger: fire-and-forget, never delays the confirmation or the webhook.
        if ledger_source:
            from src.services.payment_ledger import schedule_payment_record
            schedule_payment_record(
                # payment.captured events carry no qr_code entity: pass the qr_id resolved above
                payment_entity, qr_entity or {"id": qr_id}, phone, ledger_source,
                course_id=course_id, package=duration,
                timing=state.get("selected_timing") or state.get("timing"),
                teacher=state.get("selected_teacher") or state.get("teacher"),
                student_email=state.get("student_email"),
            )

        # Format localized onboarding confirmation template
        lang = state.get("language") or "Hinglish"
        confirmation_msg = await format_onboarding_confirmation_async(
            course_name=course_name,
            duration=duration,
            timing=timing,
            teacher=teacher,
            amount_inr=amount_inr,
            payment_id=payment_id,
            language=lang,
            student_email=customer_email if state.get("staged_enrollment") else None,
        )

        # Deliver confirmation message via WhatsApp
        if send_notification:
            try:
                await send_text_message_async(phone, confirmation_msg)
                await save_message_async(phone, "assistant", confirmation_msg)
                await log_message_async(phone, "assistant", confirmation_msg)
            except Exception as exc:
                logger.error(f"[razorpay_service] Failed to send WhatsApp confirmation message to {phone}: {exc}")

        # Log structured console lead action
        try:
            log_lead_action(
                lead_no=phone,
                lead_details=f"{course_name} ({duration}) | ₹{amount_inr:,.2f}",
                action_taken="ENROLLED",
                reason=f"Payment verified via Razorpay webhook ({event_name}: {payment_id})",
            )
        except Exception as exc:
            logger.debug(f"[razorpay_service] Error logging lead action: {exc}")

        return {
            "status": "fulfilled",
            "phone": phone,
            "payment_id": payment_id,
            "course": course_id,
            "duration": duration,
            "amount_inr": amount_inr,
            "stage": state.get("stage"),
            "payment_status": "PAID",
            "staged_enrollment": state.get("staged_enrollment", False),
            "student_email": state.get("student_email"),
            "awaiting_email_fulfillment": state.get("awaiting_email_fulfillment", False),
        }


PAYMENT_LOOKUP_COOLDOWN_SEC = int(os.getenv("PAYMENT_LOOKUP_COOLDOWN_SEC", "30"))


async def _is_payment_lookup_allowed(phone: str) -> bool:
    """At most one Razorpay lookup per client per cooldown, so a client repeating
    "I paid" ten times doesn't trigger ten API calls. Fails open: the lookup is read-only."""
    try:
        from src.services.redis_client import get_async_redis_connection
        redis = get_async_redis_connection()
        return bool(await redis.set(f"payment_lookup:{phone}", "1", nx=True, ex=PAYMENT_LOOKUP_COOLDOWN_SEC))
    except Exception as exc:
        logger.warning(f"[razorpay_service] Payment lookup rate-limit check failed for {phone}: {exc}")
        return True


def _find_captured_qr_payment(
    rzp_client: razorpay.Client, qr_id: str
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """
    Returns (qr_entity, payment_entity) for a payment on THIS QR that is `captured` AND equals
    the QR's fixed amount; otherwise None. Created / authorized / failed payments never count.
    """
    qr_entity = rzp_client.qrcode.fetch(qr_id)
    expected_paise = qr_entity.get("payment_amount")
    if not expected_paise:
        return None
    payments = (rzp_client.qrcode.fetch_all_payments(qr_id) or {}).get("items", [])
    for payment in payments:
        if payment.get("status") == "captured" and payment.get("amount") == expected_paise:
            return qr_entity, payment
    return None


async def reconcile_claimed_payment_async(
    phone: str,
    state: Optional[Dict[str, Any]] = None,
    client: Optional[razorpay.Client] = None,
    send_notification: bool = False,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Run when a client SAYS they paid: asks Razorpay about the QR codes WE sent this client
    and fulfills a payment only if it is captured, for the exact QR amount, on one of those QRs.
    Covers delayed / dropped webhooks. Every QR is single_use and created per client, so a
    payment on it can only be this client's. There is deliberately NO matching by phone
    number (that matched other people's payments). Returns (is_paid, state).
    """
    if state is None:
        state = await get_user_state_async(phone)
    if is_payment_verified(state):
        return True, state

    qr_ids = get_sent_qr_ids(state)
    if not qr_ids:
        return False, state  # we never sent this client a QR: nothing of ours to check
    if not await _is_payment_lookup_allowed(phone):
        logger.info(f"[razorpay_service] Payment lookup for {phone} skipped (cooldown {PAYMENT_LOOKUP_COOLDOWN_SEC}s)")
        return False, state

    try:
        rzp_client = client or get_razorpay_client()
    except Exception as exc:
        logger.warning(f"[razorpay_service] Cannot look up payment, Razorpay client error: {exc}")
        return False, state

    for qr_id in qr_ids:
        try:
            match = await asyncio.to_thread(_find_captured_qr_payment, rzp_client, qr_id)
        except Exception as exc:
            logger.warning(f"[razorpay_service] QR payment lookup failed for {qr_id}: {exc}")
            continue
        if not match:
            continue
        qr_entity, payment_entity = match
        result = await fulfill_razorpay_payment_async(
            {"event": "payment.captured",
             "payload": {"payment": {"entity": payment_entity}, "qr_code": {"entity": qr_entity}}},
            send_notification=send_notification,
            ledger_source="claim_lookup",
        )
        if result.get("status") in ("fulfilled", "already_fulfilled"):
            logger.info(f"[razorpay_service] ✅ Claimed payment verified for {phone} on {qr_id} ({payment_entity.get('id')})")
            return True, await get_user_state_async(phone)
        logger.warning(f"[razorpay_service] Captured payment on {qr_id} not fulfilled for {phone}: {result}")

    return False, state
