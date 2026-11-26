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
    mark_escalated,
    arm_followup_timer,
    reset_follow_up_timer,
    phone_concurrency_lock,
    track_sent_qr,
)
from src.state.payment_status import is_payment_verified

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


def adapt_contextual_quick_replies(*args, **kwargs) -> List[str]:
    """Buttons disabled: Returns empty list."""
    return []


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

    # Free trials are discontinued: trial interest becomes a 1 Week Pass request.
    from src.core.pricing_engine import route_week_pass, apply_week_pass
    flags = route_week_pass(flags)

    # 2. Pure state merge.
    flags["_raw_text"] = user_message  # injected for deterministic language override
    new_state = intent_classifier.apply_extraction_to_state(session_state, flags)
    apply_week_pass(new_state, flags)
    session_state["awaiting_trial_email"] = False  # legacy in-flight trial session

    # ── Payment claims: Razorpay is the ONLY source of truth ──────────────────
    # A client saying "I paid" never makes them PAID and never reaches the LLM. We look for a
    # captured payment on a QR we sent them; if there is none, a FIXED reply goes out, so the
    # answer is identical every time and cannot be argued into a false confirmation.
    phone_key = session_state.get("phone") or new_state.get("phone") or ""
    if _is_payment_claim(flags) and not is_payment_verified(new_state):
        from src.services.razorpay_service import reconcile_claimed_payment_async
        is_paid_now, verified_state = (
            await reconcile_claimed_payment_async(phone_key, new_state) if phone_key else (False, new_state)
        )
        if is_paid_now:
            new_state = verified_state  # verified by Razorpay: normal flow confirms it below
            session_state.update(verified_state)
        else:
            new_state["payment_claimed"] = True
            return {
                "reply": build_payment_not_received_reply(new_state.get("language") or session_state.get("language")),
                "followup": None,
                "buttons": [],
                "new_state": new_state,
            }

    # A customer can switch courses mid-conversation ("what about Kathak timings?").
    # The LLM extractor decides that (selected_course -> new_state["course"]); no keyword switching here.
    from src.core import kb_loader

    old_course = session_state.get("course") or course_id or ""
    effective_course_id = new_state.get("course") or old_course or ""

    if effective_course_id:
        entry = kb_loader.get_catalog_entry(effective_course_id)
        if entry:
            effective_course_id = entry["id"]
        new_state["course"] = effective_course_id
        session_state["course"] = effective_course_id
        # When switching to a different course, clear old course-specific timings and packages
        old_entry = kb_loader.get_catalog_entry(old_course) or kb_loader.get_catalog_entry(kb_loader.detect_course_from_text(old_course) or "")
        norm_old_course = old_entry["id"] if old_entry else old_course
        if norm_old_course and norm_old_course != effective_course_id:
            # Keep every verified paid enrollment, per course: asking about another course must never erase it,
            # and coming back to the paid course restores it (otherwise the customer would be pitched again).
            _PAID_KEYS = (
                "payment_status", "is_enrolled", "stage", "paid_amount", "payment_id", "order_id", "qr_id",
                "timing", "selected_timing", "teacher", "selected_teacher", "days", "selected_days", "days_code",
                "package", "selected_duration", "fee", "student_email", "staged_enrollment", "staged_id",
                "last_qr_package", "last_qr_amount", "payment_claimed",
            )
            paid_book = dict(session_state.get("paid_enrollments") or {})
            if is_payment_verified(session_state):
                paid_book[norm_old_course] = {k: session_state.get(k) for k in _PAID_KEYS}
            new_state["paid_enrollments"] = paid_book
            session_state["paid_enrollments"] = paid_book
            new_state["timing"] = None
            new_state["selected_timing"] = None
            new_state["timing_period"] = None
            new_state["teacher"] = None
            new_state["selected_teacher"] = None
            new_state["package"] = None
            new_state["selected_duration"] = None
            new_state["days"] = None
            new_state["selected_days"] = None
            new_state["days_code"] = None
            new_state["auto_assigned_slot_note"] = None
            new_state["teacher_timing_conflict"] = None
            new_state["fee"] = None
            new_state["base_fee"] = None
            new_state["offer_fee"] = None
            new_state["gst_amount"] = None
            new_state["gst_percentage"] = None
            session_state["timing"] = None
            session_state["selected_timing"] = None
            session_state["timing_period"] = None
            session_state["teacher"] = None
            session_state["selected_teacher"] = None
            session_state["package"] = None
            session_state["selected_duration"] = None
            session_state["days"] = None
            session_state["selected_days"] = None
            session_state["days_code"] = None
            session_state["auto_assigned_slot_note"] = None
            session_state["teacher_timing_conflict"] = None
            session_state["fee"] = None
            session_state["base_fee"] = None
            session_state["offer_fee"] = None
            session_state["gst_amount"] = None
            session_state["gst_percentage"] = None

            if paid_book.get(effective_course_id):
                # Back on a course they already paid for: restore the enrollment exactly as it was.
                for _k, _v in paid_book[effective_course_id].items():
                    new_state[_k] = _v
                    session_state[_k] = _v
                new_state["paid_course"] = effective_course_id
                session_state["paid_course"] = effective_course_id

            # Course-specific enrollment isolation
            if is_payment_verified(session_state) and not session_state.get("paid_course"):
                session_state["paid_course"] = norm_old_course
                new_state["paid_course"] = norm_old_course
            paid_c = session_state.get("paid_course")
            if paid_c and paid_c != effective_course_id:
                new_state["payment_status"] = "NONE"
                new_state["stage"] = "NEW"
                new_state["is_enrolled"] = False
                session_state["payment_status"] = "NONE"
                session_state["stage"] = "NEW"
                session_state["is_enrolled"] = False

    apply_week_pass(new_state, flags)  # again: the course-switch reset above wipes the package (idempotent)

    # ── Gmail Interception & Auto-Staging Pipeline ──────────────────────────
    from src.services.hono_staging_client import clean_email, stage_ai_enrollment_async
    is_awaiting_email = bool(
        new_state.get("awaiting_email_fulfillment")
        or session_state.get("awaiting_email_fulfillment")
        or new_state.get("classes_not_showing")
        or session_state.get("classes_not_showing")
        or (is_payment_verified(new_state) and not (new_state.get("student_email") or session_state.get("student_email")))
        or new_state.get("wants_trial")
        or session_state.get("awaiting_trial_email")
    )
    user_email = clean_email(user_message, is_awaiting_email=is_awaiting_email)
    if user_email and any(inv in user_email for inv in ("none@", "null@", "void@", "test@")):
        user_email = None

    # Captured BEFORE overwriting: the staging checks below used to compare against the
    # already-overwritten value, so a NEW email was never re-staged yet the bot said "Linked".
    prev_email = new_state.get("student_email") or session_state.get("student_email")
    already_staged = bool(
        new_state.get("staged_enrollment") or session_state.get("staged_enrollment")
        or new_state.get("staged_trial") or session_state.get("staged_trial")
    )
    if not user_email and is_awaiting_email and "@" in user_message:
        # Looks like an attempt at an email we couldn't parse safely ("abc def @gmail.com",
        # two addresses, ...). Ask for a clean retype instead of guessing a wrong address.
        lang_r = str(new_state.get("language") or session_state.get("language") or "Hinglish").lower()
        if "english" in lang_r and "hinglish" not in lang_r:
            retype = ("I couldn't read that Gmail ID clearly. 🙏 Please type it again as one word, "
                      "without spaces, e.g. *yourname@gmail.com* 😊")
        else:
            retype = ("Gmail ID clear nahi padh paaye. 🙏 Kripya apna Gmail ID ek baar phir bina space ke "
                      "type karein, jaise *yourname@gmail.com* 😊")
        return {"reply": retype, "followup": None, "buttons": [], "new_state": new_state}

    if user_email and user_email == prev_email and already_staged:
        # Client re-typed the email that is already linked (e.g. "I logged in with x@gmail.com"):
        # don't re-send the "Successfully Linked" template; let the AI answer the actual question.
        user_email = None

    if user_email:
        new_state["student_email"] = user_email
        session_state["student_email"] = user_email

        phone_val = session_state.get("phone") or new_state.get("phone") or ""
        timing_val = new_state.get("timing") or new_state.get("selected_timing")
        teacher_val = new_state.get("selected_teacher") or new_state.get("teacher")
        days_val = new_state.get("days") or new_state.get("selected_days")
        days_code_val = new_state.get("days_code")
        pkg_val = new_state.get("package") or new_state.get("selected_duration") or "1 Month"

        catalog_entry = kb_loader.get_catalog_entry(effective_course_id) if effective_course_id else None
        course_display_name = catalog_entry.get("name", effective_course_id.title()) if catalog_entry else (effective_course_id.title() if effective_course_id else "Dance & Performing Arts")

        lang = (new_state.get("language") or session_state.get("language") or "English").lower()
        is_hindi = "hindi" in lang and "hinglish" not in lang
        is_hinglish = "hinglish" in lang or "roman" in lang

        is_paid_student = bool(
            is_payment_verified(new_state)
            or new_state.get("awaiting_email_fulfillment")
            or session_state.get("awaiting_email_fulfillment")
            or new_state.get("classes_not_showing")
            or session_state.get("classes_not_showing")
        )

        is_trial_candidate = bool(
            not is_paid_student
            and (
                new_state.get("wants_trial")
                or flags.get("wants_trial")
                or new_state.get("stage") in ("TRIAL_REQUESTED", "TRIAL_STEPS_SENT")
                or session_state.get("awaiting_trial_email")
            )
        )

        # ── Path A: Paid Course Auto-Staging upon receiving Gmail ────────────
        if is_paid_student:
            from src.services.payment_ledger import schedule_email_attach
            schedule_email_attach(phone_val, user_email)  # background: Gmail onto their payment rows
            payment_id_val = new_state.get("payment_id") or new_state.get("order_id") or session_state.get("payment_id")
            paid_amt_val = new_state.get("paid_amount") or session_state.get("paid_amount") or 0.0
            staging_successful = bool(new_state.get("staged_enrollment") and prev_email == user_email)

            if not staging_successful:
                stage_payload = {
                    "email": user_email,
                    "contact": phone_val,
                    "course": course_display_name,
                    "timing": str(timing_val) if timing_val else None,
                    "teacher": str(teacher_val) if teacher_val else None,
                    "days": days_val,
                    "days_code": days_code_val,
                    "enrollment_type": "COURSE",
                    "package_duration": pkg_val,
                    "payment_id": payment_id_val,
                    "amount": paid_amt_val,
                    "currency": "INR",
                    "start_date": new_state.get("preferred_start_iso") or session_state.get("preferred_start_iso"),
                }
                # Fast Keep-Alive attempt; if network blips, background retry ensures delivery
                try:
                    staging_res = await stage_ai_enrollment_async(**stage_payload)
                    if staging_res.get("success"):
                        staging_successful = True
                        new_state["staged_enrollment"] = True
                        new_state["staged_id"] = staging_res.get("staged_id") or staging_res.get("profile_id")
                        new_state["awaiting_email_fulfillment"] = False
                        new_state["classes_not_showing"] = False
                        session_state.update(new_state)
                        logger.info(f"[conversation_handler] ✅ Auto-staged paid enrollment for {phone_val} ({user_email})")
                    else:
                        logger.warning(
                            f"[conversation_handler] ⚠️ Immediate staging returned unsuccessful for {phone_val}: "
                            f"{staging_res.get('error') or staging_res.get('message')}. Dispatching background retry."
                        )
                        from src.services.hono_staging_client import trigger_async_staging
                        trigger_async_staging(stage_payload, phone_val)
                        staging_successful = True
                        new_state["staged_enrollment"] = True
                        new_state["awaiting_email_fulfillment"] = False
                        new_state["classes_not_showing"] = False
                        session_state.update(new_state)
                except Exception as exc:
                    logger.error(f"[conversation_handler] Staging exception for paid student: {exc}. Dispatching background retry.")
                    from src.services.hono_staging_client import trigger_async_staging
                    trigger_async_staging(stage_payload, phone_val)
                    staging_successful = True
                    new_state["staged_enrollment"] = True
                    new_state["awaiting_email_fulfillment"] = False
                    new_state["classes_not_showing"] = False
                    session_state.update(new_state)

            if staging_successful:
                if is_hindi:
                    reply_text = (
                        f"🎉 *आपका अकाउंट ऐप से सफलतापूर्वक लिंक हो गया है!* 🎉\n\n"
                        f"नमस्ते! हमने आपका *{course_display_name}* का एडमिशन आपके Gmail (**{user_email}**) से कनेक्ट कर दिया है। 🌟\n\n"
                        f"📋 *आपके बैच का विवरण:*\n"
                        f"• *कोर्स:* {course_display_name} ({pkg_val})\n"
                        f"• *बैच टाइमिंग:* {timing_val or 'As per app schedule'}\n"
                        f"• *टीचर:* {teacher_val or 'Assigned Instructor'}\n\n"
                        f"🚀 *लाइव क्लास शुरू करने के स्टेप्स:*\n"
                        f"1. *Sensationz ऐप डाउनलोड करें:*\n"
                        f"   📱 Android: https://play.google.com/store/apps/details?id=com.sensationz.sensationz.dev\n"
                        f"   🍎 iOS: https://apps.apple.com/us/app/sensationz/id6761418351\n"
                        f"2. ऐप खोलें और **'Continue with Google'** पर टैप करके **{user_email}** से साइन इन करें।\n"
                        f"3. आपका बैच सीधे \"My Classes\" सेक्शन में एक्टिव मिलेगा।\n\n"
                        f"क्लास में मिलते हैं! 🙏✨"
                    )
                    followup_text = "क्या आपको ऐप डाउनलोड करने या लॉगिन करने में कोई सहायता चाहिए? 😊"
                elif is_hinglish:
                    reply_text = (
                        f"🎉 *Aapka Account Successfully Link Ho Gaya Hai!* 🎉\n\n"
                        f"Namaste! Humne aapka *{course_display_name}* admission aapke Gmail (**{user_email}**) se connect kar diya hai. 🌟\n\n"
                        f"📋 *Aapke Batch Ki Details:*\n"
                        f"• *Course:* {course_display_name} ({pkg_val})\n"
                        f"• *Batch Timing:* {timing_val or 'As per app schedule'}\n"
                        f"• *Assigned Teacher:* {teacher_val or 'Assigned Instructor'}\n\n"
                        f"🚀 *Live Class Access Karne Ke Steps:*\n"
                        f"1. *Sensationz App Download Karein:*\n"
                        f"   📱 Android: https://play.google.com/store/apps/details?id=com.sensationz.sensationz.dev\n"
                        f"   🍎 iOS: https://apps.apple.com/us/app/sensationz/id6761418351\n"
                        f"2. App open karein aur **'Continue with Google'** par tap karke **{user_email}** se sign in karein.\n"
                        f"3. Aapka batch sidhe \"My Classes\" section mein active milega.\n\n"
                        f"Class mein milte hain! 🙏✨"
                    )
                    followup_text = "Kya aapko app download karne ya login karne mein koi help chahiye? 😊"
                else:
                    reply_text = (
                        f"🎉 *Your Account is Successfully Linked!* 🎉\n\n"
                        f"We have connected your *{course_display_name}* admission to your Gmail: **{user_email}**. 🌟\n\n"
                        f"📋 *Your Enrolled Batch Details:*\n"
                        f"• *Course:* {course_display_name} ({pkg_val})\n"
                        f"• *Batch Timing:* {timing_val or 'As per app schedule'}\n"
                        f"• *Assigned Teacher:* {teacher_val or 'Assigned Instructor'}\n\n"
                        f"🚀 *Next Steps to Access Your Classes:*\n"
                        f"1. *Download the Sensationz App:*\n"
                        f"   📱 Android: https://play.google.com/store/apps/details?id=com.sensationz.sensationz.dev\n"
                        f"   🍎 iOS: https://apps.apple.com/us/app/sensationz/id6761418351\n"
                        f"2. Open the app, tap **'Continue with Google'**, and select **{user_email}**.\n"
                        f"3. Your enrolled course and live sessions are ready under \"My Classes\"!\n\n"
                        f"We look forward to seeing you in class! 🙏✨"
                    )
                    followup_text = "Would you like any assistance downloading the app or logging in? 😊"
            else:
                if is_hindi:
                    reply_text = (
                        f"✅ *आपकी जानकारी और पेमेंट दर्ज कर ली गई है!* 🌟\n\n"
                        f"हमने आपका Gmail (**{user_email}**) और पेमेंट संदर्भ (**{payment_id_val or 'Verified'}**) रिकॉर्ड कर लिया है।\n"
                        f"हमारी टीम आपका *{course_display_name}* ({pkg_val}) बैच एक्टिवेट कर रही है।\n\n"
                        f"📱 *कृपया Sensationz ऐप डाउनलोड कर लें:*\n"
                        f"• Android: https://play.google.com/store/apps/details?id=com.sensationz.sensationz.dev\n"
                        f"• iOS: https://apps.apple.com/us/app/sensationz/id6761418351\n\n"
                        f"बैच एक्टिव होते ही आपके 'My Classes' सेक्शन में क्लास दिखने लगेगी!"
                    )
                    followup_text = "क्या आपको ऐप डाउनलोड करने में कोई सहायता चाहिए? 😊"
                else:
                    reply_text = (
                        f"✅ *Your Payment & Details Are Registered!* 🌟\n\n"
                        f"We have noted your Gmail (**{user_email}**) and payment reference (**{payment_id_val or 'Verified'}**).\n"
                        f"Our support desk is currently finalizing your batch provisioning for *{course_display_name}* ({pkg_val}).\n\n"
                        f"📱 *Please download the Sensationz App:*\n"
                        f"• Android: https://play.google.com/store/apps/details?id=com.sensationz.sensationz.dev\n"
                        f"• iOS: https://apps.apple.com/us/app/sensationz/id6761418351\n\n"
                        f"Once logged in via Google, your classes will appear under **\"My Classes\"** shortly!"
                    )
                    followup_text = "Would you like any assistance downloading the app? 😊"

            return {
                "reply": reply_text,
                "followup": followup_text,
                "buttons": ["Download App", "Talk to Agent"],
                "new_state": new_state,
            }

        # ── Path B: Free Trial Auto-Staging upon receiving Gmail ─────────────
        elif is_trial_candidate:
            trial_staged_successful = bool(new_state.get("staged_trial") and prev_email == user_email)
            if not trial_staged_successful:
                try:
                    staging_res = await stage_ai_enrollment_async(
                        email=user_email,
                        contact=phone_val,
                        course=course_display_name,
                        timing=str(timing_val) if timing_val else None,
                        teacher=str(teacher_val) if teacher_val else None,
                        days=days_val,
                        days_code=days_code_val,
                        enrollment_type="TRIAL",
                        package_duration="Trial",
                        payment_id=None,
                        amount=0.0,
                        currency="INR",
                    )
                    new_state["stage"] = "TRIAL_STEPS_SENT"
                    new_state["wants_trial"] = True
                    new_state["awaiting_trial_email"] = False
                    if staging_res.get("success"):
                        trial_staged_successful = True
                        new_state["staged_trial"] = True
                        new_state["staged_id"] = staging_res.get("staged_id") or staging_res.get("profile_id")
                        session_state.update(new_state)
                        logger.info(f"[conversation_handler] ✅ Auto-staged trial for {phone_val} ({user_email})")
                    else:
                        logger.warning(
                            f"[conversation_handler] ⚠️ Trial staging returned unsuccessful for {phone_val}: "
                            f"{staging_res.get('error') or staging_res.get('message')}"
                        )
                except Exception as exc:
                    logger.error(f"[conversation_handler] Staging error for trial student: {exc}")

            if is_hindi:
                reply_text = (
                    f"हमने आपका Gmail (**{user_email}**) नोट कर लिया है! 🌟\n\n"
                    f"अपनी *{course_display_name}* की फ्री ट्रायल क्लास का बैच चुनने और क्लास एक्टिवेट करने के लिए:\n"
                    f"1. *Sensationz ऐप डाउनलोड करें:*\n"
                    f"   📱 Android: https://play.google.com/store/apps/details?id=com.sensationz.sensationz.dev\n"
                    f"   🍎 iOS: https://apps.apple.com/us/app/sensationz/id6761418351\n"
                    f"2. ऐप में **'Continue with Google'** पर टैप करके **{user_email}** से साइन इन करें।\n"
                    f"3. **Profile → Trial Links** पर जाएं और **Confirm Trial Enrollment** पर टैप करें।\n\n"
                    f"लाइव ट्रायल बुकिंग पूरी तरह ऐप के अंदर से ही पूरी होती है! 🙏✨"
                )
                followup_text = "क्या आपको ऐप डाउनलोड करने या ट्रायल स्लॉट कन्फर्म करने में कोई सहायता चाहिए? 😊"
            else:
                reply_text = (
                    f"We have noted your Gmail (**{user_email}**)! 🌟\n\n"
                    f"To pick your preferred batch timing and activate your free trial for *{course_display_name}*:\n"
                    f"1. *Download the Sensationz App:*\n"
                    f"   📱 Android: https://play.google.com/store/apps/details?id=com.sensationz.sensationz.dev\n"
                    f"   🍎 iOS: https://apps.apple.com/us/app/sensationz/id6761418351\n"
                    f"2. Tap **'Continue with Google'** and sign in using **{user_email}**.\n"
                    f"3. Go to **Profile → Trial Links** and tap **Confirm Trial Enrollment**.\n\n"
                    f"Live trial batch selection and booking are exclusively completed inside the app! 🙏✨"
                )
                followup_text = "Would you like the app download link or any assistance? 😊"

            return {
                "reply": reply_text,
                "followup": followup_text,
                "buttons": ["Download App", "How to Book", "Talk to Agent"],
                "new_state": new_state,
            }

    # ── Fast-Path Guardrail: Intercept Stop Messaging / Irritation Opt-Out ───
    if flags.get("wants_stop_messaging") or flags.get("customer_intent") == "STOP_MESSAGING":
        logger.info(f"[GUARDRAILS] Customer requested to stop messages: '{user_message}'. Intercepting with polite opt-out.")
        from src.core.kb_loader import format_support_executives_display
        support_contacts = format_support_executives_display()
        detected_lang = flags.get("detected_language") or new_state.get("language") or "Hinglish"
        is_pure_english = "english" in str(detected_lang).lower() and "hinglish" not in str(detected_lang).lower()

        if is_pure_english:
            optout_reply = (
                "We completely understand and respect your request. We have removed your number from our automated messages, "
                "and you will not receive any further messages from us. 🙏\n\n"
                "If you ever have any course-related inquiries in the future, please feel free to reach out directly to our "
                f"Sales & Customer Support team:\n{support_contacts}\n\n"
                "Wishing you the very best!"
            )
        else:
            optout_reply = (
                "Bilkul, hum samajh sakte hain. Humne aapka number automated messages se hata diya hai "
                "aur aage se aapko koi automated messages nahi aayenge. 🙏\n\n"
                "Agar future mein aapko kisi bhi course ya class ke baare mein koi jaankari chahiye ho, "
                f"toh aap directly hamari Sales & Customer Support team se connect kar sakte hain:\n{support_contacts}\n\n"
                "Wishing you the very best!"
            )

        new_state["next_followup_due_at"] = None
        new_state["is_escalated"] = True
        new_state["refusal_stage"] = 3
        new_state["wants_stop_messaging"] = True
        if phone_key:
            mark_escalated(phone_key)
        session_state.update(new_state)

        return {
            "reply": optout_reply,
            "followup": None,
            "buttons": [],
            "new_state": new_state,
        }

    # ── Fast-Path Guardrail (Pillar 2): Intercept Pure Out-of-Scope Queries ───
    is_oos = bool(
        flags.get("is_out_of_scope")
        or flags.get("customer_intent") == "OUT_OF_SCOPE"
    )
    has_academy_signal = bool(
        flags.get("selected_course")
        or flags.get("wants_fee")
        or flags.get("wants_schedule")
        or flags.get("wants_demo")
        or flags.get("wants_certificate")
        or flags.get("wants_teacher_info")
        or flags.get("claims_payment_or_enrollment")
        or flags.get("booking_intent")
        or flags.get("wants_human_agent")
        or flags.get("timing_selected")
        or flags.get("profile_created")
        or flags.get("customer_intent") in (
            "WANTS_FEE", "WANTS_SCHEDULE", "BOOKING_INTENT", "WANTS_DEMO",
            "PAYMENT_CLAIM", "COMPLAINT", "AGENT_REQUEST", "CONFIRMATION"
        )
    )

    if is_oos and not has_academy_signal:
        logger.info(f"[GUARDRAILS] Pure Out-Of-Scope query detected: '{user_message}'. Fast-path deflecting.")
        oos_reply, oos_followup = _generate_out_of_scope_deflection(
            user_message=user_message,
            detected_language=flags.get("detected_language") or new_state.get("language"),
        )
        return {
            "reply": oos_reply,
            "followup": oos_followup,
            "buttons": [],
            "new_state": new_state,
        }

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
    for t in conversation_history[-2:]:
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

    # Strip any accidental coupon codes while preserving offer prices
    main_reply = _enforce_json_coupons(main_reply, new_state, effective_course_id)

    # Sanitize any hallucinated time-of-day period claims (e.g. calling PM batches 'morning timings')
    main_reply = _sanitize_timing_period_hallucinations(main_reply, effective_course_id)

    # Strictly intercept and sanitize any hallucinated confirmation of enrollment, trial, or payment
    main_reply, followup_msg = _sanitize_confirmation_guardrails(
        main_reply, followup_msg, language=detected_lang, state=new_state
    )

    # Strictly intercept and sanitize any out-of-scope code leaks or off-topic generator output
    main_reply, followup_msg = _sanitize_out_of_scope_guardrails(
        reply=main_reply,
        followup=followup_msg,
        user_message=user_message,
        flags=flags,
        language=detected_lang,
    )

    # Strictly sanitize any hallucinated timing selection when timing is ambiguous
    if new_state.get("timing_ambiguous") or flags.get("timing_ambiguous"):
        amb_hr = new_state.get("ambiguous_hour") or flags.get("ambiguous_hour")
        if amb_hr:
            amb_cands = new_state.get("ambiguous_timing_candidates") or flags.get("ambiguous_timing_candidates") or []
            if not amb_cands and effective_course_id:
                try:
                    batches = kb_loader.get_valid_timings(effective_course_id)
                    amb_cands = [b for b in batches if b.startswith(f"{amb_hr}:") or b.startswith(f"0{amb_hr}:")]
                except Exception:
                    pass
            cands_str = " aur ".join(amb_cands) if amb_cands else f"Subah {amb_hr}:00 AM aur Shaam {amb_hr}:00 PM"

            SELECTION_LEAK = re.search(
                r"(?:aapne\s+\d+\s*(?:baje|bje)?\s*(?:ka\s+timing\s+)?select\s+kiya\s+hai|you(?:'ve|\s+have)\s+selected\s+\d+|timing\s+ke\s+liye\s+classes\s+[A-Za-z\s]+\s+ke\s+saath\s+hoti\s+hain)",
                main_reply,
                re.IGNORECASE,
            )
            # Check if the reply leaked an unrelated hour (e.g. quoting 7:00 PM when amb_hr is 5)
            mismatched_hours = []
            try:
                for hr_match in re.findall(r"\b(\d{1,2}):00\s*(?:am|pm)?\b", main_reply, re.IGNORECASE):
                    h_int = int(hr_match)
                    valid_hrs = {int(amb_hr), (int(amb_hr) % 12) + 1}
                    if h_int not in valid_hrs:
                        mismatched_hours.append(h_int)
            except Exception:
                pass

            CROSS_HOUR_LEAK = bool(mismatched_hours)
            if SELECTION_LEAK or CROSS_HOUR_LEAK:
                catalog_entry = kb_loader.get_catalog_entry(effective_course_id)
                c_title = catalog_entry.get("name", effective_course_id.title()) if catalog_entry else (effective_course_id.title() if effective_course_id else "Yoga")
                main_reply = f"{c_title} classes ke liye {amb_hr} baje do batches available hain: {cands_str}."
                followup_msg = "Aap subah ka batch prefer karenge ya shaam ka? 😊"

    # Coupon-sent bookkeeping is a plain state flag, decided from what we
    # KNOW (profile just confirmed + eligible this turn) — never by
    # scanning the model's reply text for the coupon string.
    # [COMMENTED OUT: coupons hidden]
    # if new_state.get("profile_created") and new_state.get("stage") == "PROFILE_COMPLETED":
    #     new_state["stage"] = "COUPON_SENT"
    #     new_state["coupon_sent"] = True

    is_client_paid = is_payment_verified(new_state)

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
    """[COUPONS HIDDEN]: Strips any accidental coupon code mentions while preserving offer prices."""
    if not reply:
        return reply
    # Strip (Coupon: *...*) or with coupon *...*
    reply = re.sub(r"\s*\(?\s*(?:with|using)?\s*coupon:?\s*\*?[A-Z0-9_]{3,15}\*?\s*\)?", "", reply, flags=re.IGNORECASE)
    # Strip standalone known coupon codes
    if course_id:
        from src.core import kb_loader
        coupons = kb_loader.get_course_coupons(course_id)
        for c in coupons.values():
            code = c.get("code")
            if code:
                reply = re.sub(rf"\*?{re.escape(code)}\*?", "", reply, flags=re.IGNORECASE)
    return re.sub(r"[ \t]+", " ", reply).strip()


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

    # Customer explicitly asked for links: preserve existing official links and
    # append any missing entries so a generated reply cannot contain a cropped
    # or partial platform block.
    t_lower = text.lower()
    from src.core import kb_loader
    official_links = kb_loader.format_app_links_display()
    required_domains = ("play.google.com", "apps.apple.com")
    missing_lines = [
        line for line in official_links.splitlines()
        if any(domain in line.lower() and domain not in t_lower for domain in required_domains)
        or ("shop.sensationzperformingarts.com" in line.lower() and "shop.sensationzperformingarts.com" not in t_lower)
    ]
    if missing_lines:
        missing_block = "\n".join(missing_lines)
        return f"{text.rstrip()}\n\n{missing_block}"
    return text


def _remove_native_media_url_lines(text: str, media_urls: Optional[list[str]] = None) -> str:
    """Remove raw media URLs (Cloudinary / MP4 files) from text delivered as native attachments."""
    if not text:
        return text
    urls = [str(url).strip() for url in (media_urls or []) if str(url).strip()]

    lines = []
    for line in text.splitlines():
        has_media_url = any(u in line for u in urls) or ("res.cloudinary.com" in line and ".mp4" in line)
        if has_media_url:
            for u in urls:
                pattern = rf"(?:(?:here|yahan|watch)?\s*[:\-–]?\s*)?{re.escape(u)}[.,]?"
                line = re.sub(pattern, "", line, flags=re.IGNORECASE)
            cloudinary_pattern = r"(?:(?:here|yahan|watch)?\s*[:\-–]?\s*)?https?://res\.cloudinary\.com/[^\s)]+\.mp4[.,]?"
            line = re.sub(cloudinary_pattern, "", line, flags=re.IGNORECASE)

            cleaned_line = line.strip()
            if re.match(r"^[\s•\-*]*(?:live\s+class\s+(?:practice\s+)?video)?[\s:\-–]*$", cleaned_line, re.IGNORECASE):
                continue
        lines.append(line)

    text = "\n".join(lines)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


_APP_ACCESS_STAGES = frozenset({
    "PAYMENT_CONFIRMED",
    "READY_FOR_APP_LINK",
    "ENROLLED",
    "TRIAL_STEPS_SENT",
})

_APP_HELP_REQUEST_RE = re.compile(
    r"(?:\b(?:can't|cannot|couldn't|unable|not able|problem|issue|trouble|difficulty|help)"
    r"\b.{0,40}\b(?:download|install|app|login|log in)\b"
    r"|\b(?:download|install)\s+(?:the\s+)?(?:sensationz\s+)?app\b)",
    re.IGNORECASE,
)

_APP_HELP_OFFER_RE = re.compile(
    r"(?:app\s+download\s+link|help\s+downloading|assistance\s+downloading|"
    r"download(?:ing)?\s+the\s+app|download\s+steps|install(?:ing)?\s+the\s+app)",
    re.IGNORECASE,
)

_APP_HELP_ACCEPTANCES = frozenset({
    "yes", "yes please", "yeah", "yep", "sure", "okay", "ok", "please do",
    "haan", "haan please", "ha", "ji", "ji haan", "send it", "send them",
})


def _recent_assistant_offered_app_help(history: Optional[List[dict]]) -> bool:
    """Recognize a short affirmative reply to the bot's preceding app-help offer."""
    if not history:
        return False
    for turn in reversed(history):
        if turn.get("role") not in ("assistant", "ai"):
            continue
        return bool(_APP_HELP_OFFER_RE.search(str(turn.get("content") or "")))
    return False


def _should_allow_app_links(
    *,
    text: str,
    state: Dict[str, Any],
    last_turn: Dict[str, Any],
    history: Optional[List[dict]],
) -> bool:
    """Return whether this turn is an appropriate place to include official app links.

    Explicit requests always win, including repeat requests. Automatic delivery is
    limited to the first app-access handoff after payment/trial staging, so normal
    sales and support turns do not repeatedly carry the links.
    """
    intent = str(last_turn.get("intent") or last_turn.get("customer_intent") or "").upper()
    explicit_link_request = bool(
        intent == "APP_LINK_REQUEST"
        or last_turn.get("wants_app_link")
        or kb_loader.is_app_link_request_fallback(text)
    )
    if explicit_link_request:
        return True

    app_help_requested = bool(
        last_turn.get("wants_app_guide")
        or last_turn.get("wants_app_video_guide")
        or _APP_HELP_REQUEST_RE.search(text or "")
        or (
            str(text or "").strip().lower() in _APP_HELP_ACCEPTANCES
            and _recent_assistant_offered_app_help(history)
        )
    )
    if app_help_requested:
        return True

    app_access_required = bool(
        is_payment_verified(state)
        or state.get("staged_enrollment")
        or state.get("staged_trial")
        or state.get("stage") in _APP_ACCESS_STAGES
    )
    return app_access_required and not state.get("app_links_sent", False)

# ── Strict Non-Confirmation Guardrails (Payment, Trial, Enrollment) ───────────
# The AI MUST NEVER confirm any enrollment, free trial booking, or payment under any circumstances.

# Last-line safety net on LLM output (claims are normally answered by a fixed reply before the
# LLM runs). Up to 4 filler words are allowed between subject and verb, because
# "payment ALREADY received" / "payment SUCCESSFULLY verified" slipped past the strict patterns.
_GAP = r"(?:\W+\w+){0,4}?\W+"
_PAYMENT_CONFIRMATION_PATTERNS = [
    # Latin / Hinglish / English: "<payment word> ... <confirmed verb>"
    rf"\b(?:payment|paise|fees?|amount|transaction|screenshot|slip){_GAP}(?:received|receive\s*ho|verified|verify\s*ho|confirmed|confirm\s*ho|successful|approved|mil\s*(?:gay|chuk)|aa\s*gay|check\s*ho\s*gay|dekh\s*li)",
    r"\b(?:we|i)(?:\s+have|'ve)?\s+(?:already\s+)?(?:received|got)\s+(?:your\s+)?(?:payment|fees?|amount|money|screenshot)\b",
    r"\b(?:payment|transaction)\s+(?:was|is)\s+successful\b",
    # Devanagari
    r"(?:पेमेंट|पैसे|फीस)(?:\s+\S+){0,4}?\s*(?:मिल\s*ग[यायीएई]|प्राप्त\s*हो\s*ग[यायीएई]|वेरिफाई\s*हो\s*ग[यायीएई]|कन्फर्म\s*हो\s*ग[यायीएई])",
]
# Not a confirmation when a condition comes right BEFORE the match ("once your payment is
# received") or a negation / future sits INSIDE or right AFTER it ("payment has not been
# received", "payment receive hote hi", "payment will be verified").
_CONDITION_BEFORE = re.compile(r"\b(?:once|after|when|if|until|jaise\s+hi|jab|agar)\b|जैसे\s+ही|जब|अगर", re.IGNORECASE)
_NEGATION_OR_FUTURE_AFTER = re.compile(
    r"\bnot\b|n't|\bnahi\b|\bnahin\b|नहीं|hote\s+hi|hone\s+(?:par|ke)|ke\s+baad|will\s+be|होते\s+ही|के\s+बाद",
    re.IGNORECASE,
)


def _contains_false_payment_confirmation(text: str) -> bool:
    """True if the text states a payment as received / verified (conditional or negated mentions ignored)."""
    for pattern in _PAYMENT_CONFIRMATION_PATTERNS:
        for match in re.finditer(pattern, text or "", re.IGNORECASE):
            before = text[max(0, match.start() - 40):match.start()]
            span_and_after = text[match.start():match.end() + 20]
            if _CONDITION_BEFORE.search(before) or _NEGATION_OR_FUTURE_AFTER.search(span_and_after):
                continue
            return True
    return False


def _is_payment_claim(flags: dict) -> bool:
    """Client says they already paid / asks to confirm payment (NOT asking how to pay)."""
    is_asking_to_pay = bool(
        flags.get("requests_qr")
        or flags.get("intent") == "PAYMENT_REQUEST"
        or flags.get("customer_intent") == "PAYMENT_REQUEST"
    )
    claims_paid = bool(
        flags.get("claims_payment_or_enrollment")
        or flags.get("intent") == "PAYMENT_CLAIM"
        or flags.get("customer_intent") == "PAYMENT_CLAIM"
    )
    return claims_paid and not is_asking_to_pay


def build_payment_not_received_reply(language: Optional[str]) -> str:
    """The ONE fixed answer for an unverified payment claim (same text every time)."""
    lang = str(language or "Hinglish").lower()
    if "english" in lang and "hinglish" not in lang:
        return (
            "We haven't received a payment confirmation from our payment gateway (Razorpay) for you yet. ⏳\n\n"
            "Payments made through the QR code we sent you are confirmed here automatically, usually within a few "
            "minutes, and you'll get a confirmation message as soon as it arrives.\n\n"
            "If the amount was already deducted, please share the UPI transaction ID (12-digit UTR) "
            "or type *agent* and our team will check it for you. 🙏"
        )
    if "hindi" in lang and "hinglish" not in lang:
        return (
            "हमें अभी तक हमारे पेमेंट गेटवे (Razorpay) से आपके पेमेंट का कन्फर्मेशन नहीं मिला है। ⏳\n\n"
            "हमारे भेजे गए QR कोड से किया गया पेमेंट कुछ ही मिनटों में अपने आप कन्फर्म हो जाता है, "
            "और कन्फर्म होते ही आपको यहां मैसेज मिल जाएगा।\n\n"
            "अगर आपके खाते से राशि कट गई है, तो कृपया UPI ट्रांजेक्शन ID (12 अंकों का UTR) भेजें "
            "या *agent* टाइप करें, हमारी टीम चेक कर देगी। 🙏"
        )
    return (
        "Abhi tak humare payment gateway (Razorpay) se aapke payment ka confirmation nahi aaya hai. ⏳\n\n"
        "Humare bheje gaye QR code se kiya gaya payment kuch hi minutes mein automatically confirm ho jata hai, "
        "aur confirm hote hi aapko yahan message mil jayega.\n\n"
        "Agar aapke account se paise kat gaye hain, toh kripya UPI transaction ID (12-digit UTR) bhejein "
        "ya *agent* type karein, humari team check kar degi. 🙏"
    )

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

    is_client_paid = is_payment_verified(state or {})
    is_trial_confirmed = bool(
        state and (
            state.get("staged_trial")
            or state.get("stage") == "TRIAL_CONFIRMED"
        )
    )

    full_check = f"{reply} {followup or ''}"
    has_devanagari = any(ord(c) >= 0x0900 and ord(c) <= 0x097F for c in full_check)
    is_english = bool(language and "english" in language.lower() and "hinglish" not in language.lower())

    # 1. Check Payment Confirmation (only for unverified/unpaid users)
    if not is_client_paid and _contains_false_payment_confirmation(full_check):
        # Should never fire now that claims get a fixed reply before the LLM runs. If it does,
        # something upstream regressed: grep logs for [PAYMENT_GUARDRAIL].
        logger.error(f"[PAYMENT_GUARDRAIL] Blocked unverified payment confirmation. Raw reply: {full_check[:160]!r}")
        reply_language = "Hindi" if has_devanagari else ("English" if is_english else "Hinglish")
        return build_payment_not_received_reply(reply_language), None

    # 2. Check Free Trial Confirmation (only if NOT verified/staged in session state)
    if not is_trial_confirmed and any(re.search(p, full_check, re.IGNORECASE) for p in _TRIAL_CONFIRMATION_PATTERNS):
        logger.warning(f"[guardrails] Trial confirmation intercepted and sanitized. Raw reply: {reply[:80]}...")
        if has_devanagari:
            sanitized_reply = (
                "कृपया ध्यान दें: Sensationz में अब फ्री ट्रायल क्लास उपलब्ध नहीं हैं, और व्हाट्सएप चैट पर AI कोई क्लास बुक या कन्फर्म नहीं कर सकती। "
                "आपकी सीट पेमेंट कन्फर्म होने के बाद ही एक्टिव होती है। 😊"
            )
            sanitized_followup = "क्या आपको बैच टाइमिंग या टीचर चुनने में कोई मदद चाहिए? 😊"
        elif is_english:
            sanitized_reply = (
                "Please note that Sensationz no longer offers free trial classes, and the AI cannot book or confirm any class over chat. "
                "Your seat is activated only after your payment is confirmed. 😊"
            )
            sanitized_followup = "Would you like any help choosing your batch timing or instructor? 😊"
        else:
            sanitized_reply = (
                "Dhyan dein ki Sensationz mein ab free trial classes available nahi hain, aur WhatsApp chat par AI koi class book ya confirm nahi kar sakta. "
                "Aapki seat payment confirm hone ke baad hi active hoti hai. 😊"
            )
            sanitized_followup = "Kya aapko batch timing ya teacher select karne mein koi madad chahiye? 😊"
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

    # 4. Check Gmail Account Linking Confirmation (prevent hallucinated linking when email not verified/staged)
    is_account_staged = bool(
        state and (
            state.get("staged_enrollment") or state.get("staged_trial")
        ) and (state.get("student_email") or state.get("email"))
    )
    _GMAIL_LINKED_PATTERNS = [
        r"\b(?:link\s*(?:kar\s*diya|ho\s*gaya|ho\s*chuka)|successfully\s*linked|account\s*(?:is\s*)?linked|gmail\s*(?:id\s*)?link)\b",
        r"\b(?:लिंक\s*(?:कर\s*दिया|हो\s*गया)|अकाउंट\s*लिंक)\b",
    ]
    if not is_account_staged and any(re.search(p, full_check, re.IGNORECASE) for p in _GMAIL_LINKED_PATTERNS):
        logger.warning(f"[guardrails] Fake Gmail linking intercepted and sanitized. Raw reply: {reply[:80]}...")
        if has_devanagari:
            sanitized_reply = (
                "Google Sign-In के लिए पूरा Gmail ID होना ज़रूरी है जिसमें @gmail.com लगा हो (जैसे xyz@gmail.com)। "
                "कृपया अपना पूरा Gmail एड्रेस यहां चैट में भेजें ताकि सिस्टम आपकी लाइव क्लासेज को ऐप में तुरंत एक्टिवेट कर सके। 😊"
            )
            sanitized_followup = "क्या आप अपना पूरा Gmail ID (उदा. yourname@gmail.com) शेयर कर सकते हैं? 😊"
        elif is_english:
            sanitized_reply = (
                "To link your classes with Google Sign-In on the Sensationz App, a full Gmail address ending with @gmail.com is required. "
                "Please share your complete Gmail address here in chat so our system can immediately connect your batch. 😊"
            )
            sanitized_followup = "Could you please provide your full Gmail address (e.g. yourname@gmail.com)? 😊"
        else:
            sanitized_reply = (
                "Google Sign-In ke liye pura Gmail ID zaroori hota hai jisme @gmail.com laga ho (jaise xyz@gmail.com). "
                "Kripya apna complete Gmail address yahan chat mein bhejein taaki system aapki live classes ko app mein turant activate kar sake. 😊"
            )
            sanitized_followup = "Kya aap apna pura Gmail address (e.g. yourname@gmail.com) share kar sakte hain? 😊"
        return sanitized_reply, sanitized_followup

    return reply, followup


def _generate_out_of_scope_deflection(
    user_message: str = "",
    detected_language: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """
    Returns a polite, brand-safe deflection for any out-of-scope / off-topic user inquiry,
    strictly respecting the customer's language and script.
    """
    msg = user_message or ""
    # Check for Devanagari script (Hindi, Marathi, etc.)
    has_devanagari = any("\u0900" <= c <= "\u097f" for c in msg)

    # Check for pure English
    lang = (detected_language or "").lower()
    is_pure_english = (
        ("english" in lang or lang == "en")
        and "hinglish" not in lang
        and not has_devanagari
        and not re.search(
            r"\b(?:kya|hai|hain|mujhe|mjhe|bhai|bhaiya|sir|mam|chahiye|chaiye|hlo|krdo|kardo|dedo|batao|karo|raat|subah|shaam|dopahar|kaise|nahi|nhi)\b",
            msg,
            re.IGNORECASE,
        )
    )

    if has_devanagari:
        main_reply = (
            "मैं सेंसेशनज़ एकेडमी (Sensationz Academy) का डिजिटल असिस्टेंट हूँ। "
            "मैं केवल हमारे कोर्सेस (डांस, योग, कत्थक, वेस्टर्न डांस, ज़ुम्बा, म्यूज़िक, गिटार, सिंगिंग, एक्टिंग, ड्रॉइंग), "
            "बैच टाइमिंग्स, फीस और एडमिशन से जुड़ी जानकारी में आपकी मदद कर सकता हूँ। 😊"
        )
        followup = "क्या आप हमारे किसी कोर्स या क्लास टाइमिंग्स के बारे में जानना चाहेंगे? 😊"
    elif is_pure_english:
        main_reply = (
            "I am the digital assistant for Sensationz Academy. "
            "I can only assist with information regarding our academy courses "
            "(Dance, Yoga, Kathak, Western Dance, Zumba, Music, Guitar, Singing, Acting, Drawing), "
            "schedules, fees, and admissions. 😊"
        )
        followup = "Would you like to know more about our courses or batch timings? 😊"
    else:
        # Default to polite Hinglish
        main_reply = (
            "Main Sensationz Academy ka digital assistant hoon. "
            "Main sirf hamare courses (Dance, Yoga, Kathak, Western Dance, Zumba, Music, Guitar, Singing, Acting, Drawing), "
            "batch timings, fees aur admissions se judi jaankari mein aapki madad kar sakta hoon. 😊"
        )
        followup = "Kya aap hamare kisi course ya batch timings ke baare mein jaanna chahenge? 😊"

    return main_reply, followup


def _sanitize_out_of_scope_guardrails(
    reply: str,
    followup: Optional[str] = None,
    user_message: str = "",
    flags: Optional[Dict[str, Any]] = None,
    language: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """
    Deterministic Fail-Safe Guardrail (Pillar 4):
    Catches and intercepts any leaked code blocks, programming scripts, math derivations,
    or off-topic answers in LLM output before it ever reaches the user.
    """
    if not reply:
        return reply, followup

    # Pattern 1: Markdown code fences with programming language tags or triple backticks with code
    CODE_FENCE_REGEX = re.search(
        r"```(?:python|javascript|js|typescript|ts|html|css|cpp|c|java|bash|sh|php|ruby|go|sql|json)?\s*[\r\n]+[\s\S]*?```",
        reply,
        re.IGNORECASE,
    )

    # Pattern 2: Raw programming statements / function definitions / log statements
    CODE_SYNTAX_REGEX = re.search(
        r"(?:\bprint\s*\(|console\.log\s*\(|\bdef\s+[a-zA-Z_]\w*\s*\(|\bfunction\s*\w*\s*\(|\bvar\s+\w+\s*=|\blet\s+\w+\s*=|\bconst\s+\w+\s*=|\bpublic\s+static\s+void\s+main|<!DOCTYPE\s+html>|import\s+(?:numpy|pandas|sys|os|react|express)\b)",
        reply,
        re.IGNORECASE,
    )

    # Pattern 3: Off-topic topic leakage if flagged out of scope
    is_oos_flagged = bool(flags and (flags.get("is_out_of_scope") or flags.get("customer_intent") == "OUT_OF_SCOPE"))
    mentions_academy = bool(re.search(r"\b(?:sensationz|academy|course|batch|class|timing|admission|fee)\b", reply, re.IGNORECASE))

    has_code_leak = bool(CODE_FENCE_REGEX or CODE_SYNTAX_REGEX)
    has_unredirected_oos_leak = bool(is_oos_flagged and not mentions_academy)

    if has_code_leak or has_unredirected_oos_leak:
        logger.warning(
            f"[GUARDRAILS] Post-generation Out-of-Scope leak detected! Code leak: {has_code_leak}, OOS leak: {has_unredirected_oos_leak}. Intercepting."
        )
        return _generate_out_of_scope_deflection(user_message=user_message, detected_language=language)

    return reply, followup


def _sanitize_state_grounded_replies(
    reply: str,
    followup: Optional[str],
    state: Dict[str, Any],
    last_turn: Dict[str, Any],
    active_cid: str,
) -> Tuple[str, Optional[str]]:
    """
    State is the Highest Authority (Database Guardrail):
    - Prevents hallucinated re-asking for slots that are already settled in state.
    - Prevents premature payment QR push in follow-up when customer is exploring questions/demos.
    - Suppresses follow-up when customer is in refusal/complaint mode.
    """
    if not reply:
        return reply, followup

    # Same completeness check as dispatch_payment_qr_async (Course, Timing, Teacher, Days, Package),
    # so the reply never promises a QR that the dispatcher will refuse to send.
    from src.core.helpers.slot_engine import evaluate_enrollment_slots, followup_asks_wrong_slot, slot_question
    slot_eval = evaluate_enrollment_slots(state, course_id=active_cid)
    all_slots_settled = bool(slot_eval.get("is_complete"))
    # A real enrollment detail is still missing (paid / trial leads are excluded: no QR talk is gated there).
    details_missing = slot_eval.get("next_target_slot") in ("COURSE", "TIMING", "TEACHER", "DAYS", "DURATION")

    intent = (last_turn.get("intent") or last_turn.get("customer_intent") or "").upper()
    user_asked_qr = bool(
        intent == "PAYMENT_REQUEST"
        or last_turn.get("requests_qr")
        or re.search(r"\b(send\s*qr|qr\s*code|payment\s*link|scanner|pay\s*online)\b", str(state.get("_last_user_message", "")), re.IGNORECASE)
    )
    is_question = bool(
        (intent in ("QUESTION", "COURSE_INQUIRY", "FEE_INQUIRY") and not user_asked_qr)
        or last_turn.get("question_summary")
        or last_turn.get("wants_demo")
        or last_turn.get("wants_trial_guide")
        or last_turn.get("wants_app_guide")
    )
    is_objection = bool(
        intent in ("COMPLAINT", "DISINTEREST")
        or state.get("refusal_stage", 0) > 0
        or last_turn.get("is_complaint_or_refund")
    )

    # Checkout talk the QR dispatcher would refuse to back up while a detail is missing (English + Hinglish).
    QR_PROMISE_REGEX = (
        r"(order summary|payment summary|enrollment summary|booking summary|payment ke liye summary"
        r"|qr code (?:below|above|attached|provided|here|niche|neeche|yahan)|(?:niche|neeche) (?:diya|bheja)"
        r"|scan (?:the |this )?(?:upi )?qr|qr code (?:se|ke zariye|ke through|via)|via (?:the )?upi qr|upi qr code (?:se|ke)"
        r"|complete (?:your |the )?payment|pay (?:directly|now|via|using)|direct (?:upi )?payment link"
        r"|payment (?:kar sakte|complete kar|kijiye|karein|karen|directly))"
    )
    PAYMENT_LEAK_REGEX = (
        rf"{QR_PROMISE_REGEX}|\b(total payable|final payable|payable amount|amount payable|fee breakdown|price breakdown"
        r"|kul (?:rashi|amount|payable))\b"
    )

    def _drop_sentences(text: str, pattern: str) -> str:
        """Removes only the sentences matching pattern, keeping the rest of the reply and its line layout."""
        lines = [
            " ".join(s for s in re.split(r"(?<=[.!?])\s+", line) if not re.search(pattern, s, re.IGNORECASE))
            for line in text.split("\n")
        ]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()

    # 1. OBJECTION / COMPLAINT GUARD: Suppress sales follow-up completely
    if is_objection:
        if details_missing and re.search(QR_PROMISE_REGEX, reply, re.IGNORECASE):
            reply = _drop_sentences(reply, QR_PROMISE_REGEX) or reply
        return reply, None

    # 2. QUESTION / DEMO GUARD: Ensure followup does NOT force payment QR assistance
    if is_question and followup:
        PAYMENT_PUSH_PATTERNS = [
            r"\b(complete your payment|pay via|upi qr|qr code|assistance completing your payment)\b",
            r"\b(start with\s*—\s*1 Month|1 Month or 3 Months)\b",
        ]
        if any(re.search(p, followup, re.IGNORECASE) for p in PAYMENT_PUSH_PATTERNS):
            if last_turn.get("wants_demo"):
                followup = "Did you get a chance to watch the demo video, or would you like to see another instructor? 😊"
            else:
                followup = "Does this answer your question, or would you like to know more? 😊"

    # 3. ALL SLOTS SETTLED GUARD: When Course + Timing + Teacher + Days + Package are all complete
    if all_slots_settled and not is_question and not is_objection:
        # Strip hesitant confirmation patterns ("Before I can generate... I need to confirm that all details are in place")
        HESITANT_CONFIRMATION_PATTERNS = [
            r"Before I can generate your (customized )?payment QR code,?\s*(I need to confirm that all details are in place\.?)?",
            r"Before we can generate your (customized )?payment QR code,?\s*(please confirm if all details are in place\.?)?",
            r"I need to confirm that all details are in place\.?",
        ]
        for hp in HESITANT_CONFIRMATION_PATTERNS:
            reply = re.sub(hp, "", reply, flags=re.IGNORECASE).strip()

        SLOT_REASK_PATTERNS = [
            r"\b(which package|kaunsa package|package duration|choose a package|select a package|select package duration|preferred duration|preferred package)\b",
            r"\b(start with\s*—\s*1 Month|1 Month ya 3 Months|1 Month or 3 Months)\b",
            r"\b(move on to the package duration|package duration.*available options)\b",
            r"\b(which specific timing|which timing|which batch timing|which batch|available timings for the morning|available timings for the evening|which instructor|which teacher)\b",
        ]
        has_hallucinated_slot_ask = any(re.search(p, reply, re.IGNORECASE) for p in SLOT_REASK_PATTERNS) or (followup and any(re.search(p, followup, re.IGNORECASE) for p in SLOT_REASK_PATTERNS))

        if has_hallucinated_slot_ask:
            from src.core.pricing_engine import calculate_exact_fee
            cid_curr = (active_cid or state.get("course") or "").lower().strip()
            pkg_curr = state.get("package") or state.get("selected_duration")
            bill = calculate_exact_fee(cid_curr, pkg_curr) if cid_curr and pkg_curr else None
            if bill:
                t_name = state.get("teacher") or state.get("selected_teacher") or "your instructor"
                t_slot = state.get("timing") or state.get("selected_timing") or ""
                course_title = kb_loader.get_catalog_entry(cid_curr).get("name", cid_curr.title()) if kb_loader.get_catalog_entry(cid_curr) else cid_curr.title()
                auto_note = state.get("auto_assigned_slot_note")
                auto_lead = f"{auto_note}\n\n" if auto_note else ""

                reply = (
                    f"{auto_lead}Great! You have selected *{t_name}* for the *{t_slot}* batch of *{course_title}*.\n\n"
                    f"Here is your order summary for the *{pkg_curr}* package:\n"
                    f"• *Base Price:* ₹{bill['base_price']:,}\n"
                    f"• *Discount:* -₹{bill['discount']:,}\n"
                    f"• *Offer Price:* ₹{bill['offer_price']:,}\n"
                    f"• *GST ({bill['gst_percentage']}%):* +₹{bill['gst_amount']:,}\n"
                    f"• *Total Payable:* ₹{bill['final_payable_amount']:,}\n\n"
                    f"Please complete your payment via the UPI QR code below to confirm your seat!"
                )
                followup = "Would you like any assistance completing your payment with the UPI QR code? 😊"
        elif user_asked_qr and not re.search(r"^(great|here is|here are|yeh raha|aapka|sure|congratulations|namaste)", reply.strip(), re.IGNORECASE):
            reply = f"Here is your customized UPI payment QR code for your enrollment:\n\n{reply}".strip()

    # 4. PREMATURE PAYMENT BARRIER (RULE 2: Zero unprompted payment checkout until all slots confirmed)
    # If any slot is missing, QR is strictly blocked and user must be guided to choose the missing detail.
    # 4a. Question turns: keep the answer (a fee answer may show prices) but drop any QR / checkout promise.
    if details_missing and is_question and not user_asked_qr:
        if re.search(QR_PROMISE_REGEX, reply, re.IGNORECASE) or (followup and re.search(QR_PROMISE_REGEX, followup, re.IGNORECASE)):
            logger.warning(f"[guardrails] Premature payment promise stripped from question reply (missing {slot_eval.get('missing_slots')})")
            stripped = _drop_sentences(reply, QR_PROMISE_REGEX)
            if stripped:
                reply = stripped
                followup = slot_question(slot_eval, state.get("language")) or followup
            else:
                is_question = False  # nothing left of the answer: fall through to the full replacement below

    # 4b. Every other turn: replace the reply with a request for the next missing detail.
    if details_missing and not is_objection and not is_question:
        reply_leaks = bool(re.search(PAYMENT_LEAK_REGEX, reply, re.IGNORECASE))
        followup_leaks = bool(followup and re.search(QR_PROMISE_REGEX, followup, re.IGNORECASE))

        if user_asked_qr or reply_leaks or followup_leaks:
            logger.warning(f"[guardrails] Premature payment summary replaced (missing {slot_eval.get('missing_slots')})")
            next_slot = slot_eval.get("next_target_slot")
            cid_curr = (active_cid or state.get("course") or "").lower().strip()
            course_title = kb_loader.get_catalog_entry(cid_curr).get("name", cid_curr.title()) if kb_loader.get_catalog_entry(cid_curr) else cid_curr.title()
            pkg_val = state.get("package") or state.get("selected_duration")
            timing_val = state.get("timing") or state.get("selected_timing")
            teacher_val = state.get("selected_teacher") or state.get("teacher")

            prefix = "To generate your customized payment QR code, " if user_asked_qr else ""

            if next_slot == "TIMING":
                if pkg_val:
                    reply = f"{prefix}Please select your preferred batch timing for *{course_title}* (*{pkg_val}* package) first."
                else:
                    reply = f"{prefix}Please select your preferred batch timing for *{course_title}* first."
                followup = "Which batch timing works best for your daily schedule? 😊"
            elif next_slot == "TEACHER":
                reply = f"{prefix}Please select your preferred instructor for the *{timing_val}* batch of *{course_title}* first."
                followup = "Which instructor would you prefer to learn with? 😊"
            elif next_slot == "DAYS":
                from src.core.helpers.schedule_engine import get_available_days_for_batch
                day_options = get_available_days_for_batch(cid_curr, timing=timing_val, teacher=teacher_val)
                options_text = " or ".join(f"*{d}*" for d in day_options) or "your preferred batch days"
                reply = f"{prefix}Please choose your batch days for the *{timing_val}* *{course_title}* batch with {teacher_val} first."
                followup = f"Which days work for you — {options_text}? 😊"
            elif next_slot == "DURATION":
                reply = f"{prefix}Please select your package duration for *{course_title}* (*{timing_val}* batch with {teacher_val}) first."
                followup = "Which package duration would you like to choose — 1 Month, 3 Months, 6 Months, or 1 Year? 😊"
            elif next_slot == "COURSE":
                reply = f"{prefix}Please let me know which course you would like to join first."
                followup = "Which course would you like to know more about? 😊"

    # 5. SYNC GUARD: Message #2 asks only for the next missing slot: never one already chosen, never skipping ahead.
    # Not an objection / paid / trial (slot_eval.next_target_slot is ENROLLED / TRIAL there, so this is a no-op).
    if followup and followup_asks_wrong_slot(followup, slot_eval):
        logger.warning(f"[guardrails] Message #2 asked the wrong slot (next is {slot_eval.get('next_target_slot')}), replaced: {followup!r}")
        followup = slot_question(slot_eval, state.get("language"))  # None when everything is settled (payment next)

    return reply, followup


def _slot_followup(state: Dict[str, Any], active_cid: str, current: Optional[str]) -> Optional[str]:
    """Message #2 for the lead's NEXT missing slot; `current` when there is nothing to ask (payment / paid / trial)."""
    if state.get("refusal_stage", 0) > 0:  # lead is objecting / not interested: never push the next step
        return current
    from src.core.helpers.slot_engine import evaluate_enrollment_slots, slot_question
    return slot_question(evaluate_enrollment_slots(state, course_id=active_cid), state.get("language")) or current


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


async def dispatch_class_demo_video_async(
    phone: str,
    state: dict,
    video_url: str,
    caption: str = "",
    force_resend: bool = True,
) -> bool:
    """Send a course live-class MP4 natively, with a text fallback on failure."""
    if not video_url:
        return False
    if state.get("class_demo_video_sent") and not force_resend:
        return False

    from src.services.interakt import send_video_message_async
    from src.state.chat_history import save_message_async

    delivered = await send_video_message_async(phone, video_url, caption=caption)
    if delivered:
        state["class_demo_video_sent"] = True
        print(f"[conversation_handler] Native class demo video sent for {phone}")
        asyncio.create_task(save_message_async(phone, "assistant", f"[VIDEO] {video_url}"))
        return True

    # Keep the customer's access to the video if the media provider rejects the MP4.
    fallback = f"{caption}\n{video_url}" if caption else f"🎬 Live class practice video:\n{video_url}"
    fallback_result = await send_text_message_async(phone, fallback)
    fallback_sent = (
        isinstance(fallback_result, dict)
        and "error" not in fallback_result
    ) or (
        fallback_result is not None
        and getattr(fallback_result, "status_code", 500) < 300
    )
    if fallback_sent:
        state["class_demo_video_sent"] = True
        asyncio.create_task(save_message_async(phone, "assistant", fallback))
    print(f"[conversation_handler] Native class demo video failed for {phone}; text fallback sent={fallback_sent}")
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
    timing_val = state.get("timing") or state.get("selected_timing")
    teacher_val = state.get("selected_teacher") or state.get("teacher")

    # Hard guard: ALL mandatory slots (Course, Package, Timing, Teacher, Days) must be explicitly settled
    from src.core.helpers.slot_engine import evaluate_enrollment_slots
    slot_eval = evaluate_enrollment_slots(state, course_id=cid)
    if not slot_eval.get("is_complete"):
        logger.warning(
            f"[QR Dispatch] Blocked: Missing mandatory slot(s) for {phone}. "
            f"Missing slots: {slot_eval.get('missing_slots')}"
        )
        return None

    # Anti-spam guard: Send once per package unless forced or requested
    if not force_resend and state.get("qr_code_sent") and state.get("last_qr_package") == pkg:
        return None

    from src.core.pricing_engine import calculate_exact_fee
    exact_bill = calculate_exact_fee(cid, pkg)
    if not exact_bill or not exact_bill.get("final_payable_amount") or exact_bill["final_payable_amount"] <= 0:
        logger.warning(f"[QR Dispatch] Blocked: Invalid fee calculation for {cid} / {pkg}: {exact_bill}")
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

    from src.services.razorpay_service import create_dynamic_qr_code_async, RazorpayServiceError

    timing_val = state.get("timing") or state.get("selected_timing")
    teacher_val = state.get("selected_teacher") or state.get("teacher")

    catalog_entry = kb_loader.get_catalog_entry(cid)
    course_name = catalog_entry.get("name", cid.title()) if catalog_entry else cid.title()

    try:
        qr_data = await create_dynamic_qr_code_async(
            amount=final_amount,
            amount_in_paise=final_amount_paise,
            course_id=cid,
            duration=pkg,
            timing=str(timing_val) if timing_val else None,
            teacher=str(teacher_val) if teacher_val else None,
            days=str(state.get("days") or state.get("selected_days")) if (state.get("days") or state.get("selected_days")) else None,
            customer_phone=phone,
            name=f"Sensationz - {course_name}"[:40],
            description=f"{course_name} ({pkg}) Enrollment"[:255],
            close_by_minutes=int(os.getenv("QR_EXPIRE_MINUTES", "0")),
        )
    except RazorpayServiceError as rze:
        logger.error(f"[conversation_handler] Razorpay QR generation failed: {rze}")
        return None
    except Exception as exc:
        logger.error(f"[conversation_handler] Unexpected error generating QR: {exc}")
        return None

    # "About Sensationz": faith is the main reason QR leads don't pay, so the short trust card goes ONCE per lead,
    # right before their first QR. After that it is sent only when the customer doubts us (wants_trust_proof).
    trust_cfg = ((kb_loader.get_global_rules() or {}).get("organization") or {}).get("trust_proof") or {}
    if trust_cfg.get("send_with_first_qr") and not state.get("trust_card_sent_at"):
        from src.core.trust_proof import build_trust_card
        card = build_trust_card(state.get("language"), short=True)
        if card:
            await send_text_message_async(phone, card)
            state["trust_card_sent_at"] = time.time()
            asyncio.create_task(save_message_async(phone, "assistant", card))
            asyncio.create_task(log_message_async(phone, "ai", card))
            await asyncio.sleep(1.5)  # card first, QR last: the QR stays the latest thing on screen

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
            f"🔒 *100% TRUSTED AND VERIFIED BY RAZORPAY*"
        )
    else:
        caption = (
            f"📲 *Scan & Pay via any UPI App (GPay, PhonePe, Paytm, BHIM):*\n\n"
            f"🔒 *100% TRUSTED AND VERIFIED BY RAZORPAY*"
        )

    # Deliver QR image directly to WhatsApp
    from src.services.interakt import send_image_message_async
    delivered = False
    if image_url:
        # Convert short Razorpay URL (https://rzp.io/rzp/<id>) to direct .png image URL for Interakt
        qr_media_url = image_url
        if "rzp.io/rzp/" in image_url:
            short_id = image_url.split("rzp.io/rzp/")[-1].strip().split("?")[0].strip("/")
            base_url = os.getenv("APP_BASE_URL", "https://salesagent.sensationzperformingarts.com").rstrip("/")
            qr_media_url = f"{base_url}/qr-image/{short_id}.png"

        delivered = await send_image_message_async(phone, qr_media_url, caption=caption)

    # Fallback to text message if image delivery failed
    if not delivered:
        fallback_msg = f"{caption}\n\n🖼️ *QR Code Image Link:* {image_url}"
        await send_text_message_async(phone, fallback_msg)
        delivered = True

    if delivered:
        from datetime import datetime, timezone
        now_utc = datetime.now(timezone.utc).isoformat()
        if not state.get("qr_sent_at"):  # first QR only: one summary, frozen now
            from src.services.qr_lead_summary import schedule_qr_lead_summary
            schedule_qr_lead_summary(phone, state)
        state["qr_code_sent"] = True
        state["is_qr_sent"] = True
        state["qr_sent_at"] = state.get("qr_sent_at") or now_utc
        state["qr_id"] = qr_id
        track_sent_qr(state, qr_id)  # sets last_qr_id/order_id + keeps older still-valid QRs
        state["last_qr_package"] = pkg
        state["last_qr_url"] = image_url
        state["last_qr_amount"] = final_amount
        state["payable_amount"] = final_amount
        return qr_data

    return None


_MEDIA_TAG = re.compile(r"\[MEDIA:(\w+)\]")  # set by routes._extract_message_text for inbound media


def _media_not_readable_reply(state: dict, kind: str) -> str:
    """Localized 'I can't read images/files/voice notes, please type it' reply.
    Asks specifically for the Gmail ID when we are waiting for one."""
    awaiting_gmail = bool(
        state.get("awaiting_email_fulfillment")
        or state.get("awaiting_trial_email")
        or state.get("classes_not_showing")
        or (is_payment_verified(state) and not state.get("student_email"))
    )
    lang = str(state.get("language") or "Hinglish").lower()
    is_hindi = "hindi" in lang and "hinglish" not in lang
    is_english = "english" in lang and "hinglish" not in lang
    voice = kind == "audio"

    if is_english:
        what = "voice notes" if voice else "text inside images, screenshots or files"
        ask = ("Please type your *Gmail ID* here in the chat (e.g. yourname@gmail.com) so we can link your classes."
               if awaiting_gmail else "Could you please type your message here in the chat?")
        return f"Sorry, as an AI assistant I can't read {what}. 🙏\n\n{ask} 😊"
    if is_hindi:
        what = "वॉइस नोट" if voice else "फोटो, स्क्रीनशॉट या फाइल में लिखा टेक्स्ट"
        ask = ("कृपया अपना *Gmail ID* यहां चैट में टाइप करके भेजें (जैसे yourname@gmail.com) ताकि हम आपकी क्लासेज लिंक कर सकें।"
               if awaiting_gmail else "कृपया अपना मैसेज यहां चैट में टाइप करके भेजें।")
        return f"माफ़ कीजिए, AI असिस्टेंट होने के कारण मैं {what} नहीं पढ़ सकता। 🙏\n\n{ask} 😊"
    what = "voice notes sun" if voice else "photo, screenshot ya file mein likha text padh"
    ask = ("Kripya apna *Gmail ID* yahan chat mein type karke bhejein (jaise yourname@gmail.com) taaki hum aapki classes link kar sakein."
           if awaiting_gmail else "Kripya apna message yahan chat mein type karke bhej dijiye.")
    return f"Maaf kijiye, AI assistant hone ki wajah se main {what} nahi sakta. 🙏\n\n{ask} 😊"


async def _handle_conversation_turn_unlocked(
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

    # ── Media without typed text (screenshot / photo / PDF / voice note) ──────
    # The AI cannot read media. Say so and ask the client to type it, instead of letting the
    # LLM answer a blank message (or, before 11-14, linking "none@gmail.com").
    media_kinds = _MEDIA_TAG.findall(text_clean)
    text_clean = _MEDIA_TAG.sub("", text_clean).strip()
    if media_kinds and not re.sub(r"^\[Replying to:[^\]]*\]\s*", "", text_clean, flags=re.IGNORECASE).strip():
        reply = _media_not_readable_reply(state, media_kinds[0])
        await send_text_message_async(phone, reply)
        latency_sec = round(time.time() - start_time, 2)
        asyncio.create_task(save_message_async(phone, "assistant", reply, response_time_sec=latency_sec))
        return {"status": "media_not_readable", "reply": reply}

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

    # Check for Human Agent Escalation (Chat handoff to CRM sales agent)
    # Note: Customer Support Executives handle phone calls directly; phone requests do not trigger chat handoff.
    if (last_turn.get("requests_human_agent") or last_turn.get("intent") == "AGENT_REQUEST") and not last_turn.get("wants_phone_call"):
        await execute_agent_handoff_async(
            phone,
            user_reply_text=reply_text,
            escalation_reason=f"Customer requested agent: {last_turn.get('question_summary') or text_clean[:50]}",
            start_time=start_time,
        )
        return {"status": "handed_off", "reply": reply_text}

    # ── Stop Messaging / Opt-out Intercept ─────────────────────────────────
    # When customer explicitly asks to stop texting/messaging or expresses irritation,
    # immediately acknowledge politely, provide Sales & Support team numbers, disarm follow-ups, and halt bot.
    if last_turn.get("wants_stop_messaging") or last_turn.get("intent") == "STOP_MESSAGING":
        from src.core.kb_loader import format_support_executives_display
        support_contacts = format_support_executives_display()
        lang = str(new_state.get("language") or last_turn.get("detected_language") or "Hinglish").lower()
        is_pure_english = "english" in lang and "hinglish" not in lang
        if is_pure_english:
            optout_msg = (
                "We completely understand and respect your request. We have removed your number from our automated messages, "
                "and you will not receive any further messages from us. 🙏\n\n"
                "If you ever have any course-related inquiries in the future, please feel free to reach out directly to our "
                f"Sales & Customer Support team:\n{support_contacts}\n\n"
                "Wishing you the very best!"
            )
        else:
            optout_msg = (
                "Bilkul, hum samajh sakte hain. Humne aapka number automated messages se hata diya hai "
                "aur aage se aapko koi automated messages nahi aayenge. 🙏\n\n"
                "Agar future mein aapko kisi bhi course ya class ke baare mein koi jaankari chahiye ho, "
                f"toh aap directly hamari Sales & Customer Support team se connect kar sakte hain:\n{support_contacts}\n\n"
                "Wishing you the very best!"
            )

        clean_optout = _format_for_whatsapp(optout_msg)
        await send_text_message_async(phone, clean_optout)
        latency_sec = round(time.time() - start_time, 2)
        await save_message_async(phone, "assistant", clean_optout, response_time_sec=latency_sec)
        asyncio.create_task(log_message_async(phone, "ai", clean_optout))

        new_state["next_followup_due_at"] = None
        new_state["is_escalated"] = True
        new_state["refusal_stage"] = 3
        new_state["wants_stop_messaging"] = True
        mark_escalated(phone)
        await save_user_state_async(phone, new_state)

        return {"status": "opted_out", "reply": clean_optout}

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

    # Slot selection guard: If user is actively selecting/changing a slot, it CANNOT be a refusal
    is_active_selection = bool(
        last_turn.get("intent") in ("TIMING_SELECTION", "TEACHER_SELECTION", "PACKAGE_SELECTION", "COURSE_SELECTION", "DAY_SELECTION")
        or new_state.get("timing") != state.get("timing")
        or new_state.get("teacher") != state.get("teacher")
        or new_state.get("package") != state.get("package")
        or (new_state.get("preferred_start_date") and new_state.get("preferred_start_date") != state.get("preferred_start_date"))
    )

    if is_active_selection:
        new_state["refusal_stage"] = 0
        current_refusal_stage = 0
        is_refusal_or_complaint = False
    else:
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
                from src.state.chat_state import is_protected_from_agent_assignment
                is_prot, stage_type = is_protected_from_agent_assignment(new_state)
                # Save this turn's state BEFORE the handoff: the handoff reloads it and adds the
                # escalation / assigned agent. (Saving new_state AFTER it used to wipe both.)
                await save_user_state_async(phone, new_state)
                if is_prot:
                    await execute_agent_handoff_async(
                        phone,
                        user_reply_text=None,
                        escalation_reason=(eval_res or {}).get("escalation_reason")
                        or f"Refusal for {stage_type}: {text_clean[:50]}",
                        start_time=start_time,
                        send_connecting_notice=False,
                    )
                    return {"status": "support_provided_refusal", "reply": ""}

                # The notice is sent BY the handoff, and only once an agent really owns the chat.
                await execute_agent_handoff_async(
                    phone,
                    user_reply_text=_format_for_whatsapp(_ESCALATION_NOTICE),
                    escalation_reason=(eval_res or {}).get("escalation_reason")
                    or f"Refusal escalation: {text_clean[:50]}",
                    start_time=start_time,
                    send_connecting_notice=False,
                )
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
        or last_turn.get("customer_intent") == "APP_LINK_REQUEST"
    )

    # Explicit button clicks or requests for tutorial video (trial booking or app install guide)
    is_video_guide_request = bool(
        last_turn.get("wants_trial_guide")
        or last_turn.get("wants_trial_video_guide")
        or last_turn.get("wants_app_guide")
        or last_turn.get("wants_app_video_guide")
        or last_turn.get("wants_resend_video")
        or text_clean.strip().lower() in ("watch video", "send video", "video tutorial", "video guide")
        or "watch video" in text_clean.lower()
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
        last_turn.get("wants_demo")
        or last_turn.get("wants_demo_video")
        or (
            (
                "demo" in (last_turn.get("question_summary") or "").lower()
                or any(d in main_reply.lower() for d in ("youtube.com", "youtu.be", "instagram.com"))
                or any(w in main_reply.lower() for w in ("demo video", "teaching style", "sample video", "instructor video"))
            )
            and not is_video_guide_request
        )
    )
    # Only an explicit demo request should trigger native class-video delivery.
    # This avoids sending a video merely because the generated reply mentions one.
    explicit_demo_request = bool(
        last_turn.get("wants_demo")
        or last_turn.get("wants_demo_video")
    )
    # Dynamic Media Configuration for Trial and App Download/Install
    active_cid = active_course or state.get("course") or ""
    course_data_active = kb_loader.get_course_data(active_cid)
    course_overview_active = course_data_active.get("course_overview") or {}
    class_demo_video_url = course_overview_active.get("class_raw_video_url")
    active_course_title = course_overview_active.get("name") or str(active_cid or "").replace("_", " ").title() or "Live class"
    # When the customer explicitly asks to see/send the class or demo video, ALWAYS send it!
    # Never let a past 'class_demo_video_sent' flag suppress an explicit user request.
    send_native_demo_video = bool(explicit_demo_request and class_demo_video_url)
    wants_resend = bool(last_turn.get("wants_resend_video", False) or is_video_guide_request)

    user_wants_app_guide = bool(
        last_turn.get("wants_app_guide")
        or last_turn.get("wants_app_video_guide")
    )
    user_wants_trial_guide = bool(
        last_turn.get("wants_trial_guide")
        or last_turn.get("wants_trial_video_guide")
        or (is_video_guide_request and not user_wants_app_guide)
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

    # Payment state is used both for payment guardrails and the app-access link policy.
    is_client_paid = is_payment_verified(new_state) or is_payment_verified(state)
    if is_client_paid:
        new_state["payment_claimed"] = False

    is_trial_booking_guide = bool(
        re.search(r"\b(step|download the sensationz app|profile then tap|trial links|confirm trial)\b", main_reply, re.IGNORECASE)
    )
    # STRICT GUARD: Only send trial video if customer specifically wants a trial AND is not a paid student
    customer_wants_trial = bool(
        last_turn.get("wants_trial")
        or user_wants_trial_guide
        or new_state.get("wants_trial")
    )
    is_trial_turn = bool(
        not is_client_paid
        and customer_wants_trial
        and (
            user_wants_trial_guide
            or (is_trial_booking_guide or not user_asked_demo)
            or (new_state.get("stage") == "TRIAL_REQUESTED" and not new_state.get("trial_video_sent"))
            or re.search(r"\b(trial links|confirm trial|trial enrollment|how to book\s+trial|book\s+a\s+trial)\b", main_reply, re.IGNORECASE)
        )
    )

    will_send_trial_video = bool(
        not is_client_paid
        and customer_wants_trial
        and not user_wants_app_guide
        and not user_asked_demo
        and (
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

    # If delivering trial tutorial video, append clickable video link directly to text reply
    if will_send_trial_video:
        trial_cfg = (
            course_data_active.get("trial_classes")
            or course_data_active.get("trial_class")
            or kb_loader.get_global_rules().get("universal_course_policies", {}).get("trial_classes", {})
        )
        yt_link = trial_cfg.get("how_to_book_video_url") or trial_cfg.get("video_url")
        if yt_link and yt_link not in main_reply:
            main_reply = f"{main_reply.rstrip()}\n\n🎬 *Video Link:* {yt_link}"

    is_classes_troubleshooting = bool(
        last_turn.get("intent") == "CLASSES_NOT_SHOWING"
        or last_turn.get("customer_intent") == "CLASSES_NOT_SHOWING"
        or last_turn.get("cannot_access_classes")
        or new_state.get("awaiting_email_fulfillment")
        or new_state.get("classes_not_showing")
    )

    # Keep the zero-spam policy, but do not let the sanitizer crop links from a
    # legitimate enrollment/access handoff or an app-help request.
    allow_app_links = _should_allow_app_links(
        text=text_clean,
        state=new_state,
        last_turn=last_turn,
        history=history,
    )

    clean_reply = _strip_trailing_question(_format_for_whatsapp(main_reply), followup=followup_msg) if followup_msg else _format_for_whatsapp(main_reply)
    clean_reply, clean_followup = _sanitize_confirmation_guardrails(
        clean_reply, followup_msg, language=new_state.get("language"), state=new_state
    )

    # Rule B: Post-Demo-Video Follow-up & Strict Trial Lockdown
    # When a demo video is shared, strictly enforce that Message #2 asks for the package duration
    # (or batch timing if missing), and never offers a trial.
    clean_reply_lower = (clean_reply or "").lower()
    is_demo_video_shared = bool(
        send_native_demo_video
        or explicit_demo_request
        or user_asked_demo
        or any(d in clean_reply_lower for d in ("youtube.com", "youtu.be", "instagram.com"))
        or any(w in clean_reply_lower for w in (
            "demo video", "practice video", "sample video", "upar send kar diya", "teaching style"
        ))
    )
    # The 1 Week Pass IS the customer's package: the demo-video rule below must never ask them to pick a package duration.
    pass_is_package = new_state.get("package") == "1 Week Pass"
    # A customer who already paid is enrolled: never ask them to choose a package or a batch again.
    already_paid = is_payment_verified(new_state)
    if is_demo_video_shared:
        new_state["wants_trial"] = False
        last_turn["wants_trial"] = False
        if new_state.get("stage") in ["TRIAL_REQUESTED", "TRIAL_STEPS_SENT"]:
            new_state["stage"] = "TIMING_SELECTED" if (new_state.get("timing") or new_state.get("selected_timing")) else "COURSE_CONFIRMED"

        # Never offer a trial in reply when demo video is shared
        if clean_reply and re.search(r"\b(trial|trials|ट्रायल)\b", clean_reply, re.IGNORECASE):
            clean_reply = re.sub(
                r"(?:kya\s+aap\s+trial\s+class\s+book\s+karna\s+chahenge\??|aapko\s+trial\s+class\s+book\s+karne\s+ke\s+liye\s+sensationz\s+app\s+ka\s+istemal\s+karna\s+hoga[.?]?|would\s+you\s+like\s+to\s+book\s+a\s+free\s+trial\??)[^.\n]*[.?]?",
                "",
                clean_reply,
                flags=re.IGNORECASE
            ).strip()

        # Message #2 asks for the lead's NEXT missing slot (not blindly "package": it may already be chosen)
        has_timing = bool(new_state.get("timing") or new_state.get("selected_timing"))
        if (has_timing and pass_is_package) or already_paid:
            pass  # keep the AI's own follow-up (pass pitch already matches / customer already enrolled)
        else:
            clean_followup = _slot_followup(new_state, active_cid, clean_followup)

    # Layer 2 Guard: Sanitize proactive trial pushes in followup when trial mode is LOCKED
    is_trial_unlocked = bool(
        new_state.get("wants_trial")
        or new_state.get("stage") in ["TRIAL_REQUESTED", "TRIAL_STEPS_SENT"]
        or last_turn.get("wants_trial")
        or last_turn.get("wants_pass")
        or new_state.get("package") == "1 Week Pass"
    )
    if not is_trial_unlocked:
        if clean_followup and re.search(r"\b(trial|trials|ट्रायल)\b", clean_followup, re.IGNORECASE):
            logger.warning(f"[guardrails] Proactive trial followup intercepted and replaced: {clean_followup}")
            clean_followup = _slot_followup(new_state, active_cid, None)

        if clean_reply and re.search(r"\b(trial|trials|ट्रायल)\b", clean_reply, re.IGNORECASE):
            clean_reply = re.sub(
                r"(?:kya\s+aap\s+trial\s+class\s+book\s+karna\s+chahenge\??|aapko\s+trial\s+class\s+book\s+karne\s+ke\s+liye\s+sensationz\s+app\s+ka\s+istemal\s+karna\s+hoga[.?]?|would\s+you\s+like\s+to\s+book\s+a\s+free\s+trial\??)[^.\n]*[.?]?",
                "",
                clean_reply,
                flags=re.IGNORECASE
            ).strip()
    clean_reply, clean_followup = _sanitize_out_of_scope_guardrails(
        reply=clean_reply,
        followup=clean_followup,
        user_message=text,
        flags=last_turn,
        language=new_state.get("language"),
    )
    clean_reply, clean_followup = _sanitize_state_grounded_replies(
        clean_reply, clean_followup, state=new_state, last_turn=last_turn, active_cid=active_cid
    )

    # Rule B downstream guarantee: ensure clean_followup is strictly preserved after demo video delivery
    if is_demo_video_shared:
        has_timing = bool(new_state.get("timing") or new_state.get("selected_timing"))
        if (has_timing and pass_is_package) or already_paid:
            pass  # keep the AI's own follow-up (pass pitch already matches / customer already enrolled)
        else:
            clean_followup = _slot_followup(new_state, active_cid, clean_followup)
    allow_video_links = bool(user_asked_demo or will_send_trial_video or will_send_install_video or is_video_guide_request)
    clean_reply = _ensure_app_website_links(clean_reply, user_asked_for_links=allow_app_links, allow_demo_links=allow_video_links)

    # Record automatic delivery so later unrelated turns remain link-free. An
    # explicit request still passes through _should_allow_app_links and can
    # intentionally resend the official links.
    clean_reply_lower = clean_reply.lower()
    if allow_app_links and any(
        domain in clean_reply_lower
        for domain in ("play.google.com", "apps.apple.com", "shop.sensationzperformingarts.com")
    ):
        new_state["app_links_sent"] = True
        if new_state.get("stage") == "READY_FOR_APP_LINK":
            new_state["stage"] = "APP_LINK_SENT"

    if clean_followup:
        clean_followup = _format_for_whatsapp(clean_followup)
        clean_followup = _ensure_app_website_links(clean_followup, user_asked_for_links=False, allow_demo_links=allow_video_links)

    # The raw class video is delivered as a native attachment below. Keep only
    # instructor YouTube/demo links in the text reply; strip raw Cloudinary / MP4 URLs.
    clean_reply = _remove_native_media_url_lines(clean_reply, [class_demo_video_url] if class_demo_video_url else None)
    if clean_followup:
        clean_followup = _remove_native_media_url_lines(clean_followup, [class_demo_video_url] if class_demo_video_url else None)

    # Strict Zero Follow-up on Payment Claim (only for unverified/unpaid users)
    # Note: Asking how to pay, requesting QR code, or choosing to pay online is PAYMENT_REQUEST, NOT a claim of having already paid.
    is_asking_to_pay = bool(
        last_turn.get("intent") == "PAYMENT_REQUEST"
        or last_turn.get("customer_intent") == "PAYMENT_REQUEST"
        or last_turn.get("requests_qr")
    )
    is_payment_claim = bool(
        last_turn.get("claims_payment_or_enrollment")
        or last_turn.get("intent") == "PAYMENT_CLAIM"
        or last_turn.get("customer_intent") == "PAYMENT_CLAIM"
        or new_state.get("payment_claimed")
    )
    if not is_client_paid and not is_asking_to_pay and is_payment_claim:
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
            # No free trials exist any more, and the 1 Week Pass IS a package: never ask a pass customer for a duration.
            clean_followup = (
                None
                if (pass_is_package or already_paid)
                else "Which package duration would you like to start with — 1 Month, 3 Months, 6 Months, or 1 Year? 😊"
            )

    # Deliver main reply to customer on WhatsApp (Message 1)
    if clean_reply and clean_reply.strip():
        await send_text_message_async(phone, clean_reply)
        latency_sec = round(time.time() - start_time, 2)
        await save_message_async(phone, "assistant", clean_reply, response_time_sec=latency_sec)
        asyncio.create_task(log_message_async(phone, "ai", clean_reply))

    # Trust proof: exact GSTIN + official GST-portal link + Google rating / social links, straight from data.
    # (Angry accusations / refund demands go to the human-agent path instead; skip a duplicate within 60 s.)
    if (
        last_turn.get("wants_trust_proof")
        and not last_turn.get("is_complaint_or_refund")
        and not last_turn.get("wants_stop_messaging")
        and time.time() - float(new_state.get("trust_proof_sent_at") or 0) > 60
    ):
        from src.core.trust_proof import build_trust_card
        trust_card = build_trust_card(new_state.get("language"))
        if trust_card:
            await send_text_message_async(phone, trust_card)
            new_state["trust_proof_sent_at"] = time.time()
            new_state["trust_card_sent_at"] = new_state.get("trust_card_sent_at") or time.time()  # QR won't repeat it
            await save_message_async(phone, "assistant", trust_card)
            asyncio.create_task(log_message_async(phone, "ai", trust_card))

    if send_native_demo_video:
        await dispatch_class_demo_video_async(
            phone,
            new_state,
            class_demo_video_url,
            caption=f"🎬 {active_course_title} live class practice video",
            force_resend=True,
        )

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
        last_turn.get("intent") == "PAYMENT_REQUEST"
        or last_turn.get("requests_qr")
        or re.search(r"\b(send\s*qr|qr\s*code|payment\s*link|scanner|pay\s*online)\b", text_clean, re.IGNORECASE)
    )
    if user_asked_qr:
        new_state["requests_qr"] = True
    has_course_selected = bool(active_cid or new_state.get("course"))
    is_package_selected = bool(new_state.get("package") or new_state.get("selected_duration"))
    has_timing_selected = bool(new_state.get("timing") or new_state.get("selected_timing"))
    has_teacher_selected = bool(new_state.get("teacher") or new_state.get("selected_teacher"))

    # Verify pricing calculation (Fee / Price slot)
    has_valid_fee = False
    if has_course_selected and is_package_selected:
        try:
            from src.core.pricing_engine import calculate_exact_fee
            cid_check = (active_cid or new_state.get("course") or "").lower().strip()
            pkg_check = new_state.get("package") or new_state.get("selected_duration")
            bill_check = calculate_exact_fee(cid_check, pkg_check)
            if bill_check and bill_check.get("final_payable_amount", 0) > 0:
                has_valid_fee = True
        except Exception:
            has_valid_fee = False

    # Centralized slot completeness check from slot_engine (Single Source of Truth for Course, Timing, Teacher, Days, Package):
    from src.core.helpers.slot_engine import evaluate_enrollment_slots
    slot_eval = evaluate_enrollment_slots(new_state, course_id=active_cid)

    # ALL mandatory prerequisites: Course, Timing, Teacher, Days, Package, AND Fee
    all_prerequisites_met = bool(
        slot_eval.get("is_complete")
        and has_valid_fee
    )

    is_client_paid = is_payment_verified(new_state)

    # QR Code Dispatch Synchronization Guard:
    # NEVER dispatch payment QR code if slots are incomplete OR if Message #2 is actively asking the user to choose ANY slot (timing, teacher, days, package)!
    is_asking_slot = bool(
        not slot_eval.get("is_complete")
        or (
            clean_followup and re.search(
                # No bare weekday names here: a follow-up that just mentions the chosen days
                # ("your Tuesday & Thursday batch") must not block the QR. A real days ask is
                # already covered by slot_eval being incomplete.
                r"\b(which timing|which batch|which specific timing|which instructor|which package duration|choose a package|select a package|kisme se|kaunsa batch|kaunsi timing|batch days|which days|kaunse din|kaunsa pair|pair chahenge)\b",
                clean_followup,
                re.IGNORECASE,
            )
        )
    )

    from src.core.pricing_engine import pass_qr_should_wait
    pass_qr_hold = pass_qr_should_wait(new_state, last_turn, user_asked_qr)
    # A customer who just doubted whether we are genuine must never get a payment QR in the same breath.
    trust_hold = bool(last_turn.get("wants_trust_proof")) and not user_asked_qr
    is_payment_step = bool(
        not is_client_paid
        and not pass_qr_hold
        and not trust_hold
        and all_prerequisites_met
        and (user_asked_qr or not is_asking_slot)
        and (
            user_asked_qr
            or (not new_state.get("payment_claimed") and (
                new_state.get("stage") in ["PACKAGE_SELECTED", "TIMING_SELECTED", "TEACHER_SELECTED", "COUPON_SENT", "PAYMENT_PENDING", "CHECKOUT"]
                or last_turn.get("intent") in ["PACKAGE_SELECTION", "TIMING_SELECTION", "TEACHER_SELECTION", "PAYMENT_REQUEST"]
            ))
        )
    )
    qr_sent_now = None
    if is_payment_step and not will_send_trial_video:
        qr_sent_now = await dispatch_payment_qr_async(
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

    # Deliver the follow-up as a whole different message (Message 2) after a brief pause (Plain text, no buttons)
    final_buttons = []
    # A QR just went out: summary -> card -> QR is enough. No "need help paying?" Message #2 on top
    # (the QR caption says how to pay, and the 5-min follow-up offers help).
    if clean_followup and clean_followup.strip() and not new_state.get("payment_claimed") and not qr_sent_now:
        await asyncio.sleep(2.5)
        await send_text_message_async(phone, clean_followup)
        await save_message_async(phone, "assistant", clean_followup)
        asyncio.create_task(log_message_async(phone, "ai", clean_followup))

    # Persist updated session state
    if (
        not new_state.get("payment_claimed")
        and not new_state.get("is_escalated")
        and not is_payment_verified(new_state)
        and not new_state.get("_last_turn", {}).get("is_disinterested")
        and not new_state.get("_last_turn", {}).get("is_out_of_scope")
    ):
        arm_followup_timer(new_state, topic=text_clean)
    else:
        reset_follow_up_timer(new_state)
    await save_user_state_async(phone, new_state)

    final_buttons_ret = []
    return {"status": "answered", "reply": clean_reply, "followup": clean_followup, "buttons": final_buttons_ret}


async def handle_conversation_turn_async(
    phone: str,
    text: str,
    history: Optional[List[dict]] = None,
    start_time: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Coordinates an entire conversational turn from tasks.py / routes.py:
    Guarantees serialized execution per customer phone number via phone_concurrency_lock.
    """
    async with phone_concurrency_lock(phone):
        return await _handle_conversation_turn_unlocked(
            phone=phone,
            text=text,
            history=history,
            start_time=start_time,
        )
