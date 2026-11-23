"""
Self-check: the bot's reply may only promise a payment QR when the QR dispatcher would really
send one (same 5-slot check: Course, Timing, Teacher, Days, Package). Offline, no network calls.
Run: venv\\Scripts\\python.exe -X utf8 scripts\\check_qr_reply_consistency.py

Regression for lead 919248334684 (23 Nov 2026): Kathak 5-6 PM with Ms. Madhuri runs on both
Tuesday/Thursday and Wednesday/Friday; days were never chosen, yet the reply showed the order
summary and "UPI QR code below" while the dispatcher (correctly) refused to create the QR.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.conversation_handler import _sanitize_state_grounded_replies
from src.core.helpers.slot_engine import evaluate_enrollment_slots

QR_PROMISE = "UPI QR code below"
BASE = {"course": "kathak", "stage": "PACKAGE_SELECTED", "timing": "5:00 PM to 6:00 PM",
        "selected_timing": "5:00 PM to 6:00 PM", "teacher": "Ms. Madhuri", "selected_teacher": "Ms. Madhuri",
        "package": "3 Months", "selected_duration": "3 Months"}
ORDER_SUMMARY = ("Here is your order summary for the *3 Months* package:\n• *Total Payable:* ₹1,770\n\n"
                 "Please complete your payment via the UPI QR code below to confirm your seat!")


def run(state, reply, followup, intent="PACKAGE_SELECTION"):
    out_reply, out_followup = _sanitize_state_grounded_replies(reply, followup, state, {"intent": intent}, "kathak")
    promised = QR_PROMISE.lower() in f"{out_reply} {out_followup or ''}".lower()
    # The invariant: never promise a QR the dispatcher would block.
    assert not promised or evaluate_enrollment_slots(state, course_id="kathak")["is_complete"], (out_reply, out_followup)
    return out_reply, out_followup, promised


# 1. Days missing (multiple options): no QR promise, ask exactly which days
assert evaluate_enrollment_slots(BASE, course_id="kathak")["next_target_slot"] == "DAYS"
reply, followup, promised = run(BASE, ORDER_SUMMARY, "Would you like any assistance completing your payment with the UPI QR code? 😊")
assert not promised, reply
assert "Tuesday/Thursday" in followup and "Wednesday/Friday" in followup, followup

# 2. "Sure" turn where the LLM re-asks a settled slot: must not be rewritten into a QR promise
reply, followup, promised = run(BASE, "Great!", "Which package duration would you like?", intent="OTHER")
assert not promised, reply

# 3. Days chosen: all 5 slots settled, so the order summary + QR promise is allowed again
done = {**BASE, "days": "Tuesday/Thursday", "selected_days": "Tuesday/Thursday"}
reply, followup, promised = run(done, "Great!", "Which package duration would you like?")
assert promised and "₹1,770" in reply, reply

print("OK: QR reply consistency checks passed")
