"""
Self-check: only Razorpay can make a client PAID, and the bot never confirms an unverified payment.
Uses fakes only (no Razorpay / WhatsApp / DB writes).
Run: venv\\Scripts\\python.exe scripts\\check_payment_truth.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.state.payment_status import is_payment_verified
from src.state.chat_state import track_sent_qr, get_sent_qr_ids
from src.services import razorpay_service as rzp
from src.core import conversation_handler as ch

UNPAID = {"payment_status": "NONE", "stage": "PACKAGE_SELECTED", "language": "English"}
PAID = {"payment_status": "PAID", "stage": "ENROLLED", "is_enrolled": True}

# 1. Single source of truth
assert is_payment_verified(PAID) and not is_payment_verified(UNPAID)
assert not is_payment_verified({"claims_payment": True, "payment_claimed": True, "staged_enrollment": True})
assert not is_payment_verified(None)

# 2. QR tracking keeps older, still-valid QRs (newest first)
st = {}
for qr in ("qr_A", "qr_B", "qr_A", "qr_C"):
    track_sent_qr(st, qr)
assert get_sent_qr_ids(st) == ["qr_C", "qr_A", "qr_B"] and st["last_qr_id"] == "qr_C"
assert get_sent_qr_ids({"order_id": "qr_X"}) == ["qr_X"] and get_sent_qr_ids({"order_id": "pay_1"}) == []

# 3. Guardrail: the REAL false confirmations from production are blocked; conditional/negated text is not
REAL_LEAKS = [
    "Your payment has already been received and verified! Your classes for Yoga with Mradula are confirmed.",
    "Aapka payment already receive ho gaya hai aur verify bhi ho chuka hai!",
    "We've received your payment, thank you!",
    "पेमेंट मिल गया है, धन्यवाद",
]
NOT_CONFIRMATIONS = [
    "Once your payment is received, your batch is activated automatically.",
    "Your payment has not been received yet.",
    "Abhi tak payment receive nahi hua hai.",
    "Payment receive hote hi aapko message mil jayega.",
    "Your payment will be verified by Razorpay.",
    "Here is your order summary: Base Price ₹2,100, GST ₹105.",
]
for text in REAL_LEAKS:
    assert ch._contains_false_payment_confirmation(text), f"leak not caught: {text}"
    reply, followup = ch._sanitize_confirmation_guardrails(text, "Any questions?", "English", dict(UNPAID))
    assert reply == ch.build_payment_not_received_reply("Hindi" if "मिल" in text else "English") and followup is None
for text in NOT_CONFIRMATIONS:
    assert not ch._contains_false_payment_confirmation(text), f"false positive: {text}"
ok_reply = REAL_LEAKS[0]
assert ch._sanitize_confirmation_guardrails(ok_reply, None, "English", dict(PAID))[0] == ok_reply, "paid client must pass"

# 4. Claim detection
assert ch._is_payment_claim({"claims_payment_or_enrollment": True})
assert ch._is_payment_claim({"customer_intent": "PAYMENT_CLAIM"})
assert not ch._is_payment_claim({"customer_intent": "PAYMENT_REQUEST", "claims_payment_or_enrollment": True})
assert not ch._is_payment_claim({})


# 5. Razorpay lookup (option A): only a CAPTURED payment of the EXACT QR amount on OUR QR
class FakeQRCodes:
    def __init__(self, qrs):  # {qr_id: (amount_paise, [payments])}
        self.qrs, self.calls = qrs, 0

    def fetch(self, qr_id):
        self.calls += 1
        return {"id": qr_id, "payment_amount": self.qrs[qr_id][0], "notes": {"phone": "919999999999"}}

    def fetch_all_payments(self, qr_id):
        return {"items": self.qrs[qr_id][1]}


class FakeRazorpay:
    def __init__(self, qrs):
        self.qrcode = FakeQRCodes(qrs)


fulfilled = []


async def fake_fulfill(payload, send_notification=False, ledger_source="webhook"):
    assert ledger_source == "claim_lookup", "lookup-fulfilled payments are tagged in the ledger"
    fulfilled.append(payload["payload"]["payment"]["entity"]["id"])
    return {"status": "fulfilled"}


async def fake_get_state(phone):
    return dict(PAID)


async def always_allowed(phone):
    return True


rzp.fulfill_razorpay_payment_async = fake_fulfill
rzp.get_user_state_async = fake_get_state
rzp._is_payment_lookup_allowed = always_allowed


def lookup(state, qrs):
    client = FakeRazorpay(qrs)
    result = asyncio.run(rzp.reconcile_claimed_payment_async("919999999999", dict(state), client=client))
    return result[0], client.qrcode.calls


qr_state = dict(UNPAID)
track_sent_qr(qr_state, "qr_old")
track_sent_qr(qr_state, "qr_new")

assert lookup(UNPAID, {}) == (False, 0), "no QR sent -> no API call, not paid"
assert lookup(qr_state, {"qr_new": (63000, [{"id": "pay_f", "status": "failed", "amount": 63000}]),
                         "qr_old": (63000, [{"id": "pay_c", "status": "created", "amount": 63000}])})[0] is False
assert lookup(qr_state, {"qr_new": (63000, [{"id": "pay_w", "status": "captured", "amount": 100}]),
                         "qr_old": (63000, [])})[0] is False, "wrong amount must not count"
assert not fulfilled, f"nothing may be fulfilled yet: {fulfilled}"
assert lookup(qr_state, {"qr_new": (63000, []),
                         "qr_old": (63000, [{"id": "pay_ok", "status": "captured", "amount": 63000}])})[0] is True
assert fulfilled == ["pay_ok"], "captured payment on an OLDER QR of this client is found"
assert lookup(PAID, {}) == (True, 0), "already verified -> no API call"

# 6. fulfill itself refuses a non-captured payment (shared guard for webhook + lookup)
from importlib import reload
real_rzp = reload(rzp)
res = asyncio.run(real_rzp.fulfill_razorpay_payment_async(
    {"event": "payment.captured", "payload": {"payment": {"entity": {"id": "pay_x", "status": "failed", "amount": 100}}}}))
assert res["status"] == "ignored" and "not_captured" in res["reason"], res

# 7. Fixed reply is identical every time (cannot be argued with)
assert ch.build_payment_not_received_reply("English") == ch.build_payment_not_received_reply("english")
assert "Razorpay" in ch.build_payment_not_received_reply("Hinglish")

print("OK: payment truth checks passed")
