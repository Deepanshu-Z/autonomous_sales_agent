import sys
import os
sys.path.insert(0, os.path.abspath("."))
import asyncio
from dotenv import load_dotenv

load_dotenv()

from src.core import intent_classifier
from src.state.chat_history import get_recent_history
from src.state.chat_state import get_user_state

async def test():
    phone = "917678368328"
    state = get_user_state(phone)
    history = get_recent_history(phone, 10)
    
    # Test for "plz send video"
    flags = await intent_classifier.extract_intent_and_state_changes(
        "plz send video",
        state=state,
        history=history[:-3] # state before that turn
    )
    print("--- Extractor flags for 'plz send video' ---")
    print("wants_trial:", flags.get("wants_trial"))
    print("wants_demo:", flags.get("wants_demo"))
    print("wants_demo_video:", flags.get("wants_demo_video"))
    print("customer_intent:", flags.get("customer_intent"))
    print("question_summary:", flags.get("question_summary"))

asyncio.run(test())
