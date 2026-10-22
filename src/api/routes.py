"""
routes.py — FastAPI route definitions for WhatsApp Webhook and Admin endpoints.
"""

import os
import time
import json
import asyncio
from fastapi import APIRouter, Request, Response

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
from src.workers.tasks import is_target_ad_or_message, check_target_ad, get_next_agent_email
from src.services.agent_handoff import OWNER_EMAIL, execute_agent_handoff_async
from src.services.rate_limiter import is_rate_limited
from src.core.lead_logger import log_lead_action, extract_lead_details
from src.services.razorpay_service import (
    verify_webhook_signature as verify_razorpay_signature,
    fulfill_razorpay_payment_async,
)

router = APIRouter()

AGENT_TRIGGER_WORDS = ["agent", "human", "talk to someone", "real person", "representative", "support"]


def _extract_message_text(message: dict) -> str:
    """Extract clean text from message dict, handling text, button replies, or interactive objects."""
    if not isinstance(message, dict):
        return str(message or "")

    extracted_text = ""

    # 1. Check direct button_reply or interactive dict
    if "button_reply" in message and isinstance(message["button_reply"], dict):
        extracted_text = message["button_reply"].get("title") or message["button_reply"].get("id") or ""

    if not extracted_text:
        interactive = message.get("interactive")
        if isinstance(interactive, dict):
            btn = interactive.get("button_reply") or interactive.get("list_reply")
            if isinstance(btn, dict):
                extracted_text = btn.get("title") or btn.get("id") or ""

    if not extracted_text:
        raw = message.get("message", "")
        if isinstance(raw, dict):
            btn = raw.get("button_reply") or raw.get("interactive", {}).get("button_reply")
            if isinstance(btn, dict):
                extracted_text = btn.get("title") or btn.get("id") or ""
            else:
                extracted_text = raw.get("title") or raw.get("text") or ""
        elif isinstance(raw, str):
            trimmed = raw.strip()
            if trimmed.startswith("{") and trimmed.endswith("}"):
                try:
                    parsed = json.loads(trimmed)
                    if isinstance(parsed, dict):
                        btn = parsed.get("button_reply") or parsed.get("interactive", {}).get("button_reply")
                        if isinstance(btn, dict):
                            extracted_text = btn.get("title") or btn.get("id") or trimmed
                except Exception:
                    pass
            if not extracted_text:
                extracted_text = raw

    if not extracted_text:
        extracted_text = str(message.get("text") or message.get("body") or "")

    extracted_text = (extracted_text or "").strip()

    # Capture Quoted / Replied message context (when user swipes right / drags to reply)
    quoted_text = ""
    ctx = message.get("context") if isinstance(message, dict) else None
    if isinstance(ctx, dict):
        q_msg = ctx.get("quoted_message")
        if isinstance(q_msg, dict):
            quoted_text = q_msg.get("body") or q_msg.get("text") or ""
        elif isinstance(q_msg, str):
            quoted_text = q_msg
        if not quoted_text:
            quoted_text = ctx.get("quoted_message_body") or ctx.get("body") or ctx.get("text") or ""
    elif isinstance(message, dict) and "quoted_message" in message:
        q_msg = message.get("quoted_message")
        if isinstance(q_msg, dict):
            quoted_text = q_msg.get("body") or q_msg.get("text") or ""
        elif isinstance(q_msg, str):
            quoted_text = q_msg

    if quoted_text and quoted_text.strip():
        return f"[Replying to: \"{quoted_text.strip()}\"]\n{extracted_text}"

    return extracted_text


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

    signature = request.headers.get("Interakt-Signature", "")
    if not verify_webhook_signature(raw_body, signature):
        print(f"[api]  Signature verification FAILED. Signature header: '{signature[:20]}...'")
        log_lead_action(
            lead_no="Unknown",
            lead_details="N/A",
            action_taken="IGNORED",
            reason="Invalid Interakt-Signature header",
        )
        return {"status": "invalid signature"}

    if event_type == "Webhook Test":
        print("[api]  Interakt 'Webhook Test' event verified successfully!")
        return {"status": "ok", "message": "Webhook Test received"}

    if event_type != "message_received":
        print(f"[api] Webhook event '{event_type}' ignored (non-lead event)")
        return {"status": "ignored"}

    customer = data.get("data", {}).get("customer", {})
    message = data.get("data", {}).get("message", {})
    phone = extract_phone(customer)
    text = _extract_message_text(message)
    referral = message.get("referral", {}) or {}

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

    limited, rl_info = await is_rate_limited(phone)
    if limited:
        print(f"[api] ⚠️ Rate limit exceeded for {phone}: {rl_info.get('count')}/{rl_info.get('limit')} msgs in {rl_info.get('window')}s. Dropping message.")
        log_lead_action(
            lead_no=phone,
            lead_details="Rate Limited",
            action_taken="RATE_LIMITED",
            reason=f"Exceeded {rl_info.get('limit')} msgs in {rl_info.get('window')}s",
        )
        return {"status": "ok", "message": "rate_limited"}

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
    text = _extract_message_text(message)
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

    # ── Independent Rate Limiter ──────────────────────────────────────────
    limited, rl_info = await is_rate_limited(phone)
    if limited:
        print(f"[api] ⚠️ Rate limit exceeded for {phone} (test-webhook). Dropping message.")
        return {"status": "ok", "message": "rate_limited"}

    await log_message_async(phone, "user", text)

    try:
        await add_message_to_batch_async(phone, text, referral=referral)
        return {"status": "ok", "message": "enqueued"}
    except Exception as e:
        print(f"[api] Failed to enqueue test message, running fallback: {e}")
        return await _process_fallback(phone, text, referral, time.time())


# ─────────────────────────────────────────────────────────────────────────────
# Razorpay Webhook & Payment Reconciliation Endpoints (Module 6)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/api/razorpay/webhook")
@router.post("/razorpay/webhook")
async def receive_razorpay_webhook(request: Request):
    """
    Subtask 6.1: Real-time Razorpay Webhook listener.
    Verifies X-Razorpay-Signature header against the raw body bytes.
    Listens for 'payment.captured' and 'qr_code.credited' events.
    Fulfills payment, updates state to PAID/ENROLLED, and sends WhatsApp confirmation.
    """
    raw_body_bytes = await request.body()
    try:
        raw_body_str = raw_body_bytes.decode("utf-8")
    except Exception:
        raw_body_str = ""

    signature = (
        request.headers.get("X-Razorpay-Signature")
        or request.headers.get("x-razorpay-signature")
        or ""
    )

    if not signature or not verify_razorpay_signature(raw_body_str, signature):
        print(f"[razorpay_webhook] ❌ Razorpay webhook signature verification FAILED. Signature: '{signature[:20]}...'")
        log_lead_action(
            lead_no="Unknown",
            lead_details="Razorpay Webhook",
            action_taken="IGNORED",
            reason="Invalid X-Razorpay-Signature header",
        )
        return Response(
            content=json.dumps({"status": "invalid_signature", "message": "Signature verification failed"}),
            status_code=400,
            media_type="application/json",
        )

    try:
        event_payload = json.loads(raw_body_str)
    except Exception as exc:
        print(f"[razorpay_webhook] ❌ Failed to parse JSON body: {exc}")
        return Response(
            content=json.dumps({"status": "invalid_json"}),
            status_code=400,
            media_type="application/json",
        )

    event_type = event_payload.get("event", "unknown")
    if event_type not in ["payment.captured", "qr_code.credited"]:
        print(f"[razorpay_webhook] ℹ️ Event '{event_type}' ignored (not payment.captured or qr_code.credited)")
        return {"status": "ignored", "event": event_type}

    print(f"[razorpay_webhook] ✅ Verified payment event received: '{event_type}'")
    try:
        result = await fulfill_razorpay_payment_async(event_payload)
        return {"status": "ok", "event": event_type, "result": result}
    except Exception as exc:
        print(f"[razorpay_webhook] ❌ Payment fulfillment failed for '{event_type}': {exc}")
        return Response(
            content=json.dumps({"status": "error", "message": str(exc)}),
            status_code=500,
            media_type="application/json",
        )


@router.post("/test-razorpay-webhook")
async def receive_test_razorpay_webhook(request: Request):
    """Developer test endpoint for Razorpay payment simulation without signature verification."""
    try:
        event_payload = await request.json()
    except Exception:
        return Response(
            content=json.dumps({"status": "invalid_json"}),
            status_code=400,
            media_type="application/json",
        )

    event_type = event_payload.get("event", "unknown")
    if event_type not in ["payment.captured", "qr_code.credited"]:
        return {"status": "ignored", "event": event_type}

    result = await fulfill_razorpay_payment_async(event_payload)
    return {"status": "ok", "event": event_type, "result": result}


# ─────────────────────────────────────────────────────────────────────────────
# Meta WhatsApp Cloud API — Test Webhook Endpoints
# ─────────────────────────────────────────────────────────────────────────────

def _extract_whatsapp_cloud_messages(data: dict) -> list[dict]:
    """Extracts phone, text, and referral from Meta WhatsApp Cloud API or raw JSON."""
    items = []
    if not isinstance(data, dict):
        return items

    # 1. Official Meta Cloud API payload (object: 'whatsapp_business_account')
    if "entry" in data:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                val = change.get("value", {})
                if not isinstance(val, dict):
                    continue
                contacts = {
                    c.get("wa_id"): c.get("profile", {}).get("name", "")
                    for c in val.get("contacts", [])
                    if isinstance(c, dict)
                }
                for msg in val.get("messages", []):
                    if not isinstance(msg, dict):
                        continue
                    sender = msg.get("from")
                    if not sender:
                        continue
                    mtype = msg.get("type", "text")
                    text = ""
                    if mtype == "text":
                        text = msg.get("text", {}).get("body", "")
                    elif mtype == "interactive":
                        interactive = msg.get("interactive", {})
                        text = (
                            interactive.get("button_reply", {}).get("title")
                            or interactive.get("list_reply", {}).get("title")
                            or interactive.get("button_reply", {}).get("id")
                            or ""
                        )
                    elif mtype == "button":
                        text = msg.get("button", {}).get("text", "")

                    if text:
                        referral = msg.get("referral", {}) or {}
                        items.append({
                            "phone": str(sender),
                            "text": text.strip(),
                            "referral": referral,
                            "name": contacts.get(sender, ""),
                        })
        return items

    # 2. Simplified raw JSON for developer testing (curl / Postman)
    phone = data.get("phone") or data.get("from") or data.get("wa_id") or data.get("sender")
    text = data.get("text") or data.get("message") or data.get("body")
    if phone and text:
        items.append({
            "phone": str(phone),
            "text": str(text).strip(),
            "referral": data.get("referral") or {},
            "name": data.get("name") or "Tester",
        })

    return items


def _is_whatsapp_cloud_status_event(data: dict) -> bool:
    """Checks if payload is a delivery/read receipt from WhatsApp."""
    if isinstance(data, dict) and "entry" in data:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                val = change.get("value", {})
                if isinstance(val, dict) and "statuses" in val and not val.get("messages"):
                    return True
    return False


@router.get("/test-whatsapp-webhook")
async def verify_whatsapp_test_webhook(request: Request):
    """Handles Meta WhatsApp Cloud API webhook verification challenge."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    expected_tokens = [
        os.getenv("META_VERIFY_TOKEN", "").strip(),
        os.getenv("VERIFY_TOKEN", "").strip(),
    ]
    expected_tokens = [t for t in expected_tokens if t]

    if mode == "subscribe" and token and (token in expected_tokens or not expected_tokens):
        print(f"[whatsapp_cloud] ✅ Webhook verified successfully by Meta!")
        return Response(content=challenge, media_type="text/plain")

    print(f"[whatsapp_cloud] ❌ Verification failed. Token received: '{token}'")
    return Response(content="Forbidden", status_code=403)


@router.post("/test-whatsapp-webhook")
async def receive_whatsapp_test_webhook(request: Request):
    """
    Receives and processes incoming WhatsApp Cloud API webhooks.
    Supports:
    1. Official Meta Cloud API webhook payloads (object: 'whatsapp_business_account')
    2. Status updates (sent/delivered/read) acknowledged with 200 OK
    3. Simplified raw JSON for direct API / Postman / cURL testing
    """
    try:
        data = await request.json()
    except Exception:
        return {"status": "invalid_json"}

    extracted_items = _extract_whatsapp_cloud_messages(data)
    if not extracted_items:
        if _is_whatsapp_cloud_status_event(data):
            return {"status": "ok", "message": "status_acknowledged"}
        print("[whatsapp_cloud] ℹ️ Webhook event ignored (no message text found)")
        return {"status": "ignored", "message": "no_messages_found"}

    results = []
    for item in extracted_items:
        raw_phone = item["phone"]
        text = item["text"]
        referral = item.get("referral") or {}
        name = item.get("name") or ""

        clean_num = str(raw_phone).replace("+", "").strip()
        user_key = f"wa_{clean_num}"

        limited, rl_info = await is_rate_limited(user_key)
        if limited:
            print(f"[whatsapp_cloud] ⚠️ Rate limit exceeded for {user_key}. Dropping.")
            results.append({"phone": user_key, "status": "rate_limited"})
            continue

        await log_message_async(user_key, "user", text)

        try:
            await add_message_to_batch_async(phone=user_key, text=text, referral=referral)
            results.append({"phone": user_key, "status": "enqueued"})
        except Exception as e:
            print(f"[whatsapp_cloud] Failed to enqueue message, running fallback: {e}")
            fb_res = await _process_fallback(user_key, text, referral, time.time())
            results.append({"phone": user_key, "status": "fallback", "result": fb_res})

    return {"status": "ok", "processed": results}


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

    cmd = (text or "").strip().lower()
    if cmd in ["#summary", "/summary", "#info", "#client", "summary"]:
        from src.core.helpers.client_summary import get_client_summary_async
        summary_text = await get_client_summary_async(phone)
        await send_text_message_async(phone, summary_text)
        log_lead_action(
            lead_no=phone,
            lead_details=lead_details,
            action_taken="REPLIED",
            reason="Summary command executed",
        )
        return {"status": "summary", "reply": summary_text}

    if await is_escalated_async(phone):
        log_lead_action(
            lead_no=phone,
            lead_details=lead_details,
            action_taken="IGNORED",
            reason="Already escalated to human agent (AI staying out)",
        )
        return {"status": "escalated"}

    if not state.get("already_assigned"):
        is_interakt = not (phone.startswith("wa_") or phone.startswith("ig_") or phone.startswith("fb_"))
        if OWNER_EMAIL and is_interakt:
            success = await assign_chat_to_agent_async(phone, OWNER_EMAIL)
            if success:
                state["already_assigned"] = True
                state["assigned_agent_email"] = OWNER_EMAIL
                await save_user_state_async(phone, state)
        else:
            state["already_assigned"] = True
            await save_user_state_async(phone, state)

    text_lower = text.lower()
    if matches_any(text_lower, AGENT_TRIGGER_WORDS):
        reply = "Connecting you with our team now. Someone will be with you shortly!"
        await execute_agent_handoff_async(phone, user_reply_text=reply, escalation_reason="Customer requested human agent (Handed off to support team)")
        log_lead_action(
            lead_no=phone,
            lead_details=lead_details,
            action_taken="REPLIED",
            reason="Customer requested human agent (Handed off to support team)",
        )
        return {"status": "handed off"}

    try:
        history = await get_recent_history_async(phone)
    except Exception:
        history = []

    from src.workers.tasks import handle_ai_reply_async
    await handle_ai_reply_async(phone, text, history, start_time, referral=referral)
    log_lead_action(
        lead_no=phone,
        lead_details=lead_details,
        action_taken="REPLIED",
        reason=f"{reason} — AI response sent",
    )
    return {"status": "ok"}

