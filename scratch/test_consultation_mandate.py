import asyncio
import sys
import os
import re

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, r"d:\Users\Admin\Desktop\prod_chat_base\new_sales_agent")

from src.core.conversation_handler import handle_incoming_message


async def test_consultation():
    phone = "917678368328"

    test_queries = [
        ("Timings Inquiry", "what are the batch timings?", r"(morning|evening|afternoon|suit|routine|time)"),
        ("Fees Inquiry", "fees kitni hai?", r"(goal|beginner|focus|start|fresh|plan|suit)"),
        ("Teacher Inquiry", "teachers kaise hain aur kaun padhata hai?", r"(practiced|before|teacher|fresh|experience|start)"),
        ("Demo / Trial Inquiry", "kya demo class mil sakti hai?", r"(timing|batch|slot|morning|evening|trial)"),
    ]

    for label, query, expected_pattern in test_queries:
        print(f"\n==========================================")
        print(f"TESTING: {label} -> '{query}'")
        print(f"==========================================")
        res = await handle_incoming_message(
            user_message=query,
            session_state={"stage": "NEW", "course": "yoga", "language": "Hinglish" if "hai" in query else "English"},
            conversation_history=[],
            course_id="yoga",
            phone=phone,
        )

        reply = res.get("reply") or ""
        followup = res.get("followup") or ""
        buttons = res.get("buttons") or []

        print(f"Message 1 (Reply):\n{reply}")
        print(f"\nMessage 2 (Followup):\n{followup}")
        print(f"\nButtons: {buttons}")

        # Validation: followup must exist and end with an engaging consultative question
        assert followup, f"{label}: Followup must NOT be empty!"
        assert any(c in followup for c in ["?", "？"]), f"{label}: Followup MUST end with a question mark! Got: {followup}"
        assert re.search(expected_pattern, followup, re.IGNORECASE), f"{label}: Followup must consult on expected topic ({expected_pattern})! Got: {followup}"
        print(f"✅ PASS: {label} returned an active consultative question!")

    print("\n🎉 ALL CONSULTATION LOGICS VERIFIED AND PASSED!")


if __name__ == "__main__":
    asyncio.run(test_consultation())
