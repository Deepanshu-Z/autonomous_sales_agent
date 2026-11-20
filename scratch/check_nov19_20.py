import sys, os, json
sys.path.insert(0, os.path.abspath("."))
from dotenv import load_dotenv
load_dotenv()
from src.services.supabase_client import supabase
from src.core.helpers.slot_engine import evaluate_enrollment_slots

print("=== Fetching all sessions updated on 2026-11-19 and 2026-11-20 ===")
res = (
    supabase.table("user_session_state")
    .select("*")
    .gte("updated_at", "2026-11-19T00:00:00")
    .order("updated_at", desc=True)
    .execute()
)
records = res.data or []
print(f"Total sessions updated on Nov 19 & Nov 20: {len(records)}")

for r in records:
    phone = r.get("phone")
    st = r.get("state")
    if isinstance(st, str):
        try: st = json.loads(st)
        except: st = {}
    elif not isinstance(st, dict):
        st = r
        
    cid = st.get("course") or r.get("course") or ""
    eval_res = evaluate_enrollment_slots(st, course_id=cid)
    
    qr_sent = st.get("qr_code_sent") or st.get("is_qr_sent") or r.get("is_qr_sent")
    qr_id = st.get("qr_id") or r.get("qr_id")
    
    print(f"\nPhone: {phone:15} | Course: {str(cid):12} | Stage: {str(st.get('stage')):18} | Complete: {eval_res.get('is_complete')} | QR: {qr_sent} | QR_ID: {qr_id} | Upd: {r.get('updated_at')}")
    print(f"  Slots: Timing={st.get('timing') or st.get('selected_timing')}, Teacher={st.get('teacher') or st.get('selected_teacher')}, Pkg={st.get('package') or st.get('selected_duration')}")
    if not eval_res.get('is_complete'):
        print(f"  Missing: {eval_res.get('missing_slots')}")
