"""
test_followup_unified.py — Self-check verifying both Legacy and 24-Hour follow-up modes,
including anti-loop deduplication and QR stage guards.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services import followup_service


def test_legacy_mode():
    print("--- Testing Legacy Mode (USE_LEGACY_FOLLOWUP = True) ---")
    followup_service.USE_LEGACY_FOLLOWUP = True

    # Step 0: 5-min check-in
    d0 = followup_service.get_followup_decision(
        phone="919999999999",
        count=0,
        course_id="yoga",
        course_name="Online Live Interactive Yoga",
        lead_at_qr=False,
        sent_steps=[],
    )
    assert d0.action == "SEND_MESSAGE", f"Expected SEND_MESSAGE, got {d0.action}"
    assert "Just checking in" in d0.message
    assert "Yoga" in d0.message
    assert d0.next_count == 1
    assert d0.next_gap_seconds == 1800
    print("  [PASS] Step 0 sends check-in and sets 1800s (30m) gap")

    # Step 1: 30-min escalation for regular lead
    d1 = followup_service.get_followup_decision(
        phone="919999999999",
        count=1,
        course_id="yoga",
        course_name="Online Live Interactive Yoga",
        lead_at_qr=False,
        sent_steps=[0],
    )
    assert d1.action == "ESCALATE_TO_AGENT", f"Expected ESCALATE_TO_AGENT, got {d1.action}"
    assert "Connecting you with our team now" in d1.message
    assert d1.agent_email is not None
    print("  [PASS] Step 1 escalates to human agent in Interakt")

    # Step 1: QR lead should SKIP escalation
    d1_qr = followup_service.get_followup_decision(
        phone="919999999999",
        count=1,
        course_id="yoga",
        course_name="Online Live Interactive Yoga",
        lead_at_qr=True,
        sent_steps=[0],
    )
    assert d1_qr.action == "SKIP", f"Expected SKIP for QR lead, got {d1_qr.action}"
    print("  [PASS] QR lead skips escalation and stays with AI")


def test_24h_mode():
    print("\n--- Testing 24-Hour AI Mode (USE_LEGACY_FOLLOWUP = False) ---")
    followup_service.USE_LEGACY_FOLLOWUP = False

    # Step 0: 5-min check-in
    d0 = followup_service.get_followup_decision(
        phone="919999999999",
        count=0,
        course_id="kathak",
        course_name="Online Live Interactive Kathak",
        lead_at_qr=False,
        sent_steps=[],
    )
    assert d0.action == "SEND_MESSAGE"
    assert "Just checking in" in d0.message
    assert d0.next_count == 1
    assert d0.next_gap_seconds == 1500  # 25m -> 30m total
    print("  [PASS] Step 0 sends check-in and sets 1500s (25m) gap")

    # Steps 1 to 5: Factual sequence
    expected_gaps = {1: 9000, 2: 21600, 3: 21600, 4: 21600, 5: 10800}
    sent = [0]
    for s in range(1, 6):
        d = followup_service.get_followup_decision(
            phone="919999999999",
            count=s,
            course_id="kathak",
            course_name="Online Live Interactive Kathak",
            lead_at_qr=False,
            sent_steps=sent,
        )
        assert d.action == "SEND_MESSAGE", f"Step {s} failed action: {d.action}"
        assert d.next_count == s + 1
        assert d.next_gap_seconds == expected_gaps[s], f"Step {s} gap mismatch: {d.next_gap_seconds}"
        if s == 5:
            assert d.is_trial_offer is True, "Step 5 must be 3 Free Trials offer"
            assert "3 Free Live Interactive Trial Classes" in d.message
        print(f"  [PASS] Step {s} ({d.step_label}) gap={d.next_gap_seconds}s trial={d.is_trial_offer}")
        sent.append(s)

    # Step 6: 24h agent escalation
    d6 = followup_service.get_followup_decision(
        phone="919999999999",
        count=6,
        course_id="kathak",
        course_name="Online Live Interactive Kathak",
        lead_at_qr=False,
        sent_steps=sent,
    )
    assert d6.action == "ESCALATE_TO_AGENT"
    assert "Connecting you with our team now" in d6.message
    print("  [PASS] Step 6 escalates to human agent after 24h")


def test_anti_loop_deduplication():
    print("\n--- Testing Anti-Loop Deduplication (User replies and pauses again) ---")
    followup_service.USE_LEGACY_FOLLOWUP = False

    # Scenario: User already received Step 0 (check-in) and Step 1 (Fact 1).
    # User then asked a question, bot replied, and user paused again!
    # Count triggers at 1 again because timer reset.
    d_loop = followup_service.get_followup_decision(
        phone="919999999999",
        count=1,
        course_id="kathak",
        course_name="Online Live Interactive Kathak",
        lead_at_qr=False,
        sent_steps=[0, 1],  # Steps 0 and 1 were already sent
    )
    # It must NOT resend Fact 1! It must automatically send Fact 2!
    assert d_loop.action == "SEND_MESSAGE"
    assert "step 2" in d_loop.step_label.lower() or "Fact #2" in d_loop.step_label
    assert 2 in d_loop.updated_sent_steps
    print(f"  [PASS] Anti-loop prevented duplicate! Advanced to Step 2: {d_loop.step_label}")

    # Scenario 2: If user already got all facts 1 through 5 and paused:
    d_all_seen = followup_service.get_followup_decision(
        phone="919999999999",
        count=1,
        course_id="kathak",
        course_name="Online Live Interactive Kathak",
        lead_at_qr=False,
        sent_steps=[0, 1, 2, 3, 4, 5],
    )
    # Must advance straight to agent escalation
    assert d_all_seen.action == "ESCALATE_TO_AGENT"
    print("  [PASS] Anti-loop with all facts exhausted cleanly escalates to agent")


if __name__ == "__main__":
    test_legacy_mode()
    test_24h_mode()
    test_anti_loop_deduplication()
    # Reset default back to True (Legacy)
    followup_service.USE_LEGACY_FOLLOWUP = True
    print("\n>>> ALL TESTS PASSED CLEANLY! Default is USE_LEGACY_FOLLOWUP = True. <<<")

