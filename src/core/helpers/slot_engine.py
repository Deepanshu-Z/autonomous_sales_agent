"""
slot_engine.py — Centralized Deterministic Enrollment Slot State Machine.

Evaluates customer session state across the 5 mandatory enrollment slots:
1. Course
2. Batch Timing
3. Instructor / Teacher
4. Batch Days
5. Package Duration

Core Guarantees:
- Order Independence: User can settle slots in any order (package first, teacher first, timing first).
- Single Source of Truth: is_complete is True ONLY when all required slots are non-null.
- Absolute Payment Barrier: Payment is strictly blocked unless is_complete is True.
- Zero Repetition: Next target slot strictly targets the first unfilled slot.
"""

import re
from typing import Dict, Any, List, Optional
from src.core import kb_loader
from src.state.payment_status import is_payment_verified
from src.core.helpers.schedule_engine import (
    get_available_days_for_batch,
    get_teachers_for_slot,
    get_available_timings,
)


def evaluate_enrollment_slots(state: Dict[str, Any], course_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Evaluates state and returns deterministic slot completion status,
    settled vs missing lists, and the exact next slot to target in Message #2.
    """
    s = state or {}

    # 1. Resolve Course
    cid = (course_id if course_id is not None else (s.get("active_course") or s.get("course"))) or ""
    cid = cid.lower().strip()
    catalog_entry = kb_loader.get_catalog_entry(cid)
    if not catalog_entry and cid:
        detected = kb_loader.detect_course_from_text(cid)
        if detected:
            catalog_entry = kb_loader.get_catalog_entry(detected)
    if catalog_entry:
        cid = catalog_entry.get("id", cid)
        active_title = catalog_entry.get("name", cid.title())
    else:
        active_title = cid.title() if cid else "Not Selected"

    # 2. Resolve Timing
    timing_val = s.get("timing") or s.get("selected_timing")
    timing_str = timing_val if timing_val else "NOT SELECTED"

    # 3. Resolve Teacher
    teacher_val = s.get("selected_teacher") or s.get("teacher")
    timing_period = s.get("timing_period") or (s.get("_last_turn") or {}).get("timing_period")
    constraint_note = s.get("auto_assigned_slot_note")
    constraint_res = None

    # Dynamic Constraint Solver: Teacher ↔ Timing / Period
    if cid:
        from src.core.helpers.schedule_engine import resolve_teacher_timing_constraints
        constraint_res = resolve_teacher_timing_constraints(
            course_id=cid,
            teacher=teacher_val,
            timing=timing_val,
            period=timing_period,
        )
        if constraint_res.get("auto_assigned_timing") and not timing_val:
            timing_val = constraint_res["auto_assigned_timing"]
            timing_str = timing_val
            if not constraint_note:
                constraint_note = constraint_res.get("constraint_note")

        if constraint_res.get("auto_assigned_teacher") and not teacher_val:
            teacher_val = constraint_res["auto_assigned_teacher"]
            teacher_str = teacher_val
            if not constraint_note:
                constraint_note = constraint_res.get("constraint_note")

    available_teachers = constraint_res.get("available_teachers_for_timing", []) if constraint_res else []
    teacher_str = teacher_val if teacher_val else "NOT SELECTED"

    # 4. Resolve Days
    days_val = s.get("days") or s.get("selected_days")
    # Days already chosen: only offer instructors who really teach that timing on those days
    # (e.g. Wed/Fri 5-6 PM must not list a teacher who is Tue/Thu + Sat/Sun at 5-6 PM). Used by the AI prompt AND the follow-ups.
    if days_val and cid and timing_str != "NOT SELECTED" and available_teachers:
        on_days = [t for t in available_teachers if days_val in get_available_days_for_batch(cid, timing=timing_str, teacher=t)]
        available_teachers = on_days or available_teachers
    avail_days = (
        get_available_days_for_batch(cid, timing=timing_str, teacher=teacher_str)
        if (cid and timing_str != "NOT SELECTED" and teacher_str != "NOT SELECTED")
        else []
    )
    if not days_val:
        if len(avail_days) <= 1:
            days_val = avail_days[0] if avail_days else "Monday to Friday"
    days_str = days_val if days_val else "NOT SELECTED"

    # 5. Resolve Package Duration
    pkg_val = s.get("package") or s.get("selected_duration")
    package_str = pkg_val if pkg_val else "NOT SELECTED"

    # Check Paid / Enrolled status
    is_paid = is_payment_verified(s)

    is_trial_path = bool(
        s.get("wants_trial")
        or s.get("stage") in ["TRIAL_REQUESTED", "TRIAL_STEPS_SENT"]
        or (s.get("_last_turn") or {}).get("wants_trial")
    )

    # Calculate Settled vs Missing Slots
    settled_slots: List[str] = []
    missing_slots: List[str] = []

    if cid and active_title != "Not Selected":
        settled_slots.append(f"Course: {active_title}")
    else:
        missing_slots.append("Course Selection")

    # If user is on trial path, chat does NOT collect timing, teacher, or package duration
    if is_trial_path:
        missing_slots = []
        is_complete = False
        next_target_slot = "TRIAL"
        next_target_label = "Free Trial Booking on Sensationz App"
    else:
        if timing_str != "NOT SELECTED":
            settled_slots.append(f"Batch Timing: {timing_str}")
        else:
            missing_slots.append("Batch Timing")

        if teacher_str != "NOT SELECTED":
            settled_slots.append(f"Instructor: {teacher_str}")
        else:
            missing_slots.append("Instructor Preference")

        # Only treat days as missing if there are multiple choices that user hasn't selected
        if len(avail_days) > 1 and (not s.get("days") and not s.get("selected_days")):
            missing_slots.append(f"Batch Days ({', '.join(avail_days)})")
        else:
            settled_slots.append(f"Batch Days: {days_str}")

        if package_str != "NOT SELECTED":
            settled_slots.append(f"Package Duration: {package_str}")
        else:
            missing_slots.append("Package Duration")

        # Complete if all required slots are present
        is_complete = len(missing_slots) == 0

        # Determine Next Target Slot
        next_target_slot = "PAYMENT"
        next_target_label = "Payment Summary & Checkout"

        if is_paid:
            next_target_slot = "ENROLLED"
            next_target_label = "Active Student Support"
        elif not cid or active_title == "Not Selected":
            next_target_slot = "COURSE"
            next_target_label = "Course Selection"
        elif timing_str == "NOT SELECTED":
            next_target_slot = "TIMING"
            norm_period = (timing_period or "").strip().title()
            if norm_period:
                next_target_label = f"{norm_period} Batch Timing"
            else:
                next_target_label = "Batch Timing"
        elif teacher_str == "NOT SELECTED":
            next_target_slot = "TEACHER"
            next_target_label = "Instructor Preference"
        elif len(avail_days) > 1 and (not s.get("days") and not s.get("selected_days")):
            next_target_slot = "DAYS"
            next_target_label = "Batch Days"
        elif package_str == "NOT SELECTED":
            next_target_slot = "DURATION"
            next_target_label = "Package Duration"
        else:
            next_target_slot = "PAYMENT"
            next_target_label = "Payment Summary & Checkout"

    return {
        "course_id": cid,
        "course_title": active_title,
        "timing": timing_val,
        "timing_str": timing_str,
        "teacher": teacher_val,
        "teacher_str": teacher_str,
        "available_teachers_for_timing": available_teachers,
        "available_days": avail_days,
        "days": days_val,
        "days_str": days_str,
        "package": pkg_val,
        "package_str": package_str,
        "is_complete": is_complete,
        "settled_slots": settled_slots,
        "missing_slots": missing_slots,
        "next_target_slot": next_target_slot,
        "next_target_label": next_target_label,
        "next_target_buttons": [],
        "is_paid": is_paid,
        "is_trial_path": is_trial_path,
        "constraint_note": constraint_note,
    }



# ── One shared "what do we ask next?" for the AI's Message #2 AND the timed follow-ups ──────────

_ASKS = {  # which slot a question is asking about
    # question forms only: a statement like "Aapne *Wed/Fri* select kiya hai" must NOT match
    "TIMING": re.compile(r"which (batch |specific )?timing|which time slot|kaunsa (batch )?timing|kaunsi timing|kaunsa time\b|kis time", re.I),
    "TEACHER": re.compile(r"which instructor|which teacher|kaunse instructor|kis instructor", re.I),
    "DAYS": re.compile(r"which days|kaunse din|day.?pair|kaunsa pair|pair (chahenge|best)|tue/thu ya wed/fri", re.I),
    "DURATION": re.compile(r"which package|kaunsa package|choose a package", re.I),
}
_QUESTION_SLOTS = ("COURSE", "TIMING", "TEACHER", "DAYS", "DURATION")


def _is_english(language: Optional[str]) -> bool:
    lang = str(language or "").lower()
    return "english" in lang and "hinglish" not in lang


def followup_asks_wrong_slot(text: Optional[str], slot_eval: Dict[str, Any]) -> bool:
    """True if `text` asks for any slot other than the lead's next missing one: something already chosen,
    or a later slot that skips ahead (e.g. package while Kathak batch days are still open). Not for trial / paid."""
    nxt = slot_eval.get("next_target_slot")
    if not text or nxt not in (*_QUESTION_SLOTS, "PAYMENT"):
        return False
    return any(slot != nxt and rx.search(text) for slot, rx in _ASKS.items())


def slot_question(slot_eval: Dict[str, Any], language: Optional[str] = None) -> Optional[str]:
    """The ONE question for the lead's next missing slot, or None (payment / paid / trial: nothing to ask)."""
    slot, en = slot_eval.get("next_target_slot"), _is_english(language)
    if slot == "COURSE":
        return "Which course would you like to know more about? 😊" if en else "Aap kaunsa course join karna chahenge? 😊"
    if slot == "TIMING":
        return "Which batch timing suits your daily routine best? 😊" if en else "Aap kaunsa batch timing select karna chahenge? 😊"
    if slot == "TEACHER":
        names = slot_eval.get("available_teachers_for_timing") or []
        opts = (" or " if en else " ya ").join(names)
        if en:
            return f"Which instructor would you like to learn with{' — ' + opts if opts else ''}? 😊"
        return f"Aap kis instructor ke saath seekhna chahenge{' — ' + opts if opts else ''}? 😊"
    if slot == "DAYS":
        opts = (" or " if en else " ya ").join(slot_eval.get("available_days") or [])
        return f"Which days work for you{' — ' + opts if opts else ''}? 😊" if en else f"Aap kaunse din ke batch mein join karna chahenge{' — ' + opts if opts else ''}? 😊"
    if slot == "DURATION":
        return ("Which package duration would you like to choose — 1 Month, 3 Months, 6 Months, or 1 Year? 😊" if en else
                "Aap kaunsa package duration choose karna chahenge — 1 Month starter pack ya hamara 3 Months offer package? 😊")
    return None
