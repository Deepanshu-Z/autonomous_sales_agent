"""
pricing_engine.py — Course Package Pricing, GST Breakdown, Fee Calculation & Slot-Teacher Validation.

Single-responsibility component providing deterministic Python calculations for:
1. Exact package billing (base price, discount, GST breakdown, final payable amount in INR and paise).
2. Slot-teacher compatibility validation (checks whether an instructor is assigned to a specific batch slot).
3. Transparent receipt formatting for WhatsApp / user messages.

Zero LLM Math: All fees, discounts, taxes, and schedule compatibilities are grounded
strictly in course knowledge bases (data/courses/*.json).
"""

import re
from typing import Dict, Any, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# 1. TAX & GST CALCULATION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def get_course_gst_percentage(course_id: Optional[str] = None) -> int:
    """Retrieves the official GST percentage for a course (e.g. 18 for Kathak, 5 for Yoga/Face Yoga)."""
    if not course_id:
        return 5
    from src.core.kb_loader import get_course_data, get_catalog_entry
    data = get_course_data(course_id)
    pricing = data.get("pricing", {})
    if pricing.get("gst_percentage") is not None:
        return int(pricing["gst_percentage"])
    entry = get_catalog_entry(course_id)
    if entry and entry.get("gst_percentage") is not None:
        return int(entry["gst_percentage"])
    return 5


def calculate_gst_breakdown(taxable_amount: int, gst_percentage: int) -> Dict[str, Any]:
    """Calculates exact GST amount, CGST, SGST, and total tax with rounded integers."""
    if taxable_amount <= 0 or gst_percentage <= 0:
        return {
            "percentage": gst_percentage,
            "gst_amount": 0,
            "cgst_percentage": round(gst_percentage / 2, 1),
            "cgst_amount": 0,
            "sgst_percentage": round(gst_percentage / 2, 1),
            "sgst_amount": 0,
            "total_tax": 0,
        }

    total_gst = round((taxable_amount * gst_percentage) / 100)
    cgst_amount = round(total_gst / 2)
    sgst_amount = total_gst - cgst_amount

    return {
        "percentage": gst_percentage,
        "gst_amount": total_gst,
        "cgst_percentage": round(gst_percentage / 2, 1),
        "cgst_amount": cgst_amount,
        "sgst_percentage": round(gst_percentage / 2, 1),
        "sgst_amount": sgst_amount,
        "total_tax": total_gst,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2. DISCOUNT & PACKAGE RESOLUTION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def calculate_discount(base_price: int, offer_price: int) -> Dict[str, Any]:
    """Calculates discount amount, discount percentage, and boolean flag."""
    if base_price <= 0:
        return {
            "discount": 0,
            "discount_amount": 0,
            "discount_percentage": 0.0,
            "has_discount": False,
        }

    discount_amount = max(0, base_price - offer_price)
    discount_pct = round((discount_amount / base_price) * 100, 1) if base_price > 0 else 0.0

    return {
        "discount": discount_amount,
        "discount_amount": discount_amount,
        "discount_percentage": discount_pct,
        "has_discount": discount_amount > 0,
    }


def get_valid_packages(course_id: Optional[str] = None) -> Dict[str, str]:
    """Dynamically extracts package names + formatted fee strings from the course JSON."""
    if not course_id:
        return {}
    from src.core.kb_loader import get_course_data
    data = get_course_data(course_id)
    packages = data.get("pricing", {}).get("packages", [])
    valid_pkgs: Dict[str, str] = {}
    for p in packages:
        duration = p.get("duration")
        if not duration:
            continue
        base = p.get("base_price")
        offer = p.get("offer_price")
        coupon = p.get("coupon_code")
        if base is not None and offer is not None:
            coupon_str = f" with coupon *{coupon}*" if coupon else ""
            valid_pkgs[duration] = f"₹{base:,} (Offer Price: ₹{offer:,}{coupon_str})"
        elif base is not None and coupon:
            valid_pkgs[duration] = f"₹{base:,} (Coupon: {coupon})"
        elif base is not None:
            valid_pkgs[duration] = f"₹{base:,}"
        elif offer is not None:
            valid_pkgs[duration] = f"Offer Price: ₹{offer:,}"
    return valid_pkgs


def get_package_by_duration(course_id: Optional[str] = None, duration: str = "") -> Optional[Dict[str, Any]]:
    """Looks up and returns the raw pricing package dict for a given duration.
    Works dynamically for any course. Zero hallucination, grounded strictly in course JSON.
    """
    if not course_id or not duration:
        return None

    from src.core.kb_loader import get_course_data
    course_data = get_course_data(course_id)
    packages = course_data.get("pricing", {}).get("packages", [])
    if not packages:
        return None

    query_norm = duration.lower().strip()
    m_num = re.search(r"\b(\d+)\s*(m|month|months|mahina|mahine|y|year|years|saal)?\b", query_norm)
    num_val = m_num.group(1) if m_num else ""
    unit_val = (m_num.group(2) or "m").lower() if m_num else ""

    # Special check: 12 months == 1 year
    if num_val == "12" and ("m" in unit_val or "month" in unit_val):
        num_val = "1"
        unit_val = "y"

    for pkg in packages:
        pkg_duration = pkg.get("duration", "")
        pkg_code = pkg.get("duration_code", "")
        pkg_norm = pkg_duration.lower()
        coupon_code = pkg.get("coupon_code", "")

        is_match = False
        if query_norm in pkg_norm or pkg_norm in query_norm:
            is_match = True
        elif pkg_code and query_norm == pkg_code.lower():
            is_match = True
        elif coupon_code and query_norm == coupon_code.lower():
            is_match = True
        elif num_val:
            if "y" in unit_val or "year" in unit_val or "saal" in unit_val:
                if f"{num_val}y" == pkg_code.lower() or f"{num_val} year" in pkg_norm:
                    is_match = True
            else:
                if f"{num_val}m" == pkg_code.lower() or f"{num_val} month" in pkg_norm:
                    is_match = True

        if is_match:
            return pkg

    return None


# ─────────────────────────────────────────────────────────────────────────────
# 3. EXACT BILLING & FEE CALCULATION (ZERO LLM MATH)
# ─────────────────────────────────────────────────────────────────────────────

def calculate_exact_fee(course_id: str, duration: str) -> Optional[Dict[str, Any]]:
    """Calculates the complete, deterministic fee breakdown for a course package.

    Returns:
    - base_price: integer standard price in INR
    - discount: integer discount amount (base_price - offer_price) in INR
    - discount_percentage: float percentage of discount
    - offer_price: integer discounted price in INR
    - gst_percentage: integer GST tax rate (e.g. 5, 18)
    - gst_amount: integer GST tax amount in INR
    - gst_breakdown: detailed dict with cgst_amount, sgst_amount, percentages
    - final_payable_amount: exact integer total payable at checkout in INR
    - total_payable: alias for final_payable_amount
    - amount_rupees: integer total in INR (for payment gateway order)
    - amount_paise: integer total in paise (amount_rupees * 100)
    """
    if not course_id or not duration:
        return None

    pkg = get_package_by_duration(course_id, duration)
    if not pkg:
        return None

    from src.core.kb_loader import get_course_data
    course_data = get_course_data(course_id)
    course_name = course_data.get("course_overview", {}).get("name", course_id.title())

    base_price = int(pkg.get("base_price", 0))
    offer_price = int(pkg.get("offer_price", base_price))
    gst_pct = get_course_gst_percentage(course_id)

    # Calculate discount
    discount_info = calculate_discount(base_price, offer_price)
    discount_amount = discount_info["discount_amount"]

    # Calculate GST
    json_gst_amount = pkg.get("gst_on_offer") or pkg.get("gst_on_base")
    gst_amount = int(json_gst_amount) if json_gst_amount is not None else round((offer_price * gst_pct) / 100)
    gst_breakdown = calculate_gst_breakdown(offer_price, gst_pct)
    # Ensure json-specified gst_amount aligns if present
    if json_gst_amount is not None:
        gst_breakdown["gst_amount"] = gst_amount
        gst_breakdown["total_tax"] = gst_amount
        cgst = round(gst_amount / 2)
        gst_breakdown["cgst_amount"] = cgst
        gst_breakdown["sgst_amount"] = gst_amount - cgst

    # Final payable amount
    json_total = pkg.get("total_with_gst_offer") or pkg.get("total_with_gst")
    total_payable = int(json_total) if json_total is not None else (offer_price + gst_amount)

    pkg_duration = pkg.get("duration", duration)
    pkg_code = pkg.get("duration_code", "")
    coupon_code = pkg.get("coupon_code")
    per_day = pkg.get("per_day_offer") or pkg.get("per_day")

    return {
        "course_id": course_id,
        "course_name": course_name,
        "duration": pkg_duration,
        "duration_code": pkg_code,
        "base_price": base_price,
        "discount": discount_amount,
        "discount_amount": discount_amount,
        "discount_percentage": discount_info["discount_percentage"],
        "has_discount": discount_info["has_discount"],
        "offer_price": offer_price,
        "gst_percentage": gst_pct,
        "gst_amount": gst_amount,
        "gst_breakdown": gst_breakdown,
        "final_payable_amount": total_payable,
        "total_payable": total_payable,
        "amount_rupees": total_payable,
        "amount_paise": total_payable * 100,
        "coupon_code": coupon_code,
        "per_day": per_day,
    }


def calculate_bill(course_id: str, duration: str) -> Optional[Dict[str, Any]]:
    """Clean alias for calculate_exact_fee."""
    return calculate_exact_fee(course_id, duration)


def format_fee_breakdown(course_id: str, duration: str) -> Optional[str]:
    """Generates an itemized bill receipt with base price, discount, GST breakdown, and total payable."""
    fee = calculate_exact_fee(course_id, duration)
    if not fee:
        return None

    lines = [
        f"🧾 *Fee Breakdown — {fee.get('course_name', course_id.title())} ({fee['duration']}):*",
        f"• Base Price: ₹{fee['base_price']:,}",
    ]
    if fee["discount"] > 0:
        coupon_str = f" (Coupon: *{fee['coupon_code']}*)" if fee.get("coupon_code") else ""
        lines.append(f"• Special Discount: -₹{fee['discount']:,}{coupon_str}")
        lines.append(f"• Offer Price: ₹{fee['offer_price']:,}")
    lines.append(f"• GST ({fee['gst_percentage']}%): +₹{fee['gst_amount']:,}")
    lines.append(f"• *Final Payable Amount: ₹{fee['final_payable_amount']:,}*")
    if fee.get("per_day"):
        lines.append(f"• Value: {fee['per_day']}")

    return "\n".join(lines)


def get_course_coupons(course_id: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Dynamically extracts coupon codes per package duration — zero hardcoded codes."""
    if not course_id:
        return {}
    from src.core.kb_loader import get_course_data
    data = get_course_data(course_id)
    packages = data.get("pricing", {}).get("packages", [])
    cid_upper = course_id.upper().replace(" ", "_")
    coupons = {}
    for pkg in packages:
        duration = pkg.get("duration")
        if not duration:
            continue
        offer = pkg.get("offer_price")
        base = pkg.get("base_price", 0)
        coupons[duration] = {
            "code": pkg.get("coupon_code") or f"{cid_upper}{offer if offer is not None else base}",
            "base_price": base,
            "offer_price": offer if offer is not None else base,
            "duration": duration,
        }
    return coupons


def format_package_fees_display(course_id: Optional[str] = None) -> str:
    """Formats package fees cleanly for user-facing replies and prompt display."""
    if not course_id:
        return ""
    pkgs = get_valid_packages(course_id)
    if not pkgs:
        return ""
    gst_pct = get_course_gst_percentage(course_id)
    lines = [f"• *{pkg}:* {fee}" for pkg, fee in pkgs.items()]
    lines.append(f"\n*Note:* {gst_pct}% GST will be applicable at checkout on the app/website.")
    return "\n".join(lines)


def get_pricing_and_timings_summary(course_id: str) -> Dict[str, Any]:
    """Compact packages+timings summary for cross-course reference blocks."""
    from src.core.kb_loader import get_course_data, get_catalog_entry
    from src.core.helpers.schedule_engine import get_valid_timings
    data = get_course_data(course_id)
    entry = get_catalog_entry(course_id) or {}
    return {
        "name": data.get("course_overview", {}).get("name", entry.get("name", course_id.title())),
        "packages": get_valid_packages(course_id),
        "timings": get_valid_timings(course_id),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4. SLOT-TEACHER COMPATIBILITY VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

def _parse_time_point(time_str: Optional[str]) -> Optional[Tuple[int, int, str]]:
    """Extracts starting (hour, minute, AM/PM) from a time string."""
    if not time_str:
        return None
    # 1. Range match: '5:00 PM to 6:00 PM' or '5-6 PM'
    m_range = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:to|-|–)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)", time_str, re.IGNORECASE)
    if m_range:
        s_hr = int(m_range.group(1))
        s_min = int(m_range.group(2) or 0)
        e_mer = m_range.group(6).upper()
        s_mer = m_range.group(3).upper() if m_range.group(3) else e_mer
        return s_hr, s_min, s_mer

    # 2. Single time: '5:00 PM', '5 pm', '17:00'
    m_single = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", time_str, re.IGNORECASE)
    if m_single:
        return int(m_single.group(1)), int(m_single.group(2) or 0), m_single.group(3).upper()

    # 3. 24-hour single time: '17:00'
    m_24 = re.search(r"\b(\d{1,2}):(\d{2})\b", time_str)
    if m_24:
        hr24 = int(m_24.group(1))
        min24 = int(m_24.group(2))
        mer = "PM" if hr24 >= 12 else "AM"
        hr12 = hr24 if 1 <= hr24 <= 12 else (hr24 - 12 if hr24 > 12 else 12)
        return hr12, min24, mer

    return None


def find_matching_batches(course_id: Optional[str] = None, slot_or_timing: str = "") -> List[Dict[str, Any]]:
    """Finds all batch definitions matching a slot query (e.g. '5:00 PM', 'Tuesday/Thursday 5 PM', 'Morning')."""
    if not course_id or not slot_or_timing:
        return []

    from src.core.helpers.schedule_engine import get_available_timings, classify_time_period
    batches = get_available_timings(course_id)
    if not batches:
        return []

    q_norm = slot_or_timing.strip().lower()

    # Check for day keywords
    day_keywords = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
                    "mon", "tue", "wed", "thu", "fri", "sat", "sun", "weekday", "weekend"]
    query_days = [d for d in day_keywords if d in q_norm]

    # Check for time point or period
    q_time = _parse_time_point(q_norm)
    period_query = next((p.title() for p in ("morning", "afternoon", "evening") if p in q_norm), None)

    matched = []
    for b in batches:
        days = b.get("days", "").lower()
        slot = b.get("slot", "").lower()
        t12 = b.get("time_12h", "")
        b_time = _parse_time_point(t12)

        # Day filter: if query explicitly mentions day, batch must match
        if query_days:
            if not any(d in days or d in slot for d in query_days):
                continue

        # Time filter
        if q_time:
            if b_time != q_time:
                continue
        elif period_query:
            b_period = classify_time_period(t12) or slot
            if b_period and b_period.lower() != period_query.lower():
                continue

        matched.append(b)

    return matched


def get_teachers_for_slot(course_id: Optional[str] = None, slot_or_timing: str = "") -> List[str]:
    """Returns deduplicated list of instructor names assigned to the specified slot/timing."""
    if not course_id or not slot_or_timing:
        return []

    matched_batches = find_matching_batches(course_id, slot_or_timing)
    teachers: List[str] = []
    for b in matched_batches:
        for inst in b.get("instructors", []):
            if inst and inst not in teachers:
                teachers.append(inst)

    # Fallback to schedule_engine if fine-grained batch matching yielded none
    if not teachers:
        from src.core.helpers.schedule_engine import get_teachers_for_slot as se_get_teachers
        teachers = se_get_teachers(course_id, slot_or_timing)

    return teachers


def get_slots_for_teacher(course_id: Optional[str] = None, teacher_name: str = "") -> List[Dict[str, Any]]:
    """Returns all batch timings taught by a specific teacher for the given course."""
    if not course_id or not teacher_name:
        return []
    from src.core.helpers.schedule_engine import get_slots_for_teacher as se_get_slots
    return se_get_slots(course_id, teacher_name)


def is_teacher_in_course(course_id: str, teacher_name: str) -> bool:
    """Returns True if the instructor teaches at least one batch in the given course."""
    if not course_id or not teacher_name:
        return False
    from src.core.helpers.schedule_engine import get_course_instructors, resolve_canonical_teacher
    canonical = resolve_canonical_teacher(teacher_name, course_id=course_id) or teacher_name.strip()
    instructors = get_course_instructors(course_id)
    canonical_lower = canonical.lower()
    return any(canonical_lower == i.lower() or canonical_lower in i.lower() or i.lower() in canonical_lower for i in instructors)


def is_valid_timing(course_id: str, slot_or_timing: str) -> bool:
    """Returns True if the given slot or timing exists in the course schedule."""
    if not course_id or not slot_or_timing:
        return False
    return len(find_matching_batches(course_id, slot_or_timing)) > 0


def validate_teacher_slot(course_id: str, teacher_name: str, slot_or_timing: str) -> Dict[str, Any]:
    """Validates compatibility between an instructor and a batch timing slot.

    Returns structured validation result:
    - is_valid: bool (True if teacher teaches this slot)
    - course_id: canonical course id
    - teacher: resolved canonical teacher name
    - slot: requested slot string
    - reason: "COMPATIBLE", "TEACHER_NOT_IN_COURSE", "SLOT_NOT_FOUND", "TEACHER_SLOT_MISMATCH", "INVALID_INPUT"
    - message: clear explanatory message
    - teachers_for_slot: instructors assigned to this slot
    - available_slots_for_teacher: valid slots taught by this teacher
    """
    if not course_id or not teacher_name or not slot_or_timing:
        return {
            "is_valid": False,
            "course_id": course_id or "",
            "teacher": teacher_name or "",
            "slot": slot_or_timing or "",
            "reason": "INVALID_INPUT",
            "message": "Course, teacher name, and slot/timing must all be provided.",
            "teachers_for_slot": [],
            "available_slots_for_teacher": [],
        }

    from src.core.helpers.schedule_engine import (
        resolve_canonical_teacher,
        get_course_instructors,
        get_valid_timings,
    )

    canonical_teacher = resolve_canonical_teacher(teacher_name, course_id=course_id) or teacher_name.strip()
    course_instructors = get_course_instructors(course_id)

    # 1. Verify if teacher belongs to course
    t_match = [i for i in course_instructors if canonical_teacher.lower() == i.lower() or canonical_teacher.lower() in i.lower() or i.lower() in canonical_teacher.lower()]
    if not t_match:
        return {
            "is_valid": False,
            "course_id": course_id,
            "teacher": canonical_teacher,
            "slot": slot_or_timing,
            "reason": "TEACHER_NOT_IN_COURSE",
            "message": f"Instructor '{canonical_teacher}' does not teach '{course_id}'. Available instructors: {', '.join(course_instructors)}",
            "teachers_for_slot": [],
            "available_slots_for_teacher": [],
        }
    resolved_teacher = t_match[0]

    # 2. Verify if slot exists in course
    matched_batches = find_matching_batches(course_id, slot_or_timing)
    if not matched_batches:
        valid_timings = get_valid_timings(course_id)
        return {
            "is_valid": False,
            "course_id": course_id,
            "teacher": resolved_teacher,
            "slot": slot_or_timing,
            "reason": "SLOT_NOT_FOUND",
            "message": f"Slot '{slot_or_timing}' is not offered for '{course_id}'. Available slots: {', '.join(valid_timings)}",
            "teachers_for_slot": [],
            "available_slots_for_teacher": [f"{s.get('days', '')} {s.get('time_12h', '')}".strip() for s in get_slots_for_teacher(course_id, resolved_teacher)],
        }

    # 3. Check if teacher is assigned to any of the matching batches
    slot_teachers = get_teachers_for_slot(course_id, slot_or_timing)
    is_assigned = any(
        resolved_teacher.lower() == st.lower() or resolved_teacher.lower() in st.lower() or st.lower() in resolved_teacher.lower()
        for st in slot_teachers
    )

    teacher_slots = [
        f"{s.get('days', '')} {s.get('time_12h', '')}".strip()
        for s in get_slots_for_teacher(course_id, resolved_teacher)
    ]

    if is_assigned:
        return {
            "is_valid": True,
            "course_id": course_id,
            "teacher": resolved_teacher,
            "slot": slot_or_timing,
            "reason": "COMPATIBLE",
            "message": f"{resolved_teacher} is assigned and available for the '{slot_or_timing}' batch.",
            "teachers_for_slot": slot_teachers,
            "available_slots_for_teacher": teacher_slots,
        }
    else:
        return {
            "is_valid": False,
            "course_id": course_id,
            "teacher": resolved_teacher,
            "slot": slot_or_timing,
            "reason": "TEACHER_SLOT_MISMATCH",
            "message": (
                f"{resolved_teacher} does not teach the '{slot_or_timing}' batch. "
                f"Teachers for this slot: {', '.join(slot_teachers)}. "
                f"{resolved_teacher}'s available slots: {', '.join(teacher_slots)}."
            ),
            "teachers_for_slot": slot_teachers,
            "available_slots_for_teacher": teacher_slots,
        }


def validate_slot_teacher(course_id: str, slot_or_timing: str, teacher_name: str) -> Dict[str, Any]:
    """Convenience alias for validate_teacher_slot with inverted argument order."""
    return validate_teacher_slot(course_id=course_id, teacher_name=teacher_name, slot_or_timing=slot_or_timing)


def is_teacher_available_for_slot(course_id: str, teacher_name: str, slot_or_timing: str) -> bool:
    """Boolean check: returns True if instructor is available for the given slot."""
    return validate_teacher_slot(course_id=course_id, teacher_name=teacher_name, slot_or_timing=slot_or_timing)["is_valid"]


def is_slot_teacher_compatible(course_id: str, slot_or_timing: str, teacher_name: str) -> bool:
    """Boolean check alias: returns True if slot and teacher are compatible."""
    return validate_teacher_slot(course_id=course_id, teacher_name=teacher_name, slot_or_timing=slot_or_timing)["is_valid"]


# ─────────────────────────────────────────────────────────────────────────────
# 5. ORDER SUMMARY FORMATTER (SUBTASK 3.2)
# ─────────────────────────────────────────────────────────────────────────────

def get_order_summary_details(state: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
    """Extracts, validates, and calculates all structured order details from session state.

    Supports both dictionary state and direct keyword overrides (e.g. course_id, duration, timing, teacher).
    Returns a comprehensive dictionary containing normalized student, course, schedule,
    plan, itemized pricing, tax breakdown, and account status details.
    """
    s = dict(state or {})
    s.update({k: v for k, v in kwargs.items() if v is not None})

    from src.core import kb_loader
    from src.core.helpers.schedule_engine import resolve_canonical_teacher

    # 1. Resolve Course
    raw_course = s.get("course") or s.get("active_course") or s.get("selected_course") or s.get("course_id") or ""
    catalog_entry = kb_loader.get_catalog_entry(raw_course) if raw_course else None
    if not catalog_entry and raw_course:
        detected = kb_loader.detect_course_from_text(raw_course)
        if detected:
            catalog_entry = kb_loader.get_catalog_entry(detected)
    course_id = catalog_entry.get("id", raw_course.lower().strip()) if catalog_entry else raw_course.lower().strip()

    course_data = kb_loader.get_course_data(course_id) if course_id else {}
    course_name = (
        course_data.get("course_overview", {}).get("name")
        or (catalog_entry.get("name") if catalog_entry else (course_id.title() if course_id else "Not Selected"))
    )
    mode = course_data.get("course_overview", {}).get("mode", "Online Only")
    platform = course_data.get("course_overview", {}).get("platform", "Sensationz App")
    class_days = course_data.get("course_overview", {}).get("class_days", "Monday to Friday (5 days per week)")

    # 2. Resolve Timing & Teacher
    timing = s.get("timing") or s.get("selected_timing") or ""
    raw_teacher = s.get("selected_teacher") or s.get("teacher") or s.get("teacher_name") or ""
    resolved_teacher = (
        resolve_canonical_teacher(raw_teacher, course_id=course_id) if (raw_teacher and course_id) else raw_teacher
    ) or ""

    slot_validation = None
    if course_id and timing and resolved_teacher:
        slot_validation = validate_teacher_slot(course_id=course_id, teacher_name=resolved_teacher, slot_or_timing=timing)
    elif course_id and timing:
        slot_validation = {
            "is_valid": is_valid_timing(course_id, timing),
            "reason": "SLOT_ONLY",
            "teachers_for_slot": get_teachers_for_slot(course_id, timing),
        }

    # 3. Resolve Plan & Pricing
    duration = s.get("package") or s.get("selected_duration") or s.get("duration") or ""
    fee_breakdown = calculate_exact_fee(course_id, duration) if (course_id and duration) else None

    # 4. Resolve Account & Onboarding State
    app_installed = s.get("app_installed")
    profile_created = s.get("profile_created")
    wants_trial = s.get("wants_trial") or False
    stage = s.get("stage") or "NEW"
    phone = s.get("phone") or s.get("customer_phone") or ""
    student_name = s.get("customer_name") or s.get("user_name") or s.get("name") or ""
    order_id = s.get("order_id") or ""
    payment_status = s.get("payment_status") or ("PAID" if s.get("claims_payment") else "PENDING")
    language = s.get("language") or s.get("detected_language") or "English"

    return {
        "student": {
            "name": student_name,
            "phone": phone,
            "language": language,
            "stage": stage,
        },
        "course": {
            "id": course_id,
            "name": course_name,
            "mode": mode,
            "platform": platform,
            "class_days": class_days,
            "is_digitized": bool(course_data.get("pricing")),
        },
        "schedule": {
            "timing": timing,
            "teacher": resolved_teacher,
            "slot_validation": slot_validation,
            "is_compatible": slot_validation.get("is_valid") if slot_validation else None,
        },
        "plan": {
            "duration": fee_breakdown.get("duration", duration) if fee_breakdown else duration,
            "duration_code": fee_breakdown.get("duration_code", "") if fee_breakdown else "",
            "per_day": fee_breakdown.get("per_day", "") if fee_breakdown else "",
        },
        "pricing": fee_breakdown,
        "account_status": {
            "app_installed": app_installed,
            "profile_created": profile_created,
            "wants_trial": wants_trial,
            "payment_status": payment_status,
            "order_id": order_id,
        },
        "raw_state": s,
    }


def format_order_summary(state: Optional[Dict[str, Any]] = None, **kwargs) -> str:
    """Generates a complete, beautifully formatted order summary text block.

    Includes:
    - Student & session info (phone, stage, onboarding status)
    - Course & schedule info (course name, mode, timing, instructor, validation badge)
    - Plan & duration (e.g. 3 Months, per day cost)
    - Itemized price breakdown with zero LLM math (base, discount, coupon, GST, total in INR & paise)
    - Account onboarding status (App installed, profile created, trial status)
    - Next steps / checkout directions
    """
    details = get_order_summary_details(state, **kwargs)

    st = details["student"]
    c = details["course"]
    sch = details["schedule"]
    pl = details["plan"]
    p = details["pricing"]
    acc = details["account_status"]

    lines: List[str] = [
        "========================================",
        "📋 SENSATIONZ — STUDENT ORDER SUMMARY",
        "========================================",
        "",
    ]

    # Student / Session Block
    lines.append("👤 *Student & Session Details:*")
    if st["name"]:
        lines.append(f"• Student Name: {st['name']}")
    if st["phone"]:
        lines.append(f"• Contact: {st['phone']}")
    lines.append(f"• Funnel Stage: {st['stage']}")
    if st["language"]:
        lines.append(f"• Language: {st['language']}")
    lines.append("")

    # Course & Schedule Block
    lines.append("🎓 *Course & Academic Schedule:*")
    lines.append(f"• Course: *{c['name']}*")
    lines.append(f"• Platform: {c['platform']} ({c['mode']})")
    lines.append(f"• Class Days: {c['class_days']}")

    # Batch timing
    if sch["timing"]:
        lines.append(f"• Selected Timing: *{sch['timing']}*")
    else:
        lines.append("• Selected Timing: _Not Selected Yet_")

    # Teacher
    if sch["teacher"]:
        val = sch["slot_validation"]
        if val and val.get("is_valid"):
            teacher_tag = " (Verified & Assigned ✅)"
        elif val and val.get("reason") == "TEACHER_SLOT_MISMATCH":
            teacher_tag = " (⚠️ Slot Mismatch — verify assigned batch)"
        elif val and val.get("reason") == "TEACHER_NOT_IN_COURSE":
            teacher_tag = " (⚠️ Unrecognized Instructor)"
        else:
            teacher_tag = ""
        lines.append(f"• Instructor: *{sch['teacher']}*{teacher_tag}")
    else:
        lines.append("• Instructor: _Flexible / Any Assigned Instructor_")
    lines.append("")

    # Plan Block
    lines.append("📦 *Selected Package:*")
    if pl["duration"]:
        dur_code_str = f" ({pl['duration_code']})" if pl["duration_code"] else ""
        lines.append(f"• Duration: *{pl['duration']}*{dur_code_str}")
        if pl["per_day"]:
            lines.append(f"• Daily Value: {pl['per_day']}")
    else:
        lines.append("• Duration: _Not Selected Yet_")
    lines.append("")

    # Pricing & Tax Breakdown Block (Zero LLM Math)
    lines.append("💰 *Itemized Price Breakdown (Zero LLM Math):*")
    if p:
        lines.append(f"• Base Catalog Price: ₹{p['base_price']:,}")
        if p["discount"] > 0:
            coupon_str = f" (Coupon: *{p['coupon_code']}*)" if p.get("coupon_code") else ""
            lines.append(f"• Special Discount: -₹{p['discount']:,}{coupon_str}")
            lines.append(f"• Taxable Offer Price: ₹{p['offer_price']:,}")
        else:
            lines.append("• Special Discount: ₹0 (No offer applicable for this tier)")

        # GST breakdown
        gst_bk = p.get("gst_breakdown", {})
        cgst_amt = gst_bk.get("cgst_amount", round(p["gst_amount"] / 2))
        sgst_amt = gst_bk.get("sgst_amount", p["gst_amount"] - cgst_amt)
        cgst_pct = gst_bk.get("cgst_percentage", round(p["gst_percentage"] / 2, 1))
        sgst_pct = gst_bk.get("sgst_percentage", round(p["gst_percentage"] / 2, 1))

        lines.append(f"• GST Breakdown ({p['gst_percentage']}%):")
        lines.append(f"  - CGST ({cgst_pct}%): ₹{cgst_amt:,}")
        lines.append(f"  - SGST ({sgst_pct}%): ₹{sgst_amt:,}")
        lines.append(f"  - Total GST Amount: +₹{p['gst_amount']:,}")
        lines.append("----------------------------------------")
        lines.append(f"• *TOTAL PAYABLE AMOUNT: ₹{p['final_payable_amount']:,}* ({p['amount_paise']:,} Paise)")
        lines.append("----------------------------------------")
    else:
        lines.append("• Fee: _Package duration not selected yet_")
        lines.append("  (Select 1 Month, 3 Months, 6 Months, or 1 Year to view exact breakdown)")
    lines.append("")

    # Account & Onboarding Status
    lines.append("📱 *Account & Onboarding Status:*")
    app_str = "Confirmed ✅" if acc["app_installed"] else "Pending ⏳"
    lines.append(f"• Sensationz App: {app_str}")

    profile_str = "Confirmed ✅" if acc["profile_created"] else "Pending ⏳"
    lines.append(f"• Profile Registration: {profile_str}")

    if acc["wants_trial"]:
        lines.append("• Trial Class Status: Requested 🎯")

    if acc["payment_status"]:
        lines.append(f"• Payment Status: {acc['payment_status']}")
    if acc["order_id"]:
        lines.append(f"• Order Reference: {acc['order_id']}")
    lines.append("")

    # Next Steps / Guidance
    lines.append("📲 *Next Steps to Complete Enrollment:*")
    if p and p.get("coupon_code"):
        lines.append(f"1. Open Sensationz App or visit https://shop.sensationzperformingarts.com/")
        lines.append(f"2. Log in with your registered Google profile.")
        lines.append(f"3. Select *{c['name']}* ({pl['duration']}).")
        lines.append(f"4. Apply coupon code *{p['coupon_code']}* at checkout.")
        lines.append(f"5. Complete checkout to start classes!")
    else:
        lines.append("1. Download the Sensationz App (Android & iOS).")
        lines.append("2. Complete your profile registration to unlock welcome discounts.")
        lines.append("3. Select your preferred timing and package duration.")

    lines.append("========================================")

    return "\n".join(lines)


def generate_order_summary(state: Optional[Dict[str, Any]] = None, **kwargs) -> str:
    """Convenience alias for format_order_summary."""
    return format_order_summary(state, **kwargs)

