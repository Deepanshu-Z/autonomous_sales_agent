import json
from datetime import datetime, timezone
import sys, os
sys.path.insert(0, os.path.abspath("."))
from dotenv import load_dotenv
load_dotenv()
from src.state.chat_history import get_recent_history
from src.state.chat_state import get_user_state
from src.core.pricing_engine import calculate_exact_fee

with open("scratch/all_complete_no_qr_leads.json", "r", encoding="utf-8") as f:
    all_leads = json.load(f)

# Filter past 3 days: Updated on or after 2026-11-17T00:00:00Z
past_3_days = []
for l in all_leads:
    upd_str = l.get("updated_at")
    if upd_str and upd_str >= "2026-11-17T00:00:00":
        past_3_days.append(l)

print(f"=== LEADS FROM PAST 3 DAYS (Nov 17 - Nov 20) WITH ALL SLOTS SELECTED BUT NO QR ===")
print(f"Total count: {len(past_3_days)}\n")

for idx, l in enumerate(past_3_days, 1):
    phone = l["phone"]
    cid = l["course"]
    timing = l["timing"]
    teacher = l["teacher"]
    pkg = l["package"]
    days = l["days"]
    upd = l["updated_at"]
    amt = l.get("payable_amount")
    
    print(f"==================== Lead #{idx}: {phone} ====================")
    print(f"Course:         {cid} ({l.get('course_title')})")
    print(f"Timing:         {timing}")
    print(f"Teacher:        {teacher}")
    print(f"Days:           {days}")
    print(f"Package:        {pkg}")
    print(f"Payable Fee:    ₹{amt}")
    print(f"Stage:          {l.get('stage')}")
    print(f"Last Active:    {upd}")
    
    # Get last 4 chat messages to see the context
    hist = get_recent_history(phone, limit=5)
    print("Recent Chat Context:")
    for m in hist:
        r = m.get("role")
        c = (m.get("content") or "").replace("\n", " ")
        print(f"  [{r}]: {c[:110]}")
    print()
