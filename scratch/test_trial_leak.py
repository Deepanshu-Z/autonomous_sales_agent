import sys
import os
sys.path.insert(0, os.path.abspath("."))
import asyncio
from dotenv import load_dotenv

load_dotenv()

from src.core.conversation_handler import handle_incoming_message
from src.state.chat_state import get_user_state

async def test_trial():
    phone = "917678368328"
    state = get_user_state(phone)
    text = "plz send video"
    
    from src.state.chat_history import get_recent_history
    history = get_recent_history(phone, 6)
    
    result = await handle_incoming_message(
        user_message=text,
        session_state=state,
        conversation_history=history,
        course_id=state.get("course") or "kathak",
    )
    
    new_state = result.get("new_state", {})
    last_turn = new_state.get("_last_turn", {})
    
    print("\n--- TEST RESULT ---")
    print("reply:", result.get("reply"))
    print("followup:", result.get("followup"))
    print("new_state stage:", new_state.get("stage"))
    print("new_state wants_trial:", new_state.get("wants_trial"))
    print("last_turn wants_trial:", last_turn.get("wants_trial"))
    
    is_trial_unlocked = bool(
        new_state.get("wants_trial")
        or new_state.get("stage") in ["TRIAL_REQUESTED", "TRIAL_STEPS_SENT"]
        or last_turn.get("wants_trial")
    )
    print("is_trial_unlocked:", is_trial_unlocked)

if __name__ == "__main__":
    asyncio.run(test_trial())
