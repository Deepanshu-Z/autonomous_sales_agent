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

from typing import Dict, Any, List, Optional
from src.core import kb_loader
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
    is_paid = bool(
        s.get("payment_status") == "PAID"
        or s.get("is_enrolled")
        or s.get("stage") == "ENROLLED"
    )

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
        next_target_buttons = ["Download App", "How to Book"]
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

        # Determine Next Target Slot and Buttons
        next_target_slot = "PAYMENT"
        next_target_label = "Payment Summary & Checkout"
        next_target_buttons = []

        if is_paid:
            next_target_slot = "ENROLLED"
            next_target_label = "Active Student Support"
            next_target_buttons = ["My Classes", "App Login"]
        elif not cid or active_title == "Not Selected":
            next_target_slot = "COURSE"
            next_target_label = "Course Selection"
            next_target_buttons = ["Yoga", "Kathak", "Ladies Dance"]
        elif timing_str == "NOT SELECTED":
            next_target_slot = "TIMING"
            next_target_label = "Batch Timing"
            if constraint_res and constraint_res.get("available_timings_for_teacher"):
                next_target_buttons = constraint_res["available_timings_for_teacher"][:3]
            else:
                raw_timings = [b.get("time_12h", "").strip() for b in get_available_timings(cid)] if cid else []
                clean_timings = [t for t in raw_timings if t]
                next_target_buttons = clean_timings[:3] if clean_timings else ["Morning Batch", "Evening Batch"]
        elif teacher_str == "NOT SELECTED":
            next_target_slot = "TEACHER"
            next_target_label = "Instructor Preference"
            if constraint_res and constraint_res.get("available_teachers_for_timing"):
                next_target_buttons = constraint_res["available_teachers_for_timing"][:3]
            elif available_teachers:
                next_target_buttons = available_teachers[:3]
            else:
                course_teachers = kb_loader.get_course_instructors(cid)
                next_target_buttons = course_teachers[:3] if course_teachers else ["Certified Teacher"]
        elif len(avail_days) > 1 and (not s.get("days") and not s.get("selected_days")):
            next_target_slot = "DAYS"
            next_target_label = "Batch Days"
            next_target_buttons = avail_days[:3]
        elif package_str == "NOT SELECTED":
            next_target_slot = "DURATION"
            next_target_label = "Package Duration"
            valid_pkgs = list(kb_loader.get_valid_packages(cid).keys()) if cid else ["1 Month", "3 Months"]
            next_target_buttons = [f"{p} Pack" for p in valid_pkgs[:2]]
        else:
            next_target_slot = "PAYMENT"
            next_target_label = "Payment Summary & Checkout"
            next_target_buttons = ["Talk to Agent"]

    return {
        "course_id": cid,
        "course_title": active_title,
        "timing": timing_val,
        "timing_str": timing_str,
        "teacher": teacher_val,
        "teacher_str": teacher_str,
        "available_teachers_for_timing": available_teachers,
        "days": days_val,
        "days_str": days_str,
        "package": pkg_val,
        "package_str": package_str,
        "is_complete": is_complete,
        "settled_slots": settled_slots,
        "missing_slots": missing_slots,
        "next_target_slot": next_target_slot,
        "next_target_label": next_target_label,
        "next_target_buttons": next_target_buttons,
        "is_paid": is_paid,
        "is_trial_path": is_trial_path,
        "constraint_note": constraint_note,
    }

