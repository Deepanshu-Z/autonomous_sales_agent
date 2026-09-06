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
from src.workers.tasks import is_target_ad_or_message, get_next_agent_email, is_welcome_trigger

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
    data = await request.json()

    signature = request.headers.get("Interakt-Signature", "")
    if not verify_webhook_signature(raw_body, signature):
        print("[api] Signature verification FAILED")
        return {"status": "invalid signature"}

    event_type = data.get("type")
    if event_type != "message_received":
        return {"status": "ignored"}

    customer = data["data"]["customer"]
    message = data["data"]["message"]
    phone = extract_phone(customer)
    text = message.get("message", "")
    referral = message.get("referral", {})

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
        return {"status": "ignored"}

    customer = data.get("data", {}).get("customer", {})
    message = data.get("data", {}).get("message", {})
    phone = extract_phone(customer)
    text = message.get("message", "")
    referral = message.get("referral", {})

    await log_message_async(phone, "user", text)

    try:
        await add_message_to_batch_async(phone, text, referral=referral)
        return {"status": "ok", "message": "enqueued"}
    except Exception as e:
        print(f"[api] Failed to enqueue test message, running fallback: {e}")
        return await _process_fallback(phone, text, referral, time.time())


async def _process_fallback(phone: str, text: str, referral: dict, start_time: float):
    """Fallback processor when Redis or batching is unreachable."""
    is_target = is_target_ad_or_message(text, referral, phone)
    if not is_target:
        return {"status": "ignored, not matching target ad"}

    try:
        state = await get_user_state_async(phone)
        if not state.get("is_target_ad"):
            state["is_target_ad"] = True
            await save_user_state_async(phone, state)
    except Exception:
        pass

    try:
        await save_message_async(phone, "user", text)
    except Exception:
        pass

    if await is_escalated_async(phone):
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
        return {"status": "handed off"}

    if is_welcome_trigger(text):
        msg1 = "Welcome to Sensationz! 🙏 We're excited to help you start your wellness journey."
        msg2 = "We offer Online Live Interactive Yoga classes (Monday to Friday) with certified expert instructors, beginner-friendly packages starting at just Rs. 700/month (offer price: Rs. 300)."
        msg3 = "We have batches running throughout the day (Morning, Afternoon, and Evening). Which time slot works best for your schedule?"

        await send_text_message_async(phone, msg1)
        await asyncio.sleep(1)
        await send_text_message_async(phone, msg2)
        await asyncio.sleep(1)
        await send_text_message_async(phone, msg3)
        full_welcome = f"{msg1}\n{msg2}\n{msg3}"
        await save_message_async(phone, "assistant", full_welcome)
        return {"status": "offer sent"}

    try:
        history = await get_recent_history_async(phone)
    except Exception:
        history = []

    state = await extract_and_update_slots(phone, text)
    result = await ask_rag_async(text, chat_history=history, state=state)
    reply_text = result["reply"] if isinstance(result, dict) else str(result)

    if state.get("stage") == "READY_FOR_APP_LINK":
        state["stage"] = advance_stage(state["stage"], "APP_LINK_SENT")
        await save_user_state_async(phone, state)
    elif state.get("stage") == "PROFILE_COMPLETED" and not state.get("coupon_sent"):
        state["coupon_sent"] = True
        state["stage"] = advance_stage(state["stage"], "COUPON_SENT")
        await save_user_state_async(phone, state)

    await send_text_message_async(phone, reply_text)
    latency_sec = round(time.time() - start_time, 2)
    await save_message_async(phone, "assistant", reply_text, response_time_sec=latency_sec)
    await log_message_async(phone, "ai", reply_text)
    return {"status": "ok"}
