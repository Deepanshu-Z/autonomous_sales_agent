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
from typing import Any, Dict, List, Optional, Tuple

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
    reset_follow_up_timer,
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


def adapt_contextual_quick_replies(
    state: Dict[str, Any],
    course_id: Optional[str] = None,
    raw_buttons: Optional[List[str]] = None,
    language: Optional[str] = None,
    followup: Optional[str] = None,
) -> List[str]:
    """
    Contextually adapts WhatsApp quick-reply buttons based on the sales funnel's missing slot:
    1. TIMING slot missing -> display available timing periods / slots.
    2. TEACHER slot missing -> display instructor names for that timing from COURSE_DATA.
    3. DURATION slot missing -> display package duration tiers (e.g. 1 Month Pack, 3 Months Pack).
    4. PAYMENT SUMMARY & CHECKOUT -> display checkout actions (e.g. Pay Online, Download App).

    Guarantees:
    - If followup is None/empty or payment is claimed/escalated: returns [] (zero buttons).
    - Always appends dedicated "Talk to Agent" button in the customer's language as the final button.
    - Max 3 buttons total on WhatsApp (up to 2 contextual + 1 agent button).
    - Max 20 characters per button title.
    """
    if not followup or not str(followup).strip():
        return []
    is_client_paid = bool(
        state.get("payment_status") == "PAID"
        or state.get("is_enrolled")
        or state.get("stage") == "ENROLLED"
    )
    if state.get("is_escalated") or (not is_client_paid and (
        state.get("payment_claimed")
        or state.get("_last_turn", {}).get("claims_payment_or_enrollment")
        or state.get("_last_turn", {}).get("intent") == "PAYMENT_CLAIM"
    )):
        return []

    from src.core import language_loader
    effective_lang = language or state.get("language")
    agent_btn = language_loader.get_agent_button(effective_lang)
    all_agent_labels = language_loader.get_all_agent_buttons()

    # Normalize effective course_id
    from src.core import kb_loader
    cid = (course_id or state.get("active_course") or state.get("course") or "").lower().strip()

    timing_val = state.get("timing") or state.get("selected_timing")
    teacher_val = state.get("selected_teacher") or state.get("teacher")
    duration_val = state.get("package") or state.get("selected_duration")

    # Clean raw_buttons
    cleaned_raw: List[str] = []
    if raw_buttons:
        for b in raw_buttons:
            b_str = str(b).strip()
            if b_str and b_str.lower() not in all_agent_labels:
                cleaned_raw.append(b_str)

    periods_info = kb_loader.get_course_time_periods(cid)
    missing_periods = periods_info.get("missing_periods", [])
    avail_periods = periods_info.get("available_periods", [])

    candidate_buttons: List[str] = []

    # ── 1. TIMING SLOT MISSING ──
    if not timing_val:
        filtered_timing = []
        for b_str in cleaned_raw:
            if "Morning" in missing_periods and re.search(r"\b(morning|subah|sawere|mrng)\b", b_str, re.IGNORECASE):
                continue
            if "Evening" in missing_periods and re.search(r"\b(evening|shaam|sham|night|raat|evng)\b", b_str, re.IGNORECASE):
                continue
            if "Afternoon" in missing_periods and re.search(r"\b(afternoon|dopahar|dophar)\b", b_str, re.IGNORECASE):
                continue
            filtered_timing.append(b_str)

        if filtered_timing:
            candidate_buttons = filtered_timing
        elif avail_periods:
            if avail_periods == ["Evening"]:
                candidate_buttons = ["Evening Batch", "Check Timings"]
            elif "Morning" in avail_periods and "Evening" in avail_periods:
                candidate_buttons = ["Morning Batch", "Evening Batch"]
            else:
                candidate_buttons = [f"{p} Batch" for p in avail_periods[:2]]
        else:
            valid_timings = kb_loader.get_valid_timings(cid)
            candidate_buttons = valid_timings[:2] if valid_timings else ["View Timings", "Trial Class"]

    # ── 2. TEACHER SLOT MISSING ──
    elif not teacher_val:
        course_data = kb_loader.get_course_data(cid)
        course_teachers = [t.get("name") for t in course_data.get("teachers", []) if t.get("name")]
        for b in course_data.get("batch_timings", {}).get("batches", []):
            for inst in b.get("instructors", []):
                if inst and inst not in course_teachers:
                    course_teachers.append(inst)

        # Check if cleaned_raw has any teacher names
        raw_has_teacher = False
        if cleaned_raw:
            for b_str in cleaned_raw:
                b_low = b_str.lower()
                if any(t.lower() in b_low or b_low in t.lower() for t in course_teachers):
                    raw_has_teacher = True
                    break

        if raw_has_teacher:
            candidate_buttons = cleaned_raw
        else:
            # Look up instructors assigned to the selected timing
            slot_teachers = kb_loader.get_teachers_for_slot(cid, str(timing_val))
            if slot_teachers:
                candidate_teachers = slot_teachers
            else:
                candidate_teachers = course_teachers

            if len(candidate_teachers) >= 2:
                candidate_buttons = candidate_teachers[:2]
            elif len(candidate_teachers) == 1:
                t_name = candidate_teachers[0]
                t_profile = next((t for t in course_data.get("teachers", []) if t.get("name") == t_name), None)
                if t_profile and t_profile.get("demo_video"):
                    candidate_buttons = [t_name, "Demo Video"]
                else:
                    candidate_buttons = [t_name, "Free Trial"]
            else:
                candidate_buttons = ["Instructor Info", "Free Trial"]

    # ── 3. DURATION SLOT MISSING ──
    elif not duration_val:
        # Check if raw_buttons already contains package duration, trial, or instructor options
        raw_has_duration = False
        if cleaned_raw:
            for b_str in cleaned_raw:
                if re.search(r"\b(\d+\s*(?:m|month|months|mahina|mahine|y|year|years|saal)?|pack|package|trial|demo|instructor|teacher)\b", b_str, re.IGNORECASE):
                    raw_has_duration = True
                    break

        if raw_has_duration:
            candidate_buttons = cleaned_raw
        else:
            valid_pkgs = kb_loader.get_valid_packages(cid)
            durations = list(valid_pkgs.keys()) if valid_pkgs else []
            if not durations:
                course_data = kb_loader.get_course_data(cid)
                durations = [p.get("duration") for p in course_data.get("pricing", {}).get("packages", []) if p.get("duration")]

            if durations:
                pkg_buttons = []
                for d in durations[:2]:
                    d_str = str(d).strip()
                    btn_text = f"{d_str} Pack" if not re.search(r"\b(pack|package)\b", d_str, re.IGNORECASE) else d_str
                    pkg_buttons.append(btn_text)
                candidate_buttons = pkg_buttons
            else:
                candidate_buttons = ["1 Month Pack", "3 Months Pack"]

    # ── 4. ENROLLED / PAID STUDENT ──
    elif is_client_paid:
        candidate_buttons = [b for b in cleaned_raw if not re.search(r"\b(pay|checkout)\b", b, re.IGNORECASE)]
        if not candidate_buttons:
            candidate_buttons = ["Download App", "My Classes"]

    # ── 5. PAYMENT SUMMARY & CHECKOUT (UNPAID) ──
    else:
        raw_has_checkout = False
        if cleaned_raw:
            for b_str in cleaned_raw:
                if re.search(r"\b(pay|checkout|app|download|online|website|portal)\b", b_str, re.IGNORECASE):
                    raw_has_checkout = True
                    break

        if raw_has_checkout:
            candidate_buttons = cleaned_raw
        else:
            candidate_buttons = ["Pay Online", "Download App"]

    # Deduplicate, sanitize length <= 20 chars, max 2 contextual buttons
    final_contextual: List[str] = []
    seen = set()
    for b in candidate_buttons:
        b_str = str(b).strip()
        if not b_str:
            continue
        if b_str.lower() in all_agent_labels:
            continue
        # Truncate to 20 characters (strict WhatsApp API limit)
        truncated = b_str[:20].strip()
        if truncated.lower() not in seen:
            seen.add(truncated.lower())
            final_contextual.append(truncated)
        if len(final_contextual) >= 2:
            break

    # Always append dedicated localized "Talk to Agent" button (max 3 buttons total on WhatsApp)
    agent_btn_clean = agent_btn[:20].strip()
    return final_contextual + [agent_btn_clean]


async def handle_incoming_message(
    user_message: str,
    session_state: Dict[str, Any],
    conversation_history: List[Dict[str, str]],
    course_id: Optional[str] = None,
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

    # Real-time payment verification: If user claims payment or asks about checkout/QR, check Razorpay directly
    phone_key = session_state.get("phone") or new_state.get("phone") or ""
    if phone_key and new_state.get("payment_status") != "PAID":
        is_payment_query = bool(
            flags.get("claims_payment_or_enrollment")
            or flags.get("customer_intent") == "PAYMENT_CLAIM"
            or re.search(r"\b(paid|already paid|payment done|pay kar diya|payment ho gaya|transfer kar diya|paise bhej diye|fees de di|pay online)\b", user_message, re.IGNORECASE)
        )
        if is_payment_query:
            from src.services.razorpay_service import verify_and_sync_payment_async
            is_verified, verified_state = await verify_and_sync_payment_async(phone_key, new_state)
            if is_verified:
                new_state = verified_state
                session_state.update(verified_state)

    # A customer can switch topics mid-conversation ("what about Kathak
    # timings?" or "face yoga k coupons") — if they did, that becomes the active course for THIS
    # reply's primary KB section.
    from src.core import kb_loader

    # Dynamic course switch: if customer mentions any course from the catalog, switch to it for this turn
    course_in_msg = kb_loader.detect_course_from_text(user_message, default_course=None) if user_message else None

    old_course = session_state.get("course") or course_id or ""
    if course_in_msg in ("dance", "western_dance") and old_course in ("ladies_dance", "kathak"):
        course_in_msg = old_course

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

    # Sanitize any hallucinated time-of-day period claims (e.g. calling PM batches 'morning timings')
    main_reply = _sanitize_timing_period_hallucinations(main_reply, effective_course_id)

    # Strictly intercept and sanitize any hallucinated confirmation of enrollment, trial, or payment
    main_reply, followup_msg = _sanitize_confirmation_guardrails(
        main_reply, followup_msg, language=detected_lang, state=new_state
    )

    # Coupon-sent bookkeeping is a plain state flag, decided from what we
    # KNOW (profile just confirmed + eligible this turn) — never by
    # scanning the model's reply text for the coupon string.
    if new_state.get("profile_created") and new_state.get("stage") == "PROFILE_COMPLETED":
        new_state["stage"] = "COUPON_SENT"
        new_state["coupon_sent"] = True

    is_client_paid = bool(
        new_state.get("payment_status") == "PAID"
        or new_state.get("is_enrolled")
        or new_state.get("stage") == "ENROLLED"
    )

    # Strict Zero Follow-up Rule: When an UNPAID user claims they paid, there must be NO follow up and NO buttons
    if not is_client_paid and (
        new_state.get("payment_claimed")
        or flags.get("claims_payment_or_enrollment")
        or flags.get("customer_intent") == "PAYMENT_CLAIM"
    ):
        new_state["payment_claimed"] = True
        followup_msg = None
        buttons = []

    # Dynamic WhatsApp Quick-Reply Buttons (Subtask 4.2): Contextually adapt buttons based on missing slot
    buttons = adapt_contextual_quick_replies(
        state=new_state,
        course_id=effective_course_id,
        raw_buttons=buttons,
        language=new_state.get("language") or session_state.get("language"),
        followup=followup_msg,
    )

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
        state.get("profile_created")
        or state.get("coupon_sent")
        or state.get("stage") in ("PROFILE_COMPLETED", "COUPON_SENT")
    )

    coupons = kb_loader.get_course_coupons(course_id)
    valid_codes = {c.get("code").upper() for c in coupons.values() if c.get("code")}


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


def _sanitize_timing_period_hallucinations(reply: str, course_id: Optional[str]) -> str:
    """
    100% dynamic, zero-hardcode sanity shield for time-of-day slots.
    If a course does NOT offer Morning batches (or Evening/Afternoon), but the LLM
    falsely presents PM batches as 'morning batch timings', this intercepts and corrects it.
    """
    if not reply or not course_id:
        return reply

    from src.core import kb_loader
    periods_info = kb_loader.get_course_time_periods(course_id)
    missing_periods = periods_info.get("missing_periods", [])
    if not missing_periods:
        return reply

    course_data = kb_loader.get_course_data(course_id)
    course_name = course_data.get("course_overview", {}).get("name", course_id.title())

    # Case 1: Course has NO Morning batches, but reply claims "morning batch timings are: ... PM"
    if "Morning" in missing_periods:
        has_pm_times = bool(re.search(r"\b\d{1,2}(?::\d{2})?\s*PM\b", reply, re.IGNORECASE))
        morning_claim = re.search(
            r"(?:the\s+available\s+morning\s+batch\s+timings?(?:\s+for\s+[^:\n]+)?\s*are:?|available\s+morning\s+batch\s+timings?|morning\s+batch\s+timings?\s+are:?|subah\s+k[aei]\s+batch\s+timings?\s*hain:?)",
            reply,
            re.IGNORECASE,
        )
        if morning_claim and has_pm_times:
            avail_p = " and ".join(periods_info.get("available_periods", ["Evening"]))
            replacement = (
                f"Please note that {course_name} does not offer morning batches; "
                f"classes are held in the {avail_p.lower()} only. The available {avail_p.lower()} batch timings are:"
            )
            reply = re.sub(
                r"(?i)(?:the\s+available\s+morning\s+batch\s+timings?(?:\s+for\s+[^:\n]+)?\s*are:?|available\s+morning\s+batch\s+timings?|morning\s+batch\s+timings?\s+are:?)",
                replacement,
                reply,
            )

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
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\)]+)\)", r"\2", text)
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
        demo_domains = (
            "youtube.com",
            "youtu.be",
            "instagram.com",
        )
        lines = []
        for line in text.splitlines():
            line_lower = line.lower()
            if any(d in line_lower for d in platform_domains):
                continue
            if any(d in line_lower for d in demo_domains):
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

# ── Strict Non-Confirmation Guardrails (Payment, Trial, Enrollment) ───────────
# The AI MUST NEVER confirm any enrollment, free trial booking, or payment under any circumstances.

_PAYMENT_CONFIRMATION_PATTERNS = [
    # Latin / Hinglish
    r"\b(?:aapka|aapki|apka|apki|humne|humein|hume|maine)?\s*(?:payment|paise|fees?|amount|transaction|screenshot)\s*(?:receive|received|mil\s*gay[aei]?|aa\s*gay[aei]?|verify|verified|confirm|ho\s*gay[aei]?|successful)",
    r"\b(?:payment|paise|fees?|amount)\s+(?:mil\s*chuki?\s*hai|mil\s*gayi?\s*hai|receive\s*ho\s*gayi?\s*hai|verify\s*ho\s*gayi?\s*hai|confirm\s*ho\s*gayi?\s*hai|successful\s*hai)",
    r"\b(?:screenshot|slip)\s*(?:check\s*ho\s*gay[aei]?|dekh\s*li(?:ya)?|verify\s*ho\s*gay[aei]?|confirm\s*ho\s*gay[aei]?)",
    # English
    r"\b(?:your\s+)?(?:payment|fees?|amount|transaction|screenshot)\s+(?:has\s+been\s+|is\s+)?(?:received|verified|confirmed|successful|approved)\b",
    r"\b(?:we\s+have|i\s+have)\s+received\s+(?:your\s+)?(?:payment|fees?|amount|money|screenshot)\b",
    r"\b(?:payment|transaction)\s+is\s+successful\b",
    # Devanagari
    r"(?:पेमेंट|पैसे|फीस)\s*(?:मिल\s*ग[यायीएई]|प्राप्त\s*हो\s*ग[यायीएई]|वेरिफाई\s*हो\s*ग[यायीएई]|कन्फर्म\s*हो\s*ग[यायीएई])",
]

_TRIAL_CONFIRMATION_PATTERNS = [
    # Latin / Hinglish
    r"\b(?:aapka|aapki|apka|apki)?\s*(?:free\s+)?(?:trial|demo\s*class|trial\s*class)\s*(?:confirm\s*ho\s*gay[aei]?|book\s*ho\s*gay[aei]?|kar\s*di(?:ya)?|fix\s*ho\s*gay[aei]?|ho\s*chuk[aei]?|pakki?\s*hai|pakka\s*hai)\b",
    r"\b(?:kal|aaj)\s*(?:ke\s*liye\s*)?(?:aapka|aapki)?\s*(?:trial|demo)\s*(?:book|confirm|fix)\s*ho\s*gay[aei]?\b",
    r"\b(?:maine|humne)\s*(?:aapka|aapki)?\s*(?:trial|demo)\s*(?:book|confirm)\s*kar\s*di(?:ya)?\b",
    # English
    r"\b(?:your\s+)?(?:free\s+)?(?:trial|trial\s+class|demo\s+class|demo)\s+(?:has\s+been\s+|is\s+)?(?:confirmed|booked|scheduled|reserved)\b",
    r"\b(?:i've|i\s+have|we've|we\s+have)\s+(?:booked|confirmed|scheduled)\s+your\s+(?:free\s+)?(?:trial|demo)\b",
    r"\b(?:trial|demo)\s+(?:is|has\s+been)\s+(?:booked|confirmed)\s+for\b",
    r"\b(?:trial|free\s*trial)\s+booked\s+successfully\b",
    # Devanagari
    r"(?:ट्रायल|डेमो)\s*(?:क्लास\s*)?(?:बुक\s*हो\s*ग[यायीएई]|कन्फर्म\s*हो\s*ग[यायीएई]|पक्की?\s*हो\s*ग[यायीएई]|कर\s*दि[याए])",
]

_ENROLLMENT_CONFIRMATION_PATTERNS = [
    # Latin / Hinglish
    r"\b(?:aapka|aapki|apka|apki)?\s*(?:enrollment|admission|seat)\s*(?:confirm\s*ho\s*gay[aei]?|book\s*ho\s*gay[aei]?|ho\s*chuk[aei]?|kar\s*di(?:ya)?|reserve\s*ho\s*gay[aei]?|pakki?\s*hai|pakka\s*hai|final\s*ho\s*gay[aei]?)\b",
    r"\b(?:aap\s+enroll\s+ho\s*gaye|aapka\s+admission\s+ho\s*gaya|seat\s+reserve\s+ho\s*gayi)\b",
    # English
    r"\b(?:your\s+)?(?:enrollment|admission|seat)\s+(?:has\s+been\s+|is\s+)?(?:confirmed|booked|finalized|reserved)\b",
    r"\b(?:you\s+are|you've\s+been)\s+(?:successfully\s+)?(?:enrolled|admitted)\b",
    r"\b(?:confirming|confirmed)\s+your\s+(?:enrollment|admission|seat)\b",
    r"\b(?:enrollment|admission)\s+confirmed\s+successfully\b",
    # Devanagari
    r"(?:एनरोलमेंट|एडमिशन|सीट)\s*(?:कन्फर्म\s*हो\s*ग[यायीएई]|बुक\s*हो\s*ग[यायीएई]|पक्की?\s*हो\s*ग[यायीएई]|कर\s*दि[याए])",
]


def _sanitize_confirmation_guardrails(
    reply: str,
    followup: Optional[str] = None,
    language: Optional[str] = None,
    state: Optional[dict] = None,
) -> Tuple[str, Optional[str]]:
    """
    Deterministic Guardrail: Strictly intercepts and sanitizes any false confirmation
    of course enrollment, free trial booking, or payment verification.
    Bypassed for payment and enrollment confirmations if the customer is already verified PAID/ENROLLED in session state.
    """
    if not reply:
        return reply, followup

    is_client_paid = bool(
        state and (
            state.get("payment_status") == "PAID"
            or state.get("is_enrolled")
            or state.get("stage") == "ENROLLED"
        )
    )

    full_check = f"{reply} {followup or ''}"
    has_devanagari = any(ord(c) >= 0x0900 and ord(c) <= 0x097F for c in full_check)
    is_english = bool(language and "english" in language.lower() and "hinglish" not in language.lower())

    # 1. Check Payment Confirmation (only for unverified/unpaid users)
    if not is_client_paid and any(re.search(p, full_check, re.IGNORECASE) for p in _PAYMENT_CONFIRMATION_PATTERNS):
        logger.warning(f"[guardrails] Payment confirmation intercepted and sanitized. Raw reply: {reply[:80]}...")
        if has_devanagari:
            sanitized_reply = (
                "कृपया ध्यान दें: हमने आपके पेमेंट रिकॉर्ड्स चेक किए हैं, लेकिन आपका ट्रांजेक्शन अभी तक प्राप्त/कन्फर्म नहीं हुआ है। "
                "यदि आपने Sensationz ऐप या वेबसाइट पर पेमेंट किया है, तो वह आपके अकाउंट में अपने आप अपडेट हो जाता है। "
                "यदि आपने UPI या ट्रांसफर किया है और राशि कट गई है, तो कृपया *agent* टाइप करें ताकि हमारी टीम तुरंत आपकी मदद कर सके! 😊"
            )
        elif is_english:
            sanitized_reply = (
                "Please note: We checked our payment records, but your transaction has not been confirmed yet. "
                "If you completed payment through our official Sensationz App or website checkout, it will reflect automatically under your account. "
                "If you transferred via UPI and amount was deducted, please type *agent* so a support coordinator can check and confirm your payment! 😊"
            )
        else:
            sanitized_reply = (
                "Dhyan dein: Humne aapke payment records check kiye hain, lekin aapka transaction abhi tak confirm nahi hua hai. "
                "Agar aapne official Sensationz App ya website par checkout kiya hai, toh payment aapke account mein automatically update ho jata hai. "
                "Agar aapne manual UPI ya transfer kiya hai aur paise kat gaye hain, toh kripya *agent* type karein taaki humari team verify kar sake! 😊"
            )
        return sanitized_reply, None

    # 2. Check Free Trial Confirmation
    if any(re.search(p, full_check, re.IGNORECASE) for p in _TRIAL_CONFIRMATION_PATTERNS):
        logger.warning(f"[guardrails] Trial confirmation intercepted and sanitized. Raw reply: {reply[:80]}...")
        if has_devanagari:
            sanitized_reply = (
                "कृपया ध्यान दें कि व्हाट्सएप चैट पर AI द्वारा ट्रायल क्लास सीधे बुक या कन्फर्म नहीं की जा सकती। "
                "अपनी फ्री ट्रायल क्लास कन्फर्म करने के लिए कृपया Sensationz ऐप में जाएं "
                "(Profile -> Trial Links -> बैच टाइमिंग और टीचर चुनें -> Confirm Trial Enrollment पर टैप करें)। "
                "ऐप में कन्फर्म करते ही आपकी ट्रायल क्लास सक्रिय हो जाएगी! 😊"
            )
            sanitized_followup = "क्या आपको ऐप में ट्रायल बैच टाइमिंग चुनने में कोई मदद चाहिए? 😊"
        elif is_english:
            sanitized_reply = (
                "Please note that free trial classes cannot be booked or confirmed directly over chat by the AI. "
                "To confirm your free trial, please open the Sensationz App "
                "(Go to Profile -> Trial Links -> Select your preferred batch timing & teacher -> Tap Confirm Trial Enrollment). "
                "Once confirmed in the app, your free trial class is activated! 😊"
            )
            sanitized_followup = "Would you like any help selecting your trial batch timing in the app? 😊"
        else:
            sanitized_reply = (
                "Dhyan dein ki WhatsApp chat par AI ke dwara trial class book ya confirm nahi hoti hai. "
                "Free trial confirm karne ke liye Sensationz App open karein "
                "(Profile -> Trial Links -> Batch timing aur teacher select karein -> Tap Confirm Trial Enrollment). "
                "Wahan tap karte hi aapki free trial class confirm ho jayegi! 😊"
            )
            sanitized_followup = "Kya aapko app mein trial batch timing select karne mein koi madad chahiye? 😊"
        return sanitized_reply, sanitized_followup

    # 3. Check Course Enrollment / Admission Confirmation (only for unverified/unpaid users)
    if not is_client_paid and any(re.search(p, full_check, re.IGNORECASE) for p in _ENROLLMENT_CONFIRMATION_PATTERNS):
        logger.warning(f"[guardrails] Enrollment confirmation intercepted and sanitized. Raw reply: {reply[:80]}...")
        if has_devanagari:
            sanitized_reply = (
                "कृपया ध्यान दें कि व्हाट्सएप चैट पर AI द्वारा सीधे एनरोलमेंट कन्फर्म नहीं होता है। "
                "अपना एनरोलमेंट पूरा करने के लिए Sensationz ऐप या आधिकारिक वेबसाइट (shop.sensationzperformingarts.com) पर जाएं, "
                "अपना पैकेज चुनें और चेकआउट पूरा करें। चेकआउट पूरा होते ही आपका एनरोलमेंट एक्टिवेट हो जाता है। "
                "किसी भी सहायता के लिए आप *agent* टाइप कर सकते हैं! 😊"
            )
        elif is_english:
            sanitized_reply = (
                "Please note that course enrollment cannot be confirmed directly over chat by the AI. "
                "To complete your enrollment, please select your package and complete checkout on the official Sensationz App or website (shop.sensationzperformingarts.com). "
                "Once checkout is completed, your enrollment is activated. For assistance, please type *agent*! 😊"
            )
        else:
            sanitized_reply = (
                "Dhyan dein ki WhatsApp chat par AI ke dwara enrollment confirm nahi hota hai. "
                "Course enrollment complete karne ke liye Sensationz App ya official website (shop.sensationzperformingarts.com) par jayein, "
                "apna package select karein aur checkout complete karein. Checkout ke baad aapka enrollment active ho jata hai. "
                "Kisi bhi query ke liye aap *agent* type kar sakte hain! 😊"
            )
        return sanitized_reply, None

    return reply, followup


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


async def dispatch_payment_qr_async(
    phone: str,
    state: dict,
    course_id: str,
    package: Optional[str] = None,
    force_resend: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Subtask 5.3: Dispatches a dynamic Razorpay UPI QR code image directly to the customer's WhatsApp
    with payment instructions and direct UPI payment link.

    - Computes exact fees via pricing_engine (zero LLM math).
    - Generates dynamic single_use UPI QR code via razorpay_service.
    - Sends QR code image directly via send_image_message_async.
    - Falls back to WhatsApp text message with direct payment link if image sending fails.
    - Embeds metadata notes (course, duration, timing, teacher, phone) into Razorpay QR code.
    - Updates session state with qr_code_sent=True, last_qr_id, last_qr_url, last_qr_amount.
    """
    cid = (course_id or state.get("course") or "").lower().strip()
    pkg = package or state.get("package") or state.get("selected_duration")
    if not cid or not pkg:
        return None

    # Anti-spam guard: Send once per package unless forced or requested
    if not force_resend and state.get("qr_code_sent") and state.get("last_qr_package") == pkg:
        return None

    from src.core.pricing_engine import calculate_exact_fee
    exact_bill = calculate_exact_fee(cid, pkg)
    if not exact_bill or not exact_bill.get("final_payable_amount"):
        return None

    final_amount = exact_bill["final_payable_amount"]
    final_amount_paise = exact_bill.get("final_amount_paise") or (final_amount * 100)

    # Support testing override via TEST_QR_AMOUNT (e.g. 0.1 for 10 paise QR testing)
    test_qr_amount_env = os.getenv("TEST_QR_AMOUNT")
    if test_qr_amount_env:
        try:
            override_amt = float(test_qr_amount_env)
            if override_amt > 0:
                final_amount = override_amt
                final_amount_paise = int(round(override_amt * 100))
        except (ValueError, TypeError):
            pass

    display_amount = f"₹{final_amount:.2f}" if (isinstance(final_amount, float) and final_amount < 1) else f"₹{int(final_amount):,}"

    from src.services.razorpay_service import create_dynamic_qr_code, RazorpayServiceError

    timing_val = state.get("timing") or state.get("selected_timing")
    teacher_val = state.get("selected_teacher") or state.get("teacher")

    catalog_entry = kb_loader.get_catalog_entry(cid)
    course_name = catalog_entry.get("name", cid.title()) if catalog_entry else cid.title()

    try:
        qr_data = create_dynamic_qr_code(
            amount=final_amount,
            amount_in_paise=final_amount_paise,
            course_id=cid,
            duration=pkg,
            timing=str(timing_val) if timing_val else None,
            teacher=str(teacher_val) if teacher_val else None,
            customer_phone=phone,
            name=f"Sensationz - {course_name}"[:40],
            description=f"{course_name} ({pkg}) Enrollment"[:255],
            close_by_minutes=30,
        )
    except RazorpayServiceError as rze:
        logger.error(f"[conversation_handler] Razorpay QR generation failed: {rze}")
        return None
    except Exception as exc:
        logger.error(f"[conversation_handler] Unexpected error generating QR: {exc}")
        return None

    image_url = qr_data.get("image_url")
    qr_id = qr_data.get("id")

    # Format localized payment instructions & direct UPI link
    lang = (state.get("language") or "English").lower()
    is_hindi = "hindi" in lang and "hinglish" not in lang

    if is_hindi:
        caption = (
            f"📲 *UPI QR कोड से तुरंत फीस पेमेंट करें:*\n\n"
            f"• *कोर्स:* {course_name}\n"
            f"• *ड्यूरेशन:* {pkg}\n"
            f"• *कुल फीस:* {display_amount} ({exact_bill.get('gst_percentage', 18)}% GST सहित)\n\n"
            f"👉 *सीधा UPI पेमेंट लिंक (मोबाइल पर टैप करें):*\n"
            f"{image_url}\n\n"
            f"📝 *पेमेंट निर्देश:*\n"
            f"1. ऊपर दिए गए QR कोड को किसी भी UPI ऐप (GPay, PhonePe, Paytm, BHIM) से स्कैन करें।\n"
            f"2. या मोबाइल पर सीधे भुगतान करने के लिए ऊपर दिए गए लिंक पर टैप करें।\n"
            f"3. पेमेंट पूरा होते ही आपकी क्लासेस एक्टिवेट हो जाएंगी! 🙏"
        )
    else:
        caption = (
            f"📲 *Scan & Pay via any UPI App (GPay, PhonePe, Paytm, BHIM):*\n\n"
            f"• *Course:* {course_name}\n"
            f"• *Duration:* {pkg}\n"
            f"• *Total Payable:* {display_amount} (Includes {exact_bill.get('gst_percentage', 18)}% GST)\n\n"
            f"👉 *Direct UPI Payment Link (Tap to Pay on Mobile):*\n"
            f"{image_url}\n\n"
            f"📝 *Payment Instructions:*\n"
            f"1. Scan this QR code using Google Pay, PhonePe, Paytm, or BHIM.\n"
            f"2. Or tap the direct payment link above to open your UPI app on mobile.\n"
            f"3. Your classes and account will be activated upon payment! 🙏"
        )

    # Deliver QR image directly to WhatsApp
    from src.services.interakt import send_image_message_async
    delivered = False
    if image_url:
        delivered = await send_image_message_async(phone, image_url, caption=caption)

    # Fallback to text message if image delivery failed
    if not delivered:
        fallback_msg = f"{caption}\n\n🖼️ *QR Code Image Link:* {image_url}"
        await send_text_message_async(phone, fallback_msg)
        delivered = True

    if delivered:
        state["qr_code_sent"] = True
        state["last_qr_id"] = qr_id
        state["last_qr_package"] = pkg
        state["last_qr_url"] = image_url
        state["last_qr_amount"] = final_amount
        return qr_data

    return None


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
    cmd = re.sub(r"^\[Replying to:[^\]]*\]\s*", "", text_clean, flags=re.IGNORECASE).strip().lower()

    # ── Control Commands (Tester / Dev Reset & Unescalate) ────────────────────
    if cmd in ["reset", "#reset", "/reset", "reset chat", "reset session"] or cmd.startswith("#reset") or cmd.startswith("/reset"):
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

    if cmd in ["#summary", "/summary", "#info", "#client", "summary"]:
        from src.core.helpers.client_summary import get_client_summary_async
        summary_text = await get_client_summary_async(phone)
        await send_text_message_async(phone, summary_text)
        latency_sec = round(time.time() - start_time, 2)
        asyncio.create_task(save_message_async(phone, "assistant", summary_text, response_time_sec=latency_sec))
        return {"status": "summary", "reply": summary_text}

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
    )

    reply_text = result.get("reply", "")
    followup_msg = result.get("followup")
    new_state = result.get("new_state", state)
    last_turn = new_state.get("_last_turn", {})

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

    # Only reset refusal_stage back to 0 if customer actively accepts an enrollment step (timing, package, trial)
    if current_refusal_stage >= 2 and not last_turn.get("is_disinterested"):
        if last_turn.get("wants_trial") or last_turn.get("intent") in ["TIMING_SELECTION", "PACKAGE_SELECTION", "PROFILE_COMPLETED"]:
            new_state["refusal_stage"] = 0
            current_refusal_stage = 0

    # BUG2 FIX: If stage==1 (we asked "why are you refusing?") and the customer is no longer
    # disinterested (they changed topic or asked a normal question), exit the refusal lifecycle.
    if current_refusal_stage == 1 and not last_turn.get("is_disinterested"):
        if last_turn.get("intent") not in ("DISINTEREST", "COMPLAINT", "OTHER", None):
            new_state["refusal_stage"] = 0
            current_refusal_stage = 0

    is_refusal_or_complaint = (
        last_turn.get("is_disinterested")
        or last_turn.get("is_complaint_or_refund")
        or last_turn.get("intent") in ("COMPLAINT", "DISINTEREST")
        or current_refusal_stage > 0
    )

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

    user_asked_links = bool(
        last_turn.get("intent") == "APP_LINK_REQUEST"
        or re.search(r"\b(download link|app link|website link|site link|play\s*store link|app\s*store link)\b", text_clean, re.IGNORECASE)
        or (re.search(r"\b(link|links)\b", text_clean, re.IGNORECASE) and not re.search(r"\b(demo|video|sample|preview|recording)\b", text_clean, re.IGNORECASE))
    )
    prev_ai_demo = False
    if history:
        for h_turn in reversed(history):
            if h_turn.get("role") == "assistant":
                p_text = (h_turn.get("content") or "").lower()
                if any(w in p_text for w in ["demo video", "demo", "sample video", "instructor's demo"]):
                    prev_ai_demo = True
                break

    user_asked_demo = bool(
        re.search(r"\b(demo|video|videos|recording|recordings|sample|preview)\b", text_clean, re.IGNORECASE)
        or "demo" in (last_turn.get("question_summary") or "").lower()
        or last_turn.get("wants_resend_video")
        or prev_ai_demo
        or any(d in main_reply.lower() for d in ("youtube.com", "youtu.be", "instagram.com"))
        or bool(re.search(r"\b(demo videos?|teaching styles?|instructor videos?|sample videos?)\b", main_reply, re.IGNORECASE))
    )
    # Dynamic Media Configuration for Trial and App Download/Install
    active_cid = active_course or state.get("course") or ""
    course_data_active = kb_loader.get_course_data(active_cid)
    wants_resend = bool(last_turn.get("wants_resend_video", False))

    user_wants_app_guide = bool(
        last_turn.get("wants_app_guide")
        or last_turn.get("wants_app_video_guide")
    )
    user_wants_trial_guide = bool(
        last_turn.get("wants_trial_guide")
        or last_turn.get("wants_trial_video_guide")
    )

    # General resend resolution if customer asked to resend video without specifying type
    if wants_resend and not user_wants_app_guide and not user_wants_trial_guide:
        if last_turn.get("wants_trial") or new_state.get("wants_trial") or new_state.get("stage") == "TRIAL_REQUESTED":
            user_wants_trial_guide = True
        else:
            user_wants_app_guide = True

    # Teacher demo requests must NEVER trigger tutorial guide dispatch
    if user_asked_demo:
        wants_resend = False
        user_wants_app_guide = False
        user_wants_trial_guide = False

    is_trial_booking_guide = bool(
        re.search(r"\b(step|download the sensationz app|profile then tap|trial links|confirm trial)\b", main_reply, re.IGNORECASE)
    )
    is_trial_turn = bool(
        user_wants_trial_guide
        or (last_turn.get("wants_trial") and (is_trial_booking_guide or not user_asked_demo) and not new_state.get("package"))
        or (new_state.get("stage") == "TRIAL_REQUESTED" and not new_state.get("trial_video_sent"))
        or re.search(r"\b(trial links|confirm trial|trial enrollment|how to book\s+trial|book\s+a\s+trial)\b", main_reply, re.IGNORECASE)
    )

    will_send_trial_video = bool(
        not user_wants_app_guide and not user_asked_demo and (
            user_wants_trial_guide
            or (is_trial_turn and (is_trial_booking_guide or wants_resend) and (wants_resend or not new_state.get("trial_video_sent")))
        )
    )
    will_send_install_video = bool(
        not user_asked_demo and (
            user_wants_app_guide
            or (not will_send_trial_video and (
                (new_state.get("stage") == "APP_LINK_SENT" and wants_resend)
                or (wants_resend and not new_state.get("app_download_video_sent"))
            ))
        )
    )
    sending_video = will_send_trial_video or will_send_install_video

    # App links are ONLY allowed when customer explicitly asked for links or clicked Lock My Seat
    allow_app_links = bool(user_asked_links or "lock my seat" in text_clean.lower())

    is_client_paid = bool(
        new_state.get("payment_status") == "PAID"
        or new_state.get("is_enrolled")
        or new_state.get("stage") == "ENROLLED"
    )
    if is_client_paid:
        new_state["payment_claimed"] = False

    clean_reply = _strip_trailing_question(_format_for_whatsapp(main_reply), followup=followup_msg) if followup_msg else _format_for_whatsapp(main_reply)
    clean_reply, clean_followup = _sanitize_confirmation_guardrails(
        clean_reply, followup_msg, language=new_state.get("language"), state=new_state
    )
    clean_reply = _ensure_app_website_links(clean_reply, user_asked_for_links=allow_app_links, allow_demo_links=user_asked_demo)

    if clean_followup:
        clean_followup = _format_for_whatsapp(clean_followup)
        clean_followup = _ensure_app_website_links(clean_followup, user_asked_for_links=False, allow_demo_links=user_asked_demo)

    # Strict Zero Follow-up on Payment Claim (only for unverified/unpaid users)
    if not is_client_paid and (new_state.get("payment_claimed") or last_turn.get("claims_payment_or_enrollment") or last_turn.get("intent") == "PAYMENT_CLAIM"):
        new_state["payment_claimed"] = True
        clean_followup = None
        result["buttons"] = []

    # Anti-redundancy guarantee: If links were already delivered in Message #1, do not redundantly ask if they want links in followup
    if clean_followup and ("play.google.com" in clean_reply or "sensationzperformingarts.com" in clean_reply):
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
    if will_send_trial_video:
        trial_cfg = (
            course_data_active.get("trial_classes")
            or course_data_active.get("trial_class")
            or kb_loader.get_global_rules().get("universal_course_policies", {}).get("trial_classes", {})
        )
        await dispatch_media_guide_async(phone, new_state, trial_cfg, "trial_video_sent", force_resend=bool(user_wants_trial_guide or wants_resend))
    elif will_send_install_video:
        enroll_cfg = (
            course_data_active.get("universal_enrollment_flow")
            or kb_loader.get_global_rules().get("universal_enrollment_flow", {})
        )
        await dispatch_media_guide_async(phone, new_state, enroll_cfg, "app_download_video_sent", force_resend=bool(user_wants_app_guide or wants_resend))

    # Subtask 5.3: Dispatch dynamic Razorpay UPI QR code when every detail is selected or user requests payment
    user_asked_qr = bool(
        re.search(r"\b(qr|qr code|scanner|scan|upi id|payment link|pay link|send link|bhejo qr|scanner bhejo|pay online|payment qr|how to pay|where to pay)\b", text_clean, re.IGNORECASE)
    )
    is_package_selected = bool(new_state.get("package") or new_state.get("selected_duration"))
    has_timing_selected = bool(new_state.get("timing") or new_state.get("selected_timing"))

    # Check if the assistant is still asking the customer for timing or teacher details
    is_asking_details = bool(
        re.search(
            r"\b(batch timing|which timing|which batch|konsa batch|konsi timing|select a batch|selecting a batch|select timing|which instructor|which teacher|prefer for your classes)\b",
            f"{clean_reply} {clean_followup or ''}",
            re.IGNORECASE,
        )
    )

    # Ready for payment: either user explicitly asks for QR, OR all details (package + timing) are selected and not currently asking details
    all_details_selected = bool(is_package_selected and has_timing_selected and not is_asking_details)

    is_client_paid = bool(
        new_state.get("payment_status") == "PAID"
        or new_state.get("is_enrolled")
        or new_state.get("stage") == "ENROLLED"
    )

    is_payment_step = bool(
        not is_client_paid
        and is_package_selected
        and not new_state.get("payment_claimed")
        and (
            user_asked_qr
            or (all_details_selected and (
                new_state.get("stage") in ["PACKAGE_SELECTED", "TIMING_SELECTED", "TEACHER_SELECTED", "COUPON_SENT", "PAYMENT_PENDING", "CHECKOUT"]
                or last_turn.get("intent") in ["PACKAGE_SELECTION", "TIMING_SELECTION", "TEACHER_SELECTION", "PAYMENT_REQUEST"]
                or re.search(r"\b(pay online|complete checkout|payment link|fee breakdown|total payable|scan & pay|scan and pay)\b", clean_reply, re.IGNORECASE)
            ))
        )
    )
    if is_payment_step and not will_send_trial_video:
        await dispatch_payment_qr_async(
            phone,
            new_state,
            course_id=active_cid,
            package=new_state.get("package") or new_state.get("selected_duration"),
            force_resend=user_asked_qr,
        )

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
    final_buttons = []
    if clean_followup and clean_followup.strip() and not new_state.get("payment_claimed"):
        await asyncio.sleep(2.5)
        raw_buttons = result.get("buttons") or []
        final_buttons = adapt_contextual_quick_replies(
            state=new_state,
            course_id=active_cid,
            raw_buttons=raw_buttons,
            language=new_state.get("language"),
            followup=clean_followup,
        )
        try:
            from src.services.quick_reply_service import send_followup_with_buttons_async
            await send_followup_with_buttons_async(phone, clean_followup, buttons=final_buttons)
        except ImportError:
            await send_text_message_async(phone, clean_followup)
        await save_message_async(phone, "assistant", clean_followup)
        asyncio.create_task(log_message_async(phone, "ai", clean_followup))

    # Persist updated session state
    if (
        not new_state.get("payment_claimed")
        and not new_state.get("is_escalated")
        and not new_state.get("_last_turn", {}).get("is_disinterested")
    ):
        arm_followup_timer(new_state, topic=text_clean)
    else:
        reset_follow_up_timer(new_state)
    await save_user_state_async(phone, new_state)

    final_buttons_ret = [] if new_state.get("payment_claimed") else (final_buttons or result.get("buttons", []))
    return {"status": "answered", "reply": clean_reply, "followup": clean_followup, "buttons": final_buttons_ret}