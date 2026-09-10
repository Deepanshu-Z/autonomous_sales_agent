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

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from src.core import intent_classifier
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
    new_state = intent_classifier.apply_extraction_to_state(session_state, flags)

    # A customer can switch topics mid-conversation ("what about Kathak
    # timings?" or "face yoga k coupons") — if they did, that becomes the active course for THIS
    # reply's primary KB section.
    from src.core import kb_loader

    # Dynamic course switch: if customer mentions any course from the catalog, switch to it for this turn
    course_in_msg = kb_loader.detect_course_from_text(user_message, default_course=None) if user_message else None
    effective_course_id = course_in_msg or new_state.get("course") or session_state.get("course") or course_id or ""

    if not effective_course_id and conversation_history:
        hist_text = " ".join((t.get("content") or "") for t in conversation_history[-10:])
        detected_c = kb_loader.detect_course_from_text(hist_text, default_course=None)
        if detected_c:
            effective_course_id = detected_c

    if effective_course_id:
        entry = kb_loader.get_catalog_entry(effective_course_id)
        if entry:
            effective_course_id = entry["id"]
        new_state["course"] = effective_course_id
        session_state["course"] = effective_course_id

    # 3. Build the system prompt with the freshly-understood state.
    system_prompt = prompt_module.format_system_prompt(new_state, course_id=effective_course_id)

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
    if new_state.get("profile_created") and new_state.get("stage") == "PROFILE_COMPLETED":
        new_state["stage"] = "COUPON_SENT"
        new_state["coupon_sent"] = True

    return {"reply": main_reply, "followup": followup_msg, "new_state": new_state}


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


def _strip_trailing_question(text: str) -> str:
    """If the main reply accidentally ended with a trailing question line,
    strip it so Message 1 remains purely informative without duplicating
    the dedicated follow-up question sent as Message 2."""
    if not text:
        return text
    lines = text.strip().split("\n")
    if len(lines) > 1 and lines[-1].strip().endswith("?"):
        return "\n".join(lines[:-1]).strip()
    return text.strip()


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


def _ensure_app_website_links(text: str) -> str:
    """Guarantees that whenever app or website is mentioned, official links are present."""
    if not text:
        return text
    t_lower = text.lower()
    has_links = (
        "play.google.com" in t_lower
        or "shop.sensationzperformingarts.com" in t_lower
        or "apps.apple.com" in t_lower
    )
    if has_links:
        return text
    mentions_platform = bool(
        re.search(r"\b(app|apps|application|website|web site|portal|platform|platforms|playstore|play store|appstore|app store)\b", t_lower)
    )
    if mentions_platform:
        return f"{text.rstrip()}\n\n{OFFICIAL_PLATFORM_LINKS_BLOCK}"
    return text


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
    cmd = text_clean.lower()

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
    # When the customer declines or expresses disinterest, route through the
    # refusal engine so the reply is empathetic and either asks for the reason,
    # offers a knowledge-backed solution, or escalates to a human — instead of a
    # generic "nice bot" answer.
    if last_turn.get("is_disinterested"):
        eval_res = await evaluate_refusal_async(
            text_clean, history=history or [], state=new_state
        )
        resolution = (eval_res or {}).get("resolution_type", "NOT_A_REFUSAL")
        refusal_msg = (eval_res or {}).get("reply_message")
        if refusal_msg:
            reply_text = refusal_msg
            clean_reply = _format_for_whatsapp(reply_text)
            # Escalate when the concern is unresolvable or carries legal/medical risk.
            if resolution == "ESCALATE_TO_AGENT":
                await execute_agent_handoff_async(
                    phone,
                    user_reply_text=clean_reply,
                    escalation_reason=(eval_res or {}).get("escalation_reason")
                    or f"Refusal with unresolvable/risk concern: {text_clean[:50]}",
                    start_time=start_time,
                )
                await send_text_message_async(phone, clean_reply)
                latency_sec = round(time.time() - start_time, 2)
                await save_user_state_async(phone, new_state)
                asyncio.create_task(save_message_async(phone, "assistant", clean_reply, response_time_sec=latency_sec))
                asyncio.create_task(log_message_async(phone, "ai", clean_reply))
                return {"status": "handed_off_refusal", "reply": clean_reply}

    # ── Separate Message #1 and Message #2 (Strict JSON with fallback) ───────
    main_reply = reply_text
    if "[FOLLOW_UP]" in main_reply:
        parts = main_reply.split("[FOLLOW_UP]", 1)
        main_reply = parts[0].strip()
        if not followup_msg:
            followup_msg = parts[1].strip() or None

    clean_reply = _strip_trailing_question(_format_for_whatsapp(main_reply)) if followup_msg else _format_for_whatsapp(main_reply)
    clean_reply = _ensure_app_website_links(clean_reply)
    clean_followup = _format_for_whatsapp(followup_msg) if followup_msg else None
    if clean_followup and "play.google.com" not in clean_reply:
        clean_followup = _ensure_app_website_links(clean_followup)

    # Deliver main reply to customer on WhatsApp (Message 1)
    if clean_reply and clean_reply.strip():
        await send_text_message_async(phone, clean_reply)
        latency_sec = round(time.time() - start_time, 2)
        await save_message_async(phone, "assistant", clean_reply, response_time_sec=latency_sec)
        asyncio.create_task(log_message_async(phone, "ai", clean_reply))

    # Deliver the follow-up as a whole different message (Message 2) after a brief pause
    if clean_followup and clean_followup.strip():
        await asyncio.sleep(2.5)
        await send_text_message_async(phone, clean_followup)
        await save_message_async(phone, "assistant", clean_followup)
        asyncio.create_task(log_message_async(phone, "ai", clean_followup))

    # Persist updated session state
    arm_followup_timer(new_state, topic=text_clean)
    await save_user_state_async(phone, new_state)

    return {"status": "answered", "reply": clean_reply, "followup": clean_followup}