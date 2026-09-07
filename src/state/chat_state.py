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
    
    # Explicit enrollment intent statements (e.g. "I want to join...", "enroll karna hai") without question marks are NOT questions
    enroll_statements = ["i want to join", "want to join", "join karna hai", "enroll karna hai", "admission lena hai", "i want to enroll", "mujhe join karna"]
    if any(st in txt for st in enroll_statements) and not any(qw in txt for qw in ["?", "kya", "kaise", "how", "what", "can i"]):
        return False

    # Simple greetings are NOT questions
    if txt in ["hi", "hii", "hello", "hey", "namaste", "good morning", "good evening"]:
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
        "fees", "btao", "batao", "bataen", "bataiye", "detail", "details", "info", "information"
    ]
    
    words = txt.split()
    # If text contains general question keywords with 2 or more words
    if len(words) >= 2 and any(kw in txt for kw in question_keywords):
        return True
        
    return False

from src.core.coupons import detect_package_from_text, normalize_package_name
from src.core import kb_loader


GREETING_WORDS = ["hi", "hii", "hello", "hey", "namaste", "good morning", "good evening", "good afternoon"]
CONFIRMATION_WORDS = [
    "yes", "yeah", "yep", "sure", "ok", "okay", "enroll", "join",
    "interested", "i want to join", "haan", "han",
    "karna hai", "kar do", "haan ji", "proceed", "done", "thik", "thik hai",
    "accha", "acha", "theek", "theek hai", "sahi", "sahi hai", "got it", "understood", "fine"
]

def normalize_text_for_timing(text: str) -> str:
    t = text.lower().strip()
    # Normalize unicode dashes (en-dash, em-dash, minus, horizontal bar) to standard ASCII hyphen
    t = re.sub(r"[\u2010\u2012\u2013\u2014\u2015\u2212]", "-", t)
    # Replace slashes between digits with hyphen (e.g. 5/6 -> 5-6)
    t = re.sub(r"(\d+)\s*/\s*(\d+)", r"\1-\2", t)
    # Remove :00 minutes e.g. 5:00 -> 5, 6:00 -> 6
    t = re.sub(r"(\d{1,2}):00", r"\1", t)
    return t


def detect_timing_from_text(text: str, course_id: str = "yoga") -> tuple:
    """
    Dynamically identifies a batch timing slot from user text based on the active course's valid timings.
    Supports English, Hindi, Hinglish, compact forms (6am, 5-6pm, subah 6, shaam 7).
    Returns: (timing_str or None, is_ambiguous: bool, ambiguous_range: str)
    """
    if not text:
        return None, False, ""
    t = f" {normalize_text_for_timing(text)} "

    valid_timings = kb_loader.get_valid_timings(course_id)

    is_am = bool(re.search(r"\b(?:am|morning|subah|sawere|savere|mrng|morng)\b", t))
    is_pm = bool(re.search(r"\b(?:pm|evening|shaam|sham|night|raat|dopahar|dophar|dupehar|dupahar|afternoon|aftern|lunch)\b", t))

    def re_has(p):
        return bool(re.search(p, t))

    # Priority 1: Range extraction (e.g. 6-7, 6 to 7, 6 7 am, chhe se saat)
    range_regex = r"(?<![0-9])(\d{1,2})\s*(?:am|pm)?\s*(?:to|-|se|\s)\s*(\d{1,2})\s*(am|pm)?\b"
    compact_regex = r"\b(\d{1,2})(\d{1,2})\s*(am|pm)\b"

    m_compact = re.search(compact_regex, t)
    m_range = re.search(range_regex, t)

    start_hr, end_hr, explicit_merid = None, None, None

    if m_range:
        s_candidate = int(m_range.group(1))
        e_candidate = int(m_range.group(2))
        start_hr, end_hr = s_candidate, e_candidate
        explicit_merid = (m_range.group(3) or "").lower()
        if not explicit_merid and m_range.group(0):
            if "am" in m_range.group(0) and not is_pm:
                explicit_merid = "am"
            elif "pm" in m_range.group(0) and not is_am:
                explicit_merid = "pm"

    if start_hr is None and m_compact:
        start_hr = int(m_compact.group(1))
        end_hr = int(m_compact.group(2))
        explicit_merid = (m_compact.group(3) or "").lower()

    hindi_range_map = [
        (("paanch", "chhe"), 5, 6), (("panch", "che"), 5, 6),
        (("chhe", "saat"), 6, 7), (("che", "saat"), 6, 7),
        (("saat", "aath"), 7, 8), (("aath", "nau"), 8, 9),
        (("das", "gyarah"), 10, 11), (("baarah", "ek"), 12, 1),
        (("chaar", "paanch"), 4, 5)
    ]
    for (w1, w2), s_h, e_h in hindi_range_map:
        if re.search(rf"\b{w1}\s*(?:se|to|-)\s*{w2}\b", t):
            start_hr, end_hr = s_h, e_h
            break

    if start_hr is not None and end_hr is not None:
        merid = explicit_merid or ("am" if is_am and not is_pm else "pm" if is_pm and not is_am else None)
        cands = [vt for vt in valid_timings if re.search(rf"\b{start_hr}(?::00)?\s*(?:to|-|–)\s*{end_hr}", vt)]
        if merid:
            matched = [vt for vt in cands if merid.upper() in vt.upper()]
            if matched:
                return matched[0], False, ""
        elif len(cands) == 1:
            return cands[0], False, ""
        elif len(cands) > 1:
            return None, True, f"{start_hr}:00–{end_hr}:00"

    # Priority 2: Single hours with meridiem
    hour_checks = [
        (5, ["5", "paanch", "panch"]),
        (6, ["6", "chhe", "che"]),
        (7, ["7", "saat"]),
        (8, ["8", "aath"]),
        (10, ["10", "das"]),
        (12, ["12", "baarah"]),
        (4, ["4", "chaar"]),
    ]

    for hr, aliases in hour_checks:
        for alias in aliases:
            is_hr_mentioned = (
                re_has(rf"(?<![0-9\-])\s*{alias}\s*(?:am|morning|subah|sawere)\b")
                or re_has(rf"(?<![0-9\-])\s*{alias}\s*(?:pm|evening|shaam|night|raat|dopahar|afternoon)\b")
                or (is_am and re_has(rf"(?<![0-9\-])\s*{alias}(?:\s*baje|\s*bje|\s*am|\b)"))
                or (is_pm and re_has(rf"(?<![0-9\-])\s*{alias}(?:\s*baje|\s*bje|\s*pm|\b)"))
            )
            if is_hr_mentioned:
                merid = "am" if is_am and not is_pm else ("pm" if is_pm and not is_am else None)
                if not merid:
                    if re_has(rf"(?<![0-9\-])\s*{alias}\s*(?:am|morning|subah|sawere)\b"):
                        merid = "am"
                    elif re_has(rf"(?<![0-9\-])\s*{alias}\s*(?:pm|evening|shaam|night|raat|dopahar|afternoon)\b"):
                        merid = "pm"

                cands = [vt for vt in valid_timings if re.search(rf"\b{hr}(?::00)?\s*(?:to|-|–)", vt)]
                if merid:
                    matched = [vt for vt in cands if merid.upper() in vt.upper()]
                    if matched:
                        return matched[0], False, ""
                    return None, False, ""
                elif len(cands) == 1:
                    return cands[0], False, ""
                elif len(cands) > 1:
                    return None, True, f"{hr}:00"

    # Priority 3: Single bare hour without am/pm (e.g. "6 baje", "10 baje")
    for hr, aliases in hour_checks:
        for alias in aliases:
            if re_has(rf"(?<![0-9\-])\s*{alias}(?:\s*baje|\s*bje|\b)"):
                cands = [vt for vt in valid_timings if re.search(rf"\b{hr}(?::00)?\s*(?:to|-|–)", vt)]
                if len(cands) == 1:
                    return cands[0], False, ""
                elif len(cands) > 1:
                    return None, True, f"{hr}:00"

    return None, False, ""


def resolve_llm_reply_tags(reply: str, state: dict, course_id: str = "yoga", user_text: str = "") -> tuple[bool, str]:
    """
    Detects semantic action tags and coupon codes emitted by the LLM.
    1. If [UNLOCK_COUPON] is present:
       - Checks user_text to ensure customer was not merely asking where/how to create a profile.
       - Marks state: profile_created=True, app_installed=True, coupon_sent=True, stage="COUPON_SENT"
       - Replaces [UNLOCK_COUPON] with the secret coupon code & checkout instructions
         via coupons.format_coupon_unlock_delivery().
    2. If an explicit coupon code for course_id is present:
       - Confirms state unlocked.
    Returns (state_unlocked: bool, resolved_reply: str).
    """
    if not reply or not isinstance(state, dict):
        return False, reply or ""

    from src.core import kb_loader
    from src.core.coupons import format_coupon_unlock_delivery

    resolved_reply = reply
    did_unlock = False

    # Guard: If user was asking WHERE or HOW to create a profile (e.g. 'kha bnani h', 'kaise banaye'),
    # any [UNLOCK_COUPON] is a false-positive hallucination and must NOT be unlocked.
    if user_text:
        u_clean = user_text.lower().strip()
        has_help_word = any(w in u_clean for w in ["kha", "kahan", "kaha", "where", "kaise", "how", "kidhar", "link", "steps", "bnani", "banani"])
        has_completion_word = any(w in u_clean for w in ["banadi", "bna di", "bana di", "ho gaya", "done", "created", "completed", "sign up kar", "account bana", "registered"])
        if has_help_word and not has_completion_word:
            resolved_reply = resolved_reply.replace("[UNLOCK_COUPON]", "").strip()
            return False, resolved_reply

    # 1. Check for [UNLOCK_COUPON] action tag
    if "[UNLOCK_COUPON]" in resolved_reply:
        state["profile_created"] = True
        state["app_installed"] = True
        state["coupon_sent"] = True
        state["stage"] = "COUPON_SENT"
        did_unlock = True

        hindi_markers = ["kya", "hai", "mujhe", "batao", "chahiye", "ka", "ki", "ke", "nahi", "haan", "se", "bhi", "aapka", "karein", "hain"]
        has_hindi = any(w in resolved_reply.lower() for w in hindi_markers) or any("\u0900" <= ch <= "\u097F" for ch in resolved_reply)
        lang = "hinglish" if has_hindi else "english"

        coupon_delivery = format_coupon_unlock_delivery(
            state.get("package"),
            course_id=course_id,
            language=lang
        )
        resolved_reply = resolved_reply.replace("[UNLOCK_COUPON]", coupon_delivery).strip()

    # 2. Semantic Fallback: Did LLM confirm user created their profile?
    # e.g. "Aapka profile ban gaya hai", "profile complete ho gaya", "welcome coupon mil gaya"
    # Guard: only if profile was NOT already confirmed, and reply is celebrating completion, NOT guiding to create
    if not did_unlock and not state.get("profile_created"):
        llm_confirmed_profile = bool(re.search(
            r"(?:profile|account)\s*(?:ban\s*g|complete\s*ho\s*g|creat\w+\s*ho\s*g|ban\s*chuki|ban\s*gya|ban\s*gaya|ready\s*hai)|"
            r"(?:welcome\s*coupon\s*mil\s*gaya)|"
            r"(?:congratulations.*profile)",
            resolved_reply,
            re.IGNORECASE
        )) and not bool(re.search(
            r"(?:profile\s*(?:banayein|bana\s*lijiye|complete\s*karein|banana\s*hoga|banane\s*ke\s*baad)|download\s*karein)",
            resolved_reply,
            re.IGNORECASE
        ))

        if llm_confirmed_profile:
            state["profile_created"] = True
            state["app_installed"] = True
            state["coupon_sent"] = True
            state["stage"] = "COUPON_SENT"
            did_unlock = True

            hindi_markers = ["kya", "hai", "mujhe", "batao", "chahiye", "ka", "ki", "ke", "nahi", "haan", "se", "bhi", "aapka", "karein", "hain"]
            has_hindi = any(w in resolved_reply.lower() for w in hindi_markers) or any("\u0900" <= ch <= "\u097F" for ch in resolved_reply)
            lang = "hinglish" if has_hindi else "english"

            coupon_delivery = format_coupon_unlock_delivery(
                state.get("package"),
                course_id=course_id,
                language=lang
            )
            resolved_reply = f"{resolved_reply}\n\n{coupon_delivery}".strip()

    # 3. Check if LLM mentioned an explicit coupon code (e.g. in post-unlock turns)
    coupons = kb_loader.get_course_coupons(course_id)
    coupon_codes = [c.get("code") for c in coupons.values() if c.get("code")]
    for code in coupon_codes:
        if re.search(rf"\b{re.escape(code)}\b", resolved_reply, re.IGNORECASE):
            state["profile_created"] = True
            state["app_installed"] = True
            state["coupon_sent"] = True
            state["stage"] = "COUPON_SENT"
            did_unlock = True
            break

    # 4. Anti-Hallucination Sanitizer for Unverified Sessions:
    # If session is NOT verified and was NOT unlocked, ensure NO coupon codes (real or fabricated) are leaked.
    if not did_unlock and not state.get("profile_created"):
        resolved_reply = re.sub(
            r"\*?\b(?:welcome|yoga|kathak|coupon|code|flat|save|off)\d{2,5}\b\*?",
            "welcome coupon",
            resolved_reply,
            flags=re.IGNORECASE
        )
        resolved_reply = re.sub(
            r"(?:coupon\s*code|code)\s*(?:hai|is|:)?\s*[\*\"\']?([A-Z]{3,10}\d{2,5})[\*\"\']?",
            "coupon code profile complete karne ke baad unlock hoga",
            resolved_reply,
            flags=re.IGNORECASE
        )

    return did_unlock, resolved_reply


def sync_state_from_llm_reply(reply: str, state: dict, course_id: str = "yoga") -> bool:
    """
    Detects state change from what the LLM did, NOT what the user said.
    Inspects the LLM's output for [UNLOCK_COUPON] or explicit coupon codes.
    Updates state: profile_created=True, app_installed=True, coupon_sent=True, stage="COUPON_SENT".
    Returns True if coupon was detected/unlocked and state updated, False otherwise.
    """
    did_unlock, _ = resolve_llm_reply_tags(reply, state, course_id=course_id)
    return did_unlock


def is_profile_completed_signal(text: str, state: dict = None) -> bool:
    """
    Deprecated: State changes are now detected from the LLM's output
    (via sync_state_from_llm_reply) rather than fragile keyword matching on user input.
    Preserved for backward compatibility.
    """
    if state and (state.get("profile_created") or state.get("coupon_sent")):
        return True
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

    from src.core import kb_loader
    # Dynamically detect if user is referring to a specific course from catalog
    detected_course = kb_loader.detect_course_from_text(text, default_course=None)
    if detected_course:
        state["course"] = detected_course
    course_id = state.get("course") or "yoga"
    is_course_keyword = bool(detected_course)

    text_lower = text.lower().strip()
    is_q = is_user_asking_question(text)

    # Keywords for initial contact vs confirmation vs slots
    is_greeting = matches_any(text_lower, GREETING_WORDS)
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
        elif is_greeting or is_course_keyword:
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
            course_timings = kb_loader.get_valid_timings(course_id)
            amb_nums = re.findall(r"\d+", pending_amb)
            amb_hr = int(amb_nums[0]) if amb_nums else None

            if amb_hr:
                target_merid = "am" if (is_am and not is_pm) else ("pm" if (is_pm and not is_am) else None)
                if target_merid:
                    for vt in course_timings:
                        m_vt = re.search(r"(\d{1,2})(?::\d{2})?\s*(?:to|-|–|\u2013)\s*(\d{1,2})(?::\d{2})?\s*(AM|PM)", vt, re.I)
                        if m_vt and int(m_vt.group(1)) == amb_hr and m_vt.group(3).lower() == target_merid:
                            resolved_timing = vt
                            break

            if resolved_timing:
                state["timing"] = resolved_timing
                state["stage"] = advance_stage(state["stage"], "TIMING_SELECTED")
                state.pop("pending_ambiguous_timing", None)
                state.pop("ambiguous_timing_range", None)
                timing_found = True

        if not timing_found:
            timing_str, is_ambiguous, ambiguous_range = detect_timing_from_text(text, course_id=course_id)
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
    course_pkgs = kb_loader.get_valid_packages(course_id)
    detected_pkg = detect_package_from_text(text, course_id=course_id)

    is_package_detected = False

    # Whenever a package duration is mentioned or asked about, immediately update active package slot
    if detected_pkg:
        state["package"] = detected_pkg
        state["fee"] = course_pkgs.get(detected_pkg, state.get("fee"))
        is_package_detected = True
    elif not is_q:
        # Fallback for bare price numbers in package stage from course registry
        coupons = kb_loader.get_course_coupons(course_id)
        tokens = [w.strip(".,!?:;₹") for w in text_lower.replace(",", "").split()]
        for pkg_name, data in coupons.items():
            b_str = str(data.get("base_price", ""))
            o_str = str(data.get("offer_price", ""))
            if any(tok in [b_str, o_str] for tok in tokens if tok):
                state["package"] = pkg_name
                state["fee"] = course_pkgs.get(pkg_name, state.get("fee"))
                is_package_detected = True
                break

    needs_llm_fallback = (
        not timing_found and not is_package_detected
        and not is_greeting
        and not is_q
        and len(text_lower) >= 1
        and state["stage"] in ["TIMING_SELECTED", "PACKAGE_ASKED", "PACKAGE_SELECTED", "ENROLL_CONFIRMED"]
    )
    if needs_llm_fallback:
        slot_result = await extract_slot_llm(text, chat_history, course_id=course_id)
        if slot_result["timing"] and not state.get("timing"):
            state["timing"] = slot_result["timing"]
            state["stage"] = advance_stage(state["stage"], "TIMING_SELECTED")
        if slot_result["package"] and not state.get("package"):
            state["package"] = slot_result["package"]
            state["fee"] = course_pkgs.get(slot_result["package"], "")
        
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

