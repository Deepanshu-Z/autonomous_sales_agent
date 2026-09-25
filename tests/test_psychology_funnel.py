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
        print(f"Trial User -> Stage: {new_st.get('stage')}, Coupon Sent: {new_st.get('coupon_sent')}")
        assert new_st.get("stage") == "TRIAL_CONFIRMED", f"Expected TRIAL_CONFIRMED, got {new_st.get('stage')}"
        assert not new_st.get("coupon_sent"), "Trial user must NOT have coupon_sent set"

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
        print(f"Paid User -> Stage: {new_st_paid.get('stage')}, Coupon Sent: {new_st_paid.get('coupon_sent')}")
        assert new_st_paid.get("stage") == "COUPON_SENT", f"Expected COUPON_SENT, got {new_st_paid.get('stage')}"
        assert new_st_paid.get("coupon_sent") is True, "Paid user must have coupon_sent set"

    print("✅ PASS: Trial protection, coupon gating verified without bonus tip!")


async def test_unverified_profile_blocked():
    print("\n--- Test 5: Unverified Profile Blocked (Zero Coupon & Zero Leak) ---")
    from src.core.conversation_handler import handle_incoming_message

    state = {
        "course": "yoga",
        "stage": "READY_FOR_APP_LINK",
        "package": "1 Month",
        "fee": 210,
    }
    user_msg = "Google Login Done 👍"

    # Call with non-existent phone number 7503736651
    res = await handle_incoming_message(
        user_message=user_msg,
        session_state=state,
        conversation_history=[],
        course_id="yoga",
        phone="7503736651",
    )

    new_st = res["new_state"]
    print(f"Unverified User 7503736651 -> profile_created: {new_st.get('profile_created')}, profile_claim_unverified: {new_st.get('profile_claim_unverified')}, stage: {new_st.get('stage')}")
    assert new_st.get("profile_created") is False, "Unverified user MUST NOT have profile_created=True"
    assert new_st.get("profile_claim_unverified") is True, "Must set profile_claim_unverified=True"
    assert not new_st.get("coupon_sent"), "Must NOT send coupon"
    assert not new_st.get("bonus_tip_sent"), "Must NOT send bonus tip"
    assert "YOGA70" not in res["reply"], "Coupon code MUST NOT be leaked to unverified user"
    assert "Pre-Class Morning Routine" not in res["reply"], "Bonus tip MUST NOT be delivered"
    print("✅ PASS: Unverified phone number 7503736651 blocked from receiving coupon & bonus tip!")


async def test_different_phone_manual_verification_escalation():
    print("\n--- Test 6: Different Phone Number Claim -> Immediate Manual Verification Handoff ---")
    from src.core import intent_classifier
    from src.core.conversation_handler import handle_incoming_message, handle_conversation_turn_async
    from unittest.mock import patch, AsyncMock

    # 1. Test intent classification & extractor extraction
    state_after_unverified = {
        "course": "yoga",
        "stage": "APP_LINK_SENT",
        "profile_claim_unverified": True,
    }
    customer_msg = "maine doosre number 9876543210 se banaya hai"
    flags = await intent_classifier.extract_intent_and_state_changes(
        text=customer_msg,
        state=state_after_unverified,
        history=[{"role": "assistant", "content": "Kya aapne kisi doosre number se register kiya hai?"}],
    )
    print(f"Extractor output: intent={flags.get('customer_intent')}, claims_diff={flags.get('claims_different_phone_or_account')}, diff_num={flags.get('different_phone_number')}")
    assert flags.get("claims_different_phone_or_account") is True, "Must detect claims_different_phone_or_account"
    assert flags.get("different_phone_number") == "9876543210", f"Expected '9876543210', got {flags.get('different_phone_number')}"
    assert flags.get("customer_intent") == "DIFFERENT_NUMBER_CLAIM", f"Expected DIFFERENT_NUMBER_CLAIM, got {flags.get('customer_intent')}"

    # 2. Test handle_incoming_message immediate response
    res = await handle_incoming_message(
        user_message=customer_msg,
        session_state=state_after_unverified,
        conversation_history=[],
        course_id="yoga",
        phone="7503736651",
    )
    print(f"Incoming message response: {res.get('reply')[:120]}...")
    assert "9876543210" in res.get("reply", ""), "Reply must mention the alternate phone number"
    assert "manual verification" in res.get("reply", "").lower() or "connect" in res.get("reply", "").lower(), "Must mention manual verification or team connect"

    # 3. Test handle_conversation_turn_async immediate agent escalation
    with patch("src.core.conversation_handler.execute_agent_handoff_async", new_callable=AsyncMock) as mock_handoff, \
         patch("src.core.conversation_handler.get_user_state_async", new_callable=AsyncMock) as mock_get_state, \
         patch("src.core.conversation_handler.save_user_state_async", new_callable=AsyncMock) as mock_save_state:

        mock_get_state.return_value = state_after_unverified

        turn_res = await handle_conversation_turn_async(
            phone="7503736651",
            text=customer_msg,
            history=[],
        )
        print(f"Turn Result: status={turn_res.get('status')}")
        assert turn_res.get("status") == "handed_off_different_number", f"Expected handed_off_different_number, got {turn_res.get('status')}"
        assert mock_handoff.called, "execute_agent_handoff_async MUST be called immediately"
        call_args = mock_handoff.call_args
        escalation_reason = call_args[1].get("escalation_reason", "")
        print(f"Escalation Reason: {escalation_reason}")
        assert "Manual Verification Needed" in escalation_reason, f"Unexpected escalation reason: {escalation_reason}"
        assert "9876543210" in escalation_reason, "Escalation reason must include alternate phone number"

    print("✅ PASS: Customer claiming different phone number is immediately assigned to agent for manual verification!")


async def main():
    test_bonus_tips()
    test_agent_nudge_no_hardcoding()
    await test_prod_db_verification()
    await test_trial_vs_package_stage_and_bonus()
    await test_unverified_profile_blocked()
    await test_different_phone_manual_verification_escalation()
    print("\n🎉 ALL PSYCHOLOGY FUNNEL & PROD DB VERIFIER TESTS PASSED!")


if __name__ == "__main__":
    asyncio.run(main())

