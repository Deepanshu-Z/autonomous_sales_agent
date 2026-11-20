import sys, os, json
sys.path.insert(0, os.path.abspath('.'))
from dotenv import load_dotenv
load_dotenv()
from src.state.chat_state import get_user_state
from src.state.chat_history import get_recent_history
from src.core.helpers.slot_engine import evaluate_enrollment_slots

phone = "919810276266"
st = get_user_state(phone)
print("=== STATE for 919810276266 ===")
print("Course:", st.get("course"))
print("Timing:", st.get("timing") or st.get("selected_timing"))
print("Teacher:", st.get("teacher") or st.get("selected_teacher"))
print("Package:", st.get("package") or st.get("selected_duration"))
print("Days:", st.get("days") or st.get("selected_days"))
print("Stage:", st.get("stage"))
print("QR Sent:", st.get("qr_code_sent") or st.get("is_qr_sent"))
print("Requests QR:", st.get("requests_qr"))

eval_res = evaluate_enrollment_slots(st, course_id=st.get("course"))
print("\nSlot Eval:")
print("is_complete:", eval_res.get("is_complete"))
print("missing_slots:", eval_res.get("missing_slots"))
print("settled_slots:", eval_res.get("settled_slots"))

hist = get_recent_history(phone, 15)
print(f"\nChat History ({len(hist)}):")
for m in hist:
    print(f"[{m.get('role')}]: {m.get('content')}")
