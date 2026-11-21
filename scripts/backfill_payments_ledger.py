"""
One-off backfill of the `payments` ledger from RAZORPAY (never from our own state or chat text).

Source of rows: every QR code this bot created (notes.source = whatsapp_sales_agent) since
--since, keeping only payments that are `captured` for exactly the QR's fixed amount.
Gmail comes from the client's state and is kept only if it passes sanitize_email.
Paid clients with no matching Razorpay payment are REPORTED, never given a row.

Run ON THE SERVER, after `docs/sql/create_payments_table.sql` and after
`scripts/fix_bad_student_emails.py --apply` (so corrected Gmails are copied):
    python scripts/backfill_payments_ledger.py --since 2026-09-01                 # preview
    python scripts/backfill_payments_ledger.py --since 2026-09-01 --apply         # write
    ... --exclude-phone 919999999999 --exclude-phone 918888888888                 # skip team tests
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

load_dotenv()

from src.services import payment_ledger as ledger
from src.services.razorpay_service import get_razorpay_client
from src.services.supabase_client import supabase
from src.state.chat_state import get_user_state_async
from src.state.payment_status import is_payment_verified


def paid_phones_in_state() -> set:
    """Phones our state marks PAID / ENROLLED (to report clients Razorpay can't confirm)."""
    rows = (supabase.table("user_session_state").select("phone,stage,last_topic")
            .or_("last_topic.ilike.%PAY:PAID%,stage.eq.ENROLLED").execute().data or [])
    return {ledger.normalize_phone(r["phone"]) for r in rows}


async def build_rows(since_ts: int, excluded: set):
    client = get_razorpay_client()
    qrs = await asyncio.to_thread(lambda: list(ledger.iter_sales_qr_codes(client, since_ts)))
    rows, skipped, not_paid_in_state = [], [], []
    for qr in qrs:
        for payment in await asyncio.to_thread(ledger.captured_payments_for_qr, client, qr):
            phone = (qr.get("notes") or {}).get("phone") or ""
            if ledger.normalize_phone(phone) in excluded:
                skipped.append((payment["id"], "excluded phone"))
                continue
            state = await get_user_state_async(phone)
            try:
                row = ledger.build_payment_row(
                    payment, qr, phone, "backfill",
                    timing=state.get("selected_timing") or state.get("timing"),
                    teacher=state.get("selected_teacher") or state.get("teacher"),
                    student_email=state.get("student_email"),
                )
            except ledger.LedgerRowError as exc:
                skipped.append((payment["id"], str(exc)))
                continue
            if not is_payment_verified(state):
                not_paid_in_state.append(row)
            rows.append(row)
    return len(qrs), rows, skipped, not_paid_in_state


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", default="2026-09-01", help="first QR creation date to scan (YYYY-MM-DD)")
    parser.add_argument("--exclude-phone", action="append", default=[], help="phone to skip (team test payments)")
    parser.add_argument("--apply", action="store_true", help="write rows (default: preview only)")
    args = parser.parse_args()

    if not ledger.is_ledger_enabled():
        sys.exit("Refusing: RAZORPAY_KEY_ID is a TEST key — test payments are not real payments.")

    since_ts = int(datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    excluded = {ledger.normalize_phone(p) for p in args.exclude_phone}
    qr_count, rows, skipped, not_paid_in_state = asyncio.run(build_rows(since_ts, excluded))

    try:
        existing = ledger._existing_payment_ids_sync([r["payment_id"] for r in rows])
    except Exception as exc:
        existing = set()
        print(f"(could not read existing rows — has the table been created? {exc})")
    new_rows = [r for r in rows if r["payment_id"] not in existing]
    ledger_phones = {r["phone"] for r in rows}
    unconfirmed = sorted(paid_phones_in_state() - ledger_phones)

    print(f"Scanned {qr_count} bot QR codes since {args.since}")
    print(f"Captured payments: {len(rows)} | already in ledger: {len(rows) - len(new_rows)} | to write: {len(new_rows)}")
    print(f"  with Gmail: {sum(1 for r in new_rows if r.get('student_email'))} | without Gmail (filled later): "
          f"{sum(1 for r in new_rows if not r.get('student_email'))}")
    for r in new_rows:
        print(f"  {r['captured_at'][:10]}  ..{r['phone'][-4:]}  {r['payment_id']}  ₹{r['amount_inr']:>8}  "
              f"{r['course_id']:<14} {r.get('student_email') or '-'}")
    if not_paid_in_state:
        print("\n⚠️  Real captured payments whose client is NOT marked PAID in state (team tests? missed webhook?):")
        for r in not_paid_in_state:
            print(f"  ..{r['phone'][-4:]} {r['payment_id']} ₹{r['amount_inr']} — exclude with --exclude-phone {r['phone']} if a test")
    if skipped:
        print("\nSkipped (not a complete captured payment):")
        for pid, why in skipped:
            print(f"  {pid}: {why}")
    if unconfirmed:
        print("\n⚠️  Marked PAID in our state but NO captured payment found on Razorpay (manual review, NO row written):")
        print("  " + ", ".join(f"..{p[-4:]}" for p in unconfirmed))

    if args.apply and new_rows:
        for i in range(0, len(new_rows), 50):
            ledger._upsert_rows_sync(new_rows[i:i + 50])
        print(f"\n✅ Wrote {len(new_rows)} row(s).")
    elif new_rows:
        print("\nPreview only. Re-run with --apply ON THE SERVER to write.")


if __name__ == "__main__":
    main()
