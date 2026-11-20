import sys, os, json
sys.path.insert(0, os.path.abspath('.'))
from dotenv import load_dotenv
load_dotenv()
from src.services.supabase_client import supabase

print("=== Scanning recent messages in chat_history for QR / Payment requests ===")
res = supabase.table("chat_history").select("*").order("created_at", desc=True).limit(500).execute()
records = res.data or []
print(f"Total messages inspected: {len(records)}")

payment_msgs = []
seen_phones = set()

import re
pattern = re.compile(r"\b(qr|scanner|payment|pay|link|gpay|phonepe|upi|bhejo|kaise pay|kitna|fees)\b", re.IGNORECASE)

for r in records:
    role = r.get("role")
    msg = r.get("message") or r.get("content") or ""
    phone = r.get("phone")
    created_at = r.get("created_at")
    
    if role == "user" and pattern.search(msg):
        payment_msgs.append({
            "phone": phone,
            "message": msg,
            "created_at": created_at
        })

print(f"\nFound {len(payment_msgs)} user messages mentioning payment/QR/fees.")
# Group by phone
from collections import defaultdict
by_phone = defaultdict(list)
for p in payment_msgs:
    by_phone[p["phone"]].append(p)

from src.state.chat_state import get_user_state

print(f"\nUnique leads asking about payment/QR/fees: {len(by_phone)}")
for ph, msgs in list(by_phone.items())[:20]:
    st = get_user_state(ph)
    qr_sent = st.get("qr_code_sent") or st.get("is_qr_sent")
    print(f"\nLead: {ph} | Course: {st.get('course')} | Stage: {st.get('stage')} | QR Sent: {qr_sent} | Timing: {st.get('timing')} | Pkg: {st.get('package')} | Teacher: {st.get('teacher')}")
    for m in msgs[:3]:
        print(f"  User msg ({m['created_at']}): {m['message']}")
