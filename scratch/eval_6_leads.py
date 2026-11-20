import sys, os, json
sys.path.insert(0, os.path.abspath("."))
import asyncio
from dotenv import load_dotenv
load_dotenv()

from src.core import kb_loader
from src.core.helpers.slot_engine import evaluate_enrollment_slots
from src.state.chat_history import get_recent_history
from src.state.chat_state import get_user_state

leads = ["917780358487", "918398842476", "919967286863", "918196944851", "917011064774", "919964234119"]

for ph in leads:
    st = get_user_state(ph)
    cid = st.get("course") or "ladies_dance"
    slot_eval = evaluate_enrollment_slots(st, course_id=cid)
    
    print(f"\n==================== LEAD: {ph} ====================")
    print("Course:", cid)
    print("Stage:", st.get("stage"))
    print("Timing in state:", st.get("timing") or st.get("selected_timing"))
    print("Teacher in state:", st.get("teacher") or st.get("selected_teacher"))
    print("Package in state:", st.get("package") or st.get("selected_duration"))
    print("Days in state:", st.get("days") or st.get("selected_days"))
    print("is_complete from slot_eval:", slot_eval.get("is_complete"))
    print("missing_slots from slot_eval:", slot_eval.get("missing_slots"))
    print("qr_code_sent:", st.get("qr_code_sent"))
    print("is_qr_sent:", st.get("is_qr_sent"))
    print("last_qr_package:", st.get("last_qr_package"))
    print("wants_trial:", st.get("wants_trial"))
