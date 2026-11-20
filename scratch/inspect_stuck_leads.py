import os
import sys
import json
sys.path.insert(0, os.path.abspath("."))
from dotenv import load_dotenv
load_dotenv()
from src.services.supabase_client import supabase
from src.state.chat_history import get_recent_history
from src.state.chat_state import get_user_state
from src.core.helpers.slot_engine import evaluate_enrollment_slots

phones_to_check = [
    "919818529417",
    "919497676630",
    "918875193058",
    "919833083466",
    "919680843431",
    "919886803951"
]

for phone in phones_to_check:
    print(f"\n==================== Phone: {phone} ====================")
    st = get_user_state(phone)
    course = st.get("course")
    eval_res = evaluate_enrollment_slots(st, course_id=course)
    print("Course:", course)
    print("Stage:", st.get("stage"))
    print("Timing:", st.get("timing") or st.get("selected_timing"))
    print("Teacher:", st.get("teacher") or st.get("selected_teacher"))
    print("Package:", st.get("package") or st.get("selected_duration"))
    print("Days:", st.get("days") or st.get("selected_days"))
    print("QR Sent:", st.get("qr_code_sent") or st.get("is_qr_sent"))
    print("Slot is_complete:", eval_res.get("is_complete"))
    print("Missing slots:", eval_res.get("missing_slots"))
    print("Settled slots:", eval_res.get("settled_slots"))
    
    hist = get_recent_history(phone, limit=6)
    print(f"\nRecent Chat ({len(hist)}):")
    for m in hist:
        print(f"  [{m.get('role')}]: {str(m.get('content'))[:100]}...")
