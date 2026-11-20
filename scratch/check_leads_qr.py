import os
import sys
import json
sys.path.insert(0, os.path.abspath("."))
from dotenv import load_dotenv
load_dotenv()
from src.services.supabase_client import supabase

print("=== Supabase Connection Check ===")
if not supabase:
    print("Supabase client is not available!")
    sys.exit(1)

print("Connected to Supabase.")

# Check chat_state
try:
    res = supabase.table("chat_state").select("*").order("updated_at", desc=True).limit(50).execute()
    print(f"\nRecent 50 chat_state records:")
    qr_candidates = []
    for r in res.data:
        phone = r.get("phone")
        stage = r.get("stage")
        course = r.get("course")
        qr_sent = r.get("is_qr_sent")
        req_qr = r.get("requests_qr")
        pkg = r.get("package") or r.get("selected_duration")
        timing = r.get("timing") or r.get("selected_timing")
        upd = r.get("updated_at")
        
        # Check if lead is in a stage where QR should be sent or requested QR
        if req_qr or qr_sent or pkg or stage in ["PACKAGE_SELECTED", "PAYMENT_QR_SENT", "ENROLL_CONFIRMED", "DAYS_SELECTED", "TIMING_SELECTED"]:
            qr_candidates.append(r)
        
        print(f"Phone: {phone:15} | Stage: {str(stage):18} | Course: {str(course):12} | QR Sent: {str(qr_sent):5} | Req QR: {str(req_qr):5} | Pkg: {str(pkg):10} | Timing: {str(timing):12} | Upd: {upd}")
    
    print(f"\nTotal QR Candidates / In-funnel leads in last 50: {len(qr_candidates)}")
except Exception as e:
    print("Error querying chat_state:", e)

# Also check if there's a messages table or chat_history in Supabase
try:
    res_msg = supabase.table("messages").select("*").order("created_at", desc=True).limit(10).execute()
    print(f"\nMessages table exists! Found {len(res_msg.data)} records.")
except Exception as e:
    print("Messages table query:", e)

try:
    res_leads = supabase.table("leads").select("*").order("created_at", desc=True).limit(10).execute()
    print(f"\nLeads table exists! Found {len(res_leads.data)} records.")
except Exception as e:
    print("Leads table query:", e)
