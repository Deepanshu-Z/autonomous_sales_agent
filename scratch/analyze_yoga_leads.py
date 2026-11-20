import os
import sys
import datetime
from collections import defaultdict

sys.path.insert(0, os.path.abspath("."))
from dotenv import load_dotenv
load_dotenv()
from src.services.supabase_client import supabase

START_TIME_UTC = "2026-11-19T12:30:00+00:00" # Thursday 6:00 PM IST

print(f"Querying messages since {START_TIME_UTC}...")

# 1. Paginate all messages since START_TIME_UTC
all_recent_messages = []
page_size = 1000
offset = 0

while True:
    res = (
        supabase.table("chat_history")
        .select("id, phone, role, message, created_at")
        .gte("created_at", START_TIME_UTC)
        .order("created_at", desc=False)
        .range(offset, offset + page_size - 1)
        .execute()
    )
    data = res.data or []
    all_recent_messages.extend(data)
    if len(data) < page_size:
        break
    offset += page_size

print(f"Total messages since Thu 6 PM IST: {len(all_recent_messages)}")

# Group messages by phone
phone_messages = defaultdict(list)
for m in all_recent_messages:
    phone_messages[m["phone"]].append(m)

active_phones = list(phone_messages.keys())
print(f"Total unique active phones: {len(active_phones)}")

# 2. Check user_session_state for these phones to see course
# Fetch in chunks of 100
phone_states = {}
for i in range(0, len(active_phones), 100):
    chunk = active_phones[i:i+100]
    res_st = supabase.table("user_session_state").select("*").in_("phone", chunk).execute()
    for s in (res_st.data or []):
        phone_states[s["phone"]] = s

# Filter for Yoga course
# Let's see what course values exist
course_counts = defaultdict(int)
for p in active_phones:
    st = phone_states.get(p)
    c = (st.get("course") if st else None) or "UNKNOWN"
    course_counts[c] += 1

print("\nActive leads by course:")
for c, cnt in sorted(course_counts.items(), key=lambda x: x[1], reverse=True):
    print(f"  {c}: {cnt}")

yoga_phones = [p for p in active_phones if phone_states.get(p) and str(phone_states[p].get("course")).lower() in ("yoga", "face_yoga")]
print(f"\nTotal active Yoga/Face_Yoga leads: {len(yoga_phones)}")

# 3. For each yoga lead, check if they are "new" (first message ever was on/after START_TIME_UTC)
new_yoga_leads = []
returning_yoga_leads = []

for p in yoga_phones:
    # check earliest message ever for this phone
    r_first = (
        supabase.table("chat_history")
        .select("created_at")
        .eq("phone", p)
        .order("created_at", desc=False)
        .limit(1)
        .execute()
    )
    first_created_at = r_first.data[0]["created_at"] if r_first.data else None
    if first_created_at and first_created_at >= START_TIME_UTC:
        new_yoga_leads.append(p)
    else:
        returning_yoga_leads.append(p)

print(f"New Yoga leads (first message >= Thu 6pm IST): {len(new_yoga_leads)}")
print(f"Returning Yoga leads: {len(returning_yoga_leads)}")

# 4. Analyze New Yoga Leads
print("\n=== DETAILED ANALYSIS OF NEW YOGA LEADS ===")
ghosted_leads = []
qr_generated_leads = []
converted_leads = []
engaged_leads = []

for p in new_yoga_leads:
    st = phone_states.get(p, {})
    msgs = phone_messages[p]
    
    user_msgs = [m for m in msgs if m["role"] == "user"]
    ai_msgs = [m for m in msgs if m["role"] == "assistant"]
    
    # Check QR generated
    is_qr = bool(st.get("is_qr_sent") or st.get("qr_id") or any("[IMAGE]" in m.get("message", "") for m in ai_msgs))
    if is_qr:
        qr_generated_leads.append(p)
        
    # Check Converted / Paid
    is_paid = bool(st.get("payment_status") == "PAID" or st.get("is_enrolled") or st.get("stage") == "ENROLLED")
    if is_paid:
        converted_leads.append(p)
        
    # Ghosted definition: User sent initial message/inquiry, AI replied, user never replied again (total user msgs == 1)
    if len(user_msgs) <= 1:
        ghosted_leads.append(p)
    else:
        engaged_leads.append(p)

print(f"Total New Yoga Leads: {len(new_yoga_leads)}")
print(f"Ghosted (only 1 user message, stopped replying): {len(ghosted_leads)}")
print(f"Engaged (>1 user messages): {len(engaged_leads)}")
print(f"QR Generated: {len(qr_generated_leads)}")
print(f"Converted / Paid: {len(converted_leads)}")

print("\n--- Ghosted Lead Details ---")
for p in ghosted_leads:
    st = phone_states.get(p, {})
    u_msgs = [m["message"] for m in phone_messages[p] if m["role"] == "user"]
    print(f"Phone: {p} | Course: {st.get('course')} | Stage: {st.get('stage')} | Inquiry: {u_msgs[:1]}")

print("\n--- QR Generated Lead Details ---")
for p in qr_generated_leads:
    st = phone_states.get(p, {})
    print(f"Phone: {p} | Course: {st.get('course')} | Stage: {st.get('stage')} | Timing: {st.get('timing')} | Package: {st.get('package')} | Paid: {st.get('payment_status')}")
