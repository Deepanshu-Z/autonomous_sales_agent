"""
chat_state.py — Session state management & funnel stage tracking per unique user.
Stores slot values (batch timing, selected package, app installation, profile status)
and ensures each unique phone number maintains its own isolated conversation state.
"""

import os
import re
from dotenv import load_dotenv
from supabase import create_client
from src.core.rag import extract_slot_llm
import asyncio
from rapidfuzz import fuzz
# Load environment variables (.env file)
load_dotenv()

# Initialize optional Supabase database client for persistent cloud state
supabase = None
try:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if url and key:
        supabase = create_client(url, key)
except Exception as e:
    print(f"[chat_state] Supabase init error: {e}")

# In-memory session dictionary: maps unique phone number -> user state dictionary
import time
import threading
_memory_lock = threading.Lock()
_memory_sessions: dict[str, dict] = {}



def matches_any(text: str, words: list[str]) -> bool:
    t = text.lower().strip()
    if any(re.search(rf"\b{re.escape(w)}\b", t) for w in words):
        return True
    if len(t) <= 20:
        return any(fuzz.ratio(t, w) > 85 for w in words)
    return False

STAGE_ORDER = ["NEW", "ENROLL_ASKED", "ENROLL_CONFIRMED", "TIMING_SELECTED", "PACKAGE_ASKED", "TRIAL_REQUESTED", "TRIAL_STEPS_SENT",
               "PACKAGE_SELECTED", "READY_FOR_APP_LINK", "APP_LINK_SENT", "PROFILE_COMPLETED", "COUPON_SENT"]


def advance_stage(current: str, proposed: str) -> str:
    cur_idx = STAGE_ORDER.index(current) if current in STAGE_ORDER else 0
    new_idx = STAGE_ORDER.index(proposed) if proposed in STAGE_ORDER else 0
    return proposed if new_idx >= cur_idx else current



def mark_escalated(phone: str):
    """
    Marks a user's phone number as escalated to a human agent so the AI bot stops automated replies.
    """
    if supabase:
        try:
            # Upsert into database table 'escalated_chats'
            supabase.table("escalated_chats").upsert({"phone": phone}).execute()
        except Exception as e:
            print(f"[chat_state] mark_escalated DB error: {e}")
    # Also update state in memory session cache
    state = get_user_state(phone)
    state["is_escalated"] = True
    save_user_state(phone, state)

def is_escalated(phone: str) -> bool:
    """
    Checks whether a specific phone number has been escalated to a human agent.
    """
    if supabase:
        try:
            # Query Supabase table for escalation status
            result = (
                supabase.table("escalated_chats")
                .select("phone")
                .eq("phone", phone)
                .limit(1)
                .execute()
            )
            if len(result.data) > 0:
                return True
        except Exception as e:
            print(f"[chat_state] is_escalated DB error: {e}")
    
    # Fallback to checking in-memory session state
    state = get_user_state(phone)
    return state.get("is_escalated", False)


def clear_escalation(phone: str):
    """
    Removes human agent escalation for a phone number so the bot can resume automated replies.
    """
    if supabase:
        try:
            # Remove row from Supabase escalated_chats table
            supabase.table("escalated_chats").delete().eq("phone", phone).execute()
        except Exception as e:
            print(f"[chat_state] clear_escalation DB error: {e}")
    state = get_user_state(phone)
    state["is_escalated"] = False
    save_user_state(phone, state)


import json
from src.services.redis_client import get_redis_connection

redis_conn = get_redis_connection()
STATE_CACHE_TTL = 60 * 60 * 24 * 30  # 30 days persistence across restarts


def get_default_state(phone: str) -> dict:
    """
    Returns the initial default session state dictionary for a new phone number.
    Ensures new users start fresh with stage 'NEW' and no default timing or package selected.
    """
    return {
        "phone": phone,               # Customer's unique phone number key
        "stage": "NEW",               # Funnel stage: NEW -> ENROLL_ASKED -> ENROLL_CONFIRMED -> TIMING_SELECTED -> PACKAGE_SELECTED -> READY_FOR_APP_LINK -> APP_LINK_SENT -> PROFILE_COMPLETED -> COUPON_SENT
        "timing": None,               # Selected batch timing string (e.g. "7:00–8:00 AM")
        "package": None,              # Selected package string (e.g. "3 Months")
        "fee": None,                  # Package fee string (e.g. "₹1,750")
        "app_installed": False,       # Boolean flag indicating if user installed the Sensationz App
        "profile_created": False,     # Boolean flag indicating if user created an in-app profile
        "coupon_sent": False,         # Boolean flag indicating if welcome coupon code was delivered
        "wants_trial": False,         # Boolean flag indicating if user preferred a demo/trial class first
        "is_escalated": False,        # Escalation flag to hand off to human agent
        "course": "yoga",             # Active course context (e.g. "yoga", "kathak")

        "is_target_ad": False,        # Whether user verified via secret string (AI enabled)
        "low_confidence_count": 0,     # Consecutive unanswered / low-confidence query count
        "follow_up_count": 0,
        "next_follow_up_due_at": 0,
        "last_topic": 0    
    }


def reset_follow_up_timer(state: dict):
    state["next_follow_up_due_at"] = None
    state["next_followup_due_at"] = None
    state["follow_up_count"] = 0

def arm_followup_timer(state: dict, topic:str , delay_seconds:int = 300):
    """ Call when ever bot sends a reply - start/reset in 5 min"""
    state["next_followup_due_at"] = time.time() + delay_seconds
    state["last_topic"] = topic[:200]
    state["follow_up_count"] = 0


def get_user_state(phone: str) -> dict:
    """
    Retrieves active session state dictionary for a phone number.
    Checks memory cache (5s TTL) -> Redis -> Supabase -> Default.
    """
    MEMORY_TTL = 5  # seconds — prevents stale reads in multi-worker mode

    # 1. Check in-memory cache (with TTL)
    with _memory_lock:
        if phone in _memory_sessions:
            entry = _memory_sessions[phone]
            if isinstance(entry, tuple):
                cached_state, cached_at = entry
                if (time.time() - cached_at) < MEMORY_TTL:
                    return cached_state
            elif isinstance(entry, dict):
                return entry  # legacy format, no TTL

    # 2. Check Redis cache (persists across server restarts)
    state = get_default_state(phone)
    redis_key = f"user_state:{phone}"
    try:
        raw_state = redis_conn.get(redis_key)
        if raw_state:
            cached_dict = json.loads(raw_state)
            state.update(cached_dict)
            with _memory_lock:
                _memory_sessions[phone] = (state, time.time())
            return state
    except Exception as e:
        print(f"[chat_state] Redis get_user_state read failed (non-fatal): {e}")

    # 3. Fallback to Supabase database lookup
    if supabase:
        try:
            res = supabase.table("user_session_state").select("*").eq("phone", phone).limit(1).execute()
            if res.data and len(res.data) > 0:
                state.update(res.data[0])
        except Exception as e:
            print(f"[chat_state] Supabase get_user_state read failed: {e}")

    # Save to Redis & memory cache for future fast reads
    with _memory_lock:
        _memory_sessions[phone] = (state, time.time())
    try:
        redis_conn.setex(redis_key, STATE_CACHE_TTL, json.dumps(state))
    except Exception:
        pass

    return state


def save_user_state(phone: str, state: dict):
    """
    Persists updated session state dictionary across Memory, Redis, and Supabase.
    """
    with _memory_lock:
        _memory_sessions[phone] = (state, time.time())
    redis_key = f"user_state:{phone}"

    # Persist in Redis across restarts
    try:
        redis_conn.setex(redis_key, STATE_CACHE_TTL, json.dumps(state))
    except Exception as e:
        print(f"[chat_state] Redis save_user_state failed (non-fatal): {e}")

    # Persist in Supabase DB
    if supabase:
        try:
            SUPABASE_STATE_COLUMNS = {
                "phone", "stage", "timing", "package", "fee", "app_installed",
                "profile_created", "coupon_sent", "is_escalated", "is_target_ad",
                "low_confidence_count", "already_assigned",
                "follow_up_count", "next_followup_due_at", "last_topic"
            }
            db_state = {k: v for k, v in state.items() if k in SUPABASE_STATE_COLUMNS}
            db_state["phone"] = phone
            if "next_followup_due_at" in db_state and db_state["next_followup_due_at"] is not None:
                val = db_state["next_followup_due_at"]
                if isinstance(val, (int, float)):
                    from datetime import datetime, timezone
                    db_state["next_followup_due_at"] = datetime.fromtimestamp(val, tz=timezone.utc).isoformat()
            supabase.table("user_session_state").upsert(db_state, on_conflict="phone").execute()
        except Exception as e:
            print(f"[chat_state] Supabase save_user_state failed: {e}")


# ---------------------------------------------------------------------------
# Async non-blocking state wrappers using asyncio.to_thread
# ---------------------------------------------------------------------------
async def get_user_state_async(phone: str) -> dict:
    """Non-blocking async wrapper to retrieve user state."""
    return await asyncio.to_thread(get_user_state, phone)

async def save_user_state_async(phone: str, state: dict):
    """Non-blocking async wrapper to persist user state."""
    return await asyncio.to_thread(save_user_state, phone, state)

async def mark_escalated_async(phone: str):
    """Non-blocking async wrapper to mark user as escalated."""
    return await asyncio.to_thread(mark_escalated, phone)

async def is_escalated_async(phone: str) -> bool:
    """Non-blocking async wrapper to check escalation status."""
    return await asyncio.to_thread(is_escalated, phone)

async def clear_escalation_async(phone: str):
    """Non-blocking async wrapper to clear escalation."""
    return await asyncio.to_thread(clear_escalation, phone)


def reset_user_state(phone: str) -> dict:
    """Completely resets a user's session state in-memory, in Redis, and in Supabase."""
    with _memory_lock:
        _memory_sessions.pop(phone, None)
    try:
        redis_conn.delete(f"user_state:{phone}")
        redis_conn.delete(f"phone-lock:{phone}")
        redis_conn.delete(f"is_processing:{phone}")
        redis_conn.delete(f"pending_queue:{phone}")
    except Exception as e:
        print(f"[chat_state] Redis reset error for {phone}: {e}")
    if supabase:
        try:
            supabase.table("user_session_state").delete().eq("phone", phone).execute()
            supabase.table("escalated_chats").delete().eq("phone", phone).execute()
        except Exception as e:
            print(f"[chat_state] Supabase reset error for {phone}: {e}")
    initial = get_default_state(phone)
    save_user_state(phone, initial)
    return initial


async def reset_user_state_async(phone: str) -> dict:
    """Non-blocking async wrapper to reset user state."""
    return await asyncio.to_thread(reset_user_state, phone)


def is_user_asking_question(text: str) -> bool:

    """
    Determines whether a user input message is an informational question
    (e.g. asking about video links, pricing, syllabus, faculty, or demo sessions)
    rather than a simple procedural step selection or initial greeting.
    """
    txt = text.lower().strip()
    
    # Question marks explicitly signal an informational question
    if "?" in txt:
        return True
    
    # Simple greetings or initial "yoga" mentions are NOT questions
    if txt in ["hi", "hii", "hello", "hey", "yoga", "yog", "online yoga", "yoga classes", "namaste"]:
        return False

    # Strong single-word keywords that are always questions/informational requests
    strong_question_keywords = [
        "price", "prices", "pricing", "cost", "costs", "costly", "expensive", 
        "fee", "fees", "charge", "charges", "rate", "rates", "rupee", "rupees", "rs",
        "paise", "paisa", "rupaye", "rupay", "dene", "pdnge", "padenge", "lagenge", "lagega", "lagte",
        "pay", "payment", "payments", "refund", "refunds",
        "discount", "discounts", "offer", "offers", "coupon", "coupons",
        "syllabus", "curriculum", "timing", "timings", "schedule", "schedules",
        "demo", "trial", "sample", "video", "videos", "recording", "recordings",
        "detail", "details", "info", "information", "link", "links", "website",
        "teacher", "teachers", "trainer", "trainers", "instructor", "instructors", "faculty"
    ]
    if any(kw in txt for kw in strong_question_keywords):
        return True

    # Priority keywords for class video/demo requests (triggers question mode even for short phrases)
    video_keywords = [
        "video", "videos", "demo", "recording", "recordings",
        "sample", "watch", "link", "trial", "youtube"
    ]
    if any(vk in txt for vk in video_keywords):
        return True
    
    # General informational question keywords
    question_keywords = [
        "who", "what", "where", "how", "why", "which", "when",
        "kitne", "kitna", "kya", "kaun", "kon", "kaunsa", "konsa", "kaunsi", "konsi", "kaise", "kyu", "kyon", "kab",
        "kiska", "kiski", "kiske", "kaha", "kahan",
        "teacher", "teachers", "faculty", "student", "students",
        "discount", "costly", "expensive", "syllabus", "demo", "trial",
        "hindi", "english", "classes", "fees", "language", "languages",
        "btao", "batao", "bataen", "bataiye", "detail", "details", "info", "information"
    ]
    
    words = txt.split()
    # If text contains general question keywords with 2 or more words
    if len(words) >= 2 and any(kw in txt for kw in question_keywords):
        return True
        
    return False

from src.core.coupons import VALID_PACKAGES, detect_package_from_text, normalize_package_name


GREETING_WORDS = ["hi", "hii", "hello", "hey", "namaste", "good morning", "good evening", "good afternoon"]
CONFIRMATION_WORDS = [
    "yes", "yeah", "yep", "sure", "ok", "okay", "enroll", "join",
    "interested", "i want to join", "haan", "han",
    "karna hai", "kar do", "haan ji", "proceed", "done", "thik", "thik hai",
    "accha", "acha", "theek", "theek hai", "sahi", "sahi hai", "got it", "understood", "fine"
]

def _detect_timing(text_lower: str) -> tuple:
    """
    Comprehensive timing detection supporting all user typing styles:
    Hindi 'X se Y', compact 'XYam', 'X to Y am/pm', 'X-Y am/pm', 'Xam/Xpm',
    'morning X', 'subah X', 'evening X', 'shaam X', 'night X'.
    Returns: (timing_str or None, is_ambiguous: bool, ambiguous_range: str)
    """
    t = text_lower

    def has(*pats):
        return any(p in t for p in pats)

    def re_has(pattern):
        return bool(re.search(pattern, t))

    # === Priority 0: Explicit check for UNAVAILABLE / UNSUPPORTED batch requests ===
    # If user explicitly asks for batch ranges that do not exist (e.g. 11-12, 1-2, 2-3, 3-4, 8-9 PM, 9-10, 10-11 PM),
    # return None so slot extraction NEVER locks it in, allowing RAG to explain it is unavailable.
    unsupported_patterns = [
        # 11:00 AM - 12:00 PM / 11:00 AM
        r"(?<![0-9])11\s*(?:to|-|se|\s)\s*12\b",
        r"(?<![0-9])11\s*:\s*00\s*(?:to|-|se|\s)\s*12\s*:\s*00\b",
        r"\b11se12\b",
        r"\b11to12\b",
        r"\b11-12\b",
        r"(?<![0-9])11\s*am\b",
        r"(?<![0-9])11\s*(?:baje|bje|ka\s*batch)\b",
        r"\bgyara(?:h)?\s*baje\b",

        # 1:00 PM - 2:00 PM / 1:00 PM
        r"(?<![0-9])1\s*(?:to|-|se|\s)\s*2\b",
        r"\b1-2\b",
        r"(?<![0-9])1\s*pm\b",
        r"(?<![0-9])1\s*(?:baje|bje)\b",
        r"\bek\s*baje\b",

        # 2:00 PM - 3:00 PM / 2:00 PM
        r"(?<![0-9])2\s*(?:to|-|se|\s)\s*3\b",
        r"\b2-3\b",
        r"(?<![0-9])2\s*pm\b",
        r"(?<![0-9])2\s*(?:baje|bje)\b",
        r"\bdo\s*baje\b",

        # 3:00 PM - 4:00 PM / 3:00 PM
        r"(?<![0-9])3\s*(?:to|-|se|\s)\s*4\b",
        r"\b3-4\b",
        r"(?<![0-9])3\s*pm\b",
        r"(?<![0-9])3\s*(?:baje|bje)\b",
        r"\bteen\s*baje\b",

        # 8:00 PM - 9:00 PM / 8:00 PM
        r"(?<![0-9])8\s*(?:to|-|se|\s)\s*9\s*pm\b",
        r"(?<![0-9])8\s*:\s*00\s*(?:to|-|se|\s)\s*9\s*:\s*00\s*pm\b",
        r"\b8se9pm\b",
        r"\b8to9pm\b",
        r"\b8-9pm\b",
        r"\b89pm\b",
        r"(?<![0-9])8\s*pm\b",

        # 9:00 - 10:00 (AM or PM) / 9:00 (AM or PM)
        r"(?<![0-9])9\s*(?:to|-|se|\s)\s*10\b",
        r"(?<![0-9])9\s*:\s*00\s*(?:to|-|se|\s)\s*10\s*:\s*00\b",
        r"\b9se10\b",
        r"\b9to10\b",
        r"\b9-10\b",
        r"(?<![0-9])9\s*am\b",
        r"(?<![0-9])9\s*pm\b",
        r"(?<![0-9])9\s*(?:baje|bje)\b",
        r"\bnau\s*baje\b",

        # 10:00 PM - 11:00 PM / 10:00 PM
        r"(?<![0-9])10\s*(?:to|-|se|\s)\s*11\s*pm\b",
        r"(?<![0-9])10\s*:\s*00\s*(?:to|-|se|\s)\s*11\s*:\s*00\s*pm\b",
        r"\b10se11pm\b",
        r"\b10to11pm\b",
        r"\b10-11pm\b",
        r"\b1011pm\b",
        r"(?<![0-9])10\s*pm\b",

        # 11:00 PM, 12:00 AM - 4:00 AM
        r"(?<![0-9])11\s*pm\b",
        r"(?<![0-9])12\s*am\b",
        r"(?<![0-9])1\s*am\b",
        r"(?<![0-9])2\s*am\b",
        r"(?<![0-9])3\s*am\b",
        r"(?<![0-9])4\s*am\b",
        r"(?<![0-9])4\s*(?:to|-|se|\s)\s*5\s*am\b",
    ]
    if any(re_has(p) for p in unsupported_patterns):
        return None, False, ""

    # === Priority 1: Explicit MORNING / SUBAH / AM ranges & indicators ===
    # 5:00–6:00 AM
    if (has("5 se 6 am", "5se6am", "5 to 6 am", "5to6am", "5-6 am", "5-6am", "5 6 am", "56am", "5:00 am", "5:00am", "subah 5", "5 baje subah", "morning 5", "5 morning", "morning 5 am", "5 am morning", "subah 5 baje", "5 bje subah", "subah 5 se 6", "5 to 6 morning", "5-6 morning")
            or re_has(r"(?<![0-9\-])\b5(?:\s*:\s*00)?\s*am\b")
            or re_has(r"\b(?:morning|subah)\s*5(?:\s*:\s*00)?(?:\s*am|\s*baje|\s*bje|\b)")
            or re_has(r"\b5(?:\s*:\s*00)?\s*(?:baje|bje)?\s*(?:subah|morning)\b")):
        return "5:00–6:00 AM", False, ""

    # 6:00–7:00 AM
    if (has("6 se 7 am", "6se7am", "6 to 7 am", "6to7am", "6-7 am", "6-7am", "6 7 am", "67am", "6:00 am", "6:00am", "subah 6", "6 baje subah", "morning 6", "6 morning", "morning 6 am", "6 am morning", "subah 6 baje", "6 bje subah", "subah 6 se 7", "6 to 7 morning", "6-7 morning")
            or re_has(r"(?<![0-9\-])\b6(?:\s*:\s*00)?\s*am\b")
            or re_has(r"\b(?:morning|subah)\s*6(?:\s*:\s*00)?(?:\s*am|\s*baje|\s*bje|\b)")
            or re_has(r"\b6(?:\s*:\s*00)?\s*(?:baje|bje)?\s*(?:subah|morning)\b")):
        return "6:00–7:00 AM", False, ""

    # 7:00–8:00 AM
    if (has("7 se 8 am", "7se8am", "7 to 8 am", "7to8am", "7-8 am", "7-8am", "7 8 am", "78am", "7:00 am", "7:00am", "subah 7", "7 baje subah", "morning 7", "7 morning", "morning 7 am", "7 am morning", "subah 7 baje", "7 bje subah", "subah 7 se 8", "7 to 8 morning", "7-8 morning")
            or re_has(r"(?<![0-9\-])\b7(?:\s*:\s*00)?\s*am\b")
            or re_has(r"\b(?:morning|subah)\s*7(?:\s*:\s*00)?(?:\s*am|\s*baje|\s*bje|\b)")
            or re_has(r"\b7(?:\s*:\s*00)?\s*(?:baje|bje)?\s*(?:subah|morning)\b")):
        return "7:00–8:00 AM", False, ""

    # 8:00–9:00 AM
    if (has("8 se 9 am", "8se9am", "8 to 9 am", "8to9am", "8-9 am", "8-9am", "8 9 am", "89am", "8:00 am", "8:00am", "subah 8", "8 baje subah", "morning 8", "8 morning", "morning 8 am", "8 am morning", "subah 8 baje", "8 bje subah", "subah 8 se 9", "8 to 9 morning", "8-9 morning")
            or re_has(r"(?<![0-9\-])\b8(?:\s*:\s*00)?\s*am\b")
            or re_has(r"\b(?:morning|subah)\s*8(?:\s*:\s*00)?(?:\s*am|\s*baje|\s*bje|\b)")
            or re_has(r"\b8(?:\s*:\s*00)?\s*(?:baje|bje)?\s*(?:subah|morning)\b")):
        return "8:00–9:00 AM", False, ""

    # 10:00–11:00 AM
    if (has("10 se 11 am", "10se11am", "10 to 11 am", "10to11am", "10-11 am", "10-11am", "10 11 am", "1011am", "10:00 am", "10:00am", "subah 10", "10 baje subah", "morning 10", "10 morning", "morning 10 am", "10 am morning", "subah 10 baje", "10 bje subah", "das baje", "subah 10 se 11", "10 to 11 morning", "10-11 morning")
            or re_has(r"(?<![0-9\-])\b10(?:\s*:\s*00)?\s*am\b")
            or re_has(r"\b(?:morning|subah)\s*10(?:\s*:\s*00)?(?:\s*am|\s*baje|\s*bje|\b)")
            or re_has(r"\b10(?:\s*:\s*00)?\s*(?:baje|bje)?\s*(?:subah|morning)\b")):
        return "10:00–11:00 AM", False, ""

    # === Priority 2: Explicit AFTERNOON / EVENING / SHAAM / NIGHT / PM ranges & indicators ===
    # 12:00–1:00 PM
    if (has("12 se 1 pm", "12se1pm", "12 to 1 pm", "12to1pm", "12-1 pm", "12-1pm", "12 1 pm", "121pm", "12:00 pm", "12:00pm", "dopahar", "dophar", "dupehar", "dupahar", "lunch", "baarah baje", "12 baje dopahar", "afternoon", "aftern", "aftrnoon", "aftrn", "aftn", "after noon", "afternoon batch", "afternoon slot", "afternoon timing", "afternoon class")
            or re_has(r"(?<![0-9\-])\b12(?:\s*:\s*00)?\s*pm\b")
            or re_has(r"\b(?:afternoon|aftern|aftrnoon|aftrn|aftn|dopahar|dophar|dupehar|dupahar)\b")
            or re_has(r"\b(?:afternoon|aftern|dopahar|dophar)\s*12\b")):
        return "12:00–1:00 PM", False, ""

    # 4:00–5:00 PM
    if (has("4 se 5 pm", "4se5pm", "4 to 5 pm", "4to5pm", "4-5 pm", "4-5pm", "4 5 pm", "45pm", "4:00 pm", "4:00pm", "shaam 4", "4 baje shaam", "evening 4", "4 evening", "evening 4 pm", "4 pm evening", "shaam 4 baje", "4 bje shaam", "night 4 pm", "shaam 4 se 5", "4 to 5 evening", "4-5 evening")
            or re_has(r"(?<![0-9\-])\b4(?:\s*:\s*00)?\s*pm\b")
            or re_has(r"\b(?:evening|shaam|night)\s*4(?:\s*:\s*00)?(?:\s*pm|\s*baje|\s*bje|\b)")
            or re_has(r"\b4(?:\s*:\s*00)?\s*(?:baje|bje)?\s*(?:shaam|evening|night)\b")):
        return "4:00–5:00 PM", False, ""

    # 5:00–6:00 PM
    if (has("5 se 6 pm", "5se6pm", "5 to 6 pm", "5to6pm", "5-6 pm", "5-6pm", "5 6 pm", "56pm", "5:00 pm", "5:00pm", "shaam 5", "5 baje shaam", "evening 5", "5 evening", "evening 5 pm", "5 pm evening", "shaam 5 baje", "5 bje shaam", "night 5", "night 5 pm", "shaam 5 se 6", "5 to 6 evening", "5-6 evening", "5 to 6 shaam", "5-6 shaam")
            or re_has(r"(?<![0-9\-])\b5(?:\s*:\s*00)?\s*pm\b")
            or re_has(r"\b(?:evening|shaam|night)\s*5(?:\s*:\s*00)?(?:\s*pm|\s*baje|\s*bje|\b)")
            or re_has(r"\b5(?:\s*:\s*00)?\s*(?:baje|bje)?\s*(?:shaam|evening|night)\b")):
        return "5:00–6:00 PM", False, ""

    # 6:00–7:00 PM
    if (has("6 se 7 pm", "6se7pm", "6 to 7 pm", "6to7pm", "6-7 pm", "6-7pm", "6 7 pm", "67pm", "6:00 pm", "6:00pm", "shaam 6", "6 baje shaam", "evening 6", "6 evening", "evening 6 pm", "6 pm evening", "shaam 6 baje", "6 bje shaam", "night 6", "night 6 pm", "shaam 6 se 7", "6 to 7 evening", "6-7 evening", "6 to 7 shaam", "6-7 shaam")
            or re_has(r"(?<![0-9\-])\b6(?:\s*:\s*00)?\s*pm\b")
            or re_has(r"\b(?:evening|shaam|night)\s*6(?:\s*:\s*00)?(?:\s*pm|\s*baje|\s*bje|\b)")
            or re_has(r"\b6(?:\s*:\s*00)?\s*(?:baje|bje)?\s*(?:shaam|evening|night)\b")):
        return "6:00–7:00 PM", False, ""

    # 7:00–8:00 PM
    if (has("7 se 8 pm", "7se8pm", "7 to 8 pm", "7to8pm", "7-8 pm", "7-8pm", "7 8 pm", "78pm", "7:00 pm", "7:00pm", "shaam 7", "7 baje shaam", "evening 7", "7 evening", "evening 7 pm", "7 pm evening", "shaam 7 baje", "7 bje shaam", "night 7", "night 7 pm", "shaam 7 se 8", "7 to 8 evening", "7-8 evening", "7 to 8 shaam", "7-8 shaam")
            or re_has(r"(?<![0-9\-])\b7(?:\s*:\s*00)?\s*pm\b")
            or re_has(r"\b(?:evening|shaam|night)\s*7(?:\s*:\s*00)?(?:\s*pm|\s*baje|\s*bje|\b)")
            or re_has(r"\b7(?:\s*:\s*00)?\s*(?:baje|bje)?\s*(?:shaam|evening|night)\b")):
        return "7:00–8:00 PM", False, ""

    # === Priority 3: Unambiguous ranges/times WITHOUT AM/PM (only one slot exists in entire 24h schedule) ===
    if ("pm" not in t and "night" not in t and "shaam" not in t and "evening" not in t) and (
        has("10 to 11", "10 se 11", "10-11", "10 11", "10 baje", "10 bje", "das baje") or re_has(r"(?<![0-9])\b10\b")
    ):
        return "10:00–11:00 AM", False, ""

    if ("pm" not in t and "night" not in t and "shaam" not in t and "evening" not in t) and (
        has("8 to 9", "8 se 9", "8-9", "8 9", "8 baje", "8 bje", "aath baje") or re_has(r"(?<![0-9])\b8\b")
    ):
        return "8:00–9:00 AM", False, ""

    if ("am" not in t and "morning" not in t and "subah" not in t) and (
        has("4 to 5", "4 se 5", "4-5", "4 5", "4 baje", "4 bje", "chaar baje") or re_has(r"(?<![0-9])\b4\b")
    ):
        return "4:00–5:00 PM", False, ""

    if has("12 to 1", "12 se 1", "12-1", "12 1", "12 baje", "12 bje", "dopahar") or re_has(r"(?<![0-9])\b12\b"):
        return "12:00–1:00 PM", False, ""

    # === Priority 4: Ambiguous times & ranges (both AM and PM slots exist — ask user for AM/PM clarification) ===
    if re_has(r"(?<![0-9\-])\b6(?:\s*:\s*00)?(?:\s*baje|\s*bje|\s*se|\s*to|-|\b)"):
        return None, True, "6:00"
    if re_has(r"(?<![0-9\-])\b7(?:\s*:\s*00)?(?:\s*baje|\s*bje|\s*se|\s*to|-|\b)"):
        return None, True, "7:00"
    if re_has(r"(?<![0-9\-])\b5(?:\s*:\s*00)?(?:\s*baje|\s*bje|\s*se|\s*to|-|\b)"):
        return None, True, "5:00"

    return None, False, ""



def is_profile_completed_signal(text: str, state: dict = None) -> bool:
    """
    Dynamically and robustly detects if the user indicates that they have created
    their profile, registered an account, downloaded the app, or completed setup.
    Handles Hindi, Hinglish, English, slang, and coupled coupon requests.
    Guards against how-to / future inquiries (e.g. 'profile kaise banaye', 'kese kru').
    """
    if not text:
        return False
    t = text.lower().strip()

    # 0. Negations / problem markers (profile creation failed, error, unable)
    NEGATION_MARKERS = [
        r"\b(nahi|nhi|not|unable|fail|failed|error|issue|problem)\b.*?\b(ban|bana|bna|create|download|install|chal|open|ho\s*raha|ho\s*rahi|hua|hui)\b",
        r"\b(ban|bana|bna|create|download|install)\b.*?\b(nahi|nhi|not|unable)\b",
    ]
    if any(re.search(p, t) for p in NEGATION_MARKERS):
        return False

    # 1. Emphatic assertions of prior completion (overrides any rhetorical inquiry in the same sentence)
    # e.g., "bana to li bhai ab kese banau", "bna to li", "kar to liya", "maine to bana li", "already bana li"
    EMPHATIC_ASSERTION_PATTERNS = [
        r"\b(bana|bna)\s+to\s+li\b",
        r"\b(kar|kr)\s+to\s+(liya|diya)\b",
        r"\bho\s+to\s+(gaya|gya|gayi|gai)\b",
        r"\b(already|pehle\s+se|pehle\s+hi)\s+(created|made|done|completed|registered|downloaded|installed|bana|banali|banayi)\b",
        r"\bmaine\s+to\s+(bana|create)\b",
    ]
    if any(re.search(p, t) for p in EMPHATIC_ASSERTION_PATTERNS):
        return True

    # 2. Check for pure how-to / future questions or imperatives (inquiry/request, not completion)
    # e.g., "bnalo", "banalo", "bana do", "profile kaise banaye", "kese banau", "app download karni hai"
    # Note: "ab kya karna hai" / "aage kya karein" are next-step questions, NOT how-to-create inquiries!
    INQUIRY_MARKERS = [
        r"\b(kaise|kese|how|kyun)\b.*?\b(bana|create|kare|kru|download|register|fill|khol)",
        r"\b(banana|banani)\s+h(ai)?\b",
        r"\b(download|install)\s*(karna|krna|karni|krni)\s+h(ai)?\b",
        r"\b(create|banane)\s*(karna|krna|karni|krni|chahiye)\s+h(ai)?\b",
        r"\b(bnalo|banalo|bana\s*do|bna\s*do|kardo|krdo|kar\s*do|kr\s*do)\b",
        r"\bkese\s+kru\b",
        r"\bkaise\s+karein?\b",
        r"\bhelp\s+chahiye\b",
    ]

    # 3. Standalone past-action completion verbs (Hindi/Hinglish/English)
    # e.g. "bnali", "banali", "bna li", "bana li", "banadi", "bnadi", "kardi", "krdi", "kardiya", "ho gaya", "done", "created"
    # Also covers informal present-perfect: "bna le hai", "kar le hai", "le liya hai", "hgya h", "krdia na"
    STANDALONE_PAST_VERBS = [
        r"\b(bnali|banali|bna\s*li|bana\s*li|banadi|bnadi|bna\s*di|bana\s*di|bana\s*diya|bna\s*diya|bana\s*liya|banaliya|bna\s*liya|bnaliya)\b",
        r"\b(kardi|krdi|kardiya|krdiya|kar\s*liya|kr\s*liya|karliya|krliya|karlia|krlia|kar\s*lia|kr\s*lia|kar\s*li|kr\s*li|karli|krli|kar\s*diya|kr\s*diya|kardia|krdia|kar\s*dia|kr\s*dia|kar\s*di|kr\s*di)\s*(?:na|ji|bhai|yaar|sir|mam)?\b",
        r"\b(ho\s*gaya|hogaya|ho\s*gya|hogya|h\s*gaya|hgaya|h\s*gya|hgya|ban\s*gaya|bangaya|ban\s*gya|bangya|ban\s*gayi|bangayi|ban\s*gai|bangai)\b",
        r"\b(created|completed|registered|setup\s*done|all\s*done)\b",
        # Informal present-perfect: "bna le hai", "bana le hai", "kar le hai", "kr le hai", "hgya h"
        r"\b(bna|bana)\s*le\s*(hai|h|he)\b",
        r"\b(kar|kr)\s*le\s*(hai|h|he)\b",
        r"\b(hgya|hogya|ho\s*gya|ho\s*gaya|krdia|krdiya|kar\s*diya|bna\s*li|banali)\s*(hai|h|he|na)\b",
        # "le liya hai", "le li hai" (have done it)
        r"\ble\s*(liya|li)\s*(hai|h|he)?\b",
    ]

    # 4. Direct noun + past-verb semantic combinations
    # Noun: profile / account / id / details / app
    NOUN_RE = r"(profile|profil|profl|account|acnt|acc|id|details?)"
    PAST_VERB_RE = (
        r"("
        r"create\s*k(ar|r)\s*li|create\s*k(ar|r)\s*liya|create\s*k(ar|r)\s*di|create\s*k(ar|r)\s*diya|"
        r"create\s*kiya|create\s*ho\s*g(aya|ya|yi|i)|created|"
        r"bana\s*li|banali|bna\s*li|bnali|bana\s*diya|banadi|bna\s*diya|bnadi|bna\s*di|bana\s*di|bana\s*liya|banaliya|bna\s*liya|bnaliya|"
        r"banaya|banayi|"
        r"bana\s*chuka|bna\s*chuka|ban\s*chuki|ban\s*chuka|"
        r"ban\s*gayi|bangayi|ban\s*gai|bangai|ban\s*gya|bangya|ban\s*gaya|bangaya|"
        r"kar\s*liya|kr\s*liya|karliya|krliya|karlia|krlia|kar\s*lia|kr\s*lia|kardiya|krdiya|kardia|krdia|kar\s*dia|kr\s*dia|kar\s*li|kr\s*li|karli|krli|kar\s*di|kr\s*di|kardi|krdi|"
        r"ho\s*g(aya|ya|yi|i)|hog(aya|ya|yi|i)|"
        r"ready|done|completed?|setup|set|registered"
        r")"
    )

    # App download / install completion
    APP_NOUN_RE = r"(app|application|sensationz)"
    APP_PAST_VERB_RE = (
        r"(download\s*k(ar|r)\s*li|download\s*k(ar|r)\s*liya|download\s*ho\s*g(aya|ya|yi|i)|downloaded|"
        r"install\s*k(ar|r)\s*li|install\s*k(ar|r)\s*liya|install\s*ho\s*g(aya|ya|yi|i)|installed|"
        r"khol\s*li|open\s*k(ar|r)\s*li)"
    )

    _EXPLICIT_COMPLETION_PHRASES = [
        "profile created", "profile done", "profile complete", "created profile",
        "profile ban gayi", "profile ban gai", "profile bana li", "profile banali",
        "profile bna li", "profile bnali", "profile banadi", "profile bnadi",
        "profile bna di", "profile bana di", "profile ready", "profile set",
        "account created", "account ban gaya", "account bana liya", "profile setup",
        # Colloquial task/setup completions
        "krlia", "karliya", "krliya", "karlia", "kr lia", "kar lia",
        "krdiya", "kardiya", "krdia", "kardia", "kr dia", "kar dia", "karli", "krli",
        "hogya", "hogaya", "ho gaya", "hgya", "h gya", "h gaya", "hgaya",
        "hgya h", "hogya h", "ho gya h", "krdia na", "krdiya na", "kar diya na", "kardiya na",
        "both done", "sab ho gaya", "sab ho gya", "dono ho gaya", "dono ho gya",
        "dono kaam", "dono kaam ho gaye", "dono kaam nipat gaye", "dono kaam nipat gya",
        "nipat gaya", "nipat gaye", "nipat gya", "nipta diya", "sab nipta diya",
        "kaam ho gaya", "kaam hogaya", "kaam khatam", "khatam kar diya", "finish kar diya",
        "download kar liya", "download kr liya", "install kar liya", "install kr liya",
        "app done", "downloaded", "installed", "app installed", "app downloaded",
        "setup done", "all done", "done profile created", "profile created done",
        "done created", "yes created", "id bana li", "id banali", "id ban gayi",
        "login kar liya", "login ho gaya", "signup kar liya", "sign up ho gaya",
        "registered", "registered on app", "profile is ready", "profile is done",
        # Informal present-perfect (common Hindi/Hinglish)
        "profile bna le hai", "profile bana le hai",
        "profile bna le h", "profile bana le h",
        "profile kar le hai", "profile kar le h",
        "account bna le hai", "account bana le hai",
        "sab kar le hai", "sab kar le h",
    ]

    has_past_completion = (
        any(re.search(p, t) for p in STANDALONE_PAST_VERBS)
        or bool(re.search(rf"\b{NOUN_RE}\b.*?{PAST_VERB_RE}", t))
        or bool(re.search(rf"{PAST_VERB_RE}.*?\b{NOUN_RE}\b", t))
        or bool(re.search(rf"\b{APP_NOUN_RE}\b.*?{APP_PAST_VERB_RE}", t))
        or bool(re.search(rf"{APP_PAST_VERB_RE}.*?\b{APP_NOUN_RE}\b", t))
        or any(p in t for p in _EXPLICIT_COMPLETION_PHRASES)
    )

    # 5. Contextual Affirmations (strictly when in app link / profile creation stage)
    current_stage = (state.get("stage") if state else "") or ""
    if current_stage in ["APP_LINK_SENT", "READY_FOR_APP_LINK"]:
        _AFFIRMATIVE_SIGNALS = [
            "done", "yes", "haan", "haa", "han", "hn", "ha", "ji haan", "ji han", "ji hn", "bilkul",
            "yes done", "haan done", "hn done", "han done", "ok done", "done done", "kar diya", "kardiya",
            "kr diya", "krdiya", "krdia", "kardia", "kr dia", "kar dia", "kar li", "krli", "ho gaya", "hogaya",
            "ho gya", "hogya", "hgya", "h gya", "h gaya", "hgaya", "ban gaya", "bangaya", "ban gayi", "bangayi",
            "bnali", "banali", "bna li", "bana li", "bna di", "banadi", "bnadi", "kardi", "krdi", "hnji", "hanji",
            "krdia na", "krdiya na", "kar diya na", "kardiya na", "hgya h", "hogya h", "ho gya h", "hn hn hgya h"
        ]
        words = re.findall(r"\w+", t)
        affirmative_words = {
            "done", "yes", "haan", "han", "hn", "haa", "ha", "hnji", "hanji", "bilkul",
            "hgya", "hogya", "krdia", "krdiya", "banali", "bnali", "banadi", "bnadi"
        }
        if any(w in affirmative_words for w in words):
            has_past_completion = True
        elif any(p in t for p in _AFFIRMATIVE_SIGNALS):
            has_past_completion = True

    # If there's an affirmative completion signal, it takes precedence over next-step questions
    if has_past_completion:
        return True

    # Otherwise, if it matches an inquiry marker, reject it
    if any(re.search(p, t) for p in INQUIRY_MARKERS):
        return False

    return False


async def extract_and_update_slots(phone: str, text: str, chat_history: list = None) -> dict:
    """
    Analyzes incoming user message, extracts batch timing or package selection,
    and updates funnel stage (NEW -> ENROLL_ASKED -> ENROLL_CONFIRMED -> TIMING_SELECTED -> PACKAGE_ASKED -> READY_FOR_APP_LINK).
    """
    # Retrieve current session state for this phone number asynchronously
    state = await get_user_state_async(phone)
    prev_stage = state.get("stage") or "NEW"
    # Reset follow-up counter whenever customer is actively chatting
    state["follow_up_count"] = 0

    text_lower = text.lower().strip()
    is_q = is_user_asking_question(text)

    # Keywords for initial contact vs confirmation vs slots
    is_greeting = matches_any(text_lower, GREETING_WORDS)
    is_yoga_keyword = any(w in text_lower for w in ["yoga", "yog", "yaga", "yogi", "yoga classes", "online yoga", "yoga details", "yoga course"])
    is_confirmation = matches_any(text_lower, CONFIRMATION_WORDS)

    # --- 0.1 Detect App Install & Profile Completion (High Priority Semantic Engine) ---
    is_disinterest_pending = state.get("disinterest_asked_feedback", False)
    is_profile_done = False
    if not is_disinterest_pending:
        is_profile_done = is_profile_completed_signal(text, state)

    if is_profile_done:
        state["app_installed"] = True
        state["profile_created"] = True
        state["stage"] = advance_stage(state["stage"], "PROFILE_COMPLETED")
    elif any(w in text_lower for w in ["app download", "download ho gaya", "download ho gya", "app download kar"]) and not state.get("profile_created"):
        state["app_installed"] = True

    # Stage 0: Initial Greeting & Enrollment Confirmation Check (Only if profile not already confirmed)
    if state["stage"] in ["NEW", "ENROLL_ASKED"] and not state.get("profile_created"):
        has_enroll_intent = (is_confirmation or any(w in text_lower for w in ["price", "fees", "fee", "timing", "timings", "enroll", "join", "classes", "start", "how to", "kaise", "proceed"]))
        if has_enroll_intent:
            state["stage"] = advance_stage(state["stage"], "ENROLL_CONFIRMED")
            state["timing"] = None
            state["package"] = None
            state["fee"] = None
        elif is_greeting or is_yoga_keyword:
            state["stage"] = advance_stage(state["stage"], "ENROLL_ASKED")
            state["timing"] = None
            state["package"] = None
            state["fee"] = None

    # --- 0.2 Detect Trial / Demo Request Intent ---
    trial_intent_triggers = [
        "trial", "demo", "demo class", "trial class", "free trial", "free demo",
        "pehle trial", "pehle demo", "try class", "trial lena", "demo lena",
        "after the demo", "after demo", "after trial", "after the trial",
        "pick a package after", "choose after demo", "decide after demo",
        "demo ke baad", "trial ke baad", "pehle try"
    ]
    if any(t in text_lower for t in trial_intent_triggers) and state.get("stage") not in ["PROFILE_COMPLETED", "COUPON_SENT"]:
        state["wants_trial"] = True
        if state.get("stage") not in ["TRIAL_STEPS_SENT", "READY_FOR_APP_LINK", "APP_LINK_SENT"]:
            state["stage"] = "TRIAL_REQUESTED"

    # --- 0.5 Detect Intent to Change / Remove Slots ---

    change_kws = ["change", "remove", "galat", "wrong", "dusra", "delete", "nahi chahiye", "cancel"]
    if any(kw in text_lower for kw in change_kws) and state["stage"] != "NEW":
        changed = False
        if any(w in text_lower for w in ["timing", "time", "batch", "slot", "baje"]):
            state["timing"] = None
            if state["stage"] in ["TIMING_SELECTED", "PACKAGE_ASKED", "PACKAGE_SELECTED", "READY_FOR_APP_LINK", "APP_LINK_SENT"]:
                state["stage"] = "ENROLL_CONFIRMED"
            changed = True
        
        if any(w in text_lower for w in ["package", "plan", "duration", "month", "months", "year", "mahina"]):
            state["package"] = None
            state["fee"] = None
            if state["stage"] in ["PACKAGE_SELECTED", "READY_FOR_APP_LINK", "APP_LINK_SENT"]:
                state["stage"] = "TIMING_SELECTED"
            changed = True
            
        if not changed and ("remove it" in text_lower or "change it" in text_lower or "change" in text_lower):
            # General fallback if ambiguous
            state["timing"] = None
            state["package"] = None
            state["fee"] = None
            if state["stage"] in ["TIMING_SELECTED", "PACKAGE_ASKED", "PACKAGE_SELECTED", "READY_FOR_APP_LINK", "APP_LINK_SENT"]:
                state["stage"] = "ENROLL_CONFIRMED"

    # --- 1. Detect Batch Timing Slot ---
    timing_found = False
    if not state.get("timing") and not is_q:  # Only detect if timing not already set and user is NOT asking a question
        # Check pending ambiguous timing resolution (e.g. user was asked to clarify '5:00' -> AM or PM)
        pending_amb = state.get("pending_ambiguous_timing")
        if pending_amb:
            tokens = [w.strip(".,!?:;\"'") for w in text_lower.split()]
            is_am = any(w in ["am", "subah", "morning", "mrng", "morng", "sawere", "savere"] for w in tokens) or any(w in text_lower for w in ["subah", "morning", "sawere", "savere"]) or text_lower in ["am", "subah", "morning"]
            is_pm = any(w in ["pm", "shaam", "evening", "evng", "night", "raat", "sham"] for w in tokens) or any(w in text_lower for w in ["shaam", "evening", "night", "raat", "sham"]) or text_lower in ["pm", "shaam", "evening"]
            
            resolved_timing = None
            if is_am and not is_pm:
                if "5" in pending_amb:
                    resolved_timing = "5:00–6:00 AM"
                elif "6" in pending_amb:
                    resolved_timing = "6:00–7:00 AM"
                elif "7" in pending_amb:
                    resolved_timing = "7:00–8:00 AM"
            elif is_pm and not is_am:
                if "5" in pending_amb:
                    resolved_timing = "5:00–6:00 PM"
                elif "6" in pending_amb:
                    resolved_timing = "6:00–7:00 PM"
                elif "7" in pending_amb:
                    resolved_timing = "7:00–8:00 PM"
            
            if resolved_timing:
                state["timing"] = resolved_timing
                state["stage"] = advance_stage(state["stage"], "TIMING_SELECTED")
                state.pop("pending_ambiguous_timing", None)
                state.pop("ambiguous_timing_range", None)
                timing_found = True

        if not timing_found:
            timing_str, is_ambiguous, ambiguous_range = _detect_timing(text_lower)
            if timing_str:
                state["timing"] = timing_str
                state["stage"] = advance_stage(state["stage"], "TIMING_SELECTED")
                timing_found = True
                state.pop("ambiguous_timing_range", None)  # clear any pending ambiguity
                state.pop("pending_ambiguous_timing", None)
            elif is_ambiguous:
                state["ambiguous_timing_range"] = ambiguous_range
                timing_found = True  # Prevents LLM fallback from executing and overriding ambiguous timings

    # --- 2. Detect Package / Duration Slot ---
    # Check if a package is mentioned in the text using robust pattern matching
    detected_pkg = detect_package_from_text(text)

    is_package_detected = False

    # Whenever a package duration is mentioned or asked about, immediately update active package slot
    if detected_pkg:
        state["package"] = detected_pkg
        state["fee"] = VALID_PACKAGES[detected_pkg]
        is_package_detected = True
    elif not is_q:
        # Fallback for bare numbers in package stage
        is_package_stage = (state.get("timing") is not None or state["stage"] in ["TIMING_SELECTED", "PACKAGE_ASKED", "PACKAGE_SELECTED"])
        _short_text = len(text_lower.strip()) <= 15
        tokens = [w.strip(".,!?:;₹") for w in text_lower.replace(",", "").split()]
        if _short_text and any(w in tokens for w in ["5000", "1800", "3850"]):
            state["package"] = "1 Year"
            state["fee"] = VALID_PACKAGES["1 Year"]
            is_package_detected = True
        elif _short_text and any(w in tokens for w in ["3200", "1000", "2050"]):
            state["package"] = "6 Months"
            state["fee"] = VALID_PACKAGES["6 Months"]
            is_package_detected = True
        elif _short_text and any(w in tokens for w in ["1750", "600"]):
            state["package"] = "3 Months"
            state["fee"] = VALID_PACKAGES["3 Months"]
            is_package_detected = True
        elif _short_text and any(w in tokens for w in ["700", "300", "500"]):
            state["package"] = "1 Month"
            state["fee"] = VALID_PACKAGES["1 Month"]
            is_package_detected = True

    needs_llm_fallback = (
        not timing_found and not is_package_detected
        and not is_greeting
        and not is_q
        and len(text_lower) >= 1
        and state["stage"] in ["TIMING_SELECTED", "PACKAGE_ASKED", "PACKAGE_SELECTED", "ENROLL_CONFIRMED"]
    )
    if needs_llm_fallback:
        slot_result = await extract_slot_llm(text, chat_history)
        if slot_result["timing"] and not state.get("timing"):
            state["timing"] = slot_result["timing"]
            state["stage"] = advance_stage(state["stage"], "TIMING_SELECTED")
        if slot_result["package"] and not state.get("package"):
            state["package"] = slot_result["package"]
            state["fee"] = VALID_PACKAGES[slot_result["package"]]
        
    # Update funnel stage dynamically based on timing and package availability
    if state.get("timing") and state.get("package"):
        state["stage"] = advance_stage(state["stage"], "READY_FOR_APP_LINK")
    elif state.get("timing"):
        state["stage"] = advance_stage(state["stage"], "TIMING_SELECTED")
    elif state.get("package"):
        state["stage"] = advance_stage(state["stage"], "PACKAGE_SELECTED")

    # Stage Transition when user confirms timing -> ask for package duration
    if state["stage"] == "TIMING_SELECTED" and is_confirmation and not is_q and not state.get("package"):
        state["stage"] = advance_stage(state["stage"], "PACKAGE_ASKED")


    # Persist updated session state asynchronously
    await save_user_state_async(phone, state)
    return state
