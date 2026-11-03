import sys
import os
import asyncio
from unittest.mock import patch, AsyncMock

# Set up paths
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.core import intent_classifier
from src.core.conversation_handler import handle_incoming_message
from src.core import prompt as prompt_module


async def test_classes_not_showing_intent():
    print("\n--- Test 1: Intent Extraction for Classes Not Showing ---")
    test_phrases = [
        "mrko show nhi hori",
        "KRLIA LKN MRKO SHOW NHI HORI CLASSES",
        "app me classes nahi dikh rahi",
        "batch is not showing in app",
        "not able to access classes",
    ]
    for phrase in test_phrases:
        flags = await intent_classifier.extract_intent_and_state_changes(
            text=phrase,
            state={"payment_status": "PAID", "course": "yoga"},
            history=[],
        )
        print(f"Phrase '{phrase}' -> intent: {flags.get('customer_intent')}, cannot_access: {flags.get('cannot_access_classes')}")
        assert flags.get("cannot_access_classes") is True or flags.get("customer_intent") == "CLASSES_NOT_SHOWING", (
            f"Failed for phrase: '{phrase}', got intent: {flags.get('customer_intent')}, cannot_access: {flags.get('cannot_access_classes')}"
        )
    print("✅ PASS: All variations correctly identified as CLASSES_NOT_SHOWING!")


async def test_prompt_generation_missing_email():
    print("\n--- Test 2: Prompt Guidance when Enrolled User lacks Gmail ---")
    state = {
        "payment_status": "PAID",
        "course": "yoga",
        "timing": "5:00 AM to 6:00 AM",
        "selected_timing": "5:00 AM to 6:00 AM",
        "teacher": "Jagriti Mishra",
        "selected_teacher": "Jagriti Mishra",
        "package": "1 Month",
        "student_email": None,
        "email": None,
        "_last_turn": {
            "intent": "CLASSES_NOT_SHOWING",
            "cannot_access_classes": True,
        },
    }
    sys_prompt = prompt_module.format_system_prompt(state, course_id="yoga", detected_language="Hinglish (Latin script)")
    
    assert "MISSING GMAIL LINKAGE" in sys_prompt, "Prompt must flag missing Gmail linkage"
    assert "Google Sign-In (Gmail)" in sys_prompt, "Prompt must mention Google Sign-In"
    assert "NEVER say 'login with your phone number'" in sys_prompt, "Prompt must prohibit misleading phone login instructions"
    print("✅ PASS: System prompt precisely instructs LLM to ask for Gmail and explains Google Sign-In dependency!")


async def test_email_capture_and_staging_flow():
    print("\n--- Test 3: Gmail Capture & Account Linking Response ---")
    state = {
        "payment_status": "PAID",
        "course": "yoga",
        "timing": "5:00 AM to 6:00 AM",
        "selected_timing": "5:00 AM to 6:00 AM",
        "teacher": "Jagriti Mishra",
        "selected_teacher": "Jagriti Mishra",
        "package": "1 Month",
        "phone": "7678368328",
        "student_email": None,
        "awaiting_email_fulfillment": True,
        "classes_not_showing": True,
    }
    
    # Mock stage_ai_enrollment_async to succeed
    with patch("src.services.hono_staging_client.stage_ai_enrollment_async", new_callable=AsyncMock) as mock_stage:
        mock_stage.return_value = {"success": True, "staged_id": "stage-123"}
        
        email_msg = "mera gmail deepanshu.test@gmail.com hai"
        res = await handle_incoming_message(
            user_message=email_msg,
            session_state=state,
            conversation_history=[
                {"role": "user", "content": "KRLIA LKN MRKO SHOW NHI HORI CLASSES"},
                {"role": "assistant", "content": "Aapka enrollment active hai, kripya apna Gmail provide karein."},
            ],
            course_id="yoga",
        )
        
        reply = res["reply"]
        new_state = res["new_state"]
        print(f"Bot reply snippet:\n{reply[:200]}...")
        
        assert "deepanshu.test@gmail.com" in reply, "Reply must acknowledge the linked email"
        assert "Continue with Google" in reply, "Reply must guide student to Continue with Google"
        assert new_state.get("student_email") == "deepanshu.test@gmail.com", "State must store student_email"
        assert new_state.get("awaiting_email_fulfillment") is False, "Awaiting fulfillment must be cleared"
        assert new_state.get("classes_not_showing") is False, "Classes not showing flag must be cleared"
        assert mock_stage.called, "stage_ai_enrollment_async must be called with the email"
        print("✅ PASS: Gmail successfully captured, staged, and user given clear Google sign-in steps!")


async def main():
    await test_classes_not_showing_intent()
    await test_prompt_generation_missing_email()
    await test_email_capture_and_staging_flow()
    print("\n🎉 ALL CLASSES NOT SHOWING TESTS PASSED!")


if __name__ == "__main__":
    asyncio.run(main())
