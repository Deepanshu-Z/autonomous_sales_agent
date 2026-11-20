import sys
import os
import json
sys.path.insert(0, os.path.abspath("."))
from dotenv import load_dotenv
load_dotenv()
from src.services.supabase_client import supabase
from src.core.helpers.slot_engine import evaluate_enrollment_slots
from src.core.pricing_engine import calculate_exact_fee
from src.state.chat_history import get_recent_history

print("=== Scanning ALL user_session_state records in batches ===")

all_records = []
batch_size = 500
offset = 0

while True:
    try:
        res = (
            supabase.table("user_session_state")
            .select("*")
            .order("updated_at", desc=True)
            .range(offset, offset + batch_size - 1)
            .execute()
        )
        data = res.data or []
        all_records.extend(data)
        print(f"Fetched offset {offset}..{offset + len(data) - 1} (batch: {len(data)}, total so far: {len(all_records)})")
        if len(data) < batch_size:
            break
        offset += batch_size
    except Exception as e:
        print(f"Error fetching at offset {offset}: {e}")
        break

print(f"\nTotal records fetched: {len(all_records)}")

matched_leads = []

for r in all_records:
    phone = r.get("phone")
    if not phone:
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

    # Merge top-level db fields into st if missing
    for k in ["course", "stage", "timing", "selected_timing", "teacher", "selected_teacher", "package", "selected_duration", "days", "selected_days", "payment_status", "is_qr_sent", "qr_code_sent", "qr_id", "order_id"]:
        if st.get(k) is None and r.get(k) is not None:
            st[k] = r.get(k)

    cid = (st.get("course") or "").lower().strip()
    if not cid:
        continue

    # Evaluate completeness
    slot_eval = evaluate_enrollment_slots(st, course_id=cid)
    is_complete = slot_eval.get("is_complete", False)
    missing = slot_eval.get("missing_slots", [])

    qr_sent = bool(st.get("qr_code_sent") or st.get("is_qr_sent") or r.get("is_qr_sent"))
    qr_id = st.get("qr_id") or st.get("last_qr_id") or r.get("qr_id")
    payment_status = st.get("payment_status") or "UNPAID"

    # We want leads who have ALL details selected (is_complete == True)
    # BUT QR was NOT dispatched (qr_sent == False or qr_id is None)
    if is_complete and (not qr_sent or not qr_id):
        # Calculate fee
        pkg = st.get("package") or st.get("selected_duration")
        fee_info = {}
        try:
            fee_info = calculate_exact_fee(cid, pkg) or {}
        except Exception:
            pass

        matched_leads.append({
            "phone": phone,
            "course": cid,
            "course_title": slot_eval.get("course_title"),
            "stage": st.get("stage") or r.get("stage"),
            "timing": slot_eval.get("timing_str"),
            "teacher": slot_eval.get("teacher_str"),
            "days": slot_eval.get("days_str"),
            "package": slot_eval.get("package_str"),
            "payment_status": payment_status,
            "qr_sent_flag": qr_sent,
            "qr_id": qr_id,
            "payable_amount": fee_info.get("final_payable_amount"),
            "gst_pct": fee_info.get("gst_percentage"),
            "updated_at": r.get("updated_at"),
            "raw_state": st
        })

print(f"\n=======================================================")
print(f"FOUND {len(matched_leads)} LEADS WITH ALL DETAILS SELECTED BUT NO QR DISPATCHED")
print(f"=======================================================")

for idx, m in enumerate(matched_leads, 1):
    print(f"\n--- Lead #{idx}: {m['phone']} ---")
    print(f"Course:         {m['course']} ({m['course_title']})")
    print(f"Stage:          {m['stage']}")
    print(f"Batch Timing:   {m['timing']}")
    print(f"Instructor:     {m['teacher']}")
    print(f"Batch Days:     {m['days']}")
    print(f"Package:        {m['package']}")
    print(f"Payable Amount: ₹{m['payable_amount']} (GST: {m['gst_pct']}%)")
    print(f"QR Sent Flag:   {m['qr_sent_flag']}")
    print(f"QR ID:          {m['qr_id']}")
    print(f"Payment Status: {m['payment_status']}")
    print(f"Updated At:     {m['updated_at']}")

# Save detailed json report
with open("scratch/all_complete_no_qr_leads.json", "w", encoding="utf-8") as f:
    # exclude raw_state for clean dump
    clean_dump = [{k: v for k, v in lead.items() if k != "raw_state"} for lead in matched_leads]
    json.dump(clean_dump, f, indent=2, default=str)

print(f"\nSaved full results to scratch/all_complete_no_qr_leads.json")
