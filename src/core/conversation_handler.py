"""
conversation_handler.py — Orchestrates one customer turn end-to-end.

    incoming message
          |
          v
   extract_intent_and_state_changes()  <- intent_classifier.py (silent JSON)
          |
          v
   apply_extraction_to_state()         <- pure state merge, no LLM
          |
          v
   format_system_prompt()              <- prompt.py, reads the UPDATED state
          |
          v
   reply LLM call                      <- writes the actual customer-facing text
          |
          v
   persist new state + return reply

Step 1 in your flow (the 3 fixed ad-triggered welcome messages) happens
entirely BEFORE this handler runs — that's a WhatsApp template/flow
trigger, not an AI decision, so it's out of scope here by design. Every
turn from your step 3 onward goes through this pipeline.
"""

import os
import time
import asyncio
import re
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# The single message sent to the customer on every ESCALATE_TO_AGENT resolution.
# Hardcoded so it cannot be wrong regardless of what the LLM generated.
_ESCALATION_NOTICE = (
    "Aapki query/feedback humari team tak pahunch gayi hai. "
    "Hamara ek team member aapke saath jald hi connect karega. 😊\n\n"
    "Your feedback has been escalated to our support team. "
    "Someone from our team will be with you shortly! 🙏"
)

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from src.core import intent_classifier
from src.core import kb_loader
from src.core import prompt as prompt_module
from src.core.refusal_engine import evaluate_refusal_async
from src.services.interakt import send_text_message_async
from src.services.agent_handoff import execute_agent_handoff_async
from src.state.chat_history import save_message_async
from src.state.csv_logger import log_message_async
from src.state.chat_state import (
    get_user_state_async,
    save_user_state_async,
    reset_user_state_async,
    clear_escalation_async,
    arm_followup_timer,
)

_reply_llm = None


def _get_reply_llm():
    global _reply_llm
    if _reply_llm is None:
        openrouter_key = os.getenv("OPENROUTER_API_KEY")
        if openrouter_key:
            model_name = os.getenv("OPENROUTER_REPLY_MODEL", "openai/gpt-4o-mini")
            _reply_llm = ChatOpenAI(
                model=model_name,
                api_key=openrouter_key,
                base_url="https://openrouter.ai/api/v1",
                temperature=0.2,
                model_kwargs={"response_format": {"type": "json_object"}},
            )
        else:
            openai_key = os.getenv("OPENAI_API_KEY")
            model_name = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            _reply_llm = ChatOpenAI(
                model=model_name,
                api_key=openai_key,
                temperature=0.2,
                model_kwargs={"response_format": {"type": "json_object"}},
            )
    return _reply_llm


async def handle_incoming_message(
    user_message: str,
    session_state: Dict[str, Any],
    conversation_history: List[Dict[str, str]],
    course_id: Optional[str] = None,
    phone: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns {"reply": str, "new_state": dict} — the caller persists
    new_state and appends both messages to history.
    """
    # 1. Silent semantic extraction — any language, no keyword matching.
    flags = await intent_classifier.extract_intent_and_state_changes(
        text=user_message,
        state=session_state,
        history=conversation_history,
    )

    # 2. Pure state merge.
    flags["_raw_text"] = user_message  # injected for deterministic language override
    new_state = intent_classifier.apply_extraction_to_state(session_state, flags)

    # 2b. Production App Database Verification (Real PostgreSQL RDS)
    from src.state.chat_state import is_profile_completed_signal
    profile_claimed = bool(
        flags.get("profile_created_stated") == "confirmed"
        or is_profile_completed_signal(user_message, session_state)
    )

    profile_claim_just_failed = False
    if phone and (profile_claimed or session_state.get("profile_created")):
        from src.services.app_db_verifier import verify_user_in_production_db_async
        try:
            db_res = await verify_user_in_production_db_async(phone)
            if db_res.get("verified"):
                new_state["profile_created"] = True
                new_state["app_installed"] = True
                new_state["profile_db_verified"] = True
                new_state["profile_claim_unverified"] = False
                if db_res.get("full_name"):
                    new_state["customer_name"] = db_res["full_name"]
                if db_res.get("has_trial"):
                    new_state["db_trial_booked"] = True
                if db_res.get("has_enrollment"):
                    new_state["db_course_enrolled"] = True
            else:
                # ZERO TRUST: If not verified in RDS DB (either no profile exists OR DB error/unreachable),
                # customer is strictly UNVERIFIED. NEVER unlock coupon or confirm profile!
                new_state["profile_created"] = False
                new_state["profile_db_verified"] = False
                new_state["profile_claim_unverified"] = True
                new_state["coupon_sent"] = False
                profile_claim_just_failed = bool(profile_claimed)
                # Revert stage so it does not advance to PROFILE_COMPLETED or COUPON_SENT
                prev_stage = session_state.get("stage") or "APP_LINK_SENT"
                if prev_stage in ["PROFILE_COMPLETED", "COUPON_SENT", "TRIAL_CONFIRMED"]:
                    new_state["stage"] = "APP_LINK_SENT"
                else:
                    new_state["stage"] = prev_stage
                if db_res.get("error"):
                    logger.error(f"[conversation_handler] DB verification error for {phone}: {db_res.get('error')}")
        except Exception as e:
            logger.error(f"Error checking production app DB: {e}")
            new_state["profile_created"] = False
            new_state["profile_db_verified"] = False
            new_state["profile_claim_unverified"] = True
            new_state["coupon_sent"] = False
            profile_claim_just_failed = bool(profile_claimed)
            prev_stage = session_state.get("stage") or "APP_LINK_SENT"
            if prev_stage in ["PROFILE_COMPLETED", "COUPON_SENT", "TRIAL_CONFIRMED"]:
                new_state["stage"] = "APP_LINK_SENT"
            else:
                new_state["stage"] = prev_stage

    # 2c. Immediate Routing for Alternate Phone / Account Claim (Manual Verification)
    if (
        flags.get("claims_different_phone_or_account")
        or flags.get("customer_intent") == "DIFFERENT_NUMBER_CLAIM"
        or new_state.get("_last_turn", {}).get("claims_different_phone_or_account")
    ):
        alt_phone = flags.get("different_phone_number") or new_state.get("alternate_phone_number")
        alt_phone_text = f" ({alt_phone})" if alt_phone else ""
        handoff_reply = (
            f"Ji bilkul! Maine aapka alternate number{alt_phone_text} note kar liya hai. "
            "Humari team manual verification ke liye aapse turant connect kar rahi hai. Ek minute hold kijiye! 😊\n\n"
            f"Got it! I have noted your alternate phone number{alt_phone_text}. "
            "Our team is connecting with you shortly for manual account verification! 🙏"
        )
        return {
            "reply": handoff_reply,
            "followup": None,
            "buttons": [],
            "new_state": new_state,
        }

    # 2d. Deterministic Handling for Unverified Profile Claim (Zero Hallucination / Zero Coupon Leak)
    if profile_claim_just_failed or (profile_claimed and new_state.get("profile_claim_unverified")):
        user_lang = new_state.get("language") or "English"
        is_hindi = "hinglish" in user_lang.lower() or "hindi" in user_lang.lower()

        if is_hindi:
            unverified_reply = (
                f"Aapka profile is WhatsApp number (+{phone}) par abhi Sensationz App me register nahi dikh raha hai. 📱\n\n"
                "Agar aapne Google se login kar liya hai, to kya aapne kisi *doosre mobile number* ya doosre Google account se kiya hai? "
                "Agar doosre number se kiya hai to kripya apna wo number batayein, ya pehle Sensationz App me login complete karein! 😊"
            )
            unverified_followup = "Kya aapne kisi doosre number se sign up kiya hai?"
            unverified_buttons = ["Haan, Dusre Number Se 👍", "App Me Login Kar Raha Hoon 📱"]
        else:
            unverified_reply = (
                f"We couldn't find a registered profile under this mobile number (+{phone}) in the Sensationz App yet. 📱\n\n"
                "If you logged in with Google, did you use a different mobile number or Google account? "
                "If you used another number, please share it here so our team can verify it, or complete your signup in the Sensationz App! 😊"
            )
            unverified_followup = "Did you sign up using a different phone number?"
            unverified_buttons = ["Yes, Different Number 👍", "Logging In Now 📱"]

        return {
            "reply": unverified_reply,
            "followup": unverified_followup,
            "buttons": unverified_buttons,
            "new_state": new_state,
        }

    # A customer can switch topics mid-conversation ("what about Kathak
    # timings?" or "face yoga k coupons") — if they did, that becomes the active course for THIS
    # reply's primary KB section.
    from src.core import kb_loader

    # Dynamic course switch: if customer mentions any course from the catalog, switch to it for this turn
    course_in_msg = kb_loader.detect_course_from_text(user_message, default_course=None) if user_message else None

    old_course = session_state.get("course") or course_id or ""

    # If no course is active in state or message, inspect recent user turns in conversation history
    if not course_in_msg and not old_course and not new_state.get("course") and conversation_history:
        for turn in reversed(conversation_history[-4:]):
            if turn.get("role") in ("user", "customer"):
                c = kb_loader.detect_course_from_text(turn.get("content") or "", default_course=None)
                if c:
                    course_in_msg = c
                    break

    effective_course_id = course_in_msg or new_state.get("course") or old_course or ""

    if effective_course_id:
        entry = kb_loader.get_catalog_entry(effective_course_id)
        if entry:
            effective_course_id = entry["id"]
        new_state["course"] = effective_course_id
        session_state["course"] = effective_course_id
        # When switching to a different course, clear old course-specific timings and packages
        if old_course and old_course != effective_course_id:
            new_state["timing"] = None
            new_state["package"] = None
            new_state["fee"] = None
            session_state["timing"] = None
            session_state["package"] = None
            session_state["fee"] = None

    # 3. Build the system prompt with the freshly-understood state.
    # The bot strictly uses the customer's selected language unless explicitly switched.
    from src.core import language_loader

    # [PREVIOUS BEHAVIOR - COMMENTED OUT]:
    # locked_lang = new_state.get("language") or session_state.get("language")
    # if locked_lang:
    #     detected_lang = locked_lang
    # else:
    #     _classifier_lang = flags.get("detected_language") or ""
    #     detected_lang = _classifier_lang or language_loader.get_default_language()
    # new_state["language"] = detected_lang

    _has_native_script = any(ord(c) >= 256 for c in user_message)
    if _has_native_script and flags.get("detected_language"):
        detected_lang = flags.get("detected_language")
    else:
        detected_lang = new_state.get("language") or session_state.get("language") or flags.get("detected_language") or language_loader.get_default_language()

    new_state["language"] = detected_lang

    system_prompt = prompt_module.format_system_prompt(
        new_state, course_id=effective_course_id, detected_language=detected_lang
    )

    # 4. Generate the actual reply, with conversation history for coherence.
    messages = [SystemMessage(content=system_prompt)]
    for t in conversation_history[-5:]:
        content = (t.get("content") or "").strip()
        if not content:
            continue
        msg_cls = HumanMessage if t.get("role") == "user" else AIMessage
        messages.append(msg_cls(content=content))
    messages.append(HumanMessage(content=user_message))

    llm = _get_reply_llm()
    response = await llm.ainvoke(messages)
    raw_content = (response.content or "").strip()

    main_reply = raw_content
    followup_msg = None
    buttons = []

    try:
        clean_json_str = raw_content
        if clean_json_str.startswith("```json"):
            clean_json_str = clean_json_str[7:]
        elif clean_json_str.startswith("```"):
            clean_json_str = clean_json_str[3:]
        if clean_json_str.endswith("```"):
            clean_json_str = clean_json_str[:-3]
        clean_json_str = clean_json_str.strip()

        parsed = json.loads(clean_json_str)
        if isinstance(parsed, dict):
            main_reply = parsed.get("reply", "") or ""
            followup_msg = parsed.get("followup") or None
            raw_b = parsed.get("buttons")
            buttons = [str(b).strip() for b in raw_b if str(b).strip()] if isinstance(raw_b, list) else []
    except Exception as exc:
        logger.warning(f"Failed to parse LLM2 JSON output: {exc}. Using fallback.")
        if "[FOLLOW_UP]" in raw_content:
            parts = raw_content.split("[FOLLOW_UP]", 1)
            main_reply = parts[0].strip()
            followup_msg = parts[1].strip() or None
        else:
            main_reply = raw_content
            followup_msg = None

    # Enforce exact coupons directly from the active course's JSON (zero hallucination, zero hardcode)
    main_reply = _enforce_json_coupons(main_reply, new_state, effective_course_id)

    # Coupon-sent bookkeeping is a plain state flag, decided from what we
    # KNOW (profile just confirmed + eligible this turn) — never by
    # scanning the model's reply text for the coupon string.
    if (
        new_state.get("profile_created")
        and not new_state.get("profile_claim_unverified")
        and new_state.get("stage") == "PROFILE_COMPLETED"
    ):
        if new_state.get("wants_trial"):
            new_state["stage"] = "TRIAL_CONFIRMED"
        else:
            new_state["stage"] = "COUPON_SENT"
            new_state["coupon_sent"] = True

    # In-chat bonus tip delivery (1 per course, no PDF, prefilled text)
    if (
        (new_state.get("profile_created") or new_state.get("stage") in ["PROFILE_COMPLETED", "COUPON_SENT", "TRIAL_CONFIRMED"])
        and not new_state.get("profile_claim_unverified")
        and not new_state.get("bonus_tip_sent")
    ):
        from src.core import kb_loader
        tip = kb_loader.get_course_bonus_tip(effective_course_id)
        if tip:
            main_reply = f"{main_reply}\n\n{tip}".strip()
            new_state["bonus_tip_sent"] = True

    # Guarantee dedicated "Talk to Agent" button in user's language (max 3 buttons total on WhatsApp)
    if followup_msg and buttons is not None:
        from src.core import language_loader
        agent_btn = language_loader.get_agent_button(new_state.get("language") or session_state.get("language"))
        all_agent_labels = language_loader.get_all_agent_buttons()
        filtered_b = [str(b).strip() for b in buttons if str(b).strip() and str(b).strip().lower() not in all_agent_labels]
        buttons = filtered_b[:2] + [agent_btn]

    return {"reply": main_reply, "followup": followup_msg, "buttons": buttons, "new_state": new_state}


def _enforce_json_coupons(reply: str, state: Dict[str, Any], course_id: Optional[str]) -> str:
    """
    100% dynamic, zero-hardcode coupon validation directly from course JSON.
    Reads valid codes from kb_loader.get_course_coupons(course_id).
    If the LLM outputs any uppercase code not in the course's valid codes,
    it dynamically swaps it with the official code for the customer's package from the JSON.
    """
    if not reply or not course_id:
        return reply

    from src.core import kb_loader
    from src.core.coupons import get_coupon_details

    profile_confirmed = bool(
        not state.get("profile_claim_unverified")
        and (
            state.get("profile_created")
            or state.get("coupon_sent")
            or state.get("stage") in ("PROFILE_COMPLETED", "COUPON_SENT")
        )
    )

    coupons = kb_loader.get_course_coupons(course_id)
    valid_codes = {c.get("code").upper() for c in coupons.values() if c.get("code")}

    if not profile_confirmed:
        # If profile not confirmed, redact any leaked or fabricated coupon code pattern
        reply = re.sub(r"\*?\b[A-Z]{3,12}\d{2,5}\b\*?", "welcome discount coupon", reply)
        return reply

    # If profile is confirmed:
    selected_pkg = state.get("package")
    details = get_coupon_details(selected_pkg, course_id=course_id) if selected_pkg else None
    official_code = details["code"] if details else None

    # Replace any hallucinated code tokens not found in the course JSON
    tokens = re.findall(r"\b([A-Z]{3,12}\d{2,5})\b", reply)
    for token in tokens:
        if token.upper() not in valid_codes:
            replacement = f"*{official_code}*" if official_code else "welcome discount coupon"
            reply = re.sub(rf"\b{re.escape(token)}\b", replacement, reply)

    return reply


def _strip_trailing_question(text: str, followup: Optional[str] = None) -> str:
    """If the main reply accidentally ended with a trailing question line or duplicate followup call,
    strip it so Message 1 remains purely informative without duplicating
    the dedicated follow-up question sent as Message 2."""
    if not text:
        return text
    text_clean = text.strip()
    lines = text_clean.split("\n")
    if len(lines) <= 1:
        return text_clean

    last_line = lines[-1].strip()

    # 1. If followup is provided and last line matches followup text
    if followup:
        f_clean = followup.strip().rstrip("?!.। \t\n\r😊🙏✨🌸👍").lower()
        l_clean = last_line.rstrip("?!.। \t\n\r😊🙏✨🌸👍").lower()
        if f_clean and l_clean and (f_clean in l_clean or l_clean in f_clean):
            return "\n".join(lines[:-1]).strip()

    # 2. If last line ends with a question mark (possibly followed by emojis, punctuation, or spaces)
    if re.search(r"(\?|[?？])\s*[\U00010000-\U0010ffff\s]*$", last_line):
        return "\n".join(lines[:-1]).strip()

    # 3. If last line is a call to action prompting for 'Done' / 'Yes' (e.g. 'Done' ya 'Yes' likhein / लिखें)
    if re.search(r"['\"]?(?:done|yes)['\"]?.{0,25}(?:likh|type|bata|bhejo|लिख|टाइप|बता)", last_line, re.IGNORECASE):
        return "\n".join(lines[:-1]).strip()

    return text_clean


def _format_for_whatsapp(text: str) -> str:
    """Cleans up markdown formatting for native WhatsApp rendering."""
    if not text:
        return text
    text = re.sub(r"^(?:#{1,6})\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.*?)\*\*", r"*\1*", text)
    text = re.sub(r"^\s*\*\s+", "• ", text, flags=re.MULTILINE)
    text = re.sub(r"^•\s*-\s*", "• ", text, flags=re.MULTILINE)
    return text.strip()


OFFICIAL_PLATFORM_LINKS_BLOCK = (
    "📱 Android: https://play.google.com/store/apps/details?id=com.sensationz.sensationz.dev\n"
    "🍎 iOS: https://apps.apple.com/us/app/sensationz/id6761418351\n"
    "💻 Website: https://shop.sensationzperformingarts.com/"
)


def _ensure_app_website_links(
    text: str,
    user_asked_for_links: bool = False,
    allow_demo_links: bool = False,
) -> str:
    """
    Strict Zero-Links Policy Shield:
    - If user_asked_for_links is False: Strips unrequested platform/store download links.
    - If allow_demo_links is True: Preserves YouTube demo/sample video links requested by user.
    - If user_asked_for_links is True: Ensures official platform links are appended.
    """
    if not text:
        return text

    if not user_asked_for_links:
        platform_domains = (
            "play.google.com",
            "apps.apple.com",
            "shop.sensationzperformingarts.com",
            "sensationzperformingarts.com",
        )
        youtube_domains = (
            "youtube.com",
            "youtu.be",
        )
        lines = []
        for line in text.splitlines():
            line_lower = line.lower()
            if any(d in line_lower for d in platform_domains):
                continue
            if any(d in line_lower for d in youtube_domains):
                if allow_demo_links:
                    lines.append(line)
                continue
            if ("http://" in line_lower or "https://" in line_lower) and not allow_demo_links:
                continue
            lines.append(line)
        cleaned = "\n".join(lines).strip()
        return re.sub(r"\n{3,}", "\n\n", cleaned)

    # Customer explicitly asked for links: provide official platform links
    t_lower = text.lower()
    has_links = "play.google.com" in t_lower or "shop.sensationzperformingarts.com" in t_lower
    if not has_links:
        from src.core import kb_loader
        official_links = kb_loader.format_app_links_display()
        return f"{text.rstrip()}\n\n{official_links}"
    return text

    # [PREVIOUS LOGIC - AUTO LINK INJECTION (COMMENTED OUT)]:
    # INTERMEDIATE_SELECTION_PATTERNS = (
    #     r"\b(which|kaunsa|kaunsi|kya)\b.{0,30}\b(batch|timing|slot|samay)\b",
    #     r"\b(which|kaunsa|kaunsi|kya)\b.{0,30}\b(package|duration|mahine|pack)\b",
    #     r"\b(select|choose|chuniye|bataiye)\b.{0,30}\b(package duration|package|batch timing|timing)\b",
    #     r"\b(timing|time slot|batch).{0,25}\b(works best|suits you|prefer)\b",
    # )
    # is_intermediate = any(re.search(p, t_lower) for p in INTERMEDIATE_SELECTION_PATTERNS)
    # if is_intermediate and not user_asked_for_links:
    #     if has_links:
    #         lines = [
    #             line for line in text.splitlines()
    #             if not any(d in line for d in ["play.google.com", "apps.apple.com", "shop.sensationzperformingarts.com"])
    #         ]
    #         return "\n".join(lines).strip()
    #     return text
    # if has_links:
    #     return text
    # ACTION_PATTERNS = (
    #     r"\b(download|install|daalo|download kar|install kar)\b",
    #     r"\b(register|sign.?up|create.{0,10}profile|profile bana|profile create)\b",
    #     r"\b(check.{0,20}(schedule|timing|fees).{0,20}(app|website|platform))\b",
    #     r"\b(visit.{0,10}(our|the).{0,10}(app|website|platform)|open.{0,10}(app|website))\b",
    #     r"\b(trial.{0,20}(app|download|profile|steps))\b",
    # )
    # if any(re.search(p, t_lower) for p in ACTION_PATTERNS):
    #     return f"{text.rstrip()}\n\n{OFFICIAL_PLATFORM_LINKS_BLOCK}"
    # return text


async def dispatch_media_guide_async(
    phone: str,
    state: dict,
    media_config: dict,
    state_key: str,
    force_resend: bool = False,
) -> bool:
    """
    100% Dynamic & Resilient Media Delivery:
    - Reads video URLs and captions directly from config (0 hardcoded values).
    - Checks persistent state flag (0 spam: sends at most once unless force_resend is True).
    - Tries native raw MP4 first via Interakt video API.
    - If MP4 fails or is not configured, automatically falls back to YouTube URL.
    """
    if not force_resend and state.get(state_key):
        return False

    raw_mp4 = media_config.get("raw_video_url")
    youtube_url = (
        media_config.get("how_to_book_video_url")
        or media_config.get("how_to_create_profile_video_url")
    )
    caption = media_config.get("video_caption", "")

    delivered = False
    if raw_mp4:
        from src.services.interakt import send_video_message_async
        delivered = await send_video_message_async(phone, raw_mp4, caption=caption)

    # Fallback: Deliver YouTube URL if raw video failed or is not yet configured
    if not delivered and youtube_url:
        fallback_msg = f"{caption}\n{youtube_url}" if caption else f"🎬 *Video Guide:* {youtube_url}"
        await send_text_message_async(phone, fallback_msg)
        delivered = True

    if delivered:
        state[state_key] = True
        return True
    return False



async def handle_conversation_turn_async(
    phone: str,
    text: str,
    history: Optional[List[dict]] = None,
    start_time: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Coordinates an entire conversational turn from tasks.py / routes.py:
    1. Loads session state for the phone number.
    2. Handles tester commands (reset, unescalate).
    3. Executes handle_incoming_message (Two-Step Semantic Extraction + Prompt Generation).
    4. Handles Agent Handoff if extraction flagged requests_human_agent.
    5. Sends the generated reply via WhatsApp (send_text_message_async).
    6. Persists updated state and logs messages asynchronously.
    """
    start_time = start_time or time.time()
    state = await get_user_state_async(phone)
    text_clean = (text or "").strip()
    actual_user_text = re.sub(r"^\[Replying to:[^\]]*\]\s*", "", text_clean, flags=re.DOTALL).strip()
    cmd = actual_user_text.lower()

    # ── Control Commands (Tester / Dev Reset & Unescalate) ────────────────────
    if cmd in ["reset", "#reset", "/reset", "reset chat", "reset session"]:
        await reset_user_state_async(phone)
        await clear_escalation_async(phone)
        reply = "🔄 Session reset successfully! Escalation cleared and AI re-enabled. How can I help you? 😊"
        await send_text_message_async(phone, reply)
        latency_sec = round(time.time() - start_time, 2)
        asyncio.create_task(save_message_async(phone, "assistant", reply, response_time_sec=latency_sec))
        return {"status": "reset", "reply": reply}

    if cmd in ["#unescalate", "/unescalate", "#bot", "#ai", "#enable", "unescalate"]:
        await clear_escalation_async(phone)
        reply = "🤖 AI Assistant re-enabled for this number! How can I help you? 😊"
        await send_text_message_async(phone, reply)
        latency_sec = round(time.time() - start_time, 2)
        asyncio.create_task(save_message_async(phone, "assistant", reply, response_time_sec=latency_sec))
        return {"status": "unescalated", "reply": reply}

    # ── Instant Direct Escalation on Agent Button tap ────────────────────────
    from src.core import language_loader
    all_agent_labels = language_loader.get_all_agent_buttons()
    if cmd in all_agent_labels or cmd in ["agent", "human"]:
        escalation_reply = (
            "Aapki query/request humari support team tak pahunch gayi hai. "
            "Hamara ek team member aapke saath jald hi connect karega. 😊\n\n"
            "Your request has been escalated to our support team. "
            "Someone from our team will be with you shortly! 🙏"
        )
        await execute_agent_handoff_async(
            phone,
            user_reply_text=escalation_reply,
            escalation_reason=f"Customer tapped agent button: {text_clean}",
            start_time=start_time,
        )
        return {"status": "handed_off", "reply": escalation_reply}

    # ── Orchestrate Customer Turn via handle_incoming_message ─────────────────
    active_course = state.get("course")
    result = await handle_incoming_message(
        user_message=text_clean,
        session_state=state,
        conversation_history=history or [],
        course_id=active_course,
        phone=phone,
    )

    reply_text = result.get("reply", "")
    followup_msg = result.get("followup")
    new_state = result.get("new_state", state)
    last_turn = new_state.get("_last_turn", {})

    # Check for Different Phone Number Claim -> Immediate Manual Verification Handoff
    claims_diff_num = (
        last_turn.get("claims_different_phone_or_account")
        or last_turn.get("intent") == "DIFFERENT_NUMBER_CLAIM"
        or new_state.get("claims_different_phone_or_account")
    )
    if claims_diff_num:
        alt_phone = last_turn.get("different_phone_number") or new_state.get("alternate_phone_number")
        alt_str = f" ({alt_phone})" if alt_phone else ""
        reason = (
            f"Manual Verification Needed: Customer claims profile created with different number/account"
            f"{alt_str}. Primary WhatsApp: {phone}"
        )
        new_state["is_escalated"] = True
        new_state["escalation_reason"] = reason
        await execute_agent_handoff_async(
            phone,
            user_reply_text=reply_text,
            escalation_reason=reason,
            start_time=start_time,
            send_connecting_notice=False,
        )
        await save_user_state_async(phone, new_state)
        return {"status": "handed_off_different_number", "reply": reply_text}

    # Check for Human Agent Escalation
    if last_turn.get("requests_human_agent") or last_turn.get("intent") == "AGENT_REQUEST":
        await execute_agent_handoff_async(
            phone,
            user_reply_text=reply_text,
            escalation_reason=f"Customer requested agent: {last_turn.get('question_summary') or text_clean[:50]}",
            start_time=start_time,
        )
        return {"status": "handed_off", "reply": reply_text}

    # ── Refusal / Objection Handling ───────────────────────────────────────
    # When the customer declines or is in an active refusal lifecycle,
    # route through the refusal engine (3-step lifecycle: 0=normal, 1=asked reason, 2=solution offered, 3=escalated).
    current_refusal_stage = new_state.get("refusal_stage", 0)

    from src.core.intent_classifier import is_seat_lock_signal
    # Reset refusal lifecycle if customer is no longer refusing:
    # 1. Customer clicked "Lock My Seat" or sent a positive proceed / seat-locking signal
    # 2. Customer selected a timing, package, requested trial, app link, or profile completion
    # 3. Customer asked a question or is no longer disinterested
    if (
        is_seat_lock_signal(actual_user_text)
        or is_seat_lock_signal(text_clean)
        or last_turn.get("wants_trial")
        or last_turn.get("intent") in ["TIMING_SELECTION", "PACKAGE_SELECTION", "PROFILE_COMPLETED", "APP_LINK_REQUEST"]
        or (not last_turn.get("is_disinterested") and last_turn.get("intent") not in ("DISINTEREST", "COMPLAINT"))
    ):
        new_state["refusal_stage"] = 0
        current_refusal_stage = 0

    is_refusal_or_complaint = (
        (last_turn.get("is_disinterested") or last_turn.get("is_complaint_or_refund") or last_turn.get("intent") in ("COMPLAINT", "DISINTEREST"))
        and not is_seat_lock_signal(actual_user_text)
    ) or (current_refusal_stage > 0 and not is_seat_lock_signal(actual_user_text))

    if is_refusal_or_complaint:
        eval_res = await evaluate_refusal_async(
            text_clean, history=history or [], state=new_state
        )
        resolution = (eval_res or {}).get("resolution_type", "NOT_A_REFUSAL")
        refusal_msg = (eval_res or {}).get("reply_message")
        # Guard: reject instruction-text leaks — if the LLM echoed the meta-instruction
        # description instead of generating a real reply, drop it silently.
        _INSTRUCTION_FINGERPRINTS = (
            "warmly and gracefully accept",
            "without being pushy or arguing",
            "sincerely apologize for the frustration",
            "politely explain our offering boundary",
            "warmly and empathetically ask what is holding",
            "generate a",
            "reply_message",
        )
        if refusal_msg and any(fp in refusal_msg.lower() for fp in _INSTRUCTION_FINGERPRINTS):
            logger.warning(
                f"[refusal_engine] Prompt leak detected in reply_message — dropping. "
                f"Snippet: {refusal_msg[:120]!r}"
            )
            refusal_msg = None

        # BUG3 FIX: Engine says this turn isn't a refusal at all — exit the lifecycle.
        if resolution == "NOT_A_REFUSAL":
            new_state["refusal_stage"] = 0

        if refusal_msg:
            reply_text = refusal_msg
            followup_msg = None          # Suppress pushy sales follow-up on refusal turns
            result["buttons"] = []       # Clear sales buttons

            if resolution == "ASK_REASON":
                new_state["refusal_stage"] = 1
            elif resolution == "PROVIDE_SOLUTION":
                # BUG B FIX: If we already gave a solution (stage==2) and the LLM still
                # returns PROVIDE_SOLUTION, it's a persistent refusal — force escalate.
                if current_refusal_stage >= 2:
                    resolution = "ESCALATE_TO_AGENT"
                    new_state["refusal_stage"] = 3
                    # BUG C FIX: reply_text is still the solution pitch — replace with
                    # the standard escalation notice so customer gets the correct message.
                    reply_text = _ESCALATION_NOTICE
                else:
                    new_state["refusal_stage"] = 2
            elif resolution == "ESCALATE_TO_AGENT":
                new_state["refusal_stage"] = 3

            clean_reply = _format_for_whatsapp(reply_text)
            # Always deliver the escalation notice to the customer BEFORE handoff.
            # We always use _ESCALATION_NOTICE (not clean_reply) so the message is
            # consistent regardless of what the LLM generated as the reply.
            if resolution == "ESCALATE_TO_AGENT":
                escalation_notice = _format_for_whatsapp(_ESCALATION_NOTICE)
                await send_text_message_async(phone, escalation_notice)
                latency_sec = round(time.time() - start_time, 2)
                await save_message_async(phone, "assistant", escalation_notice, response_time_sec=latency_sec)
                asyncio.create_task(log_message_async(phone, "ai", escalation_notice))
                # BUG A FIX: Do NOT pass clean_reply as user_reply_text —
                # execute_agent_handoff_async sends it to the customer again, causing a duplicate.
                # We already sent it above. Pass None so handoff is internal-only.
                await execute_agent_handoff_async(
                    phone,
                    user_reply_text=None,
                    escalation_reason=(eval_res or {}).get("escalation_reason")
                    or f"Refusal escalation: {text_clean[:50]}",
                    start_time=start_time,
                    send_connecting_notice=False,
                )
                await save_user_state_async(phone, new_state)
                return {"status": "handed_off_refusal", "reply": clean_reply}

    # ── Separate Message #1 and Message #2 (Strict JSON with fallback) ───────
    main_reply = reply_text
    if "[FOLLOW_UP]" in main_reply:
        parts = main_reply.split("[FOLLOW_UP]", 1)
        main_reply = parts[0].strip()
        if not followup_msg:
            followup_msg = parts[1].strip() or None

    explicit_link_in_text = bool(
        re.search(r"\b(download link|app link|website link|site link|play\s*store link|app\s*store link|send link|link bhejo|link do)\b", actual_user_text, re.IGNORECASE)
        or (re.search(r"\b(link|links)\b", actual_user_text, re.IGNORECASE) and not re.search(r"\b(demo|video|sample|preview|recording)\b", actual_user_text, re.IGNORECASE))
    )
    user_already_registered = bool(
        new_state.get("profile_created")
        or new_state.get("stage") in ["PROFILE_COMPLETED", "COUPON_SENT", "TRIAL_CONFIRMED"]
    )
    user_asked_links = bool(
        (last_turn.get("intent") == "APP_LINK_REQUEST" and not user_already_registered)
        or explicit_link_in_text
    )
    user_asked_demo = bool(
        re.search(r"\b(demo|video|videos|recording|recordings|sample|preview)\b", actual_user_text, re.IGNORECASE)
        or "demo" in (last_turn.get("question_summary") or "").lower()
        or last_turn.get("wants_resend_video")
        or bool(re.search(r"\b(demo videos?|teaching styles?|instructor videos?|sample videos?)\b", main_reply, re.IGNORECASE))
    )
    clean_reply = _strip_trailing_question(_format_for_whatsapp(main_reply), followup=followup_msg) if followup_msg else _format_for_whatsapp(main_reply)
    clean_reply = _ensure_app_website_links(clean_reply, user_asked_for_links=user_asked_links, allow_demo_links=user_asked_demo)

    # Dynamic Media Configuration for Trial and App Download/Install
    active_cid = active_course or state.get("course") or ""
    course_data_active = kb_loader.get_course_data(active_cid)
    wants_resend = bool(last_turn.get("wants_resend_video", False))

    is_trial_turn = bool(
        last_turn.get("wants_trial")
        or new_state.get("wants_trial")
        or new_state.get("stage") in ["TRIAL_REQUESTED", "TRIAL_STEPS_SENT"]
        or re.search(r"\b(trial links|confirm trial|trial enrollment|how to book|trial class|free trial)\b", clean_reply, re.IGNORECASE)
    )
    is_install_turn = bool(
        new_state.get("stage") in ["READY_FOR_APP_LINK", "PACKAGE_SELECTED"]
        or re.search(r"\b(download the sensationz app|install.{0,10}app|create.{0,15}profile|profile bana|complete.{0,15}profile)\b", clean_reply, re.IGNORECASE)
    )

    clean_followup = _format_for_whatsapp(followup_msg) if followup_msg else None
    if clean_followup:
        # Follow-up messages accompany WhatsApp interactive quick reply buttons and must NEVER have platform links appended
        clean_followup = _ensure_app_website_links(clean_followup, user_asked_for_links=False, allow_demo_links=False)

    # Anti-redundancy guarantee: If links were already delivered in Message #1, do not redundantly ask if they want links in followup
    if clean_followup and "play.google.com" in clean_reply:
        REDUNDANT_LINK_QUESTIONS = (
            r"\b(would you like|do you want|kya aapko|chahiye).{0,30}\b(links?|download links?)\b",
            r"\b(links?.{0,25}(download|app|website)\b.*\?)",
        )
        if any(re.search(p, clean_followup, re.IGNORECASE) for p in REDUNDANT_LINK_QUESTIONS):
            is_trial = bool(last_turn.get("wants_trial") or re.search(r"\b(trial)\b", clean_reply, re.IGNORECASE))
            clean_followup = (
                "Which batch timing works best for your trial class? 😊"
                if is_trial
                else "Which batch timing or package would you like to explore? 😊"
            )

    # Deliver main reply to customer on WhatsApp (Message 1)
    if clean_reply and clean_reply.strip():
        await send_text_message_async(phone, clean_reply)
        latency_sec = round(time.time() - start_time, 2)
        await save_message_async(phone, "assistant", clean_reply, response_time_sec=latency_sec)
        asyncio.create_task(log_message_async(phone, "ai", clean_reply))

    # Dispatch media guide alongside Message 1 if applicable
    if is_trial_turn and (re.search(r"\b(step|download the sensationz app|profile then tap|trial links|confirm trial)\b", clean_reply, re.IGNORECASE) or wants_resend):
        trial_cfg = (
            course_data_active.get("trial_classes")
            or course_data_active.get("trial_class")
            or kb_loader.get_global_rules().get("universal_course_policies", {}).get("trial_classes", {})
        )
        await dispatch_media_guide_async(phone, new_state, trial_cfg, "trial_video_sent", force_resend=wants_resend)
    elif is_install_turn and not is_trial_turn:
        enroll_cfg = (
            course_data_active.get("universal_enrollment_flow")
            or kb_loader.get_global_rules().get("universal_enrollment_flow", {})
        )
        await dispatch_media_guide_async(phone, new_state, enroll_cfg, "app_download_video_sent", force_resend=wants_resend)

    # [PREVIOUS LOGIC - INLINE TEXT VIDEO INJECTION (COMMENTED OUT)]:
    # trial_video_url = course_data_active.get("trial_class", {}).get("how_to_book_video_url")
    # if trial_video_url and trial_video_url not in clean_reply:
    #     if re.search(r"\b(trial links|confirm trial|trial enrollment|how to book|trial class|free trial)\b", clean_reply, re.IGNORECASE):
    #         if re.search(r"\b(step|download the sensationz app|profile then tap|trial links)\b", clean_reply, re.IGNORECASE):
    #             video_line = f"🎬 Watch How to Book Trial Video: {trial_video_url}"
    #             if OFFICIAL_PLATFORM_LINKS_BLOCK in clean_reply:
    #                 clean_reply = clean_reply.replace(OFFICIAL_PLATFORM_LINKS_BLOCK, f"{video_line}\n\n{OFFICIAL_PLATFORM_LINKS_BLOCK}")
    #             elif "play.google.com" in clean_reply:
    #                 parts = re.split(r"(?=📱\s*Android:)", clean_reply)
    #                 if len(parts) == 2:
    #                     clean_reply = f"{parts[0].rstrip()}\n\n{video_line}\n\n{parts[1].lstrip()}"
    #                 else:
    #                     clean_reply = f"{clean_reply.rstrip()}\n\n{video_line}"
    #             else:
    #                 clean_reply = f"{clean_reply.rstrip()}\n\n{video_line}"
    # create_profile_video_url = course_data_active.get("universal_enrollment_flow", {}).get("how_to_create_profile_video_url")
    # if create_profile_video_url and create_profile_video_url not in clean_reply:
    #     if re.search(r"\b(create.{0,15}profile|profile bana|complete.{0,15}profile|profile create)\b", clean_reply, re.IGNORECASE):
    #         if "how to book trial" not in clean_reply.lower() and "watch how to book trial" not in clean_reply.lower():
    #             video_line = f"🎬 Watch How to Create Profile Video: {create_profile_video_url}"
    #             if OFFICIAL_PLATFORM_LINKS_BLOCK in clean_reply:
    #                 clean_reply = clean_reply.replace(OFFICIAL_PLATFORM_LINKS_BLOCK, f"{video_line}\n\n{OFFICIAL_PLATFORM_LINKS_BLOCK}")
    #             elif "play.google.com" in clean_reply:
    #                 parts = re.split(r"(?=📱\s*Android:)", clean_reply)
    #                 if len(parts) == 2:
    #                     clean_reply = f"{parts[0].rstrip()}\n\n{video_line}\n\n{parts[1].lstrip()}"
    #                 else:
    #                     clean_reply = f"{clean_reply.rstrip()}\n\n{video_line}"
    #             else:
    #                 clean_reply = f"{clean_reply.rstrip()}\n\n{video_line}"

    # Deliver the follow-up as a whole different message (Message 2) after a brief pause
    if clean_followup and clean_followup.strip():
        await asyncio.sleep(2.5)
        raw_buttons = result.get("buttons") or []
        from src.core import language_loader
        agent_btn = language_loader.get_agent_button(new_state.get("language"))
        all_agent_labels = language_loader.get_all_agent_buttons()
        filtered_b = [str(b).strip() for b in raw_buttons if str(b).strip() and str(b).strip().lower() not in all_agent_labels]
        final_buttons = filtered_b[:2] + [agent_btn]
        try:
            from src.services.quick_reply_service import send_followup_with_buttons_async
            await send_followup_with_buttons_async(phone, clean_followup, buttons=final_buttons)
        except ImportError:
            await send_text_message_async(phone, clean_followup)
        await save_message_async(phone, "assistant", clean_followup)
        asyncio.create_task(log_message_async(phone, "ai", clean_followup))

    # Persist updated session state
    arm_followup_timer(new_state, topic=text_clean)
    await save_user_state_async(phone, new_state)

    return {"status": "answered", "reply": clean_reply, "followup": clean_followup, "buttons": result.get("buttons", [])}