"""
tasks.py — Background message-processing pipeline.

KEY CONCURRENCY DESIGN:
- No per-phone Redis lock — the batching debouncer already guarantees
  only one task per phone is active at a time (token-based dedup).
- ALL I/O (Interakt API, Supabase, Redis) is fully async — never blocks the event loop.
- Different phone numbers process in parallel on the same event loop.
- Redis INCR-based round-robin for agent assignment (atomic, no global lock).
"""

import sys
import os
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import time
import asyncio
import json
from dotenv import load_dotenv
from src.services.interakt import send_text_message_async, assign_chat_to_agent_async
from src.state.chat_state import reset_follow_up_timer, arm_followup_timer

from src.state.chat_history import (
    save_message, get_recent_history,
    save_message_async, get_recent_history_async
)
from src.state.csv_logger import log_message, log_message_async
from src.state.chat_state import (
    mark_escalated,
    is_escalated,
    get_user_state,
    save_user_state,
    mark_escalated_async,
    is_escalated_async,
    get_user_state_async,
    save_user_state_async,
    extract_and_update_slots,
    is_user_asking_question,
    matches_any,
    advance_stage,
)
from src.core.rag import ask_rag_async, stream_rag
from src.services.redis_client import get_redis_connection
from src.core.sales_followup import get_sales_followup
from src.services.agent_summary import send_agent_summary_async
from src.services.agent_handoff import execute_agent_handoff_async, get_next_agent_email
from src.core.refusal_engine import evaluate_refusal_async
from src.core.contextual_followup import generate_contextual_followup_async
from src.core.coupons import (
    format_coupon_banner, get_all_coupon_codes, get_coupon_details,
    detect_package_from_text
)
from src.core.intent_classifier import classify_coupon_and_profile_intent, classify_post_link_intent, is_app_link_request
import re
from src.core import kb_loader


load_dotenv()

redis_conn = get_redis_connection()

# --- Agent Config ---
PRIORITY_AGENT_EMAIL = os.getenv("PRIORITY_AGENT_EMAIL")
PRIORITY_AGENT_EMAIL_ANOTHER_1 = os.getenv("PRIORITY_AGENT_EMAIL_ANOTHER_1")
PRIORITY_AGENT_EMAIL_ANOTHER_2 = os.getenv("PRIORITY_AGENT_EMAIL_ANOTHER_2")

# Round-robin pool (only non-None entries)
AGENT_POOL = [e for e in [PRIORITY_AGENT_EMAIL_ANOTHER_1, PRIORITY_AGENT_EMAIL_ANOTHER_2] if e]

AGENT_TRIGGER_WORDS = ["agent", "human", "talk to someone", "real person", "representative", "support"]


def _agent_nudge(user_text: str) -> str:
    """
    Returns the 'contact agent' nudge message in the user's language.
    Detects Hindi/Hinglish by checking for Devanagari script or common Hindi words.
    """
    hindi_markers = ["kya", "hai", "mujhe", "batao", "dijiye", "chahiye", "ka", "ki", "ke", "nahi", "haan", "aur", "se", "bhi"]
    text_lower = user_text.lower()
    has_devanagari = any("\u0900" <= ch <= "\u097F" for ch in user_text)
    has_hindi_word = any(w in text_lower.split() for w in hindi_markers)

    if has_devanagari or has_hindi_word:
        return (
            "\n\n💬 Iske baare mein aur jaankari ke liye aap *agent* type karein, "
            "ya hamare support team se seedha baat karein: *9898989898*"
        )
    return (
        "\n\n💬 To know more, type *agent* to connect with our support team, "
        "or call us directly at *9898989898*."
    )
def _format_for_whatsapp(text: str) -> str:
    """
    Cleans up text specifically for WhatsApp rendering:
    - Converts Markdown **bold** to WhatsApp *bold*
    - Converts lines starting with loose asterisk bullets '* ' to bullet points '• '
    - Replaces ### / ## headers with *bold headers*
    """
    if not text:
        return text

    # Convert ### Header or ## Header to *Header*
    text = re.sub(r"^(?:#{1,6})\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)

    # Convert Markdown **bold** to WhatsApp *bold*
    text = re.sub(r"\*\*(.*?)\*\*", r"*\1*", text)

    # Convert lines starting with loose asterisk bullets to clean bullet '• '
    text = re.sub(r"^\s*\*\s+", "• ", text, flags=re.MULTILINE)

    # Clean double bullet markers if any (e.g. • - or - •)
    text = re.sub(r"^•\s*-\s*", "• ", text, flags=re.MULTILINE)

    return text.strip()



def is_complaint_or_refund(text: str) -> bool:
    """
    Detects if the incoming message is a refund request, cancellation dispute,
    legal threat, police complaint, or accusation of scam/fraud.
    Such messages must NEVER trigger sales, enrollment pushes, or discount coupon codes.
    """
    if not text:
        return False
    t = text.lower().strip()

    # Explicit refund / return patterns
    REFUND_PATTERNS = [
        r"\b(refund|refound)\b",
        r"\b(paise|paisa|fees|fee|money)\s+(wapis|wapas|vapis|vapas|return|doob|chahiye|mang)\b",
        r"\b(wapis|wapas|vapis|vapas|return)\s+(kro|karo|kardo|krdo|chahiye|de\s*do|dedo|do)\b",
        r"\bmoney\s*back\b",
    ]
    if any(re.search(p, t) for p in REFUND_PATTERNS):
        return True

    # Legal / Police / Consumer Forum threats
    LEGAL_PATTERNS = [
        r"\b(case|fir)\s+(kar|kr|karunga|krunga|kar rha|kr rha|file)\b",
        r"\b(police|court|consumer\s*court|consumer\s*forum|legal\s*action)\b",
        r"\b(illegal|illega)\b",
    ]
    if any(re.search(p, t) for p in LEGAL_PATTERNS):
        return True

    # Scam / Cheating / Fraud company accusations (not mere discount complaints)
    GRIEVANCE_PATTERNS = [
        r"\b(fraud|scam|cheater|chor|loot|dhokhebaaz|dhokhadhadi)\b",
    ]
    # If it accuses fraud/scam without explicitly asking for a coupon code
    if any(re.search(p, t) for p in GRIEVANCE_PATTERNS) and not any(cw in t for cw in ["coupon", "voucher", "code"]):
        return True

    return False


TARGET_AD_ID = os.getenv("TARGET_AD_ID")
TARGET_MESSAGE_TEXT = os.getenv("TARGET_MESSAGE_TEXT", "Hello!! Can I get more info on Yoga classes?")

# ── Robust Disinterest / Refusal Engine ──────────────────────────────────────
_DISINTEREST_EXCLUSIONS = [
    # Polite acknowledgments / pleasantries where negative words are NOT refusals
    "no problem", "no problems", "no prob", "no issues", "no issue", "no worries", "no worry",
    "koi nahi", "koi baat nahi", "koi dikkat nahi", "koi issue nahi", "koi problem nahi",
    "kuch nahi", "kuch nhi",
    # Expressions of uncertainty (not refusal)
    "pata nahi", "nahi pata", "pata nhi", "nhi pata",
    "dont know", "don't know", "do not know", "malum nahi", "maalum nahi",
]

_STANDALONE_REFUSAL_TOKENS = {
    "no", "nah", "nope", "nahi", "nhi", "nai",
    "never", "cancel", "stop", "rehne do", "rehn do", "leave it",
    "chhod do", "chod do", "mat karo", "mat bhejo", "dont", "don't",
    "khatam", "band karo"
}

_DISINTEREST_PHRASES = [
    "not interested", "not intersted", "not intrested", "not intrestad",
    "no thanks", "no thank you", "nahi chahiye", "nhi chahiye", "nai chahiye", "ni chahiye",
    "nahi lena", "nhi lena", "interested nahi", "interested nhi", "not for me",
    "abhi nahi", "abhi nhi", "mat bhejo", "mat send", "baad mein", "baad mai",
    "not now", "later on", "dont want", "don't want", "do not want",
    "dont need", "don't need", "do not need", "no need", "not needed",
    "nahi karna", "nhi karna", "nahi join", "nhi join", "join nahi", "join nhi",
    "zaroorat nahi", "zaroorat nhi", "zarurat nahi", "zarurat nhi",
    "nahi chahte", "nhi chahte", "nahi lete", "nhi lete",
    "no interest", "zero interest", "not looking to", "not looking for",
    "stop messaging", "stop message", "msg mat karo", "message mat karo",
    "don't message", "dont message", "please stop", "unsubscribe",
    "man nahi hai", "mann nahi hai", "mood nahi hai", "mood nhi hai",
    "bilkul nahi", "bilkul nhi", "dobara mat", "wapas mat",
    "can't join", "cannot join", "cant join", "not joining",
    "i will pass", "pass for now", "no yoga", "don't disturb", "dont disturb",
    "rehne do", "rehn do", "leave it", "chhod do", "chod do", "cancel", "mat karo", "band karo"
]

def is_disinterest_signal(text: str) -> bool:
    """
    Robust, context-aware refusal / disinterest detector.
    Returns True if user explicitly declines or refuses enrollment / class offer.
    Safely ignores false-positives like 'no problem', 'pata nahi', or question tags like 'na?'.
    """
    if not text:
        return False
    t = text.lower().strip()

    # 0. Question check: Short inquisitive tags like 'na?', 'nhi?', 'hoga na?' are questions, NOT refusals
    if "?" in text and len(t.split()) <= 3 and not any(p in t for p in ["nahi chahiye", "not interested", "dont want", "cancel"]):
        return False

    # 1. False-positive check: If text contains known non-refusal phrases or refund/complaints
    if is_complaint_or_refund(text) or any(exc in t for exc in _DISINTEREST_EXCLUSIONS):
        return False

    # 2. Check if user is specifying/correcting a slot or asking about trial (e.g. "no, evening please", "trial b book kr skte h na")
    has_no = bool(re.search(r"\b(?:no|nahi|nhi|nah|nope)\b", t))
    if has_no:
        has_timing = any(w in t for w in ["morning", "evening", "afternoon", "subah", "shaam", "am", "pm", "batch", "slot"])
        has_pkg = any(w in t for w in ["1 month", "3 month", "6 month", "1 year", "monthly", "quarterly"])
        has_trial = any(w in t for w in ["trial", "demo", "book"])
        if (has_timing or has_pkg or has_trial) and len(t.split()) >= 2 and not any(p in t for p in ["nahi chahiye", "not interested", "dont want", "nahi lena"]):
            return False

    # 3. Clean punctuation to inspect standalone tokens
    clean_t = re.sub(r"[^\w\s]", "", t).strip()
    words = clean_t.split()

    # 4. Standalone refusal check (for short replies: "no", "NO", "nah", "nahi ji", "no thanks", etc.)
    if len(words) <= 3:
        if clean_t in _STANDALONE_REFUSAL_TOKENS:
            return True
        if any(w in _STANDALONE_REFUSAL_TOKENS for w in words):
            polite_suffixes = {"ji", "please", "plz", "sir", "mam", "maam", "bhai", "yaar", "thanks", "thank", "you", "bye"}
            other_words = [w for w in words if w not in _STANDALONE_REFUSAL_TOKENS and w not in polite_suffixes]
            if len(other_words) == 0:
                return True

    # 5. Phrase check (matches anywhere in message)
    if any(phrase in t for phrase in _DISINTEREST_PHRASES):
        return True

    # 6. Regex word-boundary refusal check
    refusal_pattern = r"\b(?:not\s+interested|no\s+thanks|no\s+need|nahi\s+chahiye|nhi\s+chahiye|nahi\s+lena|nhi\s+lena|dont\s+want|don't\s+want|nahi\s+karna|nhi\s+karna|abhi\s+nahi|abhi\s+nhi|rehne\s+do|rehn\s+do|chhod\s+do|chod\s+do|leave\s+it|cancel)\b"
    if re.search(refusal_pattern, t):
        return True

    return False


def _feedback_request_msg(user_text: str) -> str:
    """
    Returns a gentle, language-aware message asking WHY the user is not interested.
    Never pressures — just invites them to share their concern.
    """
    hindi_markers = ["kya", "hai", "mujhe", "batao", "chahiye", "ka", "ki", "ke", "nahi", "nhi",
                     "haan", "aur", "se", "bhi", "abhi", "mat", "nhi"]
    has_devanagari = any("\u0900" <= ch <= "\u097F" for ch in user_text)
    has_hindi_word = any(w in user_text.lower().split() for w in hindi_markers)

    if has_devanagari or has_hindi_word:
        return (
            "Bilkul theek hai, koi problem nahi! 😊\n\n"
            "Agar aap humse share kar sakein — kya cheez rok rahi hai aapko? "
            "Timing? Fees? Ya kuch aur concern hai? "
            "Hum apni best koshish karenge, agar kuch resolve ho sake toh. "
            "Warna koi pressure nahi — jab mann kare, tab baat karte hain. 🙏"
        )
    return (
        "No problem at all! 😊\n\n"
        "If you don't mind sharing — what's holding you back? "
        "Is it the timing, the fees, or something else? "
        "We'll do our best to help if we can. "
        "Either way, no pressure — we're here whenever you're ready. 🙏"
    )

INFO_INTENT_KEYWORDS = [
    # Section 20 mapping
    "fee", "price", "cost", "charges", "paise", "paisa", "rupaye", "pdnge", "padenge", "lagenge", "lagega", "dene padenge", "dene pdnge",
    "duration",
    "batch", "timing", "schedule", "time slot",
    "teacher", "instructor",
    "platform", "app",
    "syllabus", "topics", "course",
    "trial", "demo", "sample", "reference video",
    "eligib", "age", "eligible",
    "classes per week", "how many days", "class frequency",
    "benefit", "benefits", "fayda", "fayada",
    "what to bring", "keep ready", "mat", "clothing",
    "online", "offline", "virtual",
    "device", "laptop", "mobile", "tablet",
    "enroll", "registration",
    "medical", "treatment", "cure", "disease", "pcos", "back pain",

    # Section 21 phrase variations
    "monthly fee", "quarterly fee", "six-month fee", "annual fee",
    "live yoga", "sensationz app", "sensationz",
    "morning batch", "evening batch",
    "trial session", "trial yoga class", "class", "schedule",

    # General intent phrases (from your earlier chats)
    "yoga", "sikhna", "seekhna", "learn yoga", "yoga krna",
    "other course", "other courses", "dance", "kathak", "music", "singing",
    "drawing", "acting", "aerobics", "zumba", "other classes", "all courses",
    "trust", "fraud", "scam", "genuine", "real company",
    "about company", "location", "branch", "address",
    "social media", "instagram", "facebook", "youtube", "website",
    "recording", "record", "leave", "cancel", "refund", "no refund",
    "minimum age", "8 year", "batao", "guide"
]

AGENT_SUGGEST_PATTERN = re.compile(
    r"to know more about this,?\s*you can type\s*\*?agent\*?\s*so our support team can assist you shortly\.?",
    re.IGNORECASE
)

# ---------------------------------------------------------------------------
# Round-robin agent selection (Redis INCR — atomic, multi-process safe)
# ---------------------------------------------------------------------------
def get_next_agent_email() -> tuple:
    """
    Returns (agent_email, agent_index) in round-robin order.
    Uses Redis INCR for atomicity across concurrent requests and processes.
    agent_index is used to send the summary to the matching agent phone number.
    """
    if not AGENT_POOL:
        return PRIORITY_AGENT_EMAIL, 0  # fallback
    counter = redis_conn.incr("agent_round_robin_counter")
    index = (counter - 1) % len(AGENT_POOL)
    agent = AGENT_POOL[index]
    print(f"[round-robin] counter={counter} -> agent[{index}] = {agent}")
    return agent, index


def is_welcome_trigger(text: str) -> bool:
    """Returns True if the message matches an ad trigger message (with tolerance for punctuation)."""
    t = (text or "").strip().lower()
    target = TARGET_MESSAGE_TEXT.strip().lower()
    if t == target:
        return True
    t_norm = re.sub(r"[!?. ]+", " ", t).strip()
    target_norm = re.sub(r"[!?. ]+", " ", target).strip()
    if t_norm == target_norm:
        return True
    if re.search(r"can i get more info on .* (?:classes|course)", t_norm):
        return True
    return False


# ---------------------------------------------------------------------------
# Target ad verification
# ---------------------------------------------------------------------------
def is_target_ad_or_message(text: str, referral_data: dict = None, phone: str = None) -> bool:
    """
    Checks if a message qualifies for bot response.
    1. Already-verified user (state flag: is_target_ad=True) → PASS
    2. First message: Match exact Ad ID from ENV (TARGET_AD_ID) in referral data → PASS
    3. Otherwise → FAIL (no reply, no assignment)
    """
    if phone:
        try:
            state = get_user_state(phone)
            if state.get("is_target_ad") is True:
                print(f"[2] 🎯 DECISION : {phone} -> WILL REPLY | Reason: Already verified customer (is_target_ad=True)")
                return True
        except Exception:
            pass

    # Check TEST_MODE / ALLOWED_TEST_NUMBERS bypass for direct WhatsApp testing
    if os.getenv("TEST_MODE", "").strip().lower() in ("true", "1", "yes"):
        print(f"[2] 🎯 DECISION : {phone} -> WILL REPLY | Reason: TEST_MODE is enabled in environment")
        return True

    allowed_raw = os.getenv("ALLOWED_TEST_NUMBERS", "").strip()
    if allowed_raw and phone:
        allowed_test = [p.strip().replace("+", "") for p in allowed_raw.split(",") if p.strip()]
        clean_phone = phone.replace("+", "").strip()
        if clean_phone in allowed_test:
            print(f"[2] 🎯 DECISION : {phone} -> WILL REPLY | Reason: Whitelisted in ALLOWED_TEST_NUMBERS")
            return True

    target_ad = (TARGET_AD_ID or os.getenv("TARGET_AD_ID") or "").strip()

    if isinstance(referral_data, str):
        try:
            referral_data = json.loads(referral_data)
        except Exception:
            pass

    # Match Ad ID from referral metadata against TARGET_AD_ID in ENV
    if referral_data and isinstance(referral_data, dict) and target_ad:
        source_id = str(referral_data.get("source_id") or "").strip()
        source_url = str(referral_data.get("source_url") or "").strip()
        ad_id = str(referral_data.get("ad_id") or "").strip()

        if source_id == target_ad or ad_id == target_ad or target_ad in source_url:
            print(f"[2] 🎯 DECISION : {phone} -> WILL REPLY | Reason: Ad ID match '{source_id or ad_id}' == '{target_ad}'")
            return True
        else:
            print(f"[2] 🎯 DECISION : {phone} -> WILL NOT REPLY | Reason: Ad ID MISMATCH — source_id='{source_id}' vs TARGET='{target_ad}'")
            return False

    if not referral_data:
        print(f"[2] 🎯 DECISION : {phone} -> WILL NOT REPLY | Reason: No referral data (organic / non-ad message)")
    else:
        print(f"[2] 🎯 DECISION : {phone} -> WILL NOT REPLY | Reason: TARGET_AD_ID not set in environment")

    return False



# ---------------------------------------------------------------------------
# Agent handoff (async) — delegates to reusable agent_handoff service
# ---------------------------------------------------------------------------
async def handle_agent_handoff_async(phone: str, start_time: float = None, reply: str = None, escalation_reason: str = "Customer ne 'agent' type kiya"):
    """Handles customer request to talk to a human / call support — fully async."""
    print(f"[tasks] Agent requested by {phone}")
    if not reply:
        reply = (
            "Connecting you with our support team! 🙏\n\n"
            "Aapki request humari team tak pahunch gayi hai. "
            "Ek team member aapse jald connect karenge 😊"
        )
    await execute_agent_handoff_async(phone, user_reply_text=reply, escalation_reason=escalation_reason, start_time=start_time)


def is_info_intent(text: str) -> bool:
    t = text.lower().strip()
    return any(kw in t for kw in INFO_INTENT_KEYWORDS)

def should_skip_followup(user_text: str, full_reply: str, stage: str) -> bool:
    """
    Universal suppressor for stage follow-ups (e.g. package choice / timing choice prompts).
    Returns True if a follow-up prompt should be SUPPRESSED (not sent).
    """
    u_lower = (user_text or "").lower().strip()
    r_lower = (full_reply or "").lower().strip()

    # 1. Medical & Sensitive Health Conditions (Never push sales on medical queries)
    medical_kws = [
        "cancer", "heart", "cardiac", "surgery", "doctor", "medical", "treatment",
        "disease", "illness", "bp", "blood pressure", "diabetes", "sugar",
        "spine", "spinal", "slip disc", "slipped disc", "injury", "fracture",
        "pregnant", "pregnancy", "prenatal", "postnatal", "garbhasanskar",
        "operation", "paralysis", "stroke", "kidney", "liver", "asthma",
        "arthritis", "tumour", "tumor", "chemo", "chemotherapy", "patient",
        "dawa", "dawain", "hospital", "bimari", "bimar"
    ]
    if any(kw in u_lower for kw in medical_kws) or any(kw in r_lower for kw in medical_kws):
        return True

    # 2. Unoffered / Unlisted Services & Negative Inquiries
    unoffered_kws = [
        "prenatal", "postnatal", "kids yoga", "face yoga", "offline", "studio",
        "1-on-1", "1 on 1", "one on one", "private class", "personal trainer",
        "home tutor", "personal class"
    ]
    if any(kw in u_lower for kw in unoffered_kws):
        return True

    # If the AI reply explicitly explains that something is unavailable/unoffered
    negative_phrases = [
        "available nahi", "not available", "offer nahi", "not offered",
        "nahi sikhate", "nahi karwate", "don't offer", "do not offer",
        "nahi hoti", "nahi hota", "currently not available", "currently available nahi"
    ]
    if any(p in r_lower for p in negative_phrases):
        return True

    # 3. Trial / Demo / Trust / Location inquiries (User asked to explore first)
    explore_kws = [
        "trial", "demo", "sample", "review", "reviews", "rating", "testimonial",
        "location", "address", "branch", "fraud", "fake", "trust", "legit",
        "website", "instagram", "facebook", "youtube"
    ]
    if any(kw in u_lower for kw in explore_kws):
        return True

    # 4. Support, Agent, Refund, Cancellation, Policy, Complaint, Dispute
    support_kws = ["agent", "human", "refund", "cancel", "cancellation", "complaint", "dispute", "policy", "attendance", "reschedule", "pause", "support", "call", "baat karni", "talk to"]
    if any(kw in u_lower for kw in support_kws) or any(kw in r_lower for kw in support_kws):
        return True

    # 5. Disinterest / Negative Intent — NEVER push sales after a clear refusal
    if is_disinterest_signal(user_text):
        return True

    # 6. Question already addressed in full_reply according to current stage
    if stage in ["ENROLL_CONFIRMED", "PACKAGE_SELECTED"]:
        timing_kws = ["timing", "batch", "time slot", "schedule", "samay", "kab"]
        if any(kw in r_lower for kw in timing_kws):
            return True
    elif stage in ["TIMING_SELECTED", "PACKAGE_ASKED"]:
        pkg_kws = ["package", "duration", "month", "months", "year", "fee", "fees", "price", "cost", "mahina", "₹", "rs"]
        if any(kw in r_lower for kw in pkg_kws):
            return True
    elif stage in ["READY_FOR_APP_LINK", "APP_LINK_SENT"]:
        app_kws = ["download", "install", "play store", "app store", "android", "ios", "app link", "profile", "http"]
        if any(kw in r_lower for kw in app_kws):
            return True

    return False



def _strip_trailing_questions(text: str) -> str:
    """Strips trailing LLM-generated follow-up questions from the main answer to ensure Message 1 is clean."""
    patterns = [
        r"(?i)\n*would you like to.*?\?",
        r"(?i)\n*do you want to.*?\?",
        r"(?i)\n*please tell me your preferred.*?(?:\?|\.|!)",
        r"(?i)\n*which (timing|package|teacher|time slot|duration).*?\?",
        r"(?i)\n*what is your main (goal|focus).*?\?",
        r"(?i)\n*aap (kaunsa|kis|kisme|kya|laptop|morning|subah).*?\?",
        r"(?i)\n*kya aap pehle ek free trial.*?\?",
        r"(?i)\n*kya aap.*?\?",
        r"(?i)\n*are you looking to enroll.*?\?",
        r"(?i)\n*are you ready to.*?\?",
        r"(?i)\n*how long would you like.*?\?",
        r"(?i)\n*is there anything else.*?\?",
    ]
    cleaned = text
    for p in patterns:
        cleaned = re.sub(p, "", cleaned)
    return cleaned.strip()


def get_flow_followup(state: dict) -> str:
    # 1. If user is in trial mode
    if state.get("wants_trial") or state.get("stage") in ["TRIAL_STEPS_SENT", "TRIAL_REQUESTED"]:
        return None

    active_course = state.get("course") or "yoga"

    # 2. If timing is selected but package is missing -> ALWAYS prompt for package duration
    if state.get("timing") and not state.get("package"):
        fees_block = kb_loader.format_package_fees_display(active_course)
        return (
            "Which package duration would you like to start with? 😊\n\n"
            f"Fees:\n{fees_block}\n\n"
            "*Note:* Offer price will be only applicable through app and welcome coupon. Once the app is downloaded and the profile is created, the welcome coupon will be sent here."
        )

    # 3. If ready for app link (first time only)
    if state.get("stage") == "READY_FOR_APP_LINK":
        app_links = kb_loader.format_app_links_display()
        return (
            "Aapke liye ek special welcome discount code hai 🎁\n\n"
            "Sirf Sensationz App download karein ya website par jayein aur apna profile banayein — "
            f"uske baad *Done* ya *Yes* reply karein, aur main turant aapka coupon code bhej dunga!\n\n{app_links}"
        )

    # 4. If enrollment completed, coupon sent, or app links already delivered
    if (state.get("stage") in ["PROFILE_COMPLETED", "COUPON_SENT", "APP_LINK_SENT"]
            or state.get("coupon_sent") or state.get("profile_created")):
        return None
        
    # 4. If timing is missing
    if not state.get("timing"):
        if state.get("stage") == "NEW":
            return None
        timings_block = kb_loader.format_batch_timings_display(active_course)
        return (
            "Which timing would you prefer for your classes? 😊\n"
            f"{timings_block}"
        )
    return None


from src.state.chat_state import arm_followup_timer, reset_follow_up_timer, reset_user_state_async, is_profile_completed_signal

def is_coupon_info_question(text: str) -> bool:
    """
    Detects if the user is asking an informational or troubleshooting question ABOUT coupons
    (e.g. GST, iPhone/iOS, validity, expiry, auto-debit, refund, terms, where/how to apply)
    which should be answered by RAG rather than treated as a coupon code delivery command.
    """
    t = (text or "").lower().strip()
    info_markers = [
        "gst", "tax", "hidden", "extra",
        "iphone", "ios", "apple",
        "auto-debit", "auto debit", "har mahine", "katenge", "deduct", "renewal",
        "refund", "cancel",
        "validity", "expire", "expiry", "lifetime", "kab tak", "valid",
        "cash", "offline",
        "two coupons", "multiple", "ek se zyada", "transfer", "friend", "family",
        "kahan apply", "where to apply", "where do i enter", "option nahi aa raha",
        "kaise apply", "how to apply", "kaise lagate", "kaise lagega", "kese lagega", "how it works",
        "without coupon", "bina coupon", "difference", "kitne ka padega",
        "dance", "kathak", "other courses", "registration fee", "extra pay"
    ]
    return any(m in t for m in info_markers)


def is_coupon_request(text: str) -> bool:
    """Detects if the user is asking to receive or see a coupon code (e.g. 'coupon', 'coupon bjhdo', 'code bhejo')."""
    if is_coupon_info_question(text):
        return False

    t = (text or "").lower().strip()
    words = re.findall(r"\w+", t)
    coupon_words = ["coupon", "coupons", "copon", "cupon", "kupon", "coupen", "copen", "copun", "kupan", "copan", "couopn", "coupn", "koupon", "koopon"]
    code_words = ["code", "promo", "voucher"]
    request_words = [
        "bhej", "bjh", "send", "do", "de", "dedo", "share", "give", "kahan", "kaha", "kha",
        "mil", "aaya", "aayi", "resend", "again", "plz", "please", "kya", "konsa", "which",
        "where", "ab", "now", "daal", "apply", "chahiye", "mang", "milega", "chaiye",
        "dega", "degi", "doge", "nahi de raha", "nhi de raha", "dega ya nahi", "dega yaa nahi",
        "dega ki nahi", "kab doge", "dena"
    ]

    # If any coupon word is present in the text (e.g. "coupon", "coupon bjhdo", "coupon ab?")
    if any(cw in words for cw in coupon_words) or any(cw in t for cw in coupon_words):
        return True

    # If asking for "code" or "discount code"
    if any(cw in words for cw in code_words) or any(cw in t for cw in code_words):
        if any(rw in t for rw in request_words) or len(words) <= 3:
            return True

    _EXPLICIT_KWS = [
        "konsa coupon", "kaunsa coupon", "konsa code", "kaunsa code", "kya code", "code kya", "coupon code kya",
        "konsa coupon rhga", "konsa coupon rahega", "kaunsa coupon rahega", "konsa lagega", "kaunsa lagega",
        "which coupon", "what coupon", "which code", "what code",
        "send coupon", "send code", "send discount coupon", "code do", "code bhej",
        "bhejo code", "coupon do", "coupon bhejo", "coupon bjhdo", "coupon bhej do", "coupon de do",
        "kaha hai", "kha h", "kaha h",
        "fhrse bjhdo", "phir se bhejo", "dobara bhejo", "again send", "resend", "resend code",
        "code nahi mila", "code nhi mila", "code nahi aaya", "code nhi aaya", "where is code",
        "where is coupon", "give coupon", "give code", "coupon code", "discount code", "code please",
        "tu coupon dega ya nahi", "tu coupon dega yaa nahi", "coupon dega ya nahi", "coupon dega ya nhi",
        "coupon dega yaa nhi", "coupon code bhejo", "discount code bhejo", "ab coupon do",
        "1y ka coupon", "1 year ka coupon", "6 month ka coupon", "3 month ka coupon", "1 month ka coupon",
        "1y ka code", "1 year ka code", "6 month ka code", "3 month ka code", "1 month ka code"
    ]
    return any(kw in t for kw in _EXPLICIT_KWS)


def _sanitize_locked_coupons(reply: str, state: dict) -> str:
    """
    Ensures coupon codes are NEVER leaked to the customer before profile completion.
    If the user has not completed their profile / unlocked coupon, any accidental
    coupon code mentions are sanitized out of the LLM reply.
    """
    has_unlocked = bool(
        state.get("profile_created")
        or state.get("coupon_sent")
        or state.get("stage") in ["PROFILE_COMPLETED", "COUPON_SENT"]
    )
    if has_unlocked:
        return reply

    all_codes = set()
    for item in kb_loader.get_course_catalog():
        cid = item.get("id")
        for c in kb_loader.get_course_coupons(cid).values():
            if c.get("code"):
                all_codes.add(c["code"])
    course_id = state.get("course") or "yoga"
    for c in kb_loader.get_course_coupons(course_id).values():
        if c.get("code"):
            all_codes.add(c["code"])

    coupon_patterns = [rf"\*?{re.escape(c)}\*?" for c in all_codes if c]
    cleaned = reply
    leaked = False
    for pat in coupon_patterns:
        if re.search(pat, cleaned, re.IGNORECASE):
            leaked = True
            # Remove entire lines/bullets containing the code
            cleaned = re.sub(r"^[ \t]*[\*\-•]?[ \t]*.*?" + pat + r".*?$", "", cleaned, flags=re.MULTILINE | re.IGNORECASE)
            # Remove inline mentions
            cleaned = re.sub(pat, "welcome coupon", cleaned, flags=re.IGNORECASE)

    if leaked:
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        if "profile" not in cleaned.lower() and "done" not in cleaned.lower():
            cleaned += "\n\n🎁 *(Special welcome discount coupon code app ya website par profile complete karne ke baad yahan receive hoga!)*"
    return cleaned

async def handle_ai_reply_async(phone: str, text: str, history: list, start_time: float = None):
    t0 = time.perf_counter()

    # Developer/tester reset command
    if text.strip().lower() in ["#reset", "/reset", "reset chat", "reset session"]:
        await reset_user_state_async(phone)
        reply = "🔄 Session reset successfully! You can now test from the beginning as a new customer. How can I help you? 😊"
        await send_text_message_async(phone, reply)
        return

    if is_welcome_trigger(text):
        detected_c = kb_loader.detect_course_from_text(text, default_course="yoga")
        active_course = detected_c or "yoga"
        state = await get_user_state_async(phone)
        state["course"] = active_course
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

        # Arm follow-up timer for this welcome message too
        arm_followup_timer(state, topic="welcome message")
        await save_user_state_async(phone, state)

        latency_sec = round(time.time() - start_time, 2) if start_time else None
        full_welcome = f"{msg1}\n{msg2}\n{msg3}"
        # Fire-and-forget background logging to Supabase and CSV so WhatsApp reply is never delayed
        asyncio.create_task(save_message_async(phone, "assistant", full_welcome, response_time_sec=latency_sec))
        asyncio.create_task(log_message_async(phone, "ai", full_welcome))
        return

    # Fetch previous state stage before slot extraction ok ok
    try:
        pre_state = await get_user_state_async(phone)
        prev_stage = pre_state.get("stage") or "NEW"
    except Exception:
        prev_stage = "NEW"

    t_slots = time.perf_counter()
    state = await extract_and_update_slots(phone, text, history)
    is_q = is_user_asking_question(text)
    print(f"[TIMING] {phone} slot_extraction: {time.perf_counter() - t_slots:.2f}s")

    # --- Handle Ambiguous Timing (requires AM/PM clarification from user) ---
    if state.get("ambiguous_timing_range") and not state.get("timing"):
        amb = state.get("ambiguous_timing_range")
        state["pending_ambiguous_timing"] = amb
        state["ambiguous_timing_range"] = None
        await save_user_state_async(phone, state)
        reply = (
            f"Aap {amb} ki timing chahte hain — subah (AM) ya shaam (PM)? 😊\n"
            f"• Subah ke liye likhein: '{amb} AM'\n"
            f"• Shaam ke liye likhein: '{amb} PM'"
        )
        await send_text_message_async(phone, reply)
        latency_sec = round(time.time() - start_time, 2) if start_time else None
        # Fire-and-forget background logging
        asyncio.create_task(save_message_async(phone, "assistant", reply, response_time_sec=latency_sec))
        asyncio.create_task(log_message_async(phone, "ai", reply))
        return

    # Define flags for fresh transitions and confirmations/greetings
    text_lower = text.lower().strip()
    GREETING_WORDS = ["hi", "hii", "hello", "hey", "namaste", "good morning", "good evening", "good afternoon"]
    CONFIRMATION_WORDS = [
        "yes", "yeah", "yep", "sure", "ok", "okay", "enroll", "join",
        # NOTE: 'interested' removed — it matches inside 'not interested'. Use full phrases instead:
        "i am interested", "mujhe interested", "i want to join", "haan", "han",
        "karna hai", "kar do", "haan ji", "proceed", "done", "thik", "thik hai",
        "accha", "acha", "theek", "theek hai", "sahi", "sahi hai", "got it", "understood", "fine"
    ]
    is_greeting = matches_any(text_lower, GREETING_WORDS)
    # Disinterest takes priority — never let it be treated as confirmation
    is_disinterest = is_disinterest_signal(text)
    is_confirmation = (not is_disinterest) and matches_any(text_lower, CONFIRMATION_WORDS)

    is_fresh_enroll_confirmed = (prev_stage != "ENROLL_CONFIRMED" and state["stage"] == "ENROLL_CONFIRMED")
    is_fresh_package_asked = (prev_stage != "PACKAGE_ASKED" and state["stage"] == "PACKAGE_ASKED")
    is_fresh_timing_selected = (prev_stage != "TIMING_SELECTED" and state["stage"] == "TIMING_SELECTED")
    is_fresh_package_selected = (prev_stage != "PACKAGE_SELECTED" and state["stage"] == "PACKAGE_SELECTED")

    # ── INTELLIGENT REFUSAL, RISK & RESOLUTION EVALUATION ───────────────────────
    # Reusable component: checks for course refusal, asks missing reason, resolves via KB,
    # or immediately triggers agent handoff if legal risk, safety risk, or unresolvable.
    refusal_eval = await evaluate_refusal_async(text, history, state)
    res_type = refusal_eval.get("resolution_type")

    if res_type == "ESCALATE_TO_AGENT":
        reply_msg = _format_for_whatsapp(
            refusal_eval.get("reply_message")
            or "We have listened to your query and understand your concern. Connecting you with our support team specialist..."
        )
        esc_reason = refusal_eval.get("escalation_reason") or "Customer refusal / risk"
        state["awaiting_refusal_reason"] = False
        await save_user_state_async(phone, state)
        await execute_agent_handoff_async(phone, user_reply_text=reply_msg, escalation_reason=esc_reason, start_time=start_time)
        return
    elif res_type == "ASK_REASON":
        state["awaiting_refusal_reason"] = True
        arm_followup_timer(state, topic=text)
        await save_user_state_async(phone, state)
        reply_msg = _format_for_whatsapp(refusal_eval.get("reply_message") or "")
        await send_text_message_async(phone, reply_msg)
        latency_sec = round(time.time() - start_time, 2) if start_time else None
        asyncio.create_task(save_message_async(phone, "assistant", reply_msg, response_time_sec=latency_sec))
        asyncio.create_task(log_message_async(phone, "ai", reply_msg))
        return
    elif res_type == "PROVIDE_SOLUTION":
        state["awaiting_refusal_reason"] = False
        arm_followup_timer(state, topic=text)
        await save_user_state_async(phone, state)
        reply_msg = _format_for_whatsapp(refusal_eval.get("reply_message") or "")
        await send_text_message_async(phone, reply_msg)
        latency_sec = round(time.time() - start_time, 2) if start_time else None
        asyncio.create_task(save_message_async(phone, "assistant", reply_msg, response_time_sec=latency_sec))
        asyncio.create_task(log_message_async(phone, "ai", reply_msg))
        return
    # ── TRIAL / DEMO REQUEST HANDLER (Pure procedural requests only; informational queries go to RAG) ──
    _PURE_TRIAL_BOOKING_KWS = [
        "trial book", "book trial", "trial lena hai", "demo lena hai", "trial kaise book karein",
        "trial kaise book kare", "send trial link", "trial link do", "trial link bhejo", "book my trial"
    ]
    is_pure_trial_req = (
        not is_q
        and not is_info_intent(text)
        and matches_any(text_lower, _PURE_TRIAL_BOOKING_KWS)
    )
    if is_pure_trial_req and state.get("stage") != "TRIAL_STEPS_SENT" and state.get("stage") not in ["PROFILE_COMPLETED", "COUPON_SENT"]:
        state["wants_trial"] = True
        state["stage"] = "TRIAL_STEPS_SENT"

        hindi_markers = ["kya", "hai", "mujhe", "batao", "chahiye", "ka", "ki", "ke", "nahi", "haan", "se", "bhi", "kab", "kaise", "kitna", "subah", "shaam", "pehle", "baad"]
        has_hindi = any(w in text_lower for w in hindi_markers) or any("\u0900" <= ch <= "\u097F" for ch in text)
        active_course = state.get("course") or "yoga"
        demo_block = kb_loader.format_demo_videos_display(active_course)
        demo_sec = f"\n\n🎥 *Sample / Demo Videos:*\n{demo_block}" if demo_block else ""

        app_links = kb_loader.format_app_links_display()
        if has_hindi:
            msg1 = (
                f"Aap bilkul pehle demo videos dekh sakte hain aur free live trial class attend kar sakte hain! 😊 Har student ko 3 free live trial classes milti hain.{demo_sec}"
            )
            msg2 = (
                "📲 *Live Trial Book Karne Ke Simple Steps:*\n"
                "1️⃣ Sensationz App download karein ya website visit karein\n"
                "2️⃣ Profile create karke 'Trial Links' par tap karein\n"
                f"3️⃣ Apna preferred batch timing choose karein aur live trial confirm karein!\n\n{app_links}"
            )
        else:
            msg1 = (
                f"You can watch our sample demo videos and attend free live trial classes first! 😊 Up to 3 free live trial classes are allowed per student.{demo_sec}"
            )
            msg2 = (
                "📲 *Steps to Book Your Live Trial:*\n"
                "1️⃣ Download the Sensationz App or visit our website\n"
                "2️⃣ Create your profile and tap on 'Trial Links'\n"
                f"3️⃣ Select your preferred batch timing and confirm your live trial!\n\n{app_links}"
            )
        arm_followup_timer(state, topic=text)
        await save_user_state_async(phone, state)
        msg1 = _format_for_whatsapp(msg1)
        msg2 = _format_for_whatsapp(msg2)
        await send_text_message_async(phone, msg1)
        await asyncio.sleep(1)
        await send_text_message_async(phone, msg2)
        combined = msg1 + "\n\n" + msg2
        latency_sec = round(time.time() - start_time, 2) if start_time else None
        asyncio.create_task(save_message_async(phone, "assistant", combined, response_time_sec=latency_sec))
        asyncio.create_task(log_message_async(phone, "ai", combined))
        return

    # --- DETERMINISTIC STAGE GUARDS ---

    if not is_q and not is_info_intent(text) and state["stage"] == "ENROLL_CONFIRMED" and (is_fresh_enroll_confirmed or is_confirmation or is_greeting):
        reply = get_flow_followup(state)
        if reply:
            reply = _format_for_whatsapp(reply.strip())
            arm_followup_timer(state, topic=text)
            await save_user_state_async(phone, state)
            await send_text_message_async(phone, reply)
            latency_sec = round(time.time() - start_time, 2) if start_time else None
            # Fire-and-forget background logging
            asyncio.create_task(save_message_async(phone, "assistant", reply, response_time_sec=latency_sec))
            asyncio.create_task(log_message_async(phone, "ai", reply))
            return

    if not is_q and not is_info_intent(text) and state["stage"] == "PACKAGE_ASKED" and (is_fresh_package_asked or is_confirmation or is_greeting):
        reply = get_flow_followup(state)
        if reply:
            reply = _format_for_whatsapp(reply.strip())
            arm_followup_timer(state, topic=text)
            await save_user_state_async(phone, state)
            await send_text_message_async(phone, reply)
            latency_sec = round(time.time() - start_time, 2) if start_time else None
            # Fire-and-forget background logging
            asyncio.create_task(save_message_async(phone, "assistant", reply, response_time_sec=latency_sec))
            asyncio.create_task(log_message_async(phone, "ai", reply))
            return

    if not is_q and state["stage"] in ["TIMING_SELECTED", "PACKAGE_ASKED"] and not state.get("package") and (is_fresh_timing_selected or ((is_confirmation or is_greeting) and not is_info_intent(text))):
        active_course = state.get("course") or "yoga"
        fees_block = kb_loader.format_package_fees_display(active_course)
        msg2 = (
            "Which package duration would you like to start with? 😊\n\n"
            f"Fees:\n{fees_block}\n\n"
            "*Note:* Offer price will be only applicable through app and welcome coupon. Once the app is downloaded and the profile is created, the welcome coupon will be sent here."
        )
        state["stage"] = advance_stage(state["stage"], "PACKAGE_ASKED")
        arm_followup_timer(state, topic=text)
        await save_user_state_async(phone, state)
        if msg1:
            msg1 = _format_for_whatsapp(msg1)
            msg2 = _format_for_whatsapp(msg2)
            await send_text_message_async(phone, msg1)
            await asyncio.sleep(1)
            await send_text_message_async(phone, msg2)
            combined = msg1 + "\n\n" + msg2
        else:
            msg2 = _format_for_whatsapp(msg2)
            await send_text_message_async(phone, msg2)
            combined = msg2
        latency_sec = round(time.time() - start_time, 2) if start_time else None
        # Fire-and-forget background logging
        asyncio.create_task(save_message_async(phone, "assistant", combined, response_time_sec=latency_sec))
        asyncio.create_task(log_message_async(phone, "ai", combined))
        return

    if not is_q and state["stage"] == "PACKAGE_SELECTED" and not state.get("timing") and (is_fresh_package_selected or ((is_confirmation or is_greeting) and not is_info_intent(text))):
        active_course = state.get("course") or "yoga"
        timings_block = kb_loader.format_batch_timings_display(active_course)
        msg1 = f"You've selected the {state.get('package')} package. 👍"
        msg2 = (
            "Which timing would you prefer for your classes? 😊\n"
            f"{timings_block}"
        )
        state["stage"] = advance_stage(state["stage"], "ENROLL_CONFIRMED")
        arm_followup_timer(state, topic=text)
        await save_user_state_async(phone, state)
        msg1 = _format_for_whatsapp(msg1)
        msg2 = _format_for_whatsapp(msg2)
        await send_text_message_async(phone, msg1)
        await asyncio.sleep(1)
        await send_text_message_async(phone, msg2)
        combined = msg1 + "\n\n" + msg2
        latency_sec = round(time.time() - start_time, 2) if start_time else None
        # Fire-and-forget background logging
        asyncio.create_task(save_message_async(phone, "assistant", combined, response_time_sec=latency_sec))
        asyncio.create_task(log_message_async(phone, "ai", combined))
        return


    # ── Deterministic Coupon Unlock & Resend Handlers ────────────────────────


    active_course = state.get("course") or "yoga"
    course_pkgs = kb_loader.get_valid_packages(active_course)
    detected_pkg = detect_package_from_text(text, course_id=active_course)
    if detected_pkg:
        state["package"] = detected_pkg
        state["fee"] = course_pkgs.get(detected_pkg, state.get("fee"))

    if not is_q and not is_info_intent(text) and (
        state["stage"] == "READY_FOR_APP_LINK"
        or (state["stage"] == "APP_LINK_SENT" and not state.get("profile_created") and (detected_pkg or is_fresh_package_selected))
    ):
        package = state.get("package") or (list(course_pkgs.keys())[0] if course_pkgs else "1 Month")
        fee = state.get("fee") or course_pkgs.get(package, "")
        fee_str = f" ({fee})" if fee else ""
        app_links = kb_loader.format_app_links_display()
        msg1 = f"You've selected the {package} package{fee_str}. 👍"
        msg2 = (
            "To continue, please download the Sensationz App (or access via website) and create your profile. "
            "Once that's done, just reply *Done* or *Yes* here, and I'll send you a special welcome coupon code 🎁 "
            f"that you can use to unlock your offer price during checkout in the app or website.\n\n{app_links}"
        )
        state["stage"] = advance_stage(state["stage"], "APP_LINK_SENT")
        arm_followup_timer(state, topic=text)
        await save_user_state_async(phone, state)
        msg1 = _format_for_whatsapp(msg1)
        msg2 = _format_for_whatsapp(msg2)
        await send_text_message_async(phone, msg1)
        await asyncio.sleep(1)
        await send_text_message_async(phone, msg2)
        combined = msg1 + "\n\n" + msg2
        latency_sec = round(time.time() - start_time, 2) if start_time else None
        # Fire-and-forget background logging
        asyncio.create_task(save_message_async(phone, "assistant", combined, response_time_sec=latency_sec))
        asyncio.create_task(log_message_async(phone, "ai", combined))
        return

    # ── Deterministic Coupon Unlock, Resend & Profile Completion Handlers ────
    is_complaint_or_refund_msg = is_complaint_or_refund(text)

    # --- Hybrid Intent Classification (Regex Fast-Path + LLM Fallback) ---
    # Step 1: Run regex detectors (fast, zero cost)
    _regex_profile = (not is_complaint_or_refund_msg) and is_profile_completed_signal(text, state)
    _regex_coupon = (not is_complaint_or_refund_msg) and is_coupon_request(text_lower)
    _regex_coupon_info = is_coupon_info_question(text)

    # In post-link stages, run unified router
    is_post_link_stage = bool(
        state.get("stage") in ["APP_LINK_SENT", "READY_FOR_APP_LINK", "PROFILE_COMPLETED", "COUPON_SENT"]
        or state.get("profile_created")
        or state.get("app_installed")
        or state.get("coupon_sent")
    )

    if is_post_link_stage:
        _intent = await classify_post_link_intent(
            text, state,
            regex_coupon_result=_regex_coupon,
            regex_profile_result=_regex_profile,
        )
    else:
        _intent = await classify_coupon_and_profile_intent(
            text, state,
            regex_coupon_result=_regex_coupon,
            regex_profile_result=_regex_profile,
            regex_coupon_info_result=_regex_coupon_info,
        )

    # Synchronize package if extracted by classifier
    active_course = state.get("course") or "yoga"
    course_pkgs = kb_loader.get_valid_packages(active_course)
    if _intent.get("package") and _intent["package"] in course_pkgs:
        state["package"] = _intent["package"]
        state["fee"] = course_pkgs[_intent["package"]]
    elif detected_pkg:
        state["package"] = detected_pkg
        state["fee"] = course_pkgs.get(detected_pkg, state.get("fee"))

    _is_app_link = is_app_link_request(text) or (_intent.get("intent") == "APP_LINK_REQUEST")

    profile_just_completed = (not is_complaint_or_refund_msg) and (not _is_app_link) and (
        _intent.get("is_profile_done") or (_intent.get("intent") == "COUPON_OR_PROFILE_DONE" and not _intent.get("is_coupon_request"))
    )
    if profile_just_completed:
        state["app_installed"] = True
        state["profile_created"] = True
        state["stage"] = advance_stage(state["stage"], "PROFILE_COMPLETED")

    _is_explicit_coupon_ask = (not is_complaint_or_refund_msg) and (not _is_app_link) and (
        _intent.get("is_coupon_request") or (_intent.get("intent") == "COUPON_OR_PROFILE_DONE")
    )

    hindi_markers = ["kya", "hai", "bhejo", "batao", "do", "kaha", "kha", "dobara", "phir", "fhrse", "mujhe", "konsa", "dega", "nhi", "nahi", "bhai", "dena"]
    has_hindi = any(w in text_lower for w in hindi_markers) or any("\u0900" <= ch <= "\u097F" for ch in text)

    # ── Deterministic App Link Dispatcher ────────────────────────────────────
    if _is_app_link:
        state["stage"] = advance_stage(state["stage"], "APP_LINK_SENT")
        arm_followup_timer(state, topic="app download")
        await save_user_state_async(phone, state)

        pkg_prefix_hi = f"Aapne {state.get('package')} package select kiya hai. 👍\n\n" if state.get("package") else ""
        pkg_prefix_en = f"You've selected the {state.get('package')} package. 👍\n\n" if state.get("package") else ""

        if has_hindi:
            reply = (
                f"{pkg_prefix_hi}"
                "Ye rahe Sensationz App aur website portal ke direct download links 📱✨\n\n"
                "📱 Android (Play Store): https://play.google.com/store/apps/details?id=com.sensationz.sensationz.dev\n"
                "🍎 iOS (App Store): https://apps.apple.com/us/app/sensationz/id6761418351\n"
                "💻 Website / PC / Laptop: https://shop.sensationzperformingarts.com/\n\n"
                "App download karein ya website visit karke apni profile complete karein. "
                "Uske baad yahan *Done* ya *Yes* reply karein, aur main turant aapka special welcome discount coupon code yahan bhej dunga! 🎁"
            )
        else:
            reply = (
                f"{pkg_prefix_en}"
                "Here are the download links for the Sensationz App and our website portal 📱✨\n\n"
                "📱 Android (Play Store): https://play.google.com/store/apps/details?id=com.sensationz.sensationz.dev\n"
                "🍎 iOS (App Store): https://apps.apple.com/us/app/sensationz/id6761418351\n"
                "💻 Website / PC / Laptop: https://shop.sensationzperformingarts.com/\n\n"
                "Please download the app or visit our website, complete your profile, and once done, "
                "reply *Done* or *Yes* here to receive your special welcome discount coupon code! 🎁"
            )

        reply = _format_for_whatsapp(reply)
        await send_text_message_async(phone, reply)
        latency_sec = round(time.time() - start_time, 2) if start_time else None
        asyncio.create_task(save_message_async(phone, "assistant", reply, response_time_sec=latency_sec))
        asyncio.create_task(log_message_async(phone, "ai", reply))
        return

    is_frustrated_coupon_ask = (
        (not is_complaint_or_refund_msg)
        and any(phrase in text_lower for phrase in [
            "coupon dega ya nahi", "coupon dega yaa nahi", "coupon dega ki nahi",
            "coupon kyu nahi de raha", "coupon kyun nahi de raha", "kab doge coupon",
            "code dega ya nahi", "code kyu nahi de raha", "fake coupon", "fraud coupon",
            "dhokha coupon"
        ])
        and (
            is_post_link_stage
            or profile_just_completed
        )
    )

    is_pure_question = (_intent.get("intent") == "QUESTION") and not (_intent.get("is_profile_done") or _intent.get("is_coupon_request"))

    # Condition to trigger coupon delivery:
    # In post-link stages or after profile creation, any affirmation / coupon request delivers the coupon banner immediately
    should_send_coupon_now = (
        (not is_complaint_or_refund_msg)
        and (not _is_app_link)
        and not is_pure_question
        and (
            (_is_explicit_coupon_ask and (state.get("profile_created") or is_post_link_stage))
            or is_frustrated_coupon_ask
            or profile_just_completed
            or (_intent.get("intent") == "COUPON_OR_PROFILE_DONE")
        )
    )

    if should_send_coupon_now:
        state["profile_created"] = True
        state["app_installed"] = True
        state["coupon_sent"] = True
        state["stage"] = advance_stage(state["stage"], "COUPON_SENT")

        reply = format_coupon_banner(
            state.get("package"),
            language="hindi" if has_hindi else "english",
            is_frustrated=is_frustrated_coupon_ask
        )
        arm_followup_timer(state, topic="coupon activation")
        await save_user_state_async(phone, state)
        await send_text_message_async(phone, reply)
        latency_sec = round(time.time() - start_time, 2) if start_time else None
        # Fire-and-forget background logging
        asyncio.create_task(save_message_async(phone, "assistant", reply, response_time_sec=latency_sec))
        asyncio.create_task(log_message_async(phone, "ai", reply))
        return

    elif _is_explicit_coupon_ask:
        active_course = state.get("course") or "yoga"
        course_pkgs = kb_loader.get_valid_packages(active_course)
        detected_pkg = detect_package_from_text(text, course_id=active_course)
        if detected_pkg:
            state["package"] = detected_pkg
            state["fee"] = course_pkgs.get(detected_pkg, state.get("fee"))

        app_links = kb_loader.format_app_links_display()
        # Stage A: User has NOT selected timing/package or reached app links -> Explain unlock requirement with app links
        reply = (
            "Aapka special welcome discount coupon code app ya website par profile banane ke baad unlock hota hai 🎁\n\n"
            "1️⃣ Sensationz App download karein ya website visit karein\n"
            "2️⃣ Profile complete karein\n"
            "3️⃣ Yahan *Done* ya *Yes* reply karein\n\n"
            f"Aur main turant aapka coupon code yahan bhej dunga!\n\n{app_links}"
        )
        state["stage"] = advance_stage(state["stage"], "APP_LINK_SENT")
        arm_followup_timer(state, topic=text)
        await save_user_state_async(phone, state)
        await send_text_message_async(phone, reply)
        latency_sec = round(time.time() - start_time, 2) if start_time else None
        # Fire-and-forget background logging
        asyncio.create_task(save_message_async(phone, "assistant", reply, response_time_sec=latency_sec))
        asyncio.create_task(log_message_async(phone, "ai", reply))
        return
    # ─────────────────────────────────────────────────────────────────────────



    # --- Genuine question / off-flow topic — goes to RAG ---
    t_rag = time.perf_counter()
    rag_result = await ask_rag_async(text, chat_history=history, state=state)
    full_reply = rag_result["reply"].strip()
    rag_sources = rag_result.get("sources", "")
    rag_retrieval_query = rag_result.get("retrieval_query", "")
    print(f"[TIMING] {phone} rag_query: {time.perf_counter() - t_rag:.2f}s")

    # Resolve system action tags ([UNLOCK_COUPON]) and sync session state
    from src.state.chat_state import resolve_llm_reply_tags
    active_course = state.get("course") or "yoga"
    did_unlock, full_reply = resolve_llm_reply_tags(full_reply, state, course_id=active_course, user_text=text)

    # Strip any "type agent" line the LLM generated, count it silently,
    # only resurface after 2 CONSECUTIVE flagged replies.
    flagged_this_turn = bool(AGENT_SUGGEST_PATTERN.search(full_reply))
    full_reply = AGENT_SUGGEST_PATTERN.sub("", full_reply).strip()

    # Redact / sanitize any leaked coupon codes if user has not completed profile setup
    full_reply = _sanitize_locked_coupons(full_reply, state)

    if flagged_this_turn:
        state["low_confidence_count"] = state.get("low_confidence_count", 0) + 1
    else:
        state["low_confidence_count"] = 0

    followup_separate = None  # Will be sent as a second WhatsApp message if set

    if state.get("low_confidence_count", 0) >= 2:
        # Only suggest agent if the reply was genuinely short/unhelpful (< 60 words)
        if len(full_reply.split()) < 60:
            full_reply += _agent_nudge(text)
        state["low_confidence_count"] = 0  # reset after nudging
    else:
        followup = get_flow_followup(state)
        if followup and not should_skip_followup(text, full_reply, state.get("stage")):
            # Issue 4: Two-message stream — answer first, stage question separately
            followup_separate = followup.strip()

    # Clean trailing LLM-generated questions so full_reply is purely the direct answer (Message 1)
    full_reply = _strip_trailing_questions(full_reply)

    # Post-LLM State Transitions
    if state.get("stage") == "READY_FOR_APP_LINK":
        state["stage"] = advance_stage(state["stage"], "APP_LINK_SENT")

    # Only keep nudging the customer if the flow isn't finished yet
    # if state.get("stage") not in ["COUPON_SENT"]:
    arm_followup_timer(state, topic=text)
    await save_user_state_async(phone, state)

    # Format full_reply and followup_separate for WhatsApp rendering
    full_reply = _format_for_whatsapp(full_reply)
    # Compute dynamic contextual follow-up message (Message 2) with curiosity & questions
    # to engage the customer, unless stage follow-up (e.g. package selection) is already active.
    if not followup_separate:
        dynamic_followup = await generate_contextual_followup_async(text, full_reply, history, state)
        if dynamic_followup:
            followup_separate = _format_for_whatsapp(dynamic_followup)

    t_send = time.perf_counter()
    if followup_separate:
        await send_text_message_async(phone, full_reply)
        await asyncio.sleep(1)
        await send_text_message_async(phone, followup_separate)
        combined = full_reply + "\n\n" + followup_separate
        print(f"[TIMING] {phone} interakt_send (2-msg): {time.perf_counter() - t_send:.2f}s")
        latency_sec = round(time.time() - start_time, 2) if start_time else None
        # Fire-and-forget background logging to Supabase and CSV without blocking user reply
        asyncio.create_task(save_message_async(phone, "assistant", combined, response_time_sec=latency_sec))
        asyncio.create_task(log_message_async(phone, "ai", combined, sources=rag_sources, retrieval_query=rag_retrieval_query))
    else:
        await send_text_message_async(phone, full_reply)
        latency_sec = round(time.time() - start_time, 2) if start_time else None
        # Fire-and-forget background logging to Supabase and CSV without blocking user reply
        asyncio.create_task(save_message_async(phone, "assistant", full_reply, response_time_sec=latency_sec))
        asyncio.create_task(log_message_async(phone, "ai", full_reply, sources=rag_sources, retrieval_query=rag_retrieval_query))
# ---------------------------------------------------------------------------
# Main processing pipeline (NO per-phone Redis lock — debouncer handles it)
# ---------------------------------------------------------------------------

async def _execute_pipeline_async(phone: str, text: str, referral: dict = None):
    """Internal execution logic for message processing."""
    start_time = time.time()

    # Log 1: Incoming message
    print("\n" + "="*80)
    print(f"[1] 📩 INCOMING : {phone} -> {repr(text)}")

    # Log 2: Target check & decision
    is_target = is_target_ad_or_message(text, referral, phone)

    if not is_target:
        # Log 3: Ignored action
        print(f"[3] 🚫 ACTION   : {phone} -> Ignored (No reply sent)")
        print("="*80 + "\n")
        return

    # Persist target flag
    try:
        state = await get_user_state_async(phone)
        if not state.get("is_target_ad"):
            state["is_target_ad"] = True
            await save_user_state_async(phone, state)
    except Exception as e:
        state = {}
    reset_follow_up_timer(state)
    await save_user_state_async(phone, state)

    # Fetch history
    try:
        history = await get_recent_history_async(phone)
    except Exception as e:
        history = []

    # Save incoming message (fire-and-forget in background)
    try:
        asyncio.create_task(save_message_async(phone, "user", text))
        asyncio.create_task(log_message_async(phone, "user", text))
    except Exception as e:
        pass

    # Developer / Tester control commands
    cmd = text.strip().lower()
    if cmd in ["#reset", "/reset", "reset chat", "reset session"]:
        await reset_user_state_async(phone)
        reply = "🔄 Session reset successfully! Escalation cleared and AI re-enabled. How can I help you? 😊"
        await send_text_message_async(phone, reply)
        print(f"[3] 🔄 ACTION   : {phone} -> Reset session & unescalated")
        print("="*80 + "\n")
        return
    elif cmd in ["#unescalate", "/unescalate", "#bot", "#ai", "#enable", "unescalate"]:
        from src.state.chat_state import clear_escalation_async
        await clear_escalation_async(phone)
        reply = "🤖 AI Assistant re-enabled for this number! How can I help you? 😊"
        await send_text_message_async(phone, reply)
        print(f"[3] 🤖 ACTION   : {phone} -> Unescalated & AI re-enabled")
        print("="*80 + "\n")
        return

    # Check escalation
    if await is_escalated_async(phone):
        print(f"[3] 👤 ACTION   : {phone} -> Already escalated to agent (AI staying out)")
        print("="*80 + "\n")
        return

    # Round-robin agent assignment (async, non-blocking)
    if PRIORITY_AGENT_EMAIL and not state.get("already_assigned"):
        success = await assign_chat_to_agent_async(phone, PRIORITY_AGENT_EMAIL)
        if success:
            state["already_assigned"] = True
            await save_user_state_async(phone, state)

    # Check for human agent trigger words
    text_lower = text.lower()
    if matches_any(text_lower, AGENT_TRIGGER_WORDS):
        await handle_agent_handoff_async(phone, start_time)
        print(f"[3] 👤 ACTION   : {phone} -> Handed off to human agent")
        print("="*80 + "\n")
        return

    # AI reply
    await handle_ai_reply_async(phone, text, history, start_time)
    print(f"[3] 🤖 ACTION   : {phone} -> AI Reply sent successfully (Message 1 + Message 2)")
    print("="*80 + "\n")




async def process_incoming_message_async(phone: str, text: str, referral: dict = None):
    """
    Fully async message processing pipeline with In-Flight Busy Guard and Queue Drain.
    If the AI is actively generating a reply for this phone number, new incoming messages
    are safely queued and processed together as 1 clean follow-up turn when active processing ends.
    """
    proc_lock_key = f"is_processing:{phone}"
    pending_queue_key = f"pending_queue:{phone}"

    # Check if AI is currently busy generating a reply for this user
    if redis_conn.get(proc_lock_key):
        print(f"[tasks] {phone}: AI is currently busy generating a reply — queueing message '{text}'")
        redis_conn.rpush(pending_queue_key, text)
        return

    # Acquire lock (30s max safety TTL)
    redis_conn.setex(proc_lock_key, 30, "true")

    try:
        await _execute_pipeline_async(phone, text, referral)
    finally:
        # Drain pending queue if any messages arrived while AI was typing
        try:
            raw_pending = redis_conn.lrange(pending_queue_key, 0, -1)
            redis_conn.delete(pending_queue_key)
            if raw_pending:
                pending_msgs = [m.decode() if isinstance(m, bytes) else m for m in raw_pending]
                combined_pending = "\n".join(pending_msgs)
                print(f"[tasks] {phone}: draining {len(pending_msgs)} queued messages -> '{combined_pending}'")
                # Release processing lock before recursive drain call
                redis_conn.delete(proc_lock_key)
                await process_incoming_message_async(phone, combined_pending, referral=referral)
            else:
                redis_conn.delete(proc_lock_key)
        except Exception as ex:
            redis_conn.delete(proc_lock_key)
            print(f"[tasks] {phone}: error during queue drain: {ex}")


