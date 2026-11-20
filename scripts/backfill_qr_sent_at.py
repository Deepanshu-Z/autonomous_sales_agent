"""One-off repair: restore user_session_state.qr_sent_at to the lead's FIRST QR time.

Before the fix in chat_state.save_user_state, qr_sent_at was re-stamped to "now" on every
save, so stored values roughly equal the lead's last-save time. Every QR we create on
Razorpay carries the lead's phone in notes.phone, so the earliest QR per phone is the
true first-dispatch time. (qr_id on the row is the LATEST QR, so it is only a fallback.)

    venv\\Scripts\\python scripts\\backfill_qr_sent_at.py          # dry run (default)
    venv\\Scripts\\python scripts\\backfill_qr_sent_at.py --apply  # write changes
"""
import os, re, sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from supabase import create_client
from src.services.razorpay_service import get_razorpay_client

APPLY = "--apply" in sys.argv
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
rzp = get_razorpay_client()


def key(phone) -> str:
    return re.sub(r"\D", "", str(phone or ""))[-10:]


# 1. Earliest QR created_at per phone, from every QR on the Razorpay account.
first_qr: dict[str, int] = {}
skip = 0
while True:
    items = rzp.qrcode.all({"count": 100, "skip": skip}).get("items", [])
    for qr in items:
        k = key((qr.get("notes") or {}).get("phone"))
        if k and qr.get("created_at"):
            first_qr[k] = min(first_qr.get(k, qr["created_at"]), qr["created_at"])
    if len(items) < 100:
        break
    skip += 100
print(f"Razorpay QRs scanned: {skip + len(items)} | phones with a QR: {len(first_qr)}")

# 2. Rows that the CRM treats as "QR sent".
rows, page = [], 0
while True:
    r = (sb.table("user_session_state").select("phone, qr_id, qr_sent_at, is_qr_sent")
         .or_("is_qr_sent.eq.true,qr_id.not.is.null")
         .range(page * 1000, page * 1000 + 999).execute())
    rows += r.data or []
    if len(r.data or []) < 1000:
        break
    page += 1

fixed = same = unknown = 0
for row in rows:
    created = first_qr.get(key(row["phone"]))
    if created is None and str(row.get("qr_id") or "").startswith("qr_"):
        try:
            created = rzp.qrcode.fetch(row["qr_id"])["created_at"]  # fallback: latest QR
        except Exception:
            created = None
    if created is None:
        unknown += 1
        print(f"NONE  {row['phone']}: no Razorpay QR found (qr_id={row.get('qr_id')!r}); left as-is")
        continue
    true_iso = datetime.fromtimestamp(int(created), tz=timezone.utc).isoformat()
    old = row.get("qr_sent_at")
    # Stored values are unreliable in BOTH directions: most equal the lead's first-contact
    # time (frozen updated_at), some are later re-stamps. Razorpay's earliest QR wins.
    if old and abs(datetime.fromisoformat(old).timestamp() - int(created)) < 60:
        same += 1
        continue
    fixed += 1
    print(f"{'FIX ' if APPLY else 'WOULD'} {row['phone']}: {old} -> {true_iso}")
    if APPLY:
        sb.table("user_session_state").update({"qr_sent_at": true_iso}).eq("phone", row["phone"]).execute()

print(f"\nrows: {len(rows)} | {'fixed' if APPLY else 'to fix'}: {fixed} | "
      f"already correct: {same} | no QR found (untouched): {unknown}")
if not APPLY:
    print("Dry run only. Re-run with --apply to write.")
