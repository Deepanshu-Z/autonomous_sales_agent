"""
meta_routes.py — FastAPI Webhook routes for Meta (Instagram & Facebook) comments and DMs.
Zero hardcoding: all endpoints, filtering, templates, and delay jitter loaded via meta_loader.
"""

import os
import asyncio
from fastapi import APIRouter, Request, Response, BackgroundTasks
from src.core.meta_loader import (
    get_post_config,
    get_random_public_reply,
    get_random_delay,
    format_for_meta,
    generate_crisp_comment_reply_async,
    generate_ai_private_dm_async,
    evaluate_and_generate_comment_actions_async,
)
from src.services.meta_client import (
    reply_to_comment_async,
    send_private_reply_async,
)
from src.workers.tasks import is_complaint_or_refund
from src.workers.batching import add_message_to_batch_async
from src.state.chat_state import get_user_state_async, save_user_state_async, arm_followup_timer
from src.state.chat_history import save_message_async

router = APIRouter(prefix="/meta", tags=["Meta Automation"])


@router.get("/webhook")
async def verify_meta_webhook(request: Request):
    """Handles Meta webhook verification challenge."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    expected_token = os.getenv("META_VERIFY_TOKEN", "").strip()
    if mode == "subscribe" and token and expected_token and token == expected_token:
        print("[meta_routes] ✅ Webhook verified successfully by Meta!")
        return Response(content=challenge, media_type="text/plain")

    print(f"[meta_routes] ❌ Verification failed. Token received: '{token}'")
    return Response(content="Forbidden", status_code=403)


async def _handle_comment_async(
    post_cfg: dict,
    comment_id: str,
    sender_id: str,
    username: str,
    post_id: str,
    comment_text: str = "",
    channel: str = "instagram",
):
    """
    Background execution for comment:
    1. Delay jitter to mimic human timing and avoid spam flags
    2. AI Intent & Safety Gate: evaluates comment (inquiry vs troll/complaint/spam/friend tag)
    3. If unsafe/negative/spam: suppresses auto-reply completely to protect brand
    4. If safe: posts crisp public reply + sends tailored AI Private DM
    5. Saves user comment & AI private DM to chat history so the DM thread has context
    6. Registers user state with is_target_ad=True, welcome_sent=True, and arms follow-up timer
    """
    delay = get_random_delay()
    await asyncio.sleep(delay)

    user_key = f"fb_{sender_id}" if channel == "facebook" else f"ig_{sender_id}"

    # 1. AI Intent & Safety Evaluation
    actions = await evaluate_and_generate_comment_actions_async(comment_text, username, post_cfg)
    if not actions.get("should_reply"):
        reason = actions.get("reason") or actions.get("intent") or "Safety guard"
        print(f"[meta_routes] 🛡️ Comment guardrail: auto-reply suppressed for {comment_id} (@{username}) | Intent: {actions.get('intent')} | Reason: {reason}")
        return

    public_msg = actions.get("public_reply")
    private_dm = actions.get("private_dm")

    # 2. Public comment reply (1 sentence answering query + DM CTA)
    if public_msg:
        try:
            res_pub = await reply_to_comment_async(comment_id, public_msg, channel=channel)
            if isinstance(res_pub, dict) and "error" in res_pub:
                print(f"[meta_routes] ⚠️ Public reply API error on {comment_id}: {res_pub['error']}")
            else:
                print(f"[meta_routes] 💬 Replied publicly to {channel} comment {comment_id} (@{username}) after {delay:.1f}s: '{public_msg}'")
        except Exception as e:
            print(f"[meta_routes] Error posting public comment reply for {comment_id}: {e}")

    # 3. AI Private DM reply (initiates the DM thread)
    if private_dm:
        clean_dm = format_for_meta(private_dm)
        try:
            res_dm = await send_private_reply_async(comment_id, clean_dm, channel=channel)
            if isinstance(res_dm, dict) and "error" in res_dm:
                print(f"[meta_routes] ⚠️ Private DM API error on {comment_id}: {res_dm['error']}")
            else:
                print(f"[meta_routes] 📩 Sent AI private DM for comment {comment_id} to {user_key}")
        except Exception as e:
            print(f"[meta_routes] Error sending private DM for comment {comment_id}: {e}")

    # 4. Save initial interaction to chat history so upcoming DM messages have context
    try:
        if comment_text:
            await save_message_async(user_key, "user", f"[Comment on post]: {comment_text}")
        if private_dm:
            await save_message_async(user_key, "assistant", private_dm)
    except Exception as e:
        print(f"[meta_routes] Error saving comment history for {user_key}: {e}")

    # 4. Mark user session as active target lead with welcome_sent=True
    try:
        state = await get_user_state_async(user_key)
        state.update({
            "is_target_ad": True,
            "welcome_sent": True,
            "source_post_id": post_id,
            "course": post_cfg.get("course"),
            "channel": channel,
            "username": username,
        })
        arm_followup_timer(state, topic="course intro")
        await save_user_state_async(user_key, state)
    except Exception as e:
        print(f"[meta_routes] Error setting state for {user_key}: {e}")


@router.post("/webhook")
async def receive_meta_webhook(request: Request, background_tasks: BackgroundTasks):
    """Processes incoming Meta webhook events (Instagram/Facebook comments and DMs)."""
    try:
        body = await request.json()
    except Exception:
        return {"status": "invalid json"}

    page_id = os.getenv("META_PAGE_ID", "").strip()
    obj_type = str(body.get("object") or "").lower()

    for entry in body.get("entry", []):
        entry_id = str(entry.get("id") or "")

        # ── 1. Process Comments (Instagram 'comments' & Facebook 'feed') ─────
        for change in entry.get("changes", []):
            field = change.get("field")
            if field in ("comments", "feed"):
                val = change.get("value", {})
                verb = str(val.get("verb") or "add").lower()
                item = str(val.get("item") or "comment").lower()

                # Ignore non-comment feed changes (photo, status, tag) or deletions
                if verb in ("remove", "deleted", "hide") or item not in ("comment",):
                    continue

                channel = "facebook" if (obj_type == "page" or field == "feed") else "instagram"
                comment_id = str(val.get("id") or val.get("comment_id") or "")
                post_id = str(val.get("media", {}).get("id") or val.get("post_id") or val.get("parent_id") or "")
                sender_id = str(val.get("from", {}).get("id") or val.get("sender_id") or "")
                username = str(val.get("from", {}).get("username") or val.get("from", {}).get("name") or "")
                text = str(val.get("text") or val.get("message") or "").strip()

                print(f"[meta_routes] 💬 Incoming comment from @{username} (ID: {sender_id}) on post {post_id}: '{text}'")

                # Anti-loop guard: ignore comments from our own page/account
                if (page_id and sender_id == page_id) or (entry_id and sender_id == entry_id):
                    print(f"[meta_routes] ℹ️ Ignored comment from our own account ({sender_id})")
                    continue

                if not comment_id:
                    continue

                # Match against registered target posts (or wildcard)
                post_cfg = get_post_config(post_id)
                if not post_cfg:
                    print(f"[meta_routes] ⚠️ Post {post_id} not registered in meta_campaigns.json — comment ignored")
                    continue

                # Sentiment guard: do not auto-reply to complaints or refunds
                if is_complaint_or_refund(text):
                    print(f"[meta_routes] ⚠️ Negative/complaint comment on {post_id} ignored: '{text}'")
                    continue

                # Optional keyword filter (if list is non-empty)
                keywords = post_cfg.get("trigger_keywords", [])
                if keywords and not any(k.lower() in text.lower() for k in keywords):
                    print(f"[meta_routes] ℹ️ Comment did not match trigger keywords: '{keywords}'")
                    continue

                # Run background task so webhook responds immediately with 200
                background_tasks.add_task(
                    _handle_comment_async,
                    post_cfg,
                    comment_id,
                    sender_id,
                    username,
                    post_id,
                    text,
                    channel,
                )

        # ── 2. Process Direct Messages (DMs & Standby) ───────────────────────
        messaging_events = entry.get("messaging", []) + entry.get("standby", [])
        for msg_event in messaging_events:
            sender_id = str(msg_event.get("sender", {}).get("id") or "")
            if (page_id and sender_id == page_id) or (entry_id and sender_id == entry_id):
                continue  # Outbound message guard

            msg = msg_event.get("message", {})
            if msg.get("is_echo"):
                continue  # Skip echo message of our own outbound reply

            text = msg.get("text") or msg.get("quick_reply", {}).get("payload") or ""
            text = text.strip()
            if not text:
                continue

            channel = "facebook" if obj_type == "page" else "instagram"
            user_key = f"fb_{sender_id}" if channel == "facebook" else f"ig_{sender_id}"
            referral = msg_event.get("referral") or msg.get("referral") or {}

            print(f"[meta_routes] 📩 Incoming DM from {user_key}: '{text}'")

            # Enqueue into the debouncing & AI processing pipeline (same flow as WhatsApp)
            await add_message_to_batch_async(phone=user_key, text=text, referral=referral)

    return {"status": "ok"}

