import os
import sys
import json
sys.path.insert(0, os.path.abspath("."))
from dotenv import load_dotenv
load_dotenv()
from src.services.supabase_client import supabase
from src.core.helpers.slot_engine import evaluate_enrollment_slots

print("=== Fetching all sessions from Supabase ===")
res = supabase.table("user_session_state").select("*").order("updated_at", desc=True).limit(100).execute()
records = res.data or []
print(f"Total session records fetched: {len(records)}")

stuck_leads = []
success_qr_leads = []

for r in records:
    phone = r.get("phone")
    if not phone or len(phone) < 10:
        continue
        
    state_field = r.get("state")
    if isinstance(state_field, str):
        try:
            st = json.loads(state_field)
        except Exception:
            st = {}
    elif isinstance(state_field, dict):
        st = state_field
    else:
        st = r

    cid = (st.get("course") or r.get("course") or "").lower().strip()
    stage = st.get("stage") or r.get("stage")
    pkg = st.get("package") or st.get("selected_duration")
    timing = st.get("timing") or st.get("selected_timing")
    teacher = st.get("teacher") or st.get("selected_teacher")
    days = st.get("days") or st.get("selected_days")
    qr_sent = bool(st.get("qr_code_sent") or st.get("is_qr_sent") or r.get("is_qr_sent"))
    req_qr = bool(st.get("requests_qr") or r.get("requests_qr"))
    
    # Evaluate completeness using slot_engine
    slot_eval = evaluate_enrollment_slots(st, course_id=cid)
    is_complete = slot_eval.get("is_complete")
    missing = slot_eval.get("missing_slots", [])

    lead_info = {
        "phone": phone,
        "course": cid,
        "stage": stage,
        "timing": timing,
        "teacher": teacher,
        "package": pkg,
        "days": days,
        "qr_sent": qr_sent,
        "req_qr": req_qr,
        "is_complete": is_complete,
        "missing_slots": missing,
        "updated_at": r.get("updated_at")
    }

    if qr_sent:
        success_qr_leads.append(lead_info)
    elif pkg or timing or stage in ["PACKAGE_SELECTED", "TIMING_SELECTED", "DAYS_SELECTED", "ENROLL_CONFIRMED"]:
        stuck_leads.append(lead_info)

print(f"\n================ SUMMARY ================")
print(f"Leads with QR generated: {len(success_qr_leads)}")
print(f"Leads in-funnel where QR is NOT generated: {len(stuck_leads)}")

print(f"\n=== LEADS THAT GOT QR ({len(success_qr_leads)}) ===")
for l in success_qr_leads[:10]:
    print(f"Phone: {l['phone']:15} | Course: {l['course']:12} | Pkg: {str(l['package']):10} | Timing: {str(l['timing']):18} | Teacher: {str(l['teacher']):12}")

print(f"\n=== LEADS WHERE QR WAS NOT GENERATED ({len(stuck_leads)}) ===")
for l in stuck_leads:
    print(f"Phone: {l['phone']:15} | Course: {l['course']:12} | Stage: {str(l['stage']):18} | Pkg: {str(l['package']):10} | Timing: {str(l['timing']):18} | Teacher: {str(l['teacher']):12} | Missing: {l['missing_slots']}")
