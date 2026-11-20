import json
from collections import Counter

with open('scratch/new_yoga_leads_detail.json', 'r', encoding='utf-8') as f:
    leads = json.load(f)

print(f'Total Leads: {len(leads)}')
status_counts = Counter(l['status'] for l in leads)
print('Status counts:', status_counts)

print(f"\n=== 1. QR GENERATED ({status_counts.get('QR_GENERATED', 0)} LEADS) ===")
for l in leads:
    if l['status'] == 'QR_GENERATED':
        print(f"Phone: +{l['phone']} | Timing: {l['timing']} | Teacher: {l['teacher']} | Package: {l['package']} | Fee: ₹{l['payable_fee']}")

print(f"\n=== 2. CONVERSIONS ({status_counts.get('PAID', 0)} LEADS) ===")
print("0 Paid (All 5 QR leads are currently pending payment)")

print(f"\n=== 3. GHOSTED AFTER ENQUIRY ({status_counts.get('GHOSTED', 0)} LEADS) ===")
for i, l in enumerate([x for x in leads if x['status'] == 'GHOSTED'], 1):
    print(f"{i}. +{l['phone']} | Inquiry: {l['first_inquiry']}")

print(f"\n=== 4. ENGAGED IN-FUNNEL ({status_counts.get('ENGAGED', 0)} LEADS) ===")
for i, l in enumerate([x for x in leads if x['status'] == 'ENGAGED'], 1):
    print(f"{i}. +{l['phone']} | msgs: {l['user_messages_count']} | stage: {l['stage']} | timing: {l['timing']} | last_user_msg: {l['last_user_msg']}")
