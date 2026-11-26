"""Self-check: no payment summary / QR promise goes out while an enrollment detail is missing.
Replies below are real leaked replies from chat history.
Run: python scripts/check_premature_payment.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.core.conversation_handler import _sanitize_state_grounded_replies as sanitize

LEAK = re.compile(r"summary|payable|qr|scan|complete (your )?payment|payment kar|niche diya", re.I)

# Kathak, Ms. Madhuri 4-5 PM runs Tue/Thu AND Wed/Fri -> batch days still missing.
DAYS_MISSING = {"course": "kathak", "timing": "4:00 PM to 5:00 PM", "selected_timing": "4:00 PM to 5:00 PM",
                "teacher": "Ms. Madhuri", "selected_teacher": "Ms. Madhuri", "package": "1 Year", "selected_duration": "1 Year"}
COMPLETE = {**DAYS_MISSING, "days": "Tuesday/Thursday", "selected_days": "Tuesday/Thursday", "days_code": "TT"}
PAID = {**DAYS_MISSING, "payment_status": "PAID", "is_enrolled": True}

KATHAK_LEAK = (
    "Aapne 1 saal ka package select kiya hai. Aapki classes *Kathak* ke liye *Ms. Madhuri* ke saath *4:00 PM se 5:00 PM* tak hongi. "
    "Yahan aapka transparent order fee breakdown hai:\n\n"
    "• Base Catalog Price: ₹7,000\n• Discount: -₹3,000\n• Taxable Offer Price: ₹4,000\n• GST (18%): +₹720\n"
    "• FINAL TOTAL PAYABLE AMOUNT: ₹4,720\n\n"
    "Aap is amount ka payment UPI QR code ke zariye kar sakte hain jo niche diya gaya hai."
)
ENGLISH_LEAK = (
    "Great! You have selected *Ms. Madhuri* for the *4:00 PM to 5:00 PM* batch of *Kathak*.\n\n"
    "Here is your order summary for the *1 Year* package:\n• *Total Payable:* ₹4,720\n\n"
    "Please complete your payment via the UPI QR code below to confirm your seat!"
)
PAY_FOLLOWUP = "Kya aapko payment complete karne mein koi madad chahiye UPI QR code ke saath? 😊"


def run(reply, state, turn, followup=PAY_FOLLOWUP):
    return sanitize(reply, followup, state=dict(state), last_turn=turn, active_cid="kathak")


# 1. Normal turn, days missing: whole summary replaced by the days question.
for leak in (KATHAK_LEAK, ENGLISH_LEAK):
    r, f = run(leak, DAYS_MISSING, {"intent": "PACKAGE_SELECTION"})
    assert not LEAK.search(r) and not LEAK.search(f or ""), (r, f)
    assert "days" in (r + (f or "")).lower(), (r, f)

# 2. Question turn (the hole before: guard was skipped): answer kept, QR promise dropped, days asked.
r, f = run(KATHAK_LEAK, DAYS_MISSING, {"intent": "QUESTION", "question_summary": "Kathak fee for 1 year"})
assert "₹4,720" in r, r                                    # the fee answer survives
assert not re.search(r"qr|niche diya|summary", r, re.I), r  # but no QR promise / checkout header
assert f and "din" in f.lower() or "days" in (f or "").lower(), f

# 3. Complaint turn: QR promise dropped, no sales follow-up.
r, f = run(KATHAK_LEAK, DAYS_MISSING, {"intent": "COMPLAINT"})
assert not re.search(r"qr|niche diya", r, re.I) and f is None, (r, f)

# 4. Everything selected: payment summary is allowed and untouched.
r, f = run(ENGLISH_LEAK, COMPLETE, {"intent": "PACKAGE_SELECTION"})
assert "Total Payable" in r and "QR" in r, r

# 5. Paid lead: guard does not touch the reply.
paid_reply = "Your payment is confirmed! Please complete your profile in the Sensationz App."
r, _ = run(paid_reply, PAID, {"intent": "QUESTION"}, followup=None)
assert r == paid_reply, r

print("OK: premature payment summaries are blocked; complete and paid leads unaffected")
