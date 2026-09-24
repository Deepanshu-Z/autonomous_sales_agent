"""
chat_state.py — Session state management & funnel stage tracking per unique user.
Stores slot values (batch timing, selected package, app installation, profile status)
and ensures each unique phone number maintains its own isolated conversation state.
"""

import os
import re
from dotenv import load_dotenv
from supabase import create_client
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
        "course": None,               # Active course context (e.g. "yoga", "kathak", None until selected)
        "welcome_sent": False,        # Flag to ensure initial campaign welcome flow fires only once on 1st message
        "app_download_video_sent": False,  # Prevents redundant/spam sends of the app download/install video guide
        "trial_video_sent": False,         # Prevents redundant/spam sends of the trial booking video guide

        "is_target_ad": False,        # Whether user verified via secret string (AI enabled)
        "low_confidence_count": 0,     # Consecutive unanswered / low-confidence query count
        "follow_up_count": 0,
        "next_follow_up_due_at": 0,
        "last_topic": 0,
        "refusal_stage": 0,           # 3-step refusal lifecycle: 0=normal, 1=asked reason, 2=solution offered, 3=escalated
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
                row = dict(res.data[0])
                raw_topic = row.get("last_topic") or ""
                if isinstance(raw_topic, str) and raw_topic.startswith("COURSE:"):
                    parts = raw_topic.split("||", 1)
                    c_id = parts[0].replace("COURSE:", "").strip()
                    if c_id:
                        row["course"] = c_id
                    row["last_topic"] = parts[1] if len(parts) > 1 else ""
                state.update(row)
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
            course_val = state.get("course") or ""
            topic_val = str(state.get("last_topic") or "")
            if course_val:
                db_state["last_topic"] = f"COURSE:{course_val}||{topic_val}"[:200]
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
    Detects if user message indicates that they downloaded the app,
    registered, or completed their profile.
    Guards against questions, greetings, or repeat coupon triggers when already sent.
    """
    if not text:
        return False
    t = text.lower().strip()

    # If coupon was already delivered, user is not completing profile again
    if state and state.get("coupon_sent"):
        return False

    # 1. Guard against questions or how-to inquiries (not completion)
    INQUIRY_MARKERS = [
        r"\b(kaise|kese|how|kyun|kya)\b.*?\b(bana|create|kare|kru|download|register|fill|khol)",
        r"\b(karna|krna|karni|krni|banana|banani)\s+h(ai)?\b",
        r"\b(bnalo|banalo|bana\s*do|bna\s*do|kardo|krdo|kar\s*do|kr\s*do)\b",
        r"\bkese\s+kru\b",
        r"\bkaise\s+karein?\b",
        r"\bhelp\s+chahiye\b",
    ]
    if any(re.search(p, t) for p in INQUIRY_MARKERS):
        return False

    # 2. Emphatic assertions
    EMPHATIC_ASSERTION_PATTERNS = [
        r"\b(kar|kr)\s+to\s+(liya|lia|diya|dia)\b",
        r"\bho\s+to\s+(gaya|gya|gayi|gai)\b",
        r"\b(already|pehle\s+se|pehle\s+hi)\s+(created|made|done|completed|registered|downloaded|installed|bana|banali|banayi)\b",
        r"\bmaine\s+to\s+(bana|create)\b",
    ]
    if any(re.search(p, t) for p in EMPHATIC_ASSERTION_PATTERNS):
        return True

    # 3. Standalone past-action completion verbs
    STANDALONE_PAST_VERBS = [
        r"\b(bnali|banali|bna\s*li|bana\s*li|banadi|bna\s*di|bana\s*diya|bna\s*diya|bana\s*liya|banaliya|bna\s*liya|bnaliya)\b",
        r"\b(kardi|krdi|kardiya|krdiya|kar\s*li|kr\s*li|kar\s*diya|kr\s*diya)\b",
        r"\b(ho\s*gaya|hogaya|ho\s*gya|hogya|ban\s*gaya|bangaya|ban\s*gya|bangya|ban\s*gayi|bangayi|ban\s*gai|bangai)\b",
        r"\b(created|completed|registered|setup\s*done|all\s*done)\b",
    ]
    if any(re.search(p, t) for p in STANDALONE_PAST_VERBS):
        return True

    # 4. Direct noun + past-verb semantic combinations
    NOUN_RE = r"(profile|profil|profl|account|acnt|acc|id|details?)"
    PAST_VERB_RE = (
        r"("
        r"create\s*k(ar|r)\s*li|create\s*k(ar|r)\s*liya|create\s*k(ar|r)\s*di|create\s*k(ar|r)\s*diya|"
        r"create\s*kiya|create\s*ho\s*g(aya|ya|yi|i)|created|"
        r"bana\s*li|banali|bna\s*li|bnali|bana\s*diya|banadi|bna\s*diya|bnadi|bana\s*liya|banaliya|bna\s*liya|bnaliya|"
        r"banaya|banayi|bna\s*di|banadi|"
        r"bana\s*chuka|bna\s*chuka|ban\s*chuki|ban\s*chuka|"
        r"ban\s*gayi|bangayi|ban\s*gai|bangai|ban\s*gya|bangya|ban\s*gaya|bangaya|"
        r"kar\s*liya|kr\s*liya|kardiya|krdiya|kar\s*li|kr\s*li|karli|krli|kar\s*di|kr\s*di|kardi|krdi|"
        r"ho\s*g(aya|ya|yi|i)|hog(aya|ya|yi|i)|"
        r"ready|done|complete|completed|setup|set|registered"
        r")"
    )
    if re.search(rf"\b{NOUN_RE}\b.*?{PAST_VERB_RE}", t) or re.search(rf"{PAST_VERB_RE}.*?\b{NOUN_RE}\b", t):
        return True

    # App download / install completion
    APP_NOUN_RE = r"(app|application|sensationz)"
    APP_PAST_VERB_RE = (
        r"(download\s*k(ar|r)\s*li|download\s*k(ar|r)\s*liya|download\s*ho\s*g(aya|ya|yi|i)|downloaded|"
        r"install\s*k(ar|r)\s*li|install\s*k(ar|r)\s*liya|install\s*ho\s*g(aya|ya|yi|i)|installed|"
        r"khol\s*li|open\s*k(ar|r)\s*li)"
    )
    if re.search(rf"\b{APP_NOUN_RE}\b.*?{APP_PAST_VERB_RE}", t) or re.search(rf"{APP_PAST_VERB_RE}.*?\b{APP_NOUN_RE}\b", t):
        return True

    _EXPLICIT_COMPLETION_PHRASES = [
        "google login done", "login done", "google login", "google done",
        "profile created", "profile done", "profile complete", "created profile",
        "profile ban gayi", "profile ban gai", "profile bana li", "profile banali",
        "profile bna li", "profile bnali", "profile ready", "profile set",
        "account created", "account ban gaya", "account bana liya", "profile setup",
        "both done", "sab ho gaya", "sab ho gya", "dono ho gaya", "dono ho gya",
        "download kar liya", "download kr liya", "install kar liya", "install kr liya",
        "app done", "downloaded", "installed", "app installed", "app downloaded",
        "setup done", "all done", "done profile created", "profile created done",
        "done created", "yes created", "id bana li", "id banali", "id ban gayi",
        "login kar liya", "login ho gaya", "signup kar liya", "sign up ho gaya",
        "registered", "registered on app", "profile is ready", "profile is done",
    ]
    if any(p in t for p in _EXPLICIT_COMPLETION_PHRASES):
        return True

    # 5. Contextual Affirmations (strictly when in app link stage and profile not yet marked created)
    current_stage = (state.get("stage") if state else "") or ""
    if current_stage in ["APP_LINK_SENT", "READY_FOR_APP_LINK"] and not (state and state.get("profile_created")):
        _AFFIRMATIVE_SIGNALS = [
            "done", "yes", "haan", "haa", "han", "ji haan", "ji han", "bilkul",
            "yes done", "haan done", "ok done", "done done", "kar diya", "kardiya",
            "kr diya", "krdiya", "kar li", "krli", "ho gaya", "hogaya", "ho gya", "hogya",
            "ban gaya", "bangaya", "ban gayi", "bangayi", "bnali", "banali", "bna li", "bana li",
            "bna di", "banadi", "kardi", "krdi"
        ]
        words = re.findall(r"\w+", t)
        if len(words) <= 4 and any(w in words for w in ["done", "yes", "haan", "bilkul"]):
            return True
        if any(p in t for p in _AFFIRMATIVE_SIGNALS) and len(words) <= 4:
            return True

    return False


async def extract_and_update_slots(phone: str, text: str, chat_history: list = None) -> dict:
    """
    Analyzes incoming user message using semantic extraction,
    and updates funnel state without fragile hardcoded regexes.
    """
    state = await get_user_state_async(phone)
    state["follow_up_count"] = 0

    from src.core.intent_classifier import extract_intent_and_state_changes
    from src.core import kb_loader
    from src.core.coupons import normalize_package_name

    extraction = await extract_intent_and_state_changes(text, state=state, history=chat_history)

    active_course = state.get("course")
    if extraction.get("selected_course"):
        cand_course = extraction["selected_course"].lower()
        if kb_loader.course_exists(cand_course):
            state["course"] = cand_course
            active_course = cand_course

    if active_course and extraction.get("selected_package"):
        pkg = normalize_package_name(extraction["selected_package"], course_id=active_course)
        if pkg:
            state["package"] = pkg
            course_pkgs = kb_loader.get_valid_packages(active_course)
            state["fee"] = course_pkgs.get(pkg, state.get("fee"))

    if active_course and extraction.get("selected_timing"):
        detected_t, _, _ = detect_timing_from_text(extraction["selected_timing"], course_id=active_course)
        state["timing"] = detected_t or extraction["selected_timing"]

    if extraction.get("wants_trial"):
        state["wants_trial"] = True

    app_stated = extraction.get("app_installed_stated", "unmentioned")
    if app_stated == "confirmed":
        state["app_installed"] = True
    elif app_stated == "denied":
        state["app_installed"] = False

    profile_stated = extraction.get("profile_created_stated", "unmentioned")
    if profile_stated == "confirmed":
        state["profile_created"] = True
        state["app_installed"] = True
        state["stage"] = advance_stage(state.get("stage", "NEW"), "PROFILE_COMPLETED")
    elif profile_stated == "denied":
        state["profile_created"] = False

    # Funnel stage progression based on slots
    if state.get("timing") and state.get("package"):
        state["stage"] = advance_stage(state["stage"], "READY_FOR_APP_LINK")
    elif state.get("timing"):
        state["stage"] = advance_stage(state["stage"], "TIMING_SELECTED")
    elif state.get("package"):
        state["stage"] = advance_stage(state["stage"], "PACKAGE_SELECTED")

    await save_user_state_async(phone, state)
    return state
