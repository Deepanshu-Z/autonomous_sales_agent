"""
intent_classifier.py — Two-Step Semantic Extractor (Silent Structured JSON).

Reads incoming customer messages in any language, dialect, or script (Hindi, Hinglish, English, Tamil, etc.)
and extracts pure semantic state changes and customer intent WITHOUT fragile hardcoded regexes.
"""

import os
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

CRITICAL PARTIAL / NEGATION RULE:
If customer says "app to ho gaya but profile abhi nahi" -> app_installed_stated: "confirmed", profile_created_stated: "denied".
If customer says "nahi abhi tak nahi kiya" -> app_installed_stated: "denied", profile_created_stated: "denied".

3. "selected_timing":
   - Batch timing explicitly and definitively selected by the user (e.g. "7:00–8:00 AM", "morning 7 am", "evening 6 pm", "shaam 6 baje"), else null.
   - CRITICAL NEGATIVE GUARD: Set to null if the customer is asking a question, expressing doubt, or asking about schedule flexibility or switching batches (e.g. "1 week morning and 1 week evening", "can I switch timing?", "subah aur shaam dono chalega?", "timing change ho sakti hai kya?", "is this ok?"). In these cases, customer_intent is "QUESTION" and selected_timing MUST BE null!

4. "selected_package":
   - Package duration definitively chosen by the user ("1 Month", "3 Months", "6 Months", "1 Year"), else null.
   - CRITICAL NEGATIVE GUARD: NEVER extract "1 week", "2 days", or arbitrary intervals as a package! Sensationz only offers 1 Month, 3 Months, 6 Months, and 1 Year. Set to null if the customer is asking a question.

5. "selected_course":
   - Course mentioned by user ("yoga", "face yoga", "kathak", "western dance", "zumba", "music", "guitar", "singing", "drawing", "acting", etc.), else null.

6. "wants_trial":
   - boolean: true if customer asks for demo, trial class, free trial, or wanting to try before enrolling.

7. "requests_human_agent":
   - boolean: true ONLY if customer explicitly requests to speak with a human, live agent, customer support coordinator, representative, or requests a phone call ("agent", "human", "talk to someone", "call me", "phone karo", "executive", "person").
   - NEGATIVE GUARD: DO NOT set to true for general help words or button responses ("help me", "need help", "yes help me", "madad chahiye", "guide me", "kya karna hai", "help with coupon", "help with app"). Those are informational queries, NOT human escalation!

8. "is_coupon_requested":
   - boolean: true if customer explicitly asks to receive, see, or apply their coupon code ("coupon code bhejo", "code kya hai", "discount code", "coupon do", "where is my coupon").

9. "is_disinterested":
    - boolean: true if the customer refuses, declines, rejects, or signals they will NOT enroll.
    - Includes BOTH explicit and CONTEXTUAL/IMPLICIT refusals:
      • Explicit: "not interested", "nahi chahiye", "cancel", "don't message", "no", "nahi lena", "rehne do".
      • Contextual: Customer explicitly states that an unmet requirement is a dealbreaker preventing enrollment, ONLY AFTER having been informed of our policy (e.g. "then I won't join", "offline nahi hai to rehne do", "fixed schedule nahi ho payega mujhse, cancel karo").
      • Context-aware: If the PREVIOUS AI MESSAGE informed the customer of a policy limitation (e.g. no flexible timings, online only, fixed schedule) AND the customer now expresses dissatisfaction with that limitation or says "no" / declines — this IS disinterest.
    - NEGATIVE GUARD: Do NOT set to true for pure informational questions, timing inquiries, or schedule flexibility questions ("timing kya hai?", "fees kitni hai?", "flexible timings", "subah shaam alag timing ho sakti hai?", "can I change batches?") without an explicit rejection/cancellation signal.

10. "is_complaint_or_refund":
    - boolean: true if customer demands a refund, threatens legal/police action, or accuses fraud/scam.

11. "customer_intent":
    Must be EXACTLY ONE of:
    - "QUESTION": Customer is asking an informational question or inquiring about schedule/policies (e.g. "kathak b sikhate ho ?", "fees kitni hai?", "timing kya hai?", "flexible timings", "can I switch batch?", "how to apply?", "what is syllabus?").
    - "TIMING_SELECTION": Customer is selecting or stating their preferred batch timing (e.g. "morning 7 am", "shaam 6 baje", "7:00-8:00 AM", "subah ka batch").
    - "PACKAGE_SELECTION": Customer is selecting or stating their preferred package duration (e.g. "3 months", "1 month", "1 year", "3 mahine").
    - "APP_LINK_REQUEST": Customer asks to receive or resend the app download link or website link.
    - "COUPON_REQUEST": Customer asks for their coupon code or discount code.
    - "PROFILE_COMPLETED": Customer confirms they completed profile / app setup.
    - "AGENT_REQUEST": Customer explicitly wants human/person support or a phone call ("agent", "human", "call me", "executive"). NEVER assign for general guidance or button taps like "Yes, help me" (use "QUESTION" or "OTHER").
    - "DISINTEREST": Customer declines or implicitly rejects enrollment — includes explicit refusals ("not interested", "nahi chahiye", "cancel", "no", "nahi lena") AND contextual rejections where the customer refuses to enroll after having been informed of a policy limitation.
    - "GREETING": Pure greeting without substantive question ("hi", "hello", "good morning").
    - "COMPLAINT": Refund dispute or legal grievance.
    - "OTHER": Casual acknowledgement ("ok", "theek hai") or uncategorized.

12. "question_summary":
    - Brief 3-8 word English description of what they are asking about if customer_intent is "QUESTION", else null.

13. "detected_language":
    - Detect the customer's exact language and writing script from any language in the world (e.g. "English", "Hinglish (Latin script)", "Hindi (Devanagari script)", "Tamil (Latin script)", "Tamil (Tamil script)", "Spanish", "French", "German", "Arabic", etc.).
    - STRICT SCRIPT RULES — READ CAREFULLY:
      • LATIN/ROMAN ALPHABET = NEVER DEVANAGARI: If the customer's message contains ONLY standard ASCII/Latin characters (a-z, A-Z, 0-9, punctuation, spaces), it is physically impossible for it to be Devanagari script. You MUST classify it as "Hinglish (Latin script)" (if Indic/Hindi meaning) or "English" (if pure English).
      • Hinglish (Latin script): Any message typed in the Latin alphabet that uses Hindi/Urdu words, shortened forms, or phonetic transliterations. Examples: "fees kya h", "teachers kon h", "trial kon leta h", "mjhe tumpe trust nhi hora", "kya timing h", "nhi kathak ki fees bta", "seekhna h", "acha", "fees bta do", "kya h", "kon h", "kab h", "batao", "nahi lena", "thik h", "hnji", "kaise hoga", "mujhe chahiye", "mam", "sir", "plz", "pls". ALL of these MUST be classified as "Hinglish (Latin script)".
      • English: Message is in standard English words with no Hindi/Urdu terms.
      • Classify as "Hindi (Devanagari script)" ONLY IF the customer's message literally contains Devanagari Unicode characters (e.g. "कथक की फीस बताओ", "नमस्ते", "क्या टाइमिंग है"). If you do NOT see actual Devanagari characters (अ आ इ ई etc.) in the message, NEVER output "Hindi (Devanagari script)"!
      • For any other language worldwide, accurately indicate the language and script (e.g. "Tamil (Latin script)", "Tamil (Tamil script)", "Spanish", "Arabic", etc.).

14. "explicit_language_request":
    - string or null: If customer explicitly asks to change language or talk in a specific language (e.g. "talk in Hindi", "speak in English", "Hindi please", "English me bolo", "switch language to Hindi"), specify the target language name (e.g. "Hindi", "English", "Hinglish"). Otherwise null.

RESPOND ONLY WITH STRICT JSON matching this schema:
{
  "app_installed_stated": "confirmed" | "denied" | "unmentioned",
  "profile_created_stated": "confirmed" | "denied" | "unmentioned",
  "selected_timing": string | null,
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
  "explicit_language_request": string | null
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

    context_prompt = (
        f"CUSTOMER SESSION CONTEXT:\n"
        f"- Current Stage: {s.get('stage', 'NEW')}\n"
        f"- Active Course: {s.get('course') or 'None (Not Selected)'}\n"
        f"- Selected Package: {s.get('package') or 'None'}\n"
        f"- Selected Timing: {s.get('timing') or 'None'}\n"
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

        # Deterministic course detection from user text overrides ambiguous LLM extraction
        from src.core import kb_loader
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
            detected_course = kb_loader.detect_course_from_text_fallback(text, default_course=None)
            if detected_course:
                flags["selected_course"] = detected_course
            if kb_loader.is_app_link_request_fallback(text):
                flags["customer_intent"] = "APP_LINK_REQUEST"
        except Exception:
            pass
        return flags


def is_app_link_request(text: str) -> bool:
    """Backward compatibility helper for app link check."""
    t = (text or "").lower()
    return any(w in t for w in ["app link", "download link", "play store", "app store", "send link", "link bhejo", "link do"])


def apply_extraction_to_state(state: Dict[str, Any], flags: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merges classifier flags into persistent session state.
    Pure data merge, no LLM calls, safe to unit-test.
    """
    new_state = dict(state)

    if flags.get("app_installed_stated") == "confirmed":
        new_state["app_installed"] = True
    elif flags.get("app_installed_stated") == "denied":
        new_state["app_installed"] = False

    if flags.get("profile_created_stated") == "confirmed":
        new_state["profile_created"] = True
        if new_state.get("stage") != "COUPON_SENT":
            new_state["stage"] = "PROFILE_COMPLETED"
    elif flags.get("profile_created_stated") == "denied":
        new_state["profile_created"] = False

    if flags.get("selected_timing") and flags.get("customer_intent") not in ("QUESTION", "OTHER"):
        new_state["timing"] = flags["selected_timing"]

    if flags.get("selected_package") and flags.get("customer_intent") not in ("QUESTION", "OTHER"):
        new_state["package"] = flags["selected_package"]

    if flags.get("selected_course"):
        from src.core import kb_loader
        raw_c = flags["selected_course"]
        entry = kb_loader.get_catalog_entry(raw_c)
        if entry:
            new_state["course"] = entry["id"]
        else:
            detected = kb_loader.detect_course_from_text(raw_c)
            if detected:
                new_state["course"] = detected

    if flags.get("wants_trial"):
        new_state["wants_trial"] = True

    # Language management: Lock selected language until explicitly changed
    from src.core import language_loader

    raw_text = (flags.get("_raw_text") or "").strip()
    explicit_req = flags.get("explicit_language_request")
    matched_explicit = language_loader.match_language(explicit_req) if explicit_req else None

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

    if matched_explicit:
        new_state["language"] = matched_explicit
        new_state["language_selected"] = True
    elif not new_state.get("language"):
        # Initial fallback if language not yet selected
        detected_lang = flags.get("detected_language") or language_loader.get_default_language()
        _NATIVE_SCRIPT_MARKERS = (
            "devanagari", "telugu", "tamil", "bengali", "gujarati",
            "malayalam", "kannada", "odia", "punjabi", "arabic",
            "urdu", "thai", "myanmar", "sinhala",
        )
        if raw_text and all(ord(c) < 256 for c in raw_text):
            if detected_lang and any(m in detected_lang.lower() for m in _NATIVE_SCRIPT_MARKERS):
                detected_lang = "Hinglish (Latin script)"
        new_state["language"] = detected_lang

    new_state["_last_turn"] = {
        "intent": flags.get("customer_intent"),
        "wants_trial": flags.get("wants_trial", False),
        "requests_human_agent": flags.get("requests_human_agent", False),
        "is_coupon_requested": flags.get("is_coupon_requested", False),
        "is_disinterested": flags.get("is_disinterested", False),
        "is_complaint_or_refund": flags.get("is_complaint_or_refund", False),
        "question_summary": flags.get("question_summary"),
        "detected_language": flags.get("detected_language"),
    }

    return new_state
