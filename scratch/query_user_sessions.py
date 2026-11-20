import os
import sys
import json
sys.path.insert(0, os.path.abspath("."))
from dotenv import load_dotenv
load_dotenv()
from src.services.supabase_client import supabase

print("=== Querying user_session_state ===")
try:
    res = supabase.table("user_session_state").select("*").order("updated_at", desc=True).limit(50).execute()
    data = res.data or []
    print(f"Total records retrieved: {len(data)}")
    
    qr_leads = []
    stuck_leads = []
    
    for r in data:
        phone = r.get("phone")
        state_json = r.get("state")
        if isinstance(state_json, str):
            try:
                st = json.loads(state_json)
            except Exception:
                st = {}
        elif isinstance(state_json, dict):
            st = state_json
        else:
            st = r
            
        stage = st.get("stage")
        course = st.get("course")
        qr_sent = st.get("is_qr_sent") or st.get("qr_code_sent")
        req_qr = st.get("requests_qr")
        timing = st.get("timing") or st.get("selected_timing")
        teacher = st.get("teacher") or st.get("selected_teacher")
        package = st.get("package") or st.get("selected_duration")
        order_id = st.get("order_id") or st.get("qr_id")
        updated_at = r.get("updated_at")
        
        # Check if lead interacted around package or payment
        if req_qr or qr_sent or package or stage in ["PACKAGE_SELECTED", "PAYMENT_QR_SENT", "ENROLL_CONFIRMED"]:
            qr_leads.append({
                "phone": phone,
                "stage": stage,
                "course": course,
                "timing": timing,
                "teacher": teacher,
                "package": package,
                "qr_sent": qr_sent,
                "req_qr": req_qr,
                "qr_id": order_id,
                "updated_at": updated_at
            })
            
    print(f"\n--- Leads with package or QR interactions ({len(qr_leads)}) ---")
    for q in qr_leads:
        print(f"Phone: {q['phone']:15} | Stage: {str(q['stage']):18} | Pkg: {str(q['package']):10} | Timing: {str(q['timing']):12} | QR Sent: {str(q['qr_sent']):5} | Req QR: {str(q['req_qr']):5} | QR_ID: {str(q['qr_id']):15} | Upd: {q['updated_at']}")

except Exception as e:
    import traceback
    traceback.print_exc()
