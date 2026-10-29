"""
schedule_engine.py — Batch Timings, Teacher Rosters & Schedule Queries.

Single-responsibility component for querying, classifying, and formatting
all batch timings, teacher assignments, and time slots across courses.
Zero hardcoding: all data is resolved dynamically from course knowledge bases.
"""

import re
from typing import Dict, Any, List, Optional


def classify_time_period(time_str: str) -> Optional[str]:
    """Dynamically categorizes any timing string into 'Morning', 'Afternoon', or 'Evening'.
    - Morning (AM): 4:00 AM – 11:59 AM
    - Afternoon (PM): 12:00 PM – 3:59 PM
    - Evening (PM): 4:00 PM – 11:59 PM
    """
    if not time_str:
        return None
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(AM|PM)", time_str, re.IGNORECASE)
    if not m:
        m24 = re.search(r"(\d{1,2}):(\d{2})", time_str)
        if m24:
            hr = int(m24.group(1))
            if 4 <= hr < 12:
                return "Morning"
            elif 12 <= hr < 16:
                return "Afternoon"
            elif 16 <= hr <= 23:
                return "Evening"
        return None
    hr = int(m.group(1))
    merid = m.group(3).upper()
    if merid == "AM":
        return "Morning"
    else:
        if hr == 12 or 1 <= hr < 4:
            return "Afternoon"
        return "Evening"


def get_available_timings(course_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns the standardized list of batch timings for any course.
    Each item contains human-readable keys: 'slot', 'days', 'time_12h', 'time_24h', 'instructors'.
    Dynamic, course-agnostic, reads directly from course KB.
    """
    if not course_id:
        return []
    from src.core.kb_loader import get_course_data
    course_data = get_course_data(course_id)
    batches = course_data.get("batch_timings", {}).get("batches", [])
    if not batches:
        return []

    available_timings = []
    for batch in batches:
        time_12h = batch.get("time_12h", "").strip()
        days = batch.get("days", "").strip() or batch.get("slot", "").strip()
        instructors = [str(inst).strip() for inst in batch.get("instructors", []) if str(inst).strip()]
        available_timings.append({
            "slot": batch.get("slot", "").strip(),
            "days": days,
            "time_12h": time_12h,
            "time_24h": batch.get("time_24h", "").strip(),
            "instructors": instructors,
        })
    return available_timings


def get_valid_timings(course_id: Optional[str] = None) -> List[str]:
    """Dynamically extracts formatted valid batch timing strings (e.g. '5:00–6:00 PM')
    straight from the course JSON — never a hand-typed list.
    """
    if not course_id:
        return []
    available = get_available_timings(course_id)
    valid_timings = []
    for b in available:
        t = b.get("time_12h", "")
        m = re.match(r"(\d{1,2}:\d{2}\s*(?:AM|PM))\s*(?:to|-|–)\s*(\d{1,2}:\d{2}\s*(?:AM|PM))", t, re.IGNORECASE)
        if m:
            start, end = m.group(1).strip(), m.group(2).strip()
            sp, ep = start.split(), end.split()
            canonical = f"{sp[0]}–{ep[0]} {ep[1].upper()}" if sp[1].upper() == ep[1].upper() else f"{start}–{end}"
            valid_timings.append(canonical)
        elif t:
            valid_timings.append(t)
    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for vt in valid_timings:
        if vt not in seen:
            seen.add(vt)
            deduped.append(vt)
    return deduped


def get_teachers_for_slot(course_id: Optional[str] = None, slot_or_timing: str = "") -> List[str]:
    """Returns a deduplicated list of instructor names assigned to a given timing or slot.
    Handles flexible queries like '5:00 PM', '5-6 PM', '5 pm', 'Evening', or 'Tuesday/Thursday'.
    Dynamic and reusable across all courses.
    """
    if not course_id or not slot_or_timing:
        return []

    query = slot_or_timing.lower().strip()
    cleaned_query = re.sub(r"[\s\-_–:]+", " ", query)
    q_hr, q_merid = extract_time_tokens(slot_or_timing)

    available_batches = get_available_timings(course_id)
    matched_teachers: List[str] = []

    for batch in available_batches:
        time_str = batch.get("time_12h", "").lower()
        days_str = batch.get("days", "").lower()
        slot_str = batch.get("slot", "").lower()
        cleaned_time = re.sub(r"[\s\-_–:]+", " ", time_str)

        is_match = False
        if q_hr is not None and q_merid is not None:
            b_hr, b_merid = extract_time_tokens(time_str)
            if b_hr == q_hr and b_merid == q_merid:
                is_match = True
        elif query in time_str or query == slot_str or query in days_str:
            is_match = True
        elif cleaned_query in cleaned_time:
            is_match = True

        if is_match:
            for instructor in batch.get("instructors", []):
                if instructor and instructor not in matched_teachers:
                    matched_teachers.append(instructor)

    return matched_teachers


def get_slots_for_teacher(course_id: Optional[str] = None, teacher_name: str = "") -> List[Dict[str, Any]]:
    """Returns all batch timings taught by a specific teacher for the given course.
    Matches teacher names flexibly (e.g. 'madhuri' matches 'Ms. Madhuri').
    """
    if not course_id or not teacher_name:
        return []

    target_name = teacher_name.lower().strip()
    available_batches = get_available_timings(course_id)
    matching_slots = []

    for batch in available_batches:
        for instructor in batch.get("instructors", []):
            inst_lower = instructor.lower()
            if target_name in inst_lower or inst_lower in target_name:
                matching_slots.append({
                    "time_12h": batch.get("time_12h", ""),
                    "days": batch.get("days", ""),
                    "slot": batch.get("slot", ""),
                    "instructor": instructor,
                })
                break

    return matching_slots


def get_course_time_periods(course_id: Optional[str] = None) -> Dict[str, Any]:
    """Dynamically parses and categorizes all batches for a course into time periods:
    'Morning', 'Afternoon', and 'Evening'. Zero hardcoding.
    """
    if not course_id:
        return {
            "has_timings": False,
            "available_periods": [],
            "missing_periods": ["Morning", "Afternoon", "Evening"],
            "batches_by_period": {},
            "summary": "Check official app/website for schedule.",
        }
    from src.core.kb_loader import get_course_data
    data = get_course_data(course_id)
    batches = data.get("batch_timings", {}).get("batches", [])
    if not batches:
        return {
            "has_timings": False,
            "available_periods": [],
            "missing_periods": ["Morning", "Afternoon", "Evening"],
            "batches_by_period": {},
            "summary": "Live batch schedules are available on the Sensationz App and website.",
        }

    batches_by_period: Dict[str, List[str]] = {"Morning": [], "Afternoon": [], "Evening": []}
    for b in batches:
        t = b.get("time_12h", "") or b.get("time_24h", "")
        period = classify_time_period(t)
        if not period and b.get("slot") in ("Morning", "Afternoon", "Evening"):
            period = b.get("slot")
        if period and period in batches_by_period:
            if t and t not in batches_by_period[period]:
                batches_by_period[period].append(t)

    available = [p for p in ["Morning", "Afternoon", "Evening"] if batches_by_period[p]]
    missing = [p for p in ["Morning", "Afternoon", "Evening"] if not batches_by_period[p]]

    course_name = data.get("course_overview", {}).get("name", course_id.title())
    if len(available) == 1:
        only_p = available[0]
        missing_str = " or ".join(missing)
        summary = (
            f"Batches are held in the {only_p} only ({', '.join(batches_by_period[only_p])}). "
            f"CRITICAL: {course_name} has NO {missing_str} batches!"
        )
    elif available:
        descs = [f"{p} ({', '.join(batches_by_period[p])})" for p in available]
        missing_str = f" (NO {' or '.join(missing)} batches)" if missing else ""
        summary = f"Batches available: {'; '.join(descs)}{missing_str}."
    else:
        summary = "Check the Sensationz App or website for live batch schedules."

    return {
        "has_timings": True,
        "available_periods": available,
        "missing_periods": missing,
        "batches_by_period": batches_by_period,
        "summary": summary,
    }


def format_batch_timings_display(course_id: Optional[str] = None) -> str:
    """Formats available batches cleanly for WhatsApp messages and system prompt display."""
    if not course_id:
        return ""
    from src.core.kb_loader import get_course_data
    data = get_course_data(course_id)
    batches = data.get("batch_timings", {}).get("batches", [])
    slots: Dict[str, List[str]] = {}
    for b in batches:
        slot_name = b.get("slot") or b.get("days") or "Available"
        slot_name = slot_name.strip()
        t = b.get("time_12h", "")
        if t and t not in slots.setdefault(slot_name, []):
            slots[slot_name].append(t)
    if not slots:
        return "• Flexible batch timings available — check the app/website for the current schedule."
    lines = []
    for slot, times in slots.items():
        formatted_slot = slot.replace("/", " & ")
        lines.append(f"• *{formatted_slot}:* " + ", ".join(times))
    return "\n".join(lines)


def format_teacher_batch_schedule(course_id: Optional[str] = None) -> str:
    """Pre-computes a human-readable teacher -> batch timings mapping from course JSON.
    Injected into the system prompt so the LLM doesn't need to traverse minified JSON
    to answer teacher-specific timing queries — eliminates teacher-timing hallucinations.
    """
    if not course_id:
        return ""
    from src.core.kb_loader import get_course_data
    data = get_course_data(course_id)
    batches = data.get("batch_timings", {}).get("batches", [])
    if not batches:
        return ""

    teacher_map: Dict[str, List[str]] = {}
    for b in batches:
        days = b.get("days") or b.get("slot") or ""
        time_str = b.get("time_12h", "")
        if not days or not time_str:
            continue
        for instructor in b.get("instructors", []):
            teacher_map.setdefault(instructor, []).append(f"{days} {time_str}")

    if not teacher_map:
        return ""

    lines = []
    for teacher, slots in teacher_map.items():
        seen = set()
        unique_slots = []
        for s in slots:
            if s not in seen:
                seen.add(s)
                unique_slots.append(s)
        lines.append(f"• {teacher}: {' | '.join(unique_slots)}")
    return "\n".join(lines)


def get_course_instructors(course_id: Optional[str] = None) -> List[str]:
    """Returns a deduplicated list of all canonical instructor names for a course,
    or across all active courses in the catalog if course_id is None.
    Dynamically parsed from the course JSONs.
    """
    from src.core.kb_loader import full_kb_course_ids
    cids = [course_id] if course_id else full_kb_course_ids()
    teachers: List[str] = []
    for cid in cids:
        for b in get_available_timings(cid):
            for inst in b.get("instructors", []):
                if inst and inst not in teachers:
                    teachers.append(inst)
    return teachers


def resolve_canonical_teacher(teacher_query: str, course_id: Optional[str] = None) -> Optional[str]:
    """Resolves colloquial, partial, or informal teacher references
    (e.g. 'madhuri mam', 'madhuri ji', 'madhuri ma\'am', 'sneha', 'kratika mam')
    to the exact canonical instructor name (e.g. 'Ms. Madhuri', 'Sneha Maurya', 'Kratika Gontiya').

    If course_id is provided, searches that course's roster first;
    if not matched (or course_id is None), searches all course rosters across the catalog.
    Returns canonical teacher name or None.
    """
    if not teacher_query or not str(teacher_query).strip():
        return None

    q_raw = str(teacher_query).strip()
    q_clean = re.sub(r"[^\w\s]", "", q_raw.lower())
    honorifics = {
        "ms", "miss", "mr", "mrs", "mam", "maam", "ji", "sir", "madam",
        "teacher", "guru", "dr", "faculty", "instructor"
    }
    tokens = [w for w in q_clean.split() if w not in honorifics]
    core_q = " ".join(tokens).strip()
    if not core_q or len(core_q) < 3:
        return None

    candidates = get_course_instructors(course_id)
    all_candidates = get_course_instructors(None) if course_id else candidates

    def clean_tokens(name: str) -> List[str]:
        c_clean = re.sub(r"[^\w\s]", "", name.lower())
        return [w for w in c_clean.split() if w not in honorifics]

    def find_in(pool: List[str]) -> Optional[str]:
        # 1. Exact match (case insensitive)
        for c in pool:
            if q_raw.lower() == c.lower():
                return c

        # 2. Exact match against cleaned core tokens
        for c in pool:
            c_tokens = clean_tokens(c)
            c_core = " ".join(c_tokens).strip()
            if core_q == c_core:
                return c

        # 3. Token match: if core_q is in candidate's tokens (e.g. 'madhuri' in 'ms. madhuri' or 'kratika' in 'kratika gontiya')
        for c in pool:
            c_tokens = clean_tokens(c)
            for ct in c_tokens:
                if len(ct) >= 3 and (core_q == ct or (len(core_q) >= 4 and (core_q in ct or ct in core_q))):
                    return c
        return None

    matched = find_in(candidates)
    if not matched and course_id:
        matched = find_in(all_candidates)
    return matched


# ── DAY CODES & CANONICAL NAMES ──
DAY_MAP_DISPLAY_TO_CODE = {
    # Tuesday / Thursday
    "tuesday/thursday": "TT",
    "tuesday & thursday": "TT",
    "tuesday and thursday": "TT",
    "tue/thu": "TT",
    "tue-thu": "TT",
    "tues thurs": "TT",
    "tue thu": "TT",
    "tue and thu": "TT",
    "tuesday thursday": "TT",
    "tues/thurs": "TT",
    "tt": "TT",
    "mangalvaar guruvaar": "TT",
    "mangalwar guruwar": "TT",
    "mangal aur guruwar": "TT",
    "mangalvaar": "TT",
    "mangalwar": "TT",
    "guruvaar": "TT",
    "guruwar": "TT",
    "tuesday": "TT",
    "thursday": "TT",
    "tue": "TT",
    "tues": "TT",
    "thu": "TT",
    "thur": "TT",
    "thurs": "TT",
    "t-t": "TT",

    # Wednesday / Friday
    "wednesday/friday": "WF",
    "wednesday & friday": "WF",
    "wednesday and friday": "WF",
    "wed/fri": "WF",
    "wed-fri": "WF",
    "wed fri": "WF",
    "wed and fri": "WF",
    "wednesday friday": "WF",
    "wf": "WF",
    "w-f": "WF",
    "budhvaar shukravaar": "WF",
    "budhwar shukrawar": "WF",
    "budh aur shukrawar": "WF",
    "budhvaar": "WF",
    "budhwar": "WF",
    "shukravaar": "WF",
    "shukrawar": "WF",
    "wednesday": "WF",
    "friday": "WF",
    "wed": "WF",
    "fri": "WF",

    # Saturday / Sunday
    "saturday/sunday": "SS",
    "saturday & sunday": "SS",
    "saturday and sunday": "SS",
    "sat/sun": "SS",
    "sat-sun": "SS",
    "sat sun": "SS",
    "sat and sun": "SS",
    "saturday sunday": "SS",
    "weekend": "SS",
    "weekends": "SS",
    "weekend batch": "SS",
    "ss": "SS",
    "s-s": "SS",
    "shanivaar ravivaar": "SS",
    "shaniwar raviwar": "SS",
    "shanivar ravivar": "SS",
    "shani aur raviwar": "SS",
    "shanivaar": "SS",
    "shaniwar": "SS",
    "shanivar": "SS",
    "ravivaar": "SS",
    "raviwar": "SS",
    "saturday": "SS",
    "sunday": "SS",
    "sat": "SS",
    "sun": "SS",

    # Monday to Friday
    "monday to friday": "MTWTF",
    "monday - friday": "MTWTF",
    "monday-friday": "MTWTF",
    "mon-fri": "MTWTF",
    "mon to fri": "MTWTF",
    "mon fri": "MTWTF",
    "monday through friday": "MTWTF",
    "daily": "MTWTF",
    "weekdays": "MTWTF",
    "weekday": "MTWTF",
    "weekday batch": "MTWTF",
    "mtwtf": "MTWTF",
    "m-f": "MTWTF",
    "mf": "MTWTF",
    "mon": "MTWTF",
    "monday": "MTWTF",
    "somvaar se shukravaar": "MTWTF",
    "somwar se shukrawar": "MTWTF",
    "som se shukra": "MTWTF",
    "somvaar": "MTWTF",
    "somwar": "MTWTF",

    # Tuesday / Thursday / Saturday
    "tuesday/thursday/saturday": "TTS",
    "tuesday & thursday & saturday": "TTS",
    "tts": "TTS",

    # Monday / Wednesday / Friday
    "monday/wednesday/friday": "MWF",
    "monday & wednesday & friday": "MWF",
    "mwf": "MWF",
}

CODE_TO_CANONICAL_DISPLAY = {
    "TT": "Tuesday/Thursday",
    "WF": "Wednesday/Friday",
    "SS": "Saturday/Sunday",
    "MTWTF": "Monday to Friday",
    "TTS": "Tuesday/Thursday/Saturday",
    "MWF": "Monday/Wednesday/Friday",
}


def get_day_code(day_input: Optional[str]) -> Optional[str]:
    """Returns the standardized 2-5 letter schedule code (e.g. 'TT', 'SS', 'WF', 'MTWTF') for any day string."""
    if not day_input or not str(day_input).strip():
        return None
    raw = str(day_input).strip().lower()
    if raw in DAY_MAP_DISPLAY_TO_CODE:
        return DAY_MAP_DISPLAY_TO_CODE[raw]

    # Clean punctuation and extra spaces
    cleaned = re.sub(r"[^\w/&-]", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned in DAY_MAP_DISPLAY_TO_CODE:
        return DAY_MAP_DISPLAY_TO_CODE[cleaned]

    # Substring token heuristics
    if ("sat" in raw or "shani" in raw) and ("sun" in raw or "ravi" in raw):
        return "SS"
    if ("tue" in raw or "mangal" in raw) and ("thu" in raw or "guru" in raw):
        return "TT"
    if ("wed" in raw or "budh" in raw) and ("fri" in raw or "shukra" in raw):
        return "WF"
    if "weekend" in raw:
        return "SS"
    if "weekday" in raw or "daily" in raw or ("mon" in raw and "fri" in raw):
        return "MTWTF"
    if raw in ("sat", "sun", "saturday", "sunday", "shani", "ravi"):
        return "SS"
    if raw in ("tue", "tues", "tuesday", "thu", "thur", "thurs", "thursday", "mangal", "guru"):
        return "TT"
    if raw in ("wed", "wednesday", "fri", "friday", "budh", "shukra"):
        return "WF"
    if raw in ("mon", "monday", "som", "somwar", "somvaar"):
        return "MTWTF"
    return None


def normalize_day_string(day_input: Optional[str]) -> Optional[str]:
    """Normalizes any user day input, quick reply, or abbreviation into canonical course display string
    (e.g. 'Tue & Thu' -> 'Tuesday/Thursday', 'weekend' -> 'Saturday/Sunday', 'daily' -> 'Monday to Friday').
    """
    code = get_day_code(day_input)
    if code and code in CODE_TO_CANONICAL_DISPLAY:
        return CODE_TO_CANONICAL_DISPLAY[code]
    return None


def format_days_for_button(day_str: str) -> str:
    """Formats day string for WhatsApp quick-reply button (<= 20 chars).
    e.g. 'Tuesday/Thursday' -> 'Tue & Thu'
         'Saturday/Sunday' -> 'Sat & Sun'
         'Wednesday/Friday' -> 'Wed & Fri'
         'Monday to Friday' -> 'Mon to Fri'
    """
    code = get_day_code(day_str)
    if code == "TT":
        return "Tue & Thu"
    elif code == "SS":
        return "Sat & Sun"
    elif code == "WF":
        return "Wed & Fri"
    elif code == "MTWTF":
        return "Mon to Fri"
    elif code == "TTS":
        return "Tue, Thu, Sat"
    elif code == "MWF":
        return "Mon, Wed, Fri"
    return str(day_str).replace("/", " & ")[:20]


def extract_time_tokens(time_str: Optional[str]):
    """Extracts (hour_12h, meridiem) from the start time of a timing string
    (e.g. '5:00 PM to 6:00 PM' -> (5, 'PM'), '17:00' -> (5, 'PM'), '06:03-07:00' -> (6, 'AM')).
    """
    if not time_str:
        return None, None
    m = re.search(r"(?:^|[^\d:])(\d{1,2})(?::(\d{2}))?\s*(am|pm)", str(time_str), re.IGNORECASE)
    if m:
        hr = int(m.group(1))
        merid = m.group(3).upper()
        return hr, merid
    m24 = re.search(r"(?:^|[^\d:])(\d{1,2}):(\d{2})", str(time_str))
    if m24:
        hr24 = int(m24.group(1))
        merid = "AM" if hr24 < 12 else "PM"
        hr12 = hr24 if 1 <= hr24 <= 12 else (hr24 - 12 if hr24 > 12 else 12)
        return hr12, merid
    return None, None


def get_available_days_for_batch(
    course_id: Optional[str] = None,
    timing: Optional[str] = None,
    teacher: Optional[str] = None,
) -> List[str]:
    """Returns a deduplicated list of available canonical day strings (e.g. ['Tuesday/Thursday', 'Saturday/Sunday'])
    for a given course, filtered by timing and/or instructor if provided.
    """
    if not course_id:
        return []

    batches = get_available_timings(course_id)
    if not batches:
        return []

    timing_query = str(timing).strip().lower() if timing else None
    t_clean = re.sub(r"[\s\-_–:]+", " ", timing_query) if timing_query else None
    q_hr, q_merid = extract_time_tokens(timing) if timing else (None, None)

    target_teacher = resolve_canonical_teacher(teacher, course_id=course_id) if teacher else None
    target_teacher_lower = target_teacher.lower() if target_teacher else (str(teacher).lower().strip() if teacher else None)

    matched_days: List[str] = []
    for b in batches:
        b_time = b.get("time_12h", "")
        b_days = b.get("days", "") or b.get("slot", "")
        b_insts = b.get("instructors", [])

        # 1. Timing filter (if timing provided)
        if timing:
            b_hr, b_merid = extract_time_tokens(b_time)
            b_time_clean = re.sub(r"[\s\-_–:]+", " ", b_time.lower())
            time_matches = False

            if q_hr is not None and q_merid is not None and b_hr is not None and b_merid is not None:
                # Exact start hour + meridiem match (e.g. 5 PM matches 5:00 PM to 6:00 PM, 17:00 matches 5:00 PM)
                if q_hr == b_hr and q_merid == b_merid:
                    time_matches = True
            elif timing_query and (timing_query in b_time.lower() or b_time.lower() in timing_query):
                time_matches = True
            elif t_clean and (t_clean in b_time_clean or b_time_clean in t_clean):
                time_matches = True

            if not time_matches:
                continue

        # 2. Teacher filter (if teacher provided)
        if target_teacher_lower:
            teacher_matches = False
            for inst in b_insts:
                inst_canonical = resolve_canonical_teacher(inst, course_id=course_id) or inst
                if (
                    target_teacher_lower == inst.lower()
                    or target_teacher_lower == inst_canonical.lower()
                    or target_teacher_lower in inst.lower()
                    or inst.lower() in target_teacher_lower
                ):
                    teacher_matches = True
                    break
            if not teacher_matches:
                continue

        canonical_day = normalize_day_string(b_days) or b_days.strip()
        if canonical_day and canonical_day not in matched_days:
            matched_days.append(canonical_day)

    # Robust fallback: If strict intersection of timing + teacher yielded no matches (due to slot clash),
    # fall back to teacher's days, timing's days, or course-wide days!
    if not matched_days:
        if teacher:
            fallback_teacher = get_available_days_for_batch(course_id=course_id, teacher=teacher)
            if fallback_teacher:
                return fallback_teacher
        if timing:
            fallback_timing = get_available_days_for_batch(course_id=course_id, timing=timing)
            if fallback_timing:
                return fallback_timing
        all_course_days = []
        for b in batches:
            d = normalize_day_string(b.get("days", "") or b.get("slot", "")) or (b.get("days", "") or "").strip()
            if d and d not in all_course_days:
                all_course_days.append(d)
        if len(all_course_days) == 1:
            return all_course_days

    return matched_days




