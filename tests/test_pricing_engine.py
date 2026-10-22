"""
test_pricing_engine.py — Deterministic Unit Tests for src/core/pricing_engine.py

Verifies:
1. Exact fee and bill calculations (base price, discount, GST breakdown, final payable amount)
2. Razorpay paise and rupee conversions
3. Slot-teacher compatibility validation across multiple courses
4. Edge cases: invalid courses, invalid slots, invalid teachers, flexible timing formats
"""

import sys
import os

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.core.pricing_engine import (
    calculate_exact_fee,
    calculate_bill,
    calculate_discount,
    calculate_gst_breakdown,
    get_course_gst_percentage,
    format_fee_breakdown,
    validate_teacher_slot,
    validate_slot_teacher,
    is_teacher_available_for_slot,
    is_slot_teacher_compatible,
    is_teacher_in_course,
    is_valid_timing,
    get_teachers_for_slot,
)


def test_discount_calculation():
    print("Testing calculate_discount...")
    # Base: 1750, Offer: 600 -> Discount 1150 (65.7%)
    d1 = calculate_discount(1750, 600)
    assert d1["discount"] == 1150
    assert d1["discount_amount"] == 1150
    assert d1["discount_percentage"] == 65.7
    assert d1["has_discount"] is True

    # Base: 700, Offer: 700 -> Discount 0
    d2 = calculate_discount(700, 700)
    assert d2["discount"] == 0
    assert d2["discount_percentage"] == 0.0
    assert d2["has_discount"] is False

    print("PASS: calculate_discount")


def test_gst_breakdown_calculation():
    print("Testing calculate_gst_breakdown...")
    # 18% on 700 offer price -> 126 GST (63 CGST, 63 SGST)
    gst18 = calculate_gst_breakdown(700, 18)
    assert gst18["gst_amount"] == 126
    assert gst18["cgst_amount"] == 63
    assert gst18["sgst_amount"] == 63
    assert gst18["total_tax"] == 126

    # 5% on 600 offer price -> 30 GST (15 CGST, 15 SGST)
    gst5 = calculate_gst_breakdown(600, 5)
    assert gst5["gst_amount"] == 30
    assert gst5["cgst_amount"] == 15
    assert gst5["sgst_amount"] == 15
    assert gst5["total_tax"] == 30

    print("PASS: calculate_gst_breakdown")


def test_exact_fee_yoga():
    print("Testing calculate_exact_fee for Yoga...")
    # 1 Month Yoga: Base 700, No discount, GST 5% = 35, Total 735
    y1m = calculate_exact_fee("yoga", "1 Month")
    assert y1m is not None
    assert y1m["base_price"] == 700
    assert y1m["discount"] == 0
    assert y1m["offer_price"] == 700
    assert y1m["gst_percentage"] == 5
    assert y1m["gst_amount"] == 35
    assert y1m["final_payable_amount"] == 735
    assert y1m["total_payable"] == 735
    assert y1m["amount_rupees"] == 735
    assert y1m["amount_paise"] == 73500

    # 3 Months Yoga: Base 1750, Offer 600, Discount 1150, GST 5% = 30, Total 630
    y3m = calculate_exact_fee("yoga", "3 Months")
    assert y3m is not None
    assert y3m["base_price"] == 1750
    assert y3m["discount"] == 1150
    assert y3m["offer_price"] == 600
    assert y3m["coupon_code"] == "YOGA600"
    assert y3m["gst_percentage"] == 5
    assert y3m["gst_amount"] == 30
    assert y3m["final_payable_amount"] == 630
    assert y3m["amount_paise"] == 63000

    # Flexible format testing: '3m', '3M', '1 year', '12 months'
    assert calculate_exact_fee("yoga", "3m")["final_payable_amount"] == 630
    assert calculate_exact_fee("yoga", "3M")["final_payable_amount"] == 630
    assert calculate_exact_fee("yoga", "1 year")["final_payable_amount"] == 1260
    assert calculate_exact_fee("yoga", "12 months")["final_payable_amount"] == 1260

    print("PASS: Yoga fee calculations")


def test_exact_fee_kathak():
    print("Testing calculate_exact_fee for Kathak...")
    # 1 Month Kathak: Base 1000, Offer 700, Discount 300, GST 18% = 126, Total 826
    k1m = calculate_exact_fee("kathak", "1 Month")
    assert k1m is not None
    assert k1m["base_price"] == 1000
    assert k1m["discount"] == 300
    assert k1m["offer_price"] == 700
    assert k1m["coupon_code"] == "KATHAK700"
    assert k1m["gst_percentage"] == 18
    assert k1m["gst_amount"] == 126
    assert k1m["final_payable_amount"] == 826
    assert k1m["amount_paise"] == 82600

    # 3 Months Kathak: Base 2100, Offer 1500, Discount 600, GST 18% = 270, Total 1770
    k3m = calculate_exact_fee("kathak", "3 Months")
    assert k3m is not None
    assert k3m["base_price"] == 2100
    assert k3m["discount"] == 600
    assert k3m["offer_price"] == 1500
    assert k3m["coupon_code"] == "KATHAK1500"
    assert k3m["gst_percentage"] == 18
    assert k3m["gst_amount"] == 270
    assert k3m["final_payable_amount"] == 1770
    assert k3m["amount_paise"] == 177000

    print("PASS: Kathak fee calculations")


def test_exact_fee_other_courses():
    print("Testing calculate_exact_fee for Face Yoga & Ladies Dance...")
    # Face Yoga 1M: Base 500, Offer 300, Discount 200, GST 5% = 15, Total 315
    fy1m = calculate_exact_fee("face_yoga", "1 Month")
    assert fy1m is not None
    assert fy1m["base_price"] == 500
    assert fy1m["discount"] == 200
    assert fy1m["offer_price"] == 300
    assert fy1m["gst_amount"] == 15
    assert fy1m["final_payable_amount"] == 315
    assert fy1m["amount_paise"] == 31500

    # Ladies Dance 3M: Base 1200, Offer 600, Discount 600, GST 18% = 108, Total 708
    ld3m = calculate_exact_fee("ladies_dance", "3 Months")
    assert ld3m is not None
    assert ld3m["base_price"] == 1200
    assert ld3m["discount"] == 600
    assert ld3m["offer_price"] == 600
    assert ld3m["gst_amount"] == 108
    assert ld3m["final_payable_amount"] == 708
    assert ld3m["amount_paise"] == 70800

    print("PASS: Face Yoga & Ladies Dance fee calculations")


def test_fee_receipt_formatting():
    print("Testing format_fee_breakdown...")
    receipt = format_fee_breakdown("yoga", "3 Months")
    assert receipt is not None
    assert "Base Price: ₹1,750" in receipt
    assert "Special Discount: -₹1,150" in receipt
    assert "Offer Price: ₹600" in receipt
    assert "GST (5%): +₹30" in receipt
    assert "Final Payable Amount: ₹630" in receipt
    print("PASS: format_fee_breakdown")


def test_slot_teacher_validation_kathak():
    print("Testing slot-teacher validation for Kathak...")

    # Case 1: Ms. Madhuri teaches Tue/Thu 4-5 PM
    v1 = validate_teacher_slot("kathak", "Ms. Madhuri", "Tuesday/Thursday 4:00 PM to 5:00 PM")
    assert v1["is_valid"] is True
    assert v1["reason"] == "COMPATIBLE"
    assert is_teacher_available_for_slot("kathak", "Ms. Madhuri", "Tuesday/Thursday 4:00 PM to 5:00 PM") is True

    # Case 2: Colloquial name 'madhuri mam' resolves and matches
    v2 = validate_teacher_slot("kathak", "madhuri mam", "Tuesday/Thursday 4:00 PM")
    assert v2["is_valid"] is True
    assert v2["teacher"] == "Ms. Madhuri"

    # Case 3: Kratika Gontiya does NOT teach Tue/Thu 5 PM (Batch is Madhuri, Vibha, Sneha)
    v3 = validate_teacher_slot("kathak", "Kratika Gontiya", "Tuesday/Thursday 5:00 PM to 6:00 PM")
    assert v3["is_valid"] is False
    assert v3["reason"] == "TEACHER_SLOT_MISMATCH"
    assert "Ms. Madhuri" in v3["teachers_for_slot"]

    # Case 4: Kratika Gontiya DOES teach Wed/Fri 5 PM
    v4 = validate_teacher_slot("kathak", "Kratika Gontiya", "Wednesday/Friday 5:00 PM to 6:00 PM")
    assert v4["is_valid"] is True
    assert v4["reason"] == "COMPATIBLE"

    # Case 5: Sneha Maurya teaches Tue/Thu 8-9 PM
    v5 = validate_teacher_slot("kathak", "Sneha Maurya", "Tuesday/Thursday 8:00 PM")
    assert v5["is_valid"] is True
    assert v5["reason"] == "COMPATIBLE"

    print("PASS: Kathak slot-teacher validation")


def test_slot_teacher_validation_yoga():
    print("Testing slot-teacher validation for Yoga...")

    # Case 1: Jagriti Mishra teaches 5:00 AM
    v1 = validate_teacher_slot("yoga", "Jagriti Mishra", "5:00 AM")
    assert v1["is_valid"] is True
    assert v1["reason"] == "COMPATIBLE"

    # Case 2: Jagriti Mishra does NOT teach 7:00 AM (Mradula teaches 7 AM)
    v2 = validate_teacher_slot("yoga", "Jagriti Mishra", "7:00 AM")
    assert v2["is_valid"] is False
    assert v2["reason"] == "TEACHER_SLOT_MISMATCH"
    assert "Mradula" in v2["teachers_for_slot"]

    # Case 3: Priya Mathur teaches 6:00 AM
    v3 = validate_teacher_slot("yoga", "Priya Mathur", "6:00 AM to 7:00 AM")
    assert v3["is_valid"] is True
    assert v3["reason"] == "COMPATIBLE"

    # Case 4: Inverted argument order convenience alias
    v4 = validate_slot_teacher("yoga", "6:00 AM to 7:00 AM", "Priya Mathur")
    assert v4["is_valid"] is True

    print("PASS: Yoga slot-teacher validation")


def test_validation_edge_cases():
    print("Testing validation edge cases (missing inputs, non-existent slots, foreign teachers)...")

    # Teacher does not belong to course
    v_foreign = validate_teacher_slot("yoga", "Ms. Madhuri", "5:00 AM")
    assert v_foreign["is_valid"] is False
    assert v_foreign["reason"] == "TEACHER_NOT_IN_COURSE"

    # Non-existent slot in course
    v_no_slot = validate_teacher_slot("yoga", "Jagriti Mishra", "2:00 AM")
    assert v_no_slot["is_valid"] is False
    assert v_no_slot["reason"] == "SLOT_NOT_FOUND"

    # Empty inputs
    v_empty = validate_teacher_slot("", "", "")
    assert v_empty["is_valid"] is False
    assert v_empty["reason"] == "INVALID_INPUT"

    # Unknown duration in fee calculation
    f_none = calculate_exact_fee("yoga", "10 Years")
    assert f_none is None

    # is_teacher_in_course
    assert is_teacher_in_course("kathak", "Ms. Madhuri") is True
    assert is_teacher_in_course("yoga", "Ms. Madhuri") is False
    assert is_teacher_in_course("yoga", "Jagriti Mishra") is True

    # is_valid_timing
    assert is_valid_timing("yoga", "5:00 AM") is True
    assert is_valid_timing("yoga", "2:00 AM") is False

    print("PASS: Validation edge cases")


def test_order_summary_formatter_full_state():
    print("Testing format_order_summary with full session state...")
    from src.core.pricing_engine import format_order_summary, get_order_summary_details

    state = {
        "course": "yoga",
        "timing": "5:00 AM to 6:00 AM",
        "selected_teacher": "Jagriti Mishra",
        "package": "3 Months",
        "phone": "+919876543210",
        "customer_name": "Aarav Sharma",
        "app_installed": True,
        "profile_created": True,
        "stage": "PROFILE_COMPLETED",
    }

    summary = format_order_summary(state)

    # Academic & Plan details
    assert "Online Live Interactive Yoga" in summary
    assert "5:00 AM to 6:00 AM" in summary
    assert "*Jagriti Mishra* (Verified & Assigned ✅)" in summary
    assert "*3 Months* (3M)" in summary

    # Deterministic Billing & Tax details
    assert "Base Catalog Price: ₹1,750" in summary
    assert "Special Discount: -₹1,150 (Coupon: *YOGA600*)" in summary
    assert "Taxable Offer Price: ₹600" in summary
    assert "GST Breakdown (5%):" in summary
    assert "CGST (2.5%): ₹15" in summary
    assert "SGST (2.5%): ₹15" in summary
    assert "TOTAL PAYABLE AMOUNT: ₹630" in summary
    assert "63,000 Paise" in summary

    # Account Onboarding details
    assert "Sensationz App: Confirmed ✅" in summary
    assert "Profile Registration: Confirmed ✅" in summary
    assert "Funnel Stage: PROFILE_COMPLETED" in summary
    assert "Contact: +919876543210" in summary

    print("PASS: format_order_summary (full state)")


def test_order_summary_formatter_kathak():
    print("Testing format_order_summary for Kathak with 18% GST and slot verification...")
    from src.core.pricing_engine import format_order_summary

    state = {
        "course": "kathak",
        "timing": "Tuesday/Thursday 4:00 PM to 5:00 PM",
        "selected_teacher": "madhuri mam",
        "package": "1 Month",
        "app_installed": False,
        "profile_created": False,
        "stage": "TIMING_SELECTED",
    }

    summary = format_order_summary(state)

    assert "Kathak" in summary
    assert "*Ms. Madhuri* (Verified & Assigned ✅)" in summary
    assert "*1 Month* (1M)" in summary
    assert "Base Catalog Price: ₹1,000" in summary
    assert "Special Discount: -₹300 (Coupon: *KATHAK700*)" in summary
    assert "Taxable Offer Price: ₹700" in summary
    assert "GST Breakdown (18%):" in summary
    assert "CGST (9.0%): ₹63" in summary
    assert "SGST (9.0%): ₹63" in summary
    assert "TOTAL PAYABLE AMOUNT: ₹826" in summary
    assert "Sensationz App: Pending ⏳" in summary
    assert "Profile Registration: Pending ⏳" in summary

    print("PASS: format_order_summary (Kathak)")


def test_order_summary_partial_state_and_details():
    print("Testing partial state fallbacks and get_order_summary_details...")
    from src.core.pricing_engine import format_order_summary, get_order_summary_details

    # Partial state: only course selected
    state = {"course": "face_yoga"}
    summary = format_order_summary(state)
    assert "Face Yoga" in summary
    assert "Selected Timing: _Not Selected Yet_" in summary
    assert "Duration: _Not Selected Yet_" in summary
    assert "Package duration not selected yet" in summary

    # Structured details dict check
    details = get_order_summary_details(course_id="ladies_dance", duration="3 Months", timing="7:00 AM to 8:00 AM")
    assert details["course"]["id"] == "ladies_dance"
    assert details["plan"]["duration"] == "3 Months"
    assert details["pricing"]["final_payable_amount"] == 708
    assert details["pricing"]["amount_paise"] == 70800
    assert details["pricing"]["gst_amount"] == 108

    print("PASS: partial state and get_order_summary_details")


def run_all_tests():
    print("=" * 60)
    print("RUNNING PRICING ENGINE & ORDER SUMMARY SUITE")
    print("=" * 60)

    test_discount_calculation()
    test_gst_breakdown_calculation()
    test_exact_fee_yoga()
    test_exact_fee_kathak()
    test_exact_fee_other_courses()
    test_fee_receipt_formatting()
    test_slot_teacher_validation_kathak()
    test_slot_teacher_validation_yoga()
    test_validation_edge_cases()
    test_order_summary_formatter_full_state()
    test_order_summary_formatter_kathak()
    test_order_summary_partial_state_and_details()

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED SUCCESSFULLY! ZERO LLM MATH GROUNDED.")
    print("=" * 60)


if __name__ == "__main__":
    run_all_tests()

