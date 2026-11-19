import sys
import os
sys.path.insert(0, os.path.abspath("."))
import asyncio
from dotenv import load_dotenv

load_dotenv()

from src.core import intent_classifier
from src.core import kb_loader

async def test_why():
    phone = "917678368328"
    text = "could u plz send videos"
    
    # Let's inspect history
    from src.state.chat_history import get_recent_history
    history = get_recent_history(phone, 6)
    from src.state.chat_state import get_user_state
    state = get_user_state(phone)
    
    print("State course:", state.get("course"))
    print("State stage:", state.get("stage"))
    print("class_demo_video_sent:", state.get("class_demo_video_sent"))
    
    flags = await intent_classifier.extract_intent_and_state_changes(text, state=state, history=history)
    print("\n--- Extractor Flags ---")
    print("wants_demo:", flags.get("wants_demo"))
    print("wants_demo_video:", flags.get("wants_demo_video"))
    print("wants_resend_video:", flags.get("wants_resend_video"))
    print("customer_intent:", flags.get("customer_intent"))
    print("question_summary:", flags.get("question_summary"))

    # Now let's check the conditions in conversation_handler.py:
    last_turn = flags
    text_clean = text.strip()
    explicit_demo_request = bool(
        last_turn.get("wants_demo")
        or last_turn.get("wants_demo_video")
    )
    active_cid = state.get("course") or ""
    course_data_active = kb_loader.get_course_data(active_cid)
    course_overview_active = course_data_active.get("course_overview") or {}
    class_demo_video_url = course_overview_active.get("class_raw_video_url")
    active_course_title = course_overview_active.get("name") or str(active_cid or "").replace("_", " ").title() or "Live class"
    
    is_resend_kw = any(w in text_clean.lower() for w in [
        "again", "resend", "dobara", "phir se", "fir se", "nahi aayi", "nahi aaya",
        "not received", "didn't get", "send video", "send videos", "class video", "practice video"
    ])
    demo_force_resend = bool(last_turn.get("wants_resend_video") or is_resend_kw)
    
    send_native_demo_video = bool(
        explicit_demo_request
        and class_demo_video_url
        and (demo_force_resend or not state.get("class_demo_video_sent"))
    )

    print("\n--- Dispatch Condition Evaluation ---")
    print("explicit_demo_request:", explicit_demo_request)
    print("active_cid:", active_cid)
    print("class_demo_video_url:", class_demo_video_url)
    print("is_resend_kw:", is_resend_kw)
    print("demo_force_resend:", demo_force_resend)
    print("state class_demo_video_sent:", state.get("class_demo_video_sent"))
    print("send_native_demo_video:", send_native_demo_video)

if __name__ == "__main__":
    asyncio.run(test_why())
