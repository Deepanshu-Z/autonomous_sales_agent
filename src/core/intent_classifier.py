"""
intent_classifier.py — Two-Step Semantic Extractor (Silent Structured JSON).

Reads incoming customer messages in any language, dialect, or script (Hindi, Hinglish, English, Tamil, etc.)
and extracts pure semantic state changes and customer intent WITHOUT fragile hardcoded regexes.
"""

import os
import re
import json
from typing import Optional, Dict, Any
from dotenv import load_dotenv
from langchain_core.messages import SystemMessage, HumanMessage

load_dotenv()

_classifier_llm = None


def _get_classifier_llm():
    """Lazy-initialize a lightweight LLM for fast, accurate structured extraction,
    routed through OpenRouter when configured, with OpenAI fallback."""
    global _classifier_llm
    if _classifier_llm is None:
        from langchain_openai import ChatOpenAI
        openrouter_key = os.getenv("OPENROUTER_API_KEY")
        if openrouter_key:
            model_name = os.getenv("OPENROUTER_EXTRACTOR_MODEL", "openai/gpt-4o-mini")
            _classifier_llm = ChatOpenAI(
                model=model_name,
                api_key=openrouter_key,
                base_url="https://openrouter.ai/api/v1",
                temperature=0,
                timeout=8,
                max_retries=1,
            )
        else:
            openai_key = os.getenv("OPENAI_API_KEY")
            model_name = os.getenv("OPENAI_EXTRACTOR_MODEL") or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            _classifier_llm = ChatOpenAI(
                model=model_name,
                api_key=openai_key,
                temperature=0,
                timeout=8,
                max_retries=1,
            )
    return _classifier_llm


def _empty_flags() -> Dict[str, Any]:
    """
    Safe fallback for extractor failure.
    Guarantees silent failure, not silent corruption:
    If the extractor errors or returns garbage, it becomes a safe no-op turn
    rather than wrongly flipping someone's confirmed/unconfirmed state.
    """
    return {
        "app_installed_stated": "unmentioned",
        "profile_created_stated": "unmentioned",
        "selected_timing": None,
        "selected_teacher": None,
        "selected_package": None,
        "selected_course": None,
        "wants_trial": False,
        "requests_human_agent": False,
        "is_coupon_requested": False,
        "is_disinterested": False,
        "is_complaint_or_refund": False,
        "customer_intent": "QUESTION",
        "question_summary": None,
        "detected_language": "English",
        "wants_resend_video": False,
        "claims_payment_or_enrollment": False,
        "wants_app_guide": False,
        "wants_app_video_guide": False,
        "wants_trial_guide": False,
        "wants_trial_video_guide": False,
        "unoffered_timing_mentioned": None,
    }


EXTRACTOR_SYSTEM_PROMPT = """You are a silent, highly accurate Semantic State Extractor for a WhatsApp course sales assistant.
Your job is ONLY to analyze the customer's message and determine what state changes, confirmations, or intents occurred.
You NEVER write customer-facing replies. You output ONLY valid, strict JSON.

LANGUAGE & SCRIPT AGNOSTIC:
Customers write in English, Hindi (Devanagari or Hinglish/Latin script), or other Indian languages with informal phrasing, typos, or slang.
Judge by SEMANTIC MEANING, NEVER by exact keyword presence.

EXTRACTION FIELDS:

1. "app_installed_stated":
   - "confirmed": Customer states/confirms they downloaded or installed the Sensationz App ("download kar liya", "install ho gaya", "app to ho gaya", "downloaded", "installed", "done app").
   - "denied": Customer states they have NOT downloaded it ("nahi kiya", "haven't downloaded", "app download nahi kiya").
   - "unmentioned": Not stated.

2. "profile_created_stated":
   - "confirmed": Customer states/confirms they created their profile, account, or finished signup ("profile bana li", "done", "yes done", "banadi", "created", "account ban gaya", "sab kar diya", "haan ho gaya", "krlia"). Note: If the customer replies "done", "yes", or "krlia" after the bot asked them to create their profile or install the app, this is "confirmed".
   - "denied": Customer states they have NOT created their profile or skipped it ("profile abhi nahi", "profile skip kar diya", "profile nahi banayi", "haven't done profile yet").
   - "unmentioned": Not stated.
   - CRITICAL NEGATIVE GUARD: If customer says "paid", "payment done", "paise bhej diye", or "fees bhar di", this is a PAYMENT CLAIM, NOT profile creation! Set profile_created_stated to "unmentioned" unless they explicitly mentioned app profile/signup.

CRITICAL PARTIAL / NEGATION RULE:
If customer says "app to ho gaya but profile abhi nahi" -> app_installed_stated: "confirmed", profile_created_stated: "denied".
If customer says "nahi abhi tak nahi kiya" -> app_installed_stated: "denied", profile_created_stated: "denied".

3. "selected_timing":
   - Batch timing explicitly and definitively chosen by the user from the Allowed Canonical Batch Timings in context.
   - You MUST translate and map colloquial or informal timing references (e.g. "morning 7 am", "shaam 6 baje", "7-8", "6 to 7 evening", "12-1", "10-11") directly to the exact matching canonical batch timing string from the Allowed Canonical Batch Timings in context (e.g. "7:00 AM to 8:00 AM", "6:00 PM to 7:00 PM", "12:00 PM to 1:00 PM", "10:00 AM to 11:00 AM").
   - Output ONLY the exact matching canonical string from the Allowed Canonical Batch Timings list, or null.
   - CRITICAL UNAVAILABLE / UNOFFERED TIMING GUARD:
     • If the customer asks for, selects, or mentions a timing that is NOT in the Allowed Canonical Batch Timings (for example, "11-12", "11 to 12", "3 pm", "2 to 3", "9 pm", or any unlisted hour), set selected_timing to null!
     • NEVER guess, invent, snap, or substitute an unoffered timing to an adjacent or nearby slot (e.g. NEVER convert "11-12" to "12:00 PM to 1:00 PM")!
     • When the customer gives an unoffered timing, set selected_timing to null, set customer_intent to "QUESTION", and set unoffered_timing_mentioned to the requested timing (e.g. "11-12").
   - CRITICAL NEGATIVE GUARD: Set to null if the customer is asking a general question, expressing doubt, or asking about schedule flexibility or switching batches (e.g. "1 week morning and 1 week evening", "can I switch timing?", "subah aur shaam dono chalega?", "timing change ho sakti hai kya?", "is this ok?"). In these cases, customer_intent is "QUESTION" and selected_timing MUST BE null!

4. "selected_package":
   - Package duration definitively chosen by the user in ANY language, script, or dialect worldwide (e.g. English "1 month", Hindi "1 महीना", Tamil "1 மாதம்", Malayalam "1 മാസം", Telugu "1 నెల", Spanish "1 mes", etc.).
   - You MUST translate and map it directly to the exact matching canonical package string from the Allowed Canonical Packages in context (e.g. "1 Month", "3 Months", "6 Months", "1 Year").
   - Output ONLY the exact canonical English string from the allowed list, or null. NEVER output foreign language text or unlisted intervals.
   - CRITICAL NEGATIVE GUARD: NEVER extract "1 week", "2 days", or arbitrary intervals as a package! Sensationz only offers packages listed in the course catalog. Set to null if the customer is asking a question or expressing doubt.

5. "selected_teacher":
   - Specific instructor or teacher definitively chosen, preferred, or requested by the user (e.g. "madhuri mam", "madhuri ji", "madhuri", "sneha", "mradula", "sonali mam", "parul mam", "aditi").
   - You MUST translate and map colloquial or informal teacher references directly to the exact matching canonical teacher name from the Allowed Canonical Instructors in context (e.g. "madhuri mam" -> "Ms. Madhuri", "kratika" -> "Kratika Gontiya", "sneha" -> "Sneha Maurya", "vibha" -> "Vibha Mittal").
   - Output ONLY the exact canonical string from the allowed list, or null. NEVER invent unlisted names.
   - CRITICAL NEGATIVE GUARD: Set to null if the customer is asking a general informational question about teachers (e.g. "who teaches?", "teachers kaun hain?", "faculty details", "who is the teacher for 5pm?"). In these cases, customer_intent is "QUESTION" and selected_teacher MUST BE null!

6. "selected_course":
   - Course mentioned by user ("yoga", "face yoga", "kathak", "western dance", "zumba", "music", "guitar", "singing", "drawing", "acting", etc.), else null.

7. "wants_trial":
   - boolean: true if customer asks for demo, trial class, free trial, or wanting to try before enrolling.

8. "requests_human_agent":
   - boolean: true ONLY if customer explicitly requests to speak with a human, live agent, customer support coordinator, representative, or requests a phone call ("agent", "human", "talk to someone", "call me", "phone karo", "executive", "person").
   - NEGATIVE GUARD: DO NOT set to true for general help words or button responses ("help me", "need help", "yes help me", "madad chahiye", "guide me", "kya karna hai", "help with coupon", "help with app"). Those are informational queries, NOT human escalation!

9. "is_coupon_requested":
   - boolean: true if customer explicitly asks to receive, see, or apply their coupon code ("coupon code bhejo", "code kya hai", "discount code", "coupon do", "where is my coupon").

10. "is_disinterested":
    - boolean: true if the customer refuses, declines, rejects, or signals they will NOT enroll.
    - Includes BOTH explicit and CONTEXTUAL/IMPLICIT refusals:
      • Explicit: "not interested", "nahi chahiye", "cancel", "don't message", "no", "nahi lena", "rehne do".
      • Contextual: Customer explicitly states that an unmet requirement is a dealbreaker preventing enrollment, ONLY AFTER having been informed of our policy (e.g. "then I won't join", "offline nahi hai to rehne do", "fixed schedule nahi ho payega mujhse, cancel karo").
      • Context-aware: If the PREVIOUS AI MESSAGE informed the customer of a policy limitation (e.g. no flexible timings, online only, fixed schedule) AND the customer now expresses dissatisfaction with that limitation or says "no" / declines — this IS disinterest.
    - NEGATIVE GUARD: Do NOT set to true for pure informational questions, timing inquiries, or schedule flexibility questions ("timing kya hai?", "fees kitni hai?", "flexible timings", "subah shaam alag timing ho sakti hai?", "can I change batches?") without an explicit rejection/cancellation signal.

11. "is_complaint_or_refund":
    - boolean: true if customer demands a refund, threatens legal/police action, or accuses fraud/scam.

12. "customer_intent":
    Must be EXACTLY ONE of:
    - "QUESTION": Customer is asking an informational question or inquiring about schedule/policies (e.g. "kathak b sikhate ho ?", "fees kitni hai?", "timing kya hai?", "flexible timings", "can I switch batch?", "how to apply?", "what is syllabus?").
    - "TIMING_SELECTION": Customer is selecting or stating their preferred batch timing (e.g. "morning 7 am", "shaam 6 baje", "7:00-8:00 AM", "subah ka batch").
    - "TEACHER_SELECTION": Customer is selecting or stating their preferred instructor/teacher (e.g. "madhuri mam", "i want sneha mam", "vibha mittal", "aditi vyas").
    - "PACKAGE_SELECTION": Customer is selecting or stating their preferred package duration (e.g. "3 months", "1 month", "1 year", "3 mahine").
    - "APP_LINK_REQUEST": Customer asks to receive or resend the app download link or website link.
    - "COUPON_REQUEST": Customer asks for their coupon code or discount code.
    - "PROFILE_COMPLETED": Customer confirms they completed profile / app setup.
    - "AGENT_REQUEST": Customer explicitly wants human/person support or a phone call ("agent", "human", "call me", "executive"). NEVER assign for general guidance or button taps like "Yes, help me" (use "QUESTION" or "OTHER").
    - "DISINTEREST": Customer declines or implicitly rejects enrollment — includes explicit refusals ("not interested", "nahi chahiye", "cancel", "no", "nahi lena") AND contextual rejections where the customer refuses to enroll after having been informed of a policy limitation.
    - "GREETING": Pure greeting without substantive question ("hi", "hello", "good morning").
    - "COMPLAINT": Refund dispute or legal grievance.
    - "PAYMENT_CLAIM": Customer states they completed payment, sent a payment screenshot/slip, or asks to verify payment / confirm enrollment ("paid", "payment done", "maine pay kar diya", "screenshot check karo", "paise bhej diye", "fees bhar di", "enrollment confirm karo").
    - "OTHER": Casual acknowledgement ("ok", "theek hai") or uncategorized.

13. "question_summary":
    - Brief 3-8 word English description of what they are asking about if customer_intent is "QUESTION", else null.

14. "detected_language":
    - Detect the customer's exact language and writing script from any language in the world (e.g. "English", "Hinglish (Latin script)", "Hindi (Devanagari script)", "Tamil (Latin script)", "Tamil (Tamil script)", "Spanish", "French", "German", "Arabic", etc.).
    - STRICT SCRIPT RULES — READ CAREFULLY:
      • LATIN/ROMAN ALPHABET = NEVER DEVANAGARI: If the customer's message contains ONLY standard ASCII/Latin characters (a-z, A-Z, 0-9, punctuation, spaces), it is physically impossible for it to be Devanagari script. You MUST classify it as "Hinglish (Latin script)" (if Indic/Hindi meaning) or "English" (if pure English).
      • Hinglish (Latin script): Any message typed in the Latin alphabet that uses Hindi/Urdu words, shortened forms, or phonetic transliterations. Examples: "fees kya h", "teachers kon h", "trial kon leta h", "mjhe tumpe trust nhi hora", "kya timing h", "nhi kathak ki fees bta", "seekhna h", "acha", "fees bta do", "kya h", "kon h", "kab h", "batao", "nahi lena", "thik h", "hnji", "kaise hoga", "mujhe chahiye", "mam", "sir", "plz", "pls". ALL of these MUST be classified as "Hinglish (Latin script)".
      • English: Message is in standard English words with no Hindi/Urdu terms.
      • Classify as "Hindi (Devanagari script)" ONLY IF the customer's message literally contains Devanagari Unicode characters (e.g. "कथक की फीस बताओ", "नमस्ते", "क्या टाइमिंग है"). If you do NOT see actual Devanagari characters (अ आ इ ई etc.) in the message, NEVER output "Hindi (Devanagari script)"!
      • For any other language worldwide, accurately indicate the language and script (e.g. "Tamil (Latin script)", "Tamil (Tamil script)", "Spanish", "Arabic", etc.).

15. "explicit_language_request":
    - string or null: If customer explicitly asks to change language or talk in a specific language (e.g. "talk in Hindi", "speak in English", "Hindi please", "English me bolo", "switch language to Hindi"), specify the target language name (e.g. "Hindi", "English", "Hinglish"). Otherwise null.

16. "wants_resend_video":
    - boolean: true if customer explicitly asks to send or resend a tutorial video or guide (e.g. "video bhejo", "video nahi aayi", "send video again", "video dobara bhejo", "tutorial video", "resend video", "app video"). Otherwise false.
    - CRITICAL NEGATIVE GUARD: Set to FALSE if customer is asking for an INSTRUCTOR DEMO VIDEO, sample class, or teacher preview (e.g. "demo video", "demo", "sample video", "teacher demo", "nidhi demo", "video dikhao", "demo dikhao")! Teacher demo requests are informational queries ("QUESTION"), NOT tutorial video requests!

17. "claims_payment_or_enrollment":
    - boolean: true if customer states they already paid, completed payment, transferred money, sent a payment screenshot/slip, or asks to verify payment / confirm enrollment / confirm admission ("paid", "payment done", "maine payment kar di", "screenshot check karo", "paise bhej diye", "fees bhar di", "enrollment confirm karo", "is my payment received?"). Otherwise false.

18. "wants_app_guide":
    - boolean: true if customer asks for or wants the app guide, app download video, profile setup video, or installation video/tutorial (e.g. "app guide", "app video", "how to download app video", "app video guide", "download video", "how to install app", "app kaise download kare video", "send app guide", "profile video"). Otherwise false.

19. "wants_trial_guide":
    - boolean: true if customer asks for or wants the trial booking video guide or how to book a trial class video/tutorial (e.g. "trial video guide", "trial video", "trial booking video", "how to book trial video", "demo booking video", "trial guide", "trial class video guide"). Otherwise false.

20. "unoffered_timing_mentioned":
    - string or null: If customer requested, proposed, or selected a timing that does NOT exist in the Allowed Canonical Batch Timings (e.g. "11-12", "3 pm", "2 to 3"), extract that timing string here. Otherwise null.

RESPOND ONLY WITH STRICT JSON matching this schema:
{
  "app_installed_stated": "confirmed" | "denied" | "unmentioned",
  "profile_created_stated": "confirmed" | "denied" | "unmentioned",
  "selected_timing": string | null,
  "selected_teacher": string | null,
  "selected_package": string | null,
  "selected_course": string | null,
  "wants_trial": boolean,
  "requests_human_agent": boolean,
  "is_coupon_requested": boolean,
  "is_disinterested": boolean,
  "is_complaint_or_refund": boolean,
  "customer_intent": string,
  "question_summary": string | null,
  "detected_language": string,
  "explicit_language_request": string | null,
  "wants_resend_video": boolean,
  "claims_payment_or_enrollment": boolean,
  "wants_app_guide": boolean,
  "wants_trial_guide": boolean,
  "unoffered_timing_mentioned": string | null
}"""


async def extract_intent_and_state_changes(
    text: str,
    state: Optional[Dict[str, Any]] = None,
    history: Optional[list] = None,
) -> Dict[str, Any]:
    """
    Executes the silent Extractor call.
    Returns structured dictionary with semantic state changes and intent.
    Falls back to _empty_flags() on any failure.
    """
    if not text or not text.strip():
        return _empty_flags()

    s = state or {}
    last_ai = ""
    if history:
        for turn in reversed(history):
            if turn.get("role") in ["assistant", "ai"]:
                last_ai = (turn.get("content") or "").strip()
                break

    recent_user_turns = [
        (t.get("content") or "").strip()
        for t in (history or [])
        if t.get("role") in ("user", "customer") and (t.get("content") or "").strip()
    ][-2:]
    recent_user_context = (
        "RECENT CUSTOMER MESSAGES:\n" + "\n".join(f"- \"{m}\"" for m in recent_user_turns) + "\n\n"
        if recent_user_turns else ""
    )

    from src.core import kb_loader
    from src.core.helpers import (
        get_available_timings,
        get_course_instructors,
        resolve_canonical_teacher,
    )
    active_cid = s.get("course") or kb_loader.detect_course_from_text(text, default_course=None) or ""
    course_pkgs = list(kb_loader.get_valid_packages(active_cid).keys()) if active_cid else ["1 Month", "3 Months", "6 Months", "1 Year"]
    pkgs_str = ", ".join(f'"{p}"' for p in course_pkgs) if course_pkgs else '"1 Month", "3 Months", "6 Months", "1 Year"'

    # Canonical teachers for active course (or all catalog courses if course not yet known)
    course_teachers = get_course_instructors(active_cid if active_cid else None)
    teachers_str = ", ".join(f'"{t}"' for t in course_teachers) if course_teachers else '"Check Course Batches"'

    # Canonical timings for active course
    course_batches = [b.get("time_12h", "").strip() for b in get_available_timings(active_cid)] if active_cid else []
    timings_str = ", ".join(f'"{t}"' for t in course_batches if t) if course_batches else '"Check Course Batches"'

    context_prompt = (
        f"CUSTOMER SESSION CONTEXT:\n"
        f"- Current Stage: {s.get('stage', 'NEW')}\n"
        f"- Active Course: {s.get('course') or 'None (Not Selected)'}\n"
        f"- Allowed Canonical Batch Timings for Active Course: [{timings_str}]\n"
        f"- Allowed Canonical Packages for Active Course: [{pkgs_str}]\n"
        f"- Allowed Canonical Instructors for Active Course: [{teachers_str}]\n"
        f"- Selected Package: {s.get('package') or s.get('selected_duration') or 'None'}\n"
        f"- Selected Timing: {s.get('timing') or s.get('selected_timing') or 'None'}\n"
        f"- Selected Teacher: {s.get('selected_teacher') or 'None'}\n"
        f"- App Installed in State: {bool(s.get('app_installed'))}\n"
        f"- Profile Created in State: {bool(s.get('profile_created'))}\n"
        f"- Welcome Coupon Already Sent: {bool(s.get('coupon_sent'))}\n\n"
        f"PREVIOUS AI MESSAGE:\n\"{last_ai[:300]}\"\n\n"
        f"{recent_user_context}"
        f"LATEST CUSTOMER MESSAGE:\n\"{text.strip()}\""
    )

    try:
        llm = _get_classifier_llm()
        response = await llm.ainvoke([
            SystemMessage(content=EXTRACTOR_SYSTEM_PROMPT),
            HumanMessage(content=context_prompt),
        ])
        raw = response.content.strip().strip("`").replace("json\n", "").strip()
        data = json.loads(raw)

        # Normalize and validate outputs against fallback defaults
        clean = _empty_flags()
        for k in clean:
            if k in data:
                clean[k] = data[k]

        clean["wants_app_guide"] = bool(clean.get("wants_app_guide") or data.get("wants_app_video_guide"))
        clean["wants_app_video_guide"] = clean["wants_app_guide"]
        clean["wants_trial_guide"] = bool(clean.get("wants_trial_guide") or data.get("wants_trial_video_guide"))
        clean["wants_trial_video_guide"] = clean["wants_trial_guide"]

        # Teacher demo inquiries must never trigger tutorial or install video guides
        if re.search(r"\b(demo|sample|preview)\b", text, re.IGNORECASE):
            clean["wants_resend_video"] = False
            clean["wants_app_guide"] = False
            clean["wants_app_video_guide"] = False
            clean["wants_trial_guide"] = False
            clean["wants_trial_video_guide"] = False

        # Validate selected timing against allowed canonical course batches (supports button text e.g. "5:00 PM", and casual formats e.g. "12pm", "12-1")
        raw_timing = clean.get("selected_timing") or clean.get("unoffered_timing_mentioned")
        if not raw_timing and clean.get("customer_intent") not in ("DISINTEREST", "COMPLAINT", "AGENT_REQUEST"):
            raw_timing = text.strip()

        matched_batch = None
        if raw_timing and isinstance(raw_timing, str):
            r_low = raw_timing.strip().lower()
            clean_user = r_low.replace(" ", "").replace("-", "to")
            for b in course_batches:
                b_low = b.lower()
                if r_low == b_low or b_low.startswith(r_low):
                    matched_batch = b
                    break
                # Match start hour/meridiem: e.g. '12pm' -> '12:00 PM', '5pm' -> '5:00 PM'
                start_part = b_low.split(" to ")[0].replace(":00", "").replace(" ", "")
                if clean_user == start_part or start_part.startswith(clean_user):
                    matched_batch = b
                    break
                # Match full range: e.g. '12to1' or '12pmto1pm'
                clean_batch = b_low.replace(":00", "").replace(" ", "")
                if clean_user == clean_batch or clean_user == clean_batch.replace("pm", "").replace("am", ""):
                    matched_batch = b
                    break

        if matched_batch:
            clean["selected_timing"] = matched_batch
            clean["unoffered_timing_mentioned"] = None
            if clean.get("customer_intent") in ("QUESTION", "COURSE_INQUIRY"):
                clean["customer_intent"] = "TIMING_SELECTION"
        else:
            clean["selected_timing"] = None

        # Strict canonical package normalization against allowed course packages
        raw_pkg = clean.get("selected_package")
        if raw_pkg and isinstance(raw_pkg, str):
            from src.core.coupons import normalize_package_name
            norm_pkg = normalize_package_name(raw_pkg, course_id=active_cid or "yoga")
            clean["selected_package"] = norm_pkg or (raw_pkg if raw_pkg in course_pkgs else None)

        # Strict canonical teacher normalization against allowed course teachers
        raw_teacher = clean.get("selected_teacher")
        matched_teacher = None
        if raw_teacher and isinstance(raw_teacher, str):
            matched_teacher = resolve_canonical_teacher(raw_teacher, course_id=active_cid or None)
        elif not raw_teacher and clean.get("customer_intent") not in ("QUESTION", "DISINTEREST", "COMPLAINT"):
            # Check if user named an instructor in text without LLM extracting it
            matched_teacher = resolve_canonical_teacher(text, course_id=active_cid or None)

        clean["selected_teacher"] = matched_teacher or (raw_teacher if raw_teacher in course_teachers else None)

        # Deterministic course detection from user text overrides ambiguous LLM extraction
        llm_course = clean.get("selected_course")
        valid_llm_course = None
        if llm_course:
            entry = kb_loader.get_catalog_entry(str(llm_course))
            if entry:
                valid_llm_course = entry["id"]

        text_course = kb_loader.detect_course_from_text(text, default_course=None)
        if not text_course and history:
            for turn in reversed(history[-4:]):
                if turn.get("role") in ("user", "customer"):
                    c = kb_loader.detect_course_from_text(turn.get("content") or "", default_course=None)
                    if c:
                        text_course = c
                        break

        chosen_course = text_course or valid_llm_course
        if chosen_course:
            clean["selected_course"] = chosen_course

        return clean
    except Exception as e:
        print(f"[intent_classifier] Extractor error (falling back to safe defaults): {e}")
        flags = _empty_flags()
        try:
            from src.core import kb_loader
            from src.core.helpers import resolve_canonical_teacher
            detected_course = kb_loader.detect_course_from_text_fallback(text, default_course=None)
            if detected_course:
                flags["selected_course"] = detected_course
            detected_teacher = resolve_canonical_teacher(text, course_id=detected_course)
            if detected_teacher:
                flags["selected_teacher"] = detected_teacher
                flags["customer_intent"] = "TEACHER_SELECTION"
            if kb_loader.is_app_link_request_fallback(text):
                flags["customer_intent"] = "APP_LINK_REQUEST"
            if re.search(r"\b(app guide|app video|download video|how to download|app kaise download|how to install)\b", text, re.IGNORECASE):
                flags["wants_app_guide"] = True
                flags["wants_app_video_guide"] = True
            if re.search(r"\b(trial video|trial guide|how to book trial|trial booking video)\b", text, re.IGNORECASE):
                flags["wants_trial_guide"] = True
                flags["wants_trial_video_guide"] = True
        except Exception:
            pass
        return flags


def is_app_link_request(text: str) -> bool:
    """Backward compatibility helper for app link check."""
    t = (text or "").lower()
    return any(w in t for w in ["app link", "download link", "play store", "app store", "send link", "link bhejo", "link do"])


def apply_extraction_to_state(state: Dict[str, Any], flags: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merges classifier flags into persistent session state cleanly and monotonically.
    Guarantees:
    1. Zero regression: previously confirmed slots (timing, teacher, package, fee)
       are NEVER overwritten by None, empty strings, questions, disinterest, or casual turns.
    2. Course switching: switching course resets course-specific slots cleanly only
       when the course truly changes.
    3. Deterministic pricing: when package duration is selected, fee is calculated
       via pricing_engine with exact GST and total payable (zero LLM math).
    4. Monotonic stage progression: stage only advances forward via advance_stage().
    """
    from src.state.chat_state import advance_stage
    from src.core import kb_loader
    from src.core.helpers import calculate_exact_fee, resolve_canonical_teacher

    new_state = dict(state)
    intent = flags.get("customer_intent") or "QUESTION"
    is_selection_turn = intent not in ("QUESTION", "OTHER", "DISINTEREST", "COMPLAINT", "GREETING", "AGENT_REQUEST")

    # 1. Course Selection / Switching
    if flags.get("selected_course"):
        raw_c = flags["selected_course"]
        entry = kb_loader.get_catalog_entry(raw_c)
        cand_course = entry["id"] if entry else (kb_loader.detect_course_from_text(raw_c) or None)
        if cand_course:
            old_course = new_state.get("course")
            if old_course and old_course != cand_course:
                # User switched course! Reset course-specific slots unless new ones arrived in the same turn
                if not flags.get("selected_timing"):
                    new_state["timing"] = None
                    new_state["selected_timing"] = None
                if not flags.get("selected_teacher"):
                    new_state["selected_teacher"] = None
                if not flags.get("selected_package"):
                    new_state["package"] = None
                    new_state["selected_duration"] = None
                    new_state["fee"] = None
            new_state["course"] = cand_course

    active_course = new_state.get("course")

    # 2. Timing Slot Merge (Only on selection turns or when explicitly provided)
    if flags.get("selected_timing") and is_selection_turn:
        new_state["timing"] = flags["selected_timing"]
        new_state["selected_timing"] = flags["selected_timing"]
        new_state["stage"] = advance_stage(new_state.get("stage", "NEW"), "TIMING_SELECTED")
    elif flags.get("unoffered_timing_mentioned"):
        new_state["timing"] = None
        new_state["selected_timing"] = None

    # 3. Teacher Slot Merge (Only on selection turns, canonical resolution)
    if flags.get("selected_teacher") and is_selection_turn:
        resolved = resolve_canonical_teacher(flags["selected_teacher"], course_id=active_course)
        teacher_val = resolved or flags["selected_teacher"]
        new_state["selected_teacher"] = teacher_val
        new_state["stage"] = advance_stage(new_state.get("stage", "NEW"), "TEACHER_SELECTED")

    # 4. Package / Duration Slot Merge & Deterministic Pricing
    if flags.get("selected_package") and is_selection_turn:
        from src.core.coupons import normalize_package_name
        canonical_pkg = normalize_package_name(flags["selected_package"], course_id=active_course or "yoga")
        pkg_val = canonical_pkg or flags["selected_package"]
        new_state["package"] = pkg_val
        new_state["selected_duration"] = pkg_val
        new_state["stage"] = advance_stage(new_state.get("stage", "NEW"), "PACKAGE_SELECTED")

        # Deterministic fee calculation via pricing engine
        if active_course:
            pricing = calculate_exact_fee(active_course, pkg_val)
            if pricing and "total_payable" in pricing:
                new_state["fee"] = pricing.get("total_payable")
                new_state["base_fee"] = pricing.get("base_price")
                new_state["offer_fee"] = pricing.get("offer_price")
                new_state["gst_amount"] = pricing.get("gst_amount")
                new_state["gst_percentage"] = pricing.get("gst_percentage")

    # 5. App & Profile State Merge
    app_stated = flags.get("app_installed_stated", "unmentioned")
    if app_stated == "confirmed":
        new_state["app_installed"] = True
    elif app_stated == "denied":
        new_state["app_installed"] = False

    profile_stated = flags.get("profile_created_stated", "unmentioned")
    if profile_stated == "confirmed":
        new_state["profile_created"] = True
        new_state["app_installed"] = True
        new_state["stage"] = advance_stage(new_state.get("stage", "NEW"), "PROFILE_COMPLETED")
    elif profile_stated == "denied":
        new_state["profile_created"] = False

    if flags.get("wants_trial"):
        new_state["wants_trial"] = True

    if flags.get("claims_payment_or_enrollment"):
        new_state["claims_payment"] = True

    # Language management: Lock selected language until explicitly changed
    from src.core import language_loader

    raw_text = (flags.get("_raw_text") or "").strip()
    explicit_req = flags.get("explicit_language_request")
    matched_explicit = language_loader.match_language(explicit_req) if explicit_req else None
    if not matched_explicit and explicit_req:
        matched_explicit = explicit_req.strip().title()

    # Check if raw text directly matches a language name or button
    if not matched_explicit and raw_text:
        # Check direct language button / option clicks
        matched_from_text = language_loader.match_language(raw_text)
        if matched_from_text:
            matched_explicit = matched_from_text
        else:
            # Check explicit switch phrases like "talk in Hindi", "English please"
            import re
            m = re.search(r"\b(?:speak|talk|reply|switch|change|baat)\s+(?:in|to|me|mein)?\s*([a-zA-Z]+)", raw_text, re.IGNORECASE)
            if m:
                matched_explicit = language_loader.match_language(m.group(1))

    # [PREVIOUS BEHAVIOR - COMMENTED OUT]:
    # if matched_explicit:
    #     new_state["language"] = matched_explicit
    #     new_state["language_selected"] = True
    # elif not new_state.get("language"):
    #     # Initial fallback if language not yet selected
    #     detected_lang = flags.get("detected_language") or language_loader.get_default_language()
    #     _NATIVE_SCRIPT_MARKERS = (
    #         "devanagari", "telugu", "tamil", "bengali", "gujarati",
    #         "malayalam", "kannada", "odia", "punjabi", "arabic",
    #         "urdu", "thai", "myanmar", "sinhala",
    #     )
    #     if raw_text and all(ord(c) < 256 for c in raw_text):
    #         if detected_lang and any(m in detected_lang.lower() for m in _NATIVE_SCRIPT_MARKERS):
    #             detected_lang = "Hinglish (Latin script)"
    #     new_state["language"] = detected_lang

    _NATIVE_SCRIPT_MARKERS = (
        "devanagari", "telugu", "tamil", "bengali", "gujarati",
        "malayalam", "kannada", "odia", "punjabi", "arabic",
        "urdu", "thai", "myanmar", "sinhala",
    )

    detected_lang = flags.get("detected_language")
    has_native_script = bool(raw_text and any(ord(c) >= 256 for c in raw_text))

    if matched_explicit:
        new_state["language"] = matched_explicit
        new_state["language_selected"] = True
    elif has_native_script and detected_lang:
        # User typed in a native script (e.g. Malayalam, Tamil, Devanagari, Telugu, etc.)
        new_state["language"] = detected_lang
        new_state["language_selected"] = True
    elif not new_state.get("language_selected"):
        # Language was not explicitly locked; dynamically adapt to detected language
        detected_lang = detected_lang or new_state.get("language") or language_loader.get_default_language()
        if raw_text and all(ord(c) < 256 for c in raw_text) and detected_lang:
            d_lower = detected_lang.lower()
            if "devanagari" in d_lower:
                detected_lang = "Hinglish (Latin script)"
            elif any(m in d_lower for m in _NATIVE_SCRIPT_MARKERS) and "latin" not in d_lower:
                base_lang = detected_lang.split()[0].title()
                detected_lang = f"{base_lang} (Latin script)"
        new_state["language"] = detected_lang
    elif detected_lang and "english" not in detected_lang.lower() and new_state.get("language") == "English":
        # Dynamic switch: User had default English, but started speaking a regional language
        if raw_text and all(ord(c) < 256 for c in raw_text):
            d_lower = detected_lang.lower()
            if "devanagari" in d_lower:
                detected_lang = "Hinglish (Latin script)"
            elif any(m in d_lower for m in _NATIVE_SCRIPT_MARKERS) and "latin" not in d_lower:
                base_lang = detected_lang.split()[0].title()
                detected_lang = f"{base_lang} (Latin script)"
        new_state["language"] = detected_lang

    new_state["_last_turn"] = {
        "intent": flags.get("customer_intent"),
        "wants_trial": flags.get("wants_trial", False),
        "wants_app_guide": flags.get("wants_app_guide", False),
        "wants_app_video_guide": flags.get("wants_app_guide", False),
        "wants_trial_guide": flags.get("wants_trial_guide", False),
        "wants_trial_video_guide": flags.get("wants_trial_guide", False),
        "wants_resend_video": flags.get("wants_resend_video", False),
        "requests_human_agent": flags.get("requests_human_agent", False),
        "is_coupon_requested": flags.get("is_coupon_requested", False),
        "is_disinterested": flags.get("is_disinterested", False),
        "is_complaint_or_refund": flags.get("is_complaint_or_refund", False),
        "question_summary": flags.get("question_summary"),
        "detected_language": flags.get("detected_language"),
        "claims_payment_or_enrollment": flags.get("claims_payment_or_enrollment", False),
        "unoffered_timing_mentioned": flags.get("unoffered_timing_mentioned"),
    }

    return new_state
