"""
Self-check for the payments ledger (fakes only: no Supabase / Redis / Razorpay calls).
Run: venv\\Scripts\\python.exe scripts\\check_payment_ledger.py
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["RAZORPAY_KEY_ID"] = "rzp_live_selfcheck"

from src.services import payment_ledger as ledger

NOW = int(time.time())
CAPTURED = {"id": "pay_ABC123", "status": "captured", "amount": 63000, "currency": "INR",
            "method": "upi", "created_at": NOW, "notes": {}}
QR = {"id": "qr_XYZ789", "payment_amount": 63000, "payments_amount_received": 63000,
      "notes": {"phone": "919999999999", "course_id": "yoga", "duration": "3 Months", "source": "whatsapp_sales_agent"}}


def rejects(**overrides) -> bool:
    payment = {**CAPTURED, **overrides.pop("payment", {})}
    try:
        ledger.build_payment_row(payment, overrides.pop("qr", QR), overrides.pop("phone", "919999999999"),
                                 overrides.pop("source", "webhook"), **overrides)
        return False
    except ledger.LedgerRowError:
        return True


# 1. Only real, complete, captured payments become rows (mirrors the table constraints)
row = ledger.build_payment_row(CAPTURED, QR, "+91 99999-99999", "webhook", student_email="Priya.K12@gmail.com")
assert row["payment_id"] == "pay_ABC123" and row["phone"] == "919999999999" and row["amount_inr"] == 630.0
assert row["course_id"] == "yoga" and row["package"] == "3 Months" and row["qr_id"] == "qr_XYZ789"
assert row["student_email"] == "priya.k12@gmail.com" and row["status"] == "captured"
assert ledger.build_payment_row(CAPTURED, QR, "9999999999", "webhook")["phone"] == "919999999999"
assert "student_email" not in ledger.build_payment_row(CAPTURED, QR, "919999999999", "webhook", student_email="None")
assert "student_email" not in ledger.build_payment_row(CAPTURED, QR, "919999999999", "webhook"), \
    "missing fields are omitted, so an upsert never overwrites a known Gmail with NULL"
assert rejects(payment={"id": "qr_XYZ789"}), "non-payment id"
assert rejects(payment={"id": "dummy"})
assert rejects(payment={"status": "failed"}) and rejects(payment={"status": "authorized"})
assert rejects(payment={"amount": 0})
assert rejects(payment={"amount": 100}), "₹1 team test payment is not a real payment"
assert rejects(phone="")
assert rejects(qr={"id": "qr_1", "notes": {}}), "no course"
assert rejects(source="manual")
assert rejects(payment={"created_at": None})

# 2. Test-mode Razorpay keys never write
os.environ["RAZORPAY_KEY_ID"] = "rzp_test_123"
assert not ledger.is_ledger_enabled()
os.environ["RAZORPAY_KEY_ID"] = "rzp_live_selfcheck"
assert ledger.is_ledger_enabled()


# 3. Fire-and-forget: failing DB never raises to the caller and lands in the retry queue
class FakeRedis:
    def __init__(self):
        self.lists = {}

    async def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    async def lpop(self, key):
        items = self.lists.get(key) or []
        return items.pop(0) if items else None


fake_redis = FakeRedis()
import src.services.redis_client as redis_client
redis_client.get_async_redis_connection = lambda: fake_redis
ledger.WRITE_ATTEMPTS, ledger.WRITE_TIMEOUT_SEC = 1, 1.0

db = {"rows": {}, "down": True}


def fake_upsert(rows):
    if db["down"]:
        raise ConnectionError("supabase down")
    for r in rows:
        db["rows"][r["payment_id"]] = {**db["rows"].get(r["payment_id"], {}), **r}


def fake_set_email(phone, email):
    if db["down"]:
        raise ConnectionError("supabase down")
    for r in db["rows"].values():
        if r["phone"] == phone:
            r["student_email"] = email


ledger._upsert_rows_sync, ledger._set_email_sync = fake_upsert, fake_set_email
fake_upsert.__name__, fake_set_email.__name__ = "_upsert_rows_sync", "_set_email_sync"


async def scenario():
    ledger.schedule_payment_record(CAPTURED, QR, "919999999999", "webhook")  # returns immediately
    ledger.schedule_payment_record({**CAPTURED, "status": "failed"}, QR, "919999999999", "webhook")  # ignored
    await asyncio.gather(*list(ledger._background_tasks))
    queued = [json.loads(x) for x in fake_redis.lists.get(ledger.RETRY_QUEUE_KEY, [])]
    assert len(queued) == 1 and queued[0]["op"] == "upsert", queued

    db["down"] = False                                          # DB back: worker drains the queue
    assert await ledger.drain_retry_queue_async() == 1 and "pay_ABC123" in db["rows"]

    ledger.schedule_payment_record(CAPTURED, QR, "919999999999", "webhook")  # duplicate webhook
    await asyncio.gather(*list(ledger._background_tasks))
    assert len(db["rows"]) == 1, "idempotent on payment_id"

    ledger.schedule_email_attach("9999999999", "Real.User@gmail.com")
    await asyncio.gather(*list(ledger._background_tasks))
    assert db["rows"]["pay_ABC123"]["student_email"] == "real.user@gmail.com"
    assert await ledger.attach_student_email_async("919999999999", "account") is False, "junk email refused"
    assert db["rows"]["pay_ABC123"]["student_email"] == "real.user@gmail.com"

    ledger.schedule_payment_record(CAPTURED, QR, "919999999999", "webhook")  # re-upsert without email
    await asyncio.gather(*list(ledger._background_tasks))
    assert db["rows"]["pay_ABC123"]["student_email"] == "real.user@gmail.com", "email never wiped by upsert"

    db["down"] = True                                           # repeated failures → dead-letter
    await ledger._enqueue_retry("upsert", db["rows"]["pay_ABC123"], attempts=ledger.MAX_QUEUE_ATTEMPTS - 1)
    await ledger.drain_retry_queue_async()
    assert len(fake_redis.lists.get(ledger.DEAD_LETTER_KEY, [])) == 1


asyncio.run(scenario())


# 4. Razorpay helpers: only our QRs, only captured payments of the exact QR amount
class FakeQR:
    def all(self, params):
        page = [QR, {**QR, "id": "qr_OTHER", "notes": {"source": "payment_link"}}]
        return {"items": page if params["skip"] == 0 else []}

    def fetch_all_payments(self, qr_id):
        return {"items": [CAPTURED, {**CAPTURED, "id": "pay_FAIL", "status": "failed"},
                          {**CAPTURED, "id": "pay_PART", "amount": 100}]}


class FakeClient:
    qrcode = FakeQR()


assert [q["id"] for q in ledger.iter_sales_qr_codes(FakeClient(), 0)] == ["qr_XYZ789"]
assert [p["id"] for p in ledger.captured_payments_for_qr(FakeClient(), QR)] == ["pay_ABC123"]
assert ledger.captured_payments_for_qr(FakeClient(), {**QR, "payments_amount_received": 0}) == []

print("OK: payment ledger checks passed")
