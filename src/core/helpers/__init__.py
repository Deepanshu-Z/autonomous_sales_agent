"""
src.core.helpers package — Dedicated helper engines for course schedules and pricing.
"""

from src.core.helpers.schedule_engine import (
    classify_time_period,
    get_available_timings,
    get_valid_timings,
    get_teachers_for_slot,
    get_slots_for_teacher,
    get_course_time_periods,
    format_batch_timings_display,
    format_teacher_batch_schedule,
    get_course_instructors,
    resolve_canonical_teacher,
    get_available_days_for_batch,
    get_day_code,
    normalize_day_string,
    format_days_for_button,
)

from src.core.helpers.slot_engine import evaluate_enrollment_slots

from src.core.pricing_engine import (
    get_course_gst_percentage,
    calculate_gst_breakdown,
    calculate_discount,
    get_valid_packages,
    get_package_by_duration,
    calculate_exact_fee,
    calculate_bill,
    format_fee_breakdown,
    get_course_coupons,
    format_package_fees_display,
    get_pricing_and_timings_summary,
    find_matching_batches,
    is_teacher_in_course,
    is_valid_timing,
    validate_teacher_slot,
    validate_slot_teacher,
    is_teacher_available_for_slot,
    is_slot_teacher_compatible,
    format_order_summary,
    get_order_summary_details,
    generate_order_summary,
)

__all__ = [
    # Schedule & Timings
    "evaluate_enrollment_slots",
    "classify_time_period",
    "get_available_timings",
    "get_valid_timings",
    "get_teachers_for_slot",
    "get_slots_for_teacher",
    "get_course_time_periods",
    "format_batch_timings_display",
    "format_teacher_batch_schedule",
    "get_course_instructors",
    "resolve_canonical_teacher",
    "get_available_days_for_batch",
    "get_day_code",
    "normalize_day_string",
    "format_days_for_button",
    # Pricing & Packages
    "get_course_gst_percentage",
    "calculate_gst_breakdown",
    "calculate_discount",
    "get_valid_packages",
    "get_package_by_duration",
    "calculate_exact_fee",
    "calculate_bill",
    "format_fee_breakdown",
    "get_course_coupons",
    "format_package_fees_display",
    "get_pricing_and_timings_summary",
    # Slot-Teacher Validation
    "find_matching_batches",
    "is_teacher_in_course",
    "is_valid_timing",
    "validate_teacher_slot",
    "validate_slot_teacher",
    "is_teacher_available_for_slot",
    "is_slot_teacher_compatible",
    # Order Summary Formatter
    "format_order_summary",
    "get_order_summary_details",
    "generate_order_summary",
]

