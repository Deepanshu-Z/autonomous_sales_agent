"""
test_dynamic_courses.py — Comprehensive Unit & Integration Tests for 100% Dynamic Course Scaling.
Validates:
1. Dynamic timing extraction for multiple courses (Yoga, Kathak) with AM/PM resolution.
2. Dynamic package & price detection without hardcoded price constants.
3. Dynamic welcome ad trigger response for different courses.
4. Dynamic sales follow-up questions for different courses (Yoga, Kathak, Guitar).
5. Dynamic app link formatting from global rules.
"""

import sys
import os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio

from src.core import kb_loader
from src.state.chat_state import detect_timing_from_text, extract_and_update_slots
from src.core.coupons import detect_package_from_text
from src.core.sales_followup import get_sales_followup
from src.workers.tasks import is_welcome_trigger


def test_app_links_formatting():
    links = kb_loader.format_app_links_display()
    assert "play.google.com" in links
    assert "apple.com" in links
    assert "shop.sensationzperformingarts.com" in links
    print("✅ test_app_links_formatting passed!")


def test_dynamic_timings_multiple_courses():
    # Yoga has 6am-7am, 7am-8am, etc.
    t_yoga, is_amb, _ = detect_timing_from_text("6 am", course_id="yoga")
    assert t_yoga == "6:00–7:00 AM"

    # Kathak only has PM timings: 4:00–5:00 PM, 5:00–6:00 PM, 6:00–7:00 PM
    t_kathak_am, _, _ = detect_timing_from_text("6 am", course_id="kathak")
    assert t_kathak_am is None, "Kathak has no 6 AM slot!"

    t_kathak_pm, _, _ = detect_timing_from_text("6 pm", course_id="kathak")
    assert t_kathak_pm == "6:00–7:00 PM"

    # In Kathak, "6 baje" should unambiguously resolve to PM since there is no AM slot
    t_kathak_baje, is_amb, _ = detect_timing_from_text("6 baje", course_id="kathak")
    assert t_kathak_baje == "6:00–7:00 PM"
    assert not is_amb
    print("✅ test_dynamic_timings_multiple_courses passed!")


def test_dynamic_package_detection():
    # Test Yoga pricing (1M is 700, 3M offer is 600)
    pkg_yoga = detect_package_from_text("700", course_id="yoga")
    assert pkg_yoga == "1 Month"

    pkg_yoga_dur = detect_package_from_text("3 mahine", course_id="yoga")
    assert pkg_yoga_dur == "3 Months"

    # Test Kathak pricing (1 Month offer is 700, 3 Months offer is 1500)
    pkg_kathak_700 = detect_package_from_text("700", course_id="kathak")
    assert pkg_kathak_700 == "1 Month"

    pkg_kathak_1500 = detect_package_from_text("1500", course_id="kathak")
    assert pkg_kathak_1500 == "3 Months"

    # Test Ladies Dance pricing (1 Month is 500, 3 Months offer is 600)
    pkg_dance_500 = detect_package_from_text("500", course_id="ladies_dance")
    assert pkg_dance_500 == "1 Month"

    pkg_dance_600 = detect_package_from_text("600", course_id="ladies_dance")
    assert pkg_dance_600 == "3 Months"

    print("✅ test_dynamic_package_detection passed!")


def test_dynamic_sales_followup():
    # Kathak package asked follow up should display Kathak fees (₹700, ₹1,500 etc)
    state_kathak = {
        "stage": "PACKAGE_ASKED",
        "course": "kathak",
        "timing": "6:00–7:00 PM",
    }
    followup_kathak = get_sales_followup("soch raha hu", "Sure!", state_kathak)
    assert "700" in followup_kathak
    assert "1,500" in followup_kathak

    # Yoga follow up should display Yoga fees (₹700, ₹600)
    state_yoga = {
        "stage": "PACKAGE_ASKED",
        "course": "yoga",
        "timing": "6:00–7:00 AM",
    }
    followup_yoga = get_sales_followup("soch raha hu", "Sure!", state_yoga)
    assert "700" in followup_yoga

    # NEW stage follow up should interpolate course name
    state_new_kathak = {"stage": "NEW", "course": "kathak"}
    new_followup = get_sales_followup("soch raha hu", "Hello", state_new_kathak)
    assert "Kathak" in new_followup

    print("✅ test_dynamic_sales_followup passed!")


def test_welcome_trigger_dynamic():
    assert is_welcome_trigger("Hello!! Can I get more info on Yoga classes?")
    assert is_welcome_trigger("Can I get more info on Kathak classes?")
    assert is_welcome_trigger("can i get more info on guitar course")
    print("✅ test_welcome_trigger_dynamic passed!")


async def test_slot_extraction_dynamic():
    from src.state.chat_state import reset_user_state_async
    phone = "+919999900001"
    await reset_user_state_async(phone)
    # User mentions Kathak course and timing
    res = await extract_and_update_slots(phone, "I want to join kathak classes at 5pm")
    assert res.get("course") == "kathak"
    assert "5:00" in res.get("timing") and "PM" in res.get("timing")
    assert res.get("stage") in ["TIMING_SELECTED", "PACKAGE_ASKED"]

    # User mentions 3 months package
    res2 = await extract_and_update_slots(phone, "3 months")
    assert res2.get("package") == "3 Months"
    # Kathak 3 months fee is ₹2,100 (Offer Price: ₹1,500)
    assert "1,500" in (res2.get("fee") or "")

    print("✅ test_slot_extraction_dynamic passed!")


if __name__ == "__main__":
    test_app_links_formatting()
    test_dynamic_timings_multiple_courses()
    test_dynamic_package_detection()
    test_dynamic_sales_followup()
    test_welcome_trigger_dynamic()
    asyncio.run(test_slot_extraction_dynamic())
    print("\n🎉 ALL MULTI-COURSE DYNAMIC TESTS PASSED!")
