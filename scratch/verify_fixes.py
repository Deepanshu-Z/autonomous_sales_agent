import sys
sys.stdout.reconfigure(encoding='utf-8')
import asyncio
from src.services.hono_staging_client import clean_email
from src.core.conversation_handler import _sanitize_confirmation_guardrails
from src.core.intent_classifier import extract_intent_and_state_changes

async def test_suite():
    print("--- Test 1: clean_email for bare username during email fulfillment ---")
    email1 = clean_email("xyz", is_awaiting_email=True)
    assert email1 == "deepanshupokhriyal07@gmail.com", f"Expected deepanshupokhriyal07@gmail.com, got {email1}"
    print(f"Pass: 'deepanshupokhriyal07' -> {email1}")

    email2 = clean_email("this is my gmail: deepanshupokhriyal07", is_awaiting_email=True)
    assert email2 == "deepanshupokhriyal07@gmail.com", f"Expected deepanshupokhriyal07@gmail.com, got {email2}"
    print(f"Pass: 'this is my gmail: deepanshupokhriyal07' -> {email2}")

    email3 = clean_email("yhi h mra complete gmail", is_awaiting_email=True)
    assert email3 is None, f"Expected None, got {email3}"
    print(f"Pass: 'yhi h mra complete gmail' -> {email3}")

    print("\n--- Test 2: intent_classifier on 'deepanshupokhriyal07' for paid student ---")
    mock_paid_state = {
        "course": "kathak",
        "timing": "4:00 PM to 5:00 PM",
        "teacher": "Ms. Madhuri",
        "days": "Tuesday & Thursday",
        "package": "1 Month",
        "payment_status": "PAID",
        "is_enrolled": True,
        "awaiting_email_fulfillment": True,
        "student_email": None,
    }
    flags = await extract_intent_and_state_changes(
        text="deepanshupokhriyal07",
        state=mock_paid_state,
        history=[],
    )
    print(f"Flags extracted: customer_intent={flags.get('customer_intent')}, unoffered_type={flags.get('unoffered_type')}, unoffered_value={flags.get('unoffered_value')}")
    assert flags.get("unoffered_type") != "TIMING", "ERROR: deepanshupokhriyal07 was classified as unoffered timing!"
    assert flags.get("unoffered_value") != "deepanshupokhriyal07", "ERROR: unoffered_value contains username!"
    print("Pass: deepanshupokhriyal07 is NOT classified as a batch timing!")

    print("\n--- Test 3: Guardrail intercept on fake Gmail linking hallucination ---")
    fake_hallucinated_reply = "Shukriya! Aapka Gmail ID link kar diya gaya hai. Ab aap Sensationz App mein login karke apne Kathak Classical Dance classes access kar sakte hain."
    sanitized_reply, sanitized_followup = _sanitize_confirmation_guardrails(
        reply=fake_hallucinated_reply,
        followup=None,
        state=mock_paid_state,  # staged_enrollment is False, student_email is None
        language="Hinglish"
    )
    print(f"Sanitized reply:\n{sanitized_reply}\nFollowup:\n{sanitized_followup}")
    assert "link kar diya gaya hai" not in sanitized_reply, "ERROR: fake link message was not intercepted!"
    assert "@gmail.com" in sanitized_reply, "ERROR: sanitized reply did not explain @gmail.com requirement!"
    print("Pass: Fake Gmail linking was intercepted deterministically!")

    print("\nALL VERIFICATION TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(test_suite())
