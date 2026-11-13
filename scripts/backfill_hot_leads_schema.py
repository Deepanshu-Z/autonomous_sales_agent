import os
import re
import time
from dotenv import load_dotenv

load_dotenv('local_auto/.env')
from supabase import create_client

url = os.getenv('SUPABASE_URL')
key = os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_KEY')
if not url or not key:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY")

sb = create_client(url, key)

STANDARD_FEES = {
    'ladies_dance': {'1 Month': 590, '3 Months': 708, '6 Months': 1062, '1 Year': 1416},
    'kathak': {'1 Month': 826, '3 Months': 1770, '6 Months': 2832, '1 Year': 4720},
    'yoga': {'1 Month': 735, '3 Months': 630, '6 Months': 1050, '1 Year': 1260},
}

def clean_phone(p: str) -> str:
    return re.sub(r'\D', '', p or '')

def main():
    print("[backfill] 1. Fetching all user_session_state records...")
    offset = 0
    limit = 1000
    all_sessions = []
    while True:
        res = (
            sb.table('user_session_state')
            .select('phone, stage, timing, package, fee, last_topic, updated_at')
            .range(offset, offset + limit - 1)
            .execute()
        )
        batch = res.data or []
        all_sessions.extend(batch)
        print(f"  Fetched {len(batch)} rows (total so far: {len(all_sessions)})...")
        if len(batch) < limit:
            break
        offset += limit

    print(f"[backfill] Total sessions fetched: {len(all_sessions)}")

    # Index by clean phone
    session_map = {}
    for s in all_sessions:
        p = clean_phone(s.get('phone'))
        if p:
            session_map[p] = s

    # 2. Check chat_history for user QR requests
    print("[backfill] 2. Checking chat_history for user QR requests...")
    user_qr_phones = set()
    try:
        user_res = (
            sb.table('chat_history')
            .select('phone')
            .eq('role', 'user')
            .or_('message.ilike.%qr%,message.ilike.%scanner%,message.ilike.%payment link%')
            .limit(1000)
            .execute()
        )
        for r in user_res.data or []:
            p = clean_phone(r.get('phone'))
            if p:
                user_qr_phones.add(p)
        print(f"  Found {len(user_qr_phones)} distinct phones with user QR requests.")
    except Exception as e:
        print(f"  Warning: Failed querying chat_history user QR requests: {e}")

    # 3. Check chat_history for assistant QR deliveries
    print("[backfill] 3. Checking chat_history for assistant QR deliveries...")
    assistant_qr_phones = set()
    try:
        asst_res = (
            sb.table('chat_history')
            .select('phone')
            .eq('role', 'assistant')
            .or_('message.ilike.%UPI QR code%,message.ilike.%Scan & Pay%,message.ilike.%rzp.io%')
            .limit(1000)
            .execute()
        )
        for r in asst_res.data or []:
            p = clean_phone(r.get('phone'))
            if p:
                assistant_qr_phones.add(p)
        print(f"  Found {len(assistant_qr_phones)} distinct phones with assistant QR delivery messages.")
    except Exception as e:
        print(f"  Warning: Failed querying chat_history assistant QR messages: {e}")

    # 4. Prepare updates for each session
    updates_to_run = []
    qr_sent_count = 0
    requests_qr_count = 0

    for s in all_sessions:
        raw_phone = s.get('phone')
        cp = clean_phone(raw_phone)
        lt = s.get('last_topic') or ''
        pkg = s.get('package') or ''
        fee = s.get('fee')
        stage = s.get('stage') or ''

        payload = {'phone': raw_phone}
        has_change = False

        # Course
        m_c = re.search(r'COURSE:([^|]+)', lt)
        if m_c:
            payload['course'] = m_c.group(1).strip()
            has_change = True

        # Teacher
        m_t = re.search(r'TEACHER:([^|]+)', lt)
        if m_t:
            payload['teacher'] = m_t.group(1).strip()
            has_change = True

        # QR Sent
        is_qr = False
        qr_id = None
        m_ord = re.search(r'ORDER:([^|]+)', lt)
        m_pay = re.search(r'PAY:([^|]+)', lt)
        if m_ord:
            ord_v = m_ord.group(1).strip()
            if ord_v.startswith('qr_') or ord_v.startswith('order_') or ord_v.startswith('pay_'):
                is_qr = True
                qr_id = ord_v
        elif m_pay and m_pay.group(1).strip() == 'PAID':
            is_qr = True
        elif cp in assistant_qr_phones:
            is_qr = True

        if is_qr:
            payload['is_qr_sent'] = True
            qr_sent_count += 1
            has_change = True
            if qr_id:
                payload['qr_id'] = qr_id

        # Requests QR
        if not is_qr and (cp in user_qr_phones or stage == 'PACKAGE_SELECTED'):
            payload['requests_qr'] = True
            requests_qr_count += 1
            has_change = True

        # Amount
        m_amt = re.search(r'AMOUNT:([0-9.]+)', lt)
        if m_amt:
            try:
                payload['payable_amount'] = float(m_amt.group(1))
                has_change = True
            except Exception:
                pass
        elif fee and str(fee).replace('.', '', 1).isdigit():
            payload['payable_amount'] = float(fee)
            has_change = True
        elif payload.get('course') and pkg:
            c_fees = STANDARD_FEES.get(payload['course'], {})
            if pkg in c_fees:
                payload['payable_amount'] = float(c_fees[pkg])
                has_change = True

        if has_change:
            updates_to_run.append(payload)

    print(f"\n[backfill] Prepared {len(updates_to_run)} rows to update:")
    print(f"  - Leads with is_qr_sent = True: {qr_sent_count}")
    print(f"  - Leads with requests_qr = True: {requests_qr_count}")

    # 5. Apply updates in batches of 100 via upsert
    batch_size = 100
    total_batches = (len(updates_to_run) + batch_size - 1) // batch_size
    print(f"\n[backfill] Executing upserts in {total_batches} batches of {batch_size}...")

    success_count = 0
    fail_count = 0
    for i in range(0, len(updates_to_run), batch_size):
        batch = updates_to_run[i:i + batch_size]
        batch_idx = (i // batch_size) + 1
        try:
            sb.table('user_session_state').upsert(batch, on_conflict='phone').execute()
            success_count += len(batch)
            if batch_idx % 10 == 0 or batch_idx == total_batches:
                print(f"  Batch {batch_idx}/{total_batches} completed ({success_count}/{len(updates_to_run)} rows)...")
        except Exception as e:
            print(f"  Batch {batch_idx}/{total_batches} failed: {e}")
            # Try row-by-row fallback for this batch
            for row in batch:
                try:
                    p = row.pop('phone')
                    sb.table('user_session_state').update(row).eq('phone', p).execute()
                    success_count += 1
                except Exception as inner_e:
                    fail_count += 1

    print(f"\n[backfill] FINISHED!")
    print(f"  Total succeeded: {success_count}")
    print(f"  Total failed: {fail_count}")

if __name__ == '__main__':
    main()

