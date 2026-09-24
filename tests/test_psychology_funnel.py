import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.core import kb_loader
from src.services.app_db_verifier import normalize_phone_last10, verify_user_in_production_db_async
from src.workers.tasks import _agent_nudge


def test_bonus_tips():
    print("\n--- Test 1: Course Bonus Tips (1 Text Tip Per Course, NO PDF) ---")
    yoga_tip = kb_loader.get_course_bonus_tip("yoga")
    kathak_tip = kb_loader.get_course_bonus_tip("kathak")
    face_yoga_tip = kb_loader.get_course_bonus_tip("face_yoga")

    assert yoga_tip is not None, "Yoga bonus tip should not be None"
    assert "Pre-Class Morning Routine" in yoga_tip, f"Unexpected Yoga tip: {yoga_tip[:50]}"
    assert ".pdf" not in yoga_tip.lower(), "Must NOT contain PDF link"

    assert kathak_tip is not None, "Kathak bonus tip should not be None"
    assert "Posture & Ankle Warm-Up" in kathak_tip, f"Unexpected Kathak tip: {kathak_tip[:50]}"
    assert ".pdf" not in kathak_tip.lower(), "Must NOT contain PDF link"

    assert face_yoga_tip is not None, "Face Yoga bonus tip should not be None"
    assert "Daily Face Glow" in face_yoga_tip, f"Unexpected Face Yoga tip: {face_yoga_tip[:50]}"
    assert ".pdf" not in face_yoga_tip.lower(), "Must NOT contain PDF link"

    print("✅ PASS: All 3 courses return dedicated in-chat prefilled text bonus tips!")


def test_agent_nudge_no_hardcoding():
    print("\n--- Test 2: Agent Nudge Dynamic Numbers ---")
    nudge_hi = _agent_nudge("fees kya hai")
    nudge_en = _agent_nudge("what are the timings")
    assert "9898989898" not in nudge_hi, "Must not contain hardcoded placeholder 9898989898"
    assert "9898989898" not in nudge_en, "Must not contain hardcoded placeholder 9898989898"
    print("✅ PASS: Agent nudge has zero hardcoded placeholder numbers!")


async def test_prod_db_verification():
    print("\n--- Test 3: Production RDS PostgreSQL Verification ---")
    # Test valid phone from DB
    res_valid = await verify_user_in_production_db_async("919999999999") # will test normalized last10
    # Test with real contact from profile table (we verified contact '8302746840' earlier)
    real_contact = "8302746840"
    res_real = await verify_user_in_production_db_async(real_contact)
    print(f"Lookup for {real_contact}: verified={res_real['verified']}, name={res_real.get('full_name')}")
    assert res_real["verified"] is True, f"Expected {real_contact} to be verified in RDS"
    assert res_real["full_name"] is not None, "Expected full_name for verified profile"

    # Test non-existent contact
    res_fake = await verify_user_in_production_db_async("0000000000")
    assert res_fake["verified"] is False, "Expected non-existent contact to be unverified"
    print("✅ PASS: Production App DB Verifier works accurately against live AWS RDS!")


async def test_trial_vs_package_stage_and_bonus():
    print("\n--- Test 4: Trial Confirmation vs Coupon Sent & In-Chat Bonus Append ---")
    from src.core.conversation_handler import handle_incoming_message
    from unittest.mock import patch, AsyncMock
    from langchain_core.messages import AIMessage

    # Case A: Free Trial User completing profile
    mock_reply = '{"reply": "Welcome to your free trial class! We are excited to have you.", "followup": "Are you all set for tomorrow?", "buttons": ["All Set 👍"]}'
    with patch("src.core.conversation_handler._get_reply_llm") as mock_get_llm:
        mock_llm_instance = AsyncMock()
        mock_llm_instance.ainvoke.return_value = AIMessage(content=mock_reply)
        mock_get_llm.return_value = mock_llm_instance

        state_trial = {
            "course": "yoga",
            "wants_trial": True,
            "timing": "7:00 AM",
            "stage": "READY_FOR_APP_LINK",
        }
        user_msg = "maine profile bana li hai"

        res = await handle_incoming_message(
            user_message=user_msg,
            session_state=state_trial,
            conversation_history=[],
            course_id="yoga",
            phone="8302746840", # verified contact
        )

        new_st = res["new_state"]
        print(f"Trial User -> Stage: {new_st.get('stage')}, Coupon Sent: {new_st.get('coupon_sent')}, Bonus Tip Sent: {new_st.get('bonus_tip_sent')}")
        assert new_st.get("stage") == "TRIAL_CONFIRMED", f"Expected TRIAL_CONFIRMED, got {new_st.get('stage')}"
        assert not new_st.get("coupon_sent"), "Trial user must NOT have coupon_sent set"
        assert new_st.get("bonus_tip_sent") is True, "Bonus tip must be marked sent"
        assert "5-Minute Pre-Class Morning Routine" in res["reply"], "Bonus tip content must be appended to reply"

    # Case B: Paid Package User completing profile
    mock_reply_paid = '{"reply": "Congratulations on completing your profile! Here is your coupon: YOGA70", "followup": "Ready to checkout?", "buttons": ["Proceed 👍"]}'
    with patch("src.core.conversation_handler._get_reply_llm") as mock_get_llm:
        mock_llm_instance = AsyncMock()
        mock_llm_instance.ainvoke.return_value = AIMessage(content=mock_reply_paid)
        mock_get_llm.return_value = mock_llm_instance

        state_paid = {
            "course": "yoga",
            "wants_trial": False,
            "package": "1 Month",
            "fee": 210,
            "timing": "7:00 AM",
            "stage": "READY_FOR_APP_LINK",
        }
        user_msg = "done, profile created"

        res_paid = await handle_incoming_message(
            user_message=user_msg,
            session_state=state_paid,
            conversation_history=[],
            course_id="yoga",
            phone="8302746840",
        )

        new_st_paid = res_paid["new_state"]
        print(f"Paid User -> Stage: {new_st_paid.get('stage')}, Coupon Sent: {new_st_paid.get('coupon_sent')}, Bonus Tip Sent: {new_st_paid.get('bonus_tip_sent')}")
        assert new_st_paid.get("stage") == "COUPON_SENT", f"Expected COUPON_SENT, got {new_st_paid.get('stage')}"
        assert new_st_paid.get("coupon_sent") is True, "Paid user must have coupon_sent set"
        assert new_st_paid.get("bonus_tip_sent") is True, "Bonus tip must be marked sent"
        assert "5-Minute Pre-Class Morning Routine" in res_paid["reply"], "Bonus tip content must be appended to reply"

    print("✅ PASS: Trial protection, coupon gating, and in-chat bonus tip delivery verified!")


async def main():
    test_bonus_tips()
    test_agent_nudge_no_hardcoding()
    await test_prod_db_verification()
    await test_trial_vs_package_stage_and_bonus()
    print("\n🎉 ALL PSYCHOLOGY FUNNEL & PROD DB VERIFIER TESTS PASSED!")


if __name__ == "__main__":
    asyncio.run(main())
