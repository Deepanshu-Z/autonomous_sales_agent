import sys, os, json
sys.path.insert(0, os.path.abspath('.'))
from dotenv import load_dotenv
load_dotenv()
from src.state.chat_state import get_user_state
from src.state.chat_history import get_recent_history
from src.core.helpers.slot_engine import evaluate_enrollment_slots
from src.core import intent_classifier
from src.core.pricing_engine import calculate_exact_fee

phone = "919811134113"
st = get_user_state(phone)
hist = get_recent_history(phone, 15)

print("=== User State ===")
print("course:", st.get("course"))
print("timing:", st.get("timing") or st.get("selected_timing"))
print("teacher:", st.get("teacher") or st.get("selected_teacher"))
print("package:", st.get("package") or st.get("selected_duration"))
print("days:", st.get("days") or st.get("selected_days"))
print("stage:", st.get("stage"))
print("qr_code_sent:", st.get("qr_code_sent"))
print("is_qr_sent:", st.get("is_qr_sent"))
print("last_qr_package:", st.get("last_qr_package"))
print("last_qr_id:", st.get("last_qr_id"))

cid = st.get("course") or "ladies_dance"
slot_eval = evaluate_enrollment_slots(st, course_id=cid)
print("\nSlot Eval:")
print("is_complete:", slot_eval.get("is_complete"))
print("missing_slots:", slot_eval.get("missing_slots"))

bill = calculate_exact_fee(cid, st.get("package") or "1 Month")
print("\nBill:", bill)
