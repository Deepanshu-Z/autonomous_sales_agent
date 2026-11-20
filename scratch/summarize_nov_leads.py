import json
from datetime import datetime

with open("scratch/all_complete_no_qr_leads.json", "r", encoding="utf-8") as f:
    leads = json.load(f)

print(f"Total leads in json: {len(leads)}")

# Filter recent (November 2026)
nov_leads = [l for l in leads if str(l.get("updated_at", "")).startswith("2026-11")]
oct_leads = [l for l in leads if str(l.get("updated_at", "")).startswith("2026-10")]
sep_leads = [l for l in leads if str(l.get("updated_at", "")).startswith("2026-09")]

print(f"November 2026 leads: {len(nov_leads)}")
print(f"October 2026 leads:  {len(oct_leads)}")
print(f"September 2026 leads:{len(sep_leads)}")

print("\n=== ALL NOVEMBER 2026 LEADS (COMPLETE SLOTS, NO QR) ===")
for i, l in enumerate(nov_leads, 1):
    upd = str(l.get("updated_at"))[:16].replace("T", " ")
    amt = l.get("payable_amount")
    print(f"{i:2d}. Phone: {l['phone']:15} | {l['course']:12} | Timing: {str(l['timing']):18} | Teacher: {str(l['teacher']):12} | Pkg: {str(l['package']):8} | Fee: ₹{amt} | Upd: {upd}")
