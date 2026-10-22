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

    available_batches = get_available_timings(course_id)
    matched_teachers: List[str] = []

    for batch in available_batches:
        time_str = batch.get("time_12h", "").lower()
        days_str = batch.get("days", "").lower()
        slot_str = batch.get("slot", "").lower()
        cleaned_time = re.sub(r"[\s\-_–:]+", " ", time_str)

        is_match = False
        if query in time_str or query in days_str or query in slot_str:
            is_match = True
        elif cleaned_query in cleaned_time:
            is_match = True
        else:
            hour_match = re.search(r"\b(\d{1,2})\s*(am|pm)\b", query)
            if hour_match:
                hr_token = f"{hour_match.group(1)} 00 {hour_match.group(2)}"
                alt_token = f"{hour_match.group(1)} {hour_match.group(2)}"
                if hr_token in cleaned_time or alt_token in cleaned_time:
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



