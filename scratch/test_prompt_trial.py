import sys
import os
sys.path.insert(0, os.path.abspath("."))
import asyncio
from dotenv import load_dotenv

load_dotenv()

from src.state.chat_history import get_recent_history
from src.state.chat_state import get_user_state

def test_lead_turn():
    phone = "917678368328"
    state = get_user_state(phone)
    print("State wants_trial:", state.get("wants_trial"))
    print("State stage:", state.get("stage"))

    from src.core.prompt import format_system_prompt
    sys_prompt = format_system_prompt(
        state=state,
        course_id=state.get("course") or "kathak"
    )
    print("\n--- System Prompt lines mentioning 'trial' ---")
    for i, line in enumerate(sys_prompt.split("\n")):
        if "trial" in line.lower():
            print(f"{i}: {line[:120]}")

test_lead_turn()
