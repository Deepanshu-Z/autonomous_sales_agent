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
from src.core.conversation_handler import handle_conversation_turn_async
import re
from src.core import kb_loader
from src.core.lead_logger import log_lead_action, extract_lead_details


load_dotenv()

redis_conn = get_redis_connection()

# --- Agent Config (Centralized in agent_handoff.py) ---
from src.services.agent_handoff import AGENT_POOL, PRIORITY_AGENT_EMAIL, OWNER_EMAIL

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

# Round-robin agent selection imported from src.services.agent_handoff


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
def check_target_ad(text: str, referral_data: dict = None, phone: str = None) -> tuple[bool, str]:
    """
    Evaluates whether an incoming message qualifies for bot response.
    Returns: (is_target: bool, reason: str)
    1. Already-verified user (state flag: is_target_ad=True) → PASS
    2. First message: Match exact Ad ID from ENV (TARGET_AD_ID) in referral data → PASS
    3. Whitelist / TEST_MODE → PASS
    4. Otherwise → FAIL
    """
    if phone:
        try:
            state = get_user_state(phone)
            if state.get("is_target_ad") is True:
                return True, "Already verified customer (is_target_ad=True)"
        except Exception:
            pass

    # Check TEST_MODE / ALLOWED_TEST_NUMBERS bypass for direct WhatsApp testing
    if os.getenv("TEST_MODE", "").strip().lower() in ("true", "1", "yes"):
        return True, "TEST_MODE is enabled in environment"

    allowed_raw = os.getenv("ALLOWED_TEST_NUMBERS", "").strip()
    if allowed_raw and phone:
        allowed_test = [p.strip().replace("+", "") for p in allowed_raw.split(",") if p.strip()]
        clean_phone = phone.replace("+", "").strip()
        if clean_phone in allowed_test:
            return True, "Whitelisted in ALLOWED_TEST_NUMBERS"

    if isinstance(referral_data, str):
        try:
            referral_data = json.loads(referral_data)
        except Exception:
            pass

    # Match Ad ID from referral metadata strictly against registered campaigns in ad_campaigns.json
    if referral_data and isinstance(referral_data, dict):
        source_id = str(referral_data.get("source_id") or "").strip()
        source_url = str(referral_data.get("source_url") or "").strip()
        ad_id = str(referral_data.get("ad_id") or "").strip()
        received_id = source_id or ad_id

        from src.core import campaign_loader
        if received_id and campaign_loader.is_registered_ad_id(received_id):
            return True, f"Campaign Ad ID matched: '{received_id}'"

        return False, f"Ad ID '{received_id}' is not registered in ad_campaigns.json"

    return False, "No referral data (organic / non-ad message)"


def is_target_ad_or_message(text: str, referral_data: dict = None, phone: str = None) -> bool:
    """Checks if a message qualifies for bot response."""
    is_target, reason = check_target_ad(text, referral_data, phone)
    if is_target:
        print(f"[2] 🎯 DECISION : {phone} -> WILL REPLY | Reason: {reason}")
    else:
        print(f"[2] 🎯 DECISION : {phone} -> WILL NOT REPLY | Reason: {reason}")
    return is_target



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

    active_course = state.get("course")
    if not active_course:
        return None

    # 2. If timing is selected but package is missing -> ALWAYS prompt for package duration
    if state.get("timing") and not state.get("package"):
        fees_block = kb_loader.format_package_fees_display(active_course)
        return (
            "Which package duration would you like to start with? 😊\n\n"
            f"Fees:\n{fees_block}\n\n"
            "*Note:* Offer price will be only applicable through app and Ganesh Chaturthi Offer coupon. Once the app is downloaded and the profile is created, the Ganesh Chaturthi Offer coupon will be sent here."
        )

    # 3. If ready for app download & profile setup (first time only)
    if state.get("stage") == "READY_FOR_APP_LINK":
        # [PREVIOUS LOGIC - COMMENTED OUT]:
        # app_links = kb_loader.format_app_links_display()
        # return (
        #     "Aapke liye ek special welcome discount code hai 🎁\n\n"
        #     "Sirf Sensationz App download karein ya website par jayein aur apna profile banayein — "
        #     f"uske baad *Done* ya *Yes* reply karein, aur main turant aapka coupon code bhej dunga!\n\n{app_links}"
        # )
        return (
            "Aapke liye ek special welcome discount code hai 🎁\n\n"
            "Play Store ya App Store par \"Sensationz\" search karke app download karein aur Google se login karke apna profile banayein — "
            "uske baad *Done* ya *Yes* reply karein, aur main turant aapka coupon code bhej dunga! 😊"
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
    return reply  # Coupons are now unrestricted
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
    course_id = state.get("course")
    if course_id:
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
            cleaned = re.sub(pat, "Ganesh Chaturthi Offer coupon", cleaned, flags=re.IGNORECASE)

    if leaked:
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        if "profile" not in cleaned.lower() and "done" not in cleaned.lower():
            cleaned += "\n\n🎁🪔 *(Ganesh Chaturthi Offer coupon code app ya website par profile complete karne ke baad yahan receive hoga!)*"
    return cleaned

async def handle_ai_reply_async(phone: str, text: str, history: list, start_time: float = None, referral: dict = None):
    t0 = time.perf_counter()

    # Developer/tester reset command
    if text.strip().lower() in ["reset", "#reset", "/reset", "reset chat", "reset session"]:
        await reset_user_state_async(phone)
        reply = "🔄 Session reset successfully! You can now test from the beginning as a new customer. How can I help you? 😊"
        await send_text_message_async(phone, reply)
        return

    state = await get_user_state_async(phone)

    # Initial Campaign Welcome Flow (First Contact)
    from src.core import campaign_loader
    from src.services.quick_reply_service import send_followup_with_buttons_async

    has_prior_ai_reply = any(t.get("role") in ("assistant", "ai") for t in (history or []))

    # Turn 1: Send campaign welcome messages & buttons from ad_campaigns.json directly
    if not state.get("welcome_sent") and not has_prior_ai_reply:
        campaign = campaign_loader.match_campaign(referral=referral, text=text)
        if not campaign:
            print(f"[tasks] {phone}: No registered campaign matches referral ad_id — ignoring 1st message.")
            return

        matched_course = campaign.get("course")
        if matched_course:
            state["course"] = matched_course
        state["is_target_ad"] = True
        state["welcome_sent"] = True

        welcome_msgs = campaign_loader.get_welcome_messages(campaign)
        msg1 = welcome_msgs.get("message_1")
        msg2 = welcome_msgs.get("message_2")
        buttons = campaign_loader.get_campaign_buttons(campaign)

        if msg1:
            await send_text_message_async(phone, msg1)
            latency_sec = round(time.time() - start_time, 2) if start_time else None
            asyncio.create_task(save_message_async(phone, "assistant", msg1, response_time_sec=latency_sec))
            asyncio.create_task(log_message_async(phone, "ai", msg1))
            await asyncio.sleep(1.5)

        if msg2:
            await send_followup_with_buttons_async(phone, msg2, buttons=buttons)
            asyncio.create_task(save_message_async(phone, "assistant", msg2))
            asyncio.create_task(log_message_async(phone, "ai", msg2))

        arm_followup_timer(state, topic="welcome message")
        await save_user_state_async(phone, state)
        return

    # All subsequent turns handled dynamically by Two-Step Semantic Architecture (zero hardcoded values)
    await handle_conversation_turn_async(phone, text, history=history, start_time=start_time)
# ---------------------------------------------------------------------------
# Main processing pipeline (NO per-phone Redis lock — debouncer handles it)
# ---------------------------------------------------------------------------

async def _execute_pipeline_async(phone: str, text: str, referral: dict = None):
    """Internal execution logic for message processing."""
    start_time = time.time()

    # Log 1: Incoming message
    print("\n" + "="*80)


    # Log 2: Target check & decision
    is_target, target_reason = check_target_ad(text, referral, phone)
    lead_details = extract_lead_details(referral, phone=phone)

    if not is_target:
        # Log 3: Ignored action
        print("="*80 + "\n")
        log_lead_action(
            lead_no=phone,
            lead_details=lead_details,
            action_taken="IGNORED",
            reason=target_reason,
        )
        return

    # Persist target flag and ad details in state
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
    except Exception as e:
        state = {}
    reset_follow_up_timer(state)
    await save_user_state_async(phone, state)

    # Re-extract lead details with populated state
    lead_details = extract_lead_details(referral, state=state, phone=phone)

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
    if cmd in ["reset", "#reset", "/reset", "reset chat", "reset session"]:
        await reset_user_state_async(phone)
        reply = "🔄 Session reset successfully! Escalation cleared and AI re-enabled. How can I help you? 😊"
        await send_text_message_async(phone, reply)
        print(f"[3] 🔄 ACTION   : {phone} -> Reset session & unescalated")
        print("="*80 + "\n")
        log_lead_action(
            lead_no=phone,
            lead_details=lead_details,
            action_taken="REPLIED",
            reason="Session reset command executed",
        )
        return
    elif cmd in ["#unescalate", "/unescalate", "#bot", "#ai", "#enable", "unescalate"]:
        from src.state.chat_state import clear_escalation_async
        await clear_escalation_async(phone)
        reply = "🤖 AI Assistant re-enabled for this number! How can I help you? 😊"
        await send_text_message_async(phone, reply)
        log_lead_action(
            lead_no=phone,
            lead_details=lead_details,
            action_taken="REPLIED",
            reason="AI re-enabled command executed",
        )
        return

    # Check escalation
    if await is_escalated_async(phone):
        print(f"[3] 👤 ACTION   : {phone} -> Already escalated to agent (AI staying out)")
        print("="*80 + "\n")
        log_lead_action(
            lead_no=phone,
            lead_details=lead_details,
            action_taken="IGNORED",
            reason="Already escalated to human agent (AI staying out)",
        )
        return

    # Initial lead assignment to Supreme Owner for monitoring
    if not state.get("already_assigned"):
        if OWNER_EMAIL:
            success = await assign_chat_to_agent_async(phone, OWNER_EMAIL)
            if success:
                state["already_assigned"] = True
                state["assigned_agent_email"] = OWNER_EMAIL
                await save_user_state_async(phone, state)

    # Check for human agent trigger words
    text_lower = text.lower()
    if matches_any(text_lower, AGENT_TRIGGER_WORDS):
        await handle_agent_handoff_async(phone, start_time)
        print(f"[3] 👤 ACTION   : {phone} -> Handed off to human agent")
        print("="*80 + "\n")
        log_lead_action(
            lead_no=phone,
            lead_details=lead_details,
            action_taken="REPLIED",
            reason="Customer requested human agent (Handed off to support team)",
        )
        return

    # AI reply
    await handle_ai_reply_async(phone, text, history, start_time, referral=referral)
    log_lead_action(
        lead_no=phone,
        lead_details=lead_details,
        action_taken="REPLIED",
        reason=f"{target_reason} — AI response sent",
    )




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


