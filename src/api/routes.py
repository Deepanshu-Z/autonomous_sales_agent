"""
routes.py — FastAPI route definitions for WhatsApp Webhook and Admin endpoints.
"""

import time
import asyncio
from fastapi import APIRouter, Request

from src.api.deps import extract_phone
from src.services.interakt import (
    send_text_message_async,
    assign_chat_to_agent_async,
    verify_webhook_signature,
)
from src.state.csv_logger import log_message_async
from src.state.chat_history import (
    save_message_async,
    get_recent_history_async,
    get_full_history_for_agent_async,
)
from src.core.rag import ask_rag_async
from src.state.chat_state import (
    is_escalated_async,
    mark_escalated_async,
    get_user_state_async,
    save_user_state_async,
    extract_and_update_slots,
    matches_any,
    advance_stage,
)
from src.workers.batching import add_message_to_batch_async
from src.workers.tasks import is_target_ad_or_message, check_target_ad, get_next_agent_email, is_welcome_trigger
from src.core.lead_logger import log_lead_action, extract_lead_details
from src.core.conversation_handler import handle_conversation_turn_async

router = APIRouter()

AGENT_TRIGGER_WORDS = ["agent", "human", "talk to someone", "real person", "representative", "support"]


@router.get("/health")
async def health_check():
    return {"status": "healthy", "service": "sensationz-whatsapp-ai"}


@router.get("/chat-history/{phone}")
async def view_chat_history(phone: str):
    return await get_full_history_for_agent_async(phone)


@router.post("/webhook")
async def receive_interakt_webhook(request: Request):
    raw_body = await request.body()
    try:
        data = await request.json()
    except Exception:
        data = {}

    event_type = data.get("type", "unknown")
    print(f"\n[api] 🔔 WEBHOOK HIT RECEIVED: event_type='{event_type}'")

    signature = request.headers.get("Interakt-Signature", "")
    if not verify_webhook_signature(raw_body, signature):
        print(f"[api] ❌ Signature verification FAILED. Signature header: '{signature[:20]}...'")
        log_lead_action(
            lead_no="Unknown",
            lead_details="N/A",
            action_taken="IGNORED",
            reason="Invalid Interakt-Signature header",
        )
        return {"status": "invalid signature"}

    # Special handling for Interakt dashboard "Test Webhook" button
    if event_type == "Webhook Test":
        print("[api] ✅ Interakt 'Webhook Test' event verified successfully!")
        return {"status": "ok", "message": "Webhook Test received"}

    if event_type != "message_received":
        print(f"[api] Webhook event '{event_type}' ignored (non-lead event)")
        return {"status": "ignored"}

    customer = data.get("data", {}).get("customer", {})
    message = data.get("data", {}).get("message", {})
    phone = extract_phone(customer)
    text = message.get("message", "")
    referral = message.get("referral", {}) or {}

    # Enrich referral with ad_tag from customer traits/tags/headline if missing
    if isinstance(referral, dict):
        traits = customer.get("traits", {}) if isinstance(customer, dict) else {}
        ad_tag = (
            referral.get("ad_tag")
            or referral.get("tag")
            or traits.get("ad_tag")
            or traits.get("tag")
            or (customer.get("tags")[0] if isinstance(customer.get("tags"), list) and customer.get("tags") else None)
            or referral.get("headline")
        )
        if ad_tag and "ad_tag" not in referral:
            referral["ad_tag"] = ad_tag

    await log_message_async(phone, "user", text)

    try:
        await add_message_to_batch_async(phone, text, referral=referral)
        return {"status": "ok", "message": "enqueued"}
    except Exception as e:
        print(f"[api] Failed to enqueue message, running fallback: {e}")
        return await _process_fallback(phone, text, referral, time.time())


@router.post("/test-webhook")
async def receive_test_webhook(request: Request):
    """Test endpoint — skips signature verification."""
    data = await request.json()
    if data.get("type") != "message_received":
        print(f"[api] Webhook event '{data.get('type')}' ignored (non-lead event)")
        return {"status": "ignored"}

    customer = data.get("data", {}).get("customer", {})
    message = data.get("data", {}).get("message", {})
    phone = extract_phone(customer)
    text = message.get("message", "")
    referral = message.get("referral", {}) or {}

    # Enrich referral with ad_tag from customer traits/tags/headline if missing
    if isinstance(referral, dict):
        traits = customer.get("traits", {}) if isinstance(customer, dict) else {}
        ad_tag = (
            referral.get("ad_tag")
            or referral.get("tag")
            or traits.get("ad_tag")
            or traits.get("tag")
            or (customer.get("tags")[0] if isinstance(customer.get("tags"), list) and customer.get("tags") else None)
            or referral.get("headline")
        )
        if ad_tag and "ad_tag" not in referral:
            referral["ad_tag"] = ad_tag

    await log_message_async(phone, "user", text)

    try:
        await add_message_to_batch_async(phone, text, referral=referral)
        return {"status": "ok", "message": "enqueued"}
    except Exception as e:
        print(f"[api] Failed to enqueue test message, running fallback: {e}")
        return await _process_fallback(phone, text, referral, time.time())


async def _process_fallback(phone: str, text: str, referral: dict, start_time: float):
    """Fallback processor when Redis or batching is unreachable."""
    is_target, reason = check_target_ad(text, referral, phone)
    lead_details = extract_lead_details(referral, phone=phone)

    if not is_target:
        log_lead_action(
            lead_no=phone,
            lead_details=lead_details,
            action_taken="IGNORED",
            reason=reason,
        )
        return {"status": "ignored, not matching target ad"}

    try:
        state = await get_user_state_async(phone)
        if not state.get("is_target_ad"):
            state["is_target_ad"] = True
        if referral and isinstance(referral, dict):
            ad_id = referral.get("ad_id") or referral.get("source_id")
            if ad_id:
                state["ad_id"] = str(ad_id)
            ad_tag = referral.get("ad_tag") or referral.get("tag") or referral.get("headline")
            if ad_tag:
                state["ad_tag"] = str(ad_tag)
        await save_user_state_async(phone, state)
    except Exception:
        state = {}

    lead_details = extract_lead_details(referral, state=state, phone=phone)

    try:
        await save_message_async(phone, "user", text)
    except Exception:
        pass

    if await is_escalated_async(phone):
        log_lead_action(
            lead_no=phone,
            lead_details=lead_details,
            action_taken="IGNORED",
            reason="Already escalated to human agent (AI staying out)",
        )
        return {"status": "escalated"}

    agent, _agent_index = get_next_agent_email()
    if agent:
        await assign_chat_to_agent_async(phone, agent)

    text_lower = text.lower()
    if matches_any(text_lower, AGENT_TRIGGER_WORDS):
        reply = "Connecting you with our team now. Someone will be with you shortly!"
        await send_text_message_async(phone, reply)
        await mark_escalated_async(phone)
        await save_message_async(phone, "assistant", reply)
        log_lead_action(
            lead_no=phone,
            lead_details=lead_details,
            action_taken="REPLIED",
            reason="Customer requested human agent (Handed off to support team)",
        )
        return {"status": "handed off"}

    if is_welcome_trigger(text):
        from src.core import kb_loader
        detected_c = kb_loader.detect_course_from_text(text, default_course="yoga")
        active_course = detected_c or "yoga"
        course_data = kb_loader.get_course_data(active_course)
        course_name = course_data.get("course", active_course.title())
        pkgs = course_data.get("pricing", {}).get("packages", [])
        starter_pkg = pkgs[0] if pkgs else {}
        base_p = starter_pkg.get("base_price", 700)
        offer_p = starter_pkg.get("offer_price", 300)
        dur = starter_pkg.get("duration", "1 Month")

        msg1 = "Welcome to Sensationz! 🙏 We're excited to help you start your journey."
        msg2 = f"We offer Online Live Interactive {course_name} classes (Monday to Friday) with certified expert instructors, beginner-friendly packages starting at just Rs. {base_p:,}/{dur.lower()} (offer price: Rs. {offer_p:,})."
        msg3 = "We have batches running throughout the day. Which time slot works best for your schedule?"

        await send_text_message_async(phone, msg1)
        await asyncio.sleep(1)
        await send_text_message_async(phone, msg2)
        await asyncio.sleep(1)
        await send_text_message_async(phone, msg3)
        full_welcome = f"{msg1}\n{msg2}\n{msg3}"
        await save_message_async(phone, "assistant", full_welcome)
        log_lead_action(
            lead_no=phone,
            lead_details=lead_details,
            action_taken="REPLIED",
            reason=f"{reason} — Welcome offer sent",
        )
        return {"status": "offer sent"}

    try:
        history = await get_recent_history_async(phone)
    except Exception:
        history = []

    turn_res = await handle_conversation_turn_async(phone, text, history=history, start_time=start_time)
    log_lead_action(
        lead_no=phone,
        lead_details=lead_details,
        action_taken="REPLIED",
        reason=f"{reason} — AI turn completed ({turn_res.get('status')})",
    )
    return {"status": "ok"}
