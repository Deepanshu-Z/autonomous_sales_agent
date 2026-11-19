import sys
import os
sys.path.insert(0, os.path.abspath("."))
import asyncio
from dotenv import load_dotenv

load_dotenv()

from src.core import intent_classifier

async def test_extraction():
    state = {"course": "kathak", "stage": "NEW"}
    text = "can u send me class video"
    flags = await intent_classifier.extract_intent_and_state_changes(text=text, state=state, history=[])
    print("--- Flags for 'can u send me class video' ---")
    print("wants_demo:", flags.get("wants_demo"))
    print("wants_demo_video:", flags.get("wants_demo_video"))
    print("wants_resend_video:", flags.get("wants_resend_video"))
    print("wants_trial_video_guide:", flags.get("wants_trial_video_guide"))
    print("customer_intent:", flags.get("customer_intent"))
    print("question_summary:", flags.get("question_summary"))
    
    text2 = "send videos again"
    flags2 = await intent_classifier.extract_intent_and_state_changes(text=text2, state=state, history=[
        {"role": "user", "content": "can u send me class video"},
        {"role": "assistant", "content": "I've sent our live class practice video above! 🎬"}
    ])
    print("\n--- Flags for 'send videos again' ---")
    print("wants_demo:", flags2.get("wants_demo"))
    print("wants_demo_video:", flags2.get("wants_demo_video"))
    print("wants_resend_video:", flags2.get("wants_resend_video"))
    print("wants_trial_video_guide:", flags2.get("wants_trial_video_guide"))
    print("customer_intent:", flags2.get("customer_intent"))
    print("question_summary:", flags2.get("question_summary"))

if __name__ == "__main__":
    asyncio.run(test_extraction())
