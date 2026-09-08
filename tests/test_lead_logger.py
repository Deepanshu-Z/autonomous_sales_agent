"""
test_lead_logger.py — Verification of structured lead console logging.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.lead_logger import extract_lead_details, log_lead_action
from src.workers.tasks import check_target_ad

def run_tests():
    print("Testing extract_lead_details and check_target_ad...\n")

    # Set TARGET_AD_ID for testing
    os.environ["TARGET_AD_ID"] = "120247632705360010"
    import src.workers.tasks as tasks_mod
    tasks_mod.TARGET_AD_ID = "120247632705360010"

    # Test 1: Target Ad Lead
    target_referral = {
        "source_id": "120247632705360010",
        "source_type": "ad",
        "source_url": "https://fb.com/l.php?ad_id=120247632705360010",
        "headline": "Yoga Starter Package 2024",
    }
    details_1 = extract_lead_details(target_referral, phone="919876543210")
    is_target_1, reason_1 = check_target_ad("Hello, I want yoga", target_referral, phone="919876543210")
    print("--- TEST 1: Target Ad Lead ---")
    log_lead_action(
        lead_no="919876543210",
        lead_details=details_1,
        action_taken="REPLIED" if is_target_1 else "IGNORED",
        reason=reason_1,
    )
    assert is_target_1 is True, "Expected target ad to pass"
    assert "120247632705360010" in details_1, "Expected Ad ID in details"
    assert "Yoga Starter Package 2024" in details_1, "Expected Ad Tag / Headline in details"

    # Test 2: Non-Target Ad Lead (Wrong Ad)
    wrong_referral = {
        "source_id": "9999999999",
        "source_type": "ad",
        "source_url": "https://fb.com/l.php?ad_id=9999999999",
        "headline": "Kathak Dance Classes",
    }
    details_2 = extract_lead_details(wrong_referral, phone="919876543211")
    is_target_2, reason_2 = check_target_ad("Hello", wrong_referral, phone="919876543211")
    print("--- TEST 2: Wrong Ad Lead ---")
    log_lead_action(
        lead_no="919876543211",
        lead_details=details_2,
        action_taken="IGNORED",
        reason=reason_2,
    )
    assert is_target_2 is False, "Expected wrong ad to fail"
    assert "MISMATCH" in reason_2, "Expected MISMATCH in reason"

    # Test 3: Organic Lead (No referral data)
    details_3 = extract_lead_details(None, phone="919876543212")
    is_target_3, reason_3 = check_target_ad("Hi", None, phone="919876543212")
    print("--- TEST 3: Organic Lead ---")
    log_lead_action(
        lead_no="919876543212",
        lead_details=details_3,
        action_taken="IGNORED",
        reason=reason_3,
    )
    assert is_target_3 is False, "Expected organic lead to fail"
    assert "No referral data" in reason_3, "Expected No referral data in reason"

    # Test 4: Whitelisted Test Number
    os.environ["ALLOWED_TEST_NUMBERS"] = "919999999999"
    details_4 = extract_lead_details(None, phone="919999999999")
    is_target_4, reason_4 = check_target_ad("Test bot", None, phone="919999999999")
    print("--- TEST 4: Whitelisted Test Number ---")
    log_lead_action(
        lead_no="919999999999",
        lead_details=details_4,
        action_taken="REPLIED",
        reason=reason_4,
    )
    assert is_target_4 is True, "Expected whitelisted number to pass"
    assert "Whitelisted" in reason_4, "Expected Whitelisted in reason"

    print("\n ALL TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    run_tests()
