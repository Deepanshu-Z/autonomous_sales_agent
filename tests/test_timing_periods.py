import sys
import os
import asyncio
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

from src.core import kb_loader
from src.core import prompt
from src.core import conversation_handler


def test_timing_periods_derivation():
    print("\n[TEST 1] Testing get_course_time_periods derivation across courses...")

    # Kathak: Evening only
    kathak_p = kb_loader.get_course_time_periods("kathak")
    assert kathak_p["has_timings"] is True
    assert kathak_p["available_periods"] == ["Evening"]
    assert "Morning" in kathak_p["missing_periods"]
    assert "Afternoon" in kathak_p["missing_periods"]
    assert "NO Morning" in kathak_p["summary"]
    print("  -> Kathak: Correctly classified as Evening only, zero Morning/Afternoon batches.")

    # Yoga: Morning, Afternoon, Evening
    yoga_p = kb_loader.get_course_time_periods("yoga")
    assert yoga_p["has_timings"] is True
    assert "Morning" in yoga_p["available_periods"]
    assert "Evening" in yoga_p["available_periods"]
    print("  -> Yoga: Correctly classified with Morning and Evening batches.")

    # Face Yoga: Morning, Afternoon, Evening
    face_p = kb_loader.get_course_time_periods("face_yoga")
    assert face_p["has_timings"] is True
    assert "Morning" in face_p["available_periods"]
    assert "Evening" in face_p["available_periods"]
    print("  -> Face Yoga: Correctly classified with Morning and Evening batches.")

    # Dance (Non-digitized)
    dance_p = kb_loader.get_course_time_periods("dance")
    assert dance_p["has_timings"] is False
    assert len(dance_p["available_periods"]) == 0
    print("  -> Dance (non-digitized): Correctly handled as no stored batch timings.")


def test_prompt_injection():
    print("\n[TEST 2] Testing prompt injection of timing periods...")
    sys_prompt = prompt.format_system_prompt({"stage": "NEW"}, course_id="kathak")
    assert "Course Available Timing Periods" in sys_prompt
    assert "NO Morning or Afternoon batches" in sys_prompt
    assert "STRICT TIME-OF-DAY INTEGRITY & ZERO BATCH HALLUCINATION" in sys_prompt
    print("  -> Kathak system prompt correctly warns against morning batches and enforces time-of-day integrity.")


async def test_kathak_morning_batch_llm_response():
    print("\n[TEST 3] Testing LLM response for 'Morning Batch' on Kathak...")
    session_state = {
        "stage": "TRIAL_STEPS_SENT",
        "course": "kathak",
        "wants_trial": True,
        "trial_video_sent": True,
        "timing": None,
        "package": None,
    }
    history = [
        {"role": "user", "content": "can i get trial for kathak?"},
        {
            "role": "assistant",
            "content": "Yes, free trial classes are available for Kathak! Download the Sensationz App... Which batch timing would you like to choose for your free trial?",
        },
    ]

    res = await conversation_handler.handle_incoming_message(
        user_message="Morning Batch",
        session_state=session_state,
        conversation_history=history,
        course_id="kathak",
    )
    reply = res.get("reply", "")
    followup = res.get("followup", "")
    buttons = res.get("buttons", [])

    print(f"\n--- AI REPLY ---\n{reply}")
    print(f"\n--- FOLLOWUP ---\n{followup}")
    print(f"\n--- BUTTONS ---\n{buttons}\n")

    # The AI must NOT claim that 4:00 PM or 5:00 PM are "morning batch timings"
    assert "available morning batch timings for kathak are" not in reply.lower(), "AI must not claim morning batch timings exist for Kathak!"
    # The AI must clarify that morning batches are not available / batches are evening
    assert any(w in reply.lower() for w in ["evening", "shaam", "not offer", "not available", "no morning"]), "AI must clarify Kathak batches are evening or morning is not available!"
    # Buttons must NOT offer "Morning Batch"
    assert not any("morning" in b.lower() for b in buttons), f"Buttons must not include 'Morning Batch'! Got: {buttons}"

    print("  -> Kathak 'Morning Batch' query correctly handled without hallucination and with valid buttons!")


async def main():
    test_timing_periods_derivation()
    test_prompt_injection()
    await test_kathak_morning_batch_llm_response()
    print("\n✅ ALL TIMING PERIOD TESTS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    asyncio.run(main())
