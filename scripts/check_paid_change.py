"""Self-check: a PAID student asking for another timing / teacher / days / package is flagged (from the classifier's
extracted meaning, any language) so the handler sends Customer Support; the booked batch never changes.
Flags below are real classifier outputs for "Mere 6 to 7 pm ko chahiye" / "Aj class kab he Madhuri mam ki".
Run: python scripts/check_paid_change.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.core.intent_classifier import apply_extraction_to_state

PAID = {"course": "kathak", "payment_status": "PAID", "stage": "ENROLLED", "is_enrolled": True,
        "timing": "4:00 PM to 5:00 PM", "selected_teacher": "Ms. Madhuri", "days": "Tuesday/Thursday",
        "days_code": "TT", "package": "3 Months"}


def turn(state, **flags):
    ns = apply_extraction_to_state(dict(state), flags)
    return ns["_last_turn"].get("paid_change_requested"), ns


want_6_7 = {"customer_intent": "TIMING_SELECTION", "selected_timing": "6:00 PM to 7:00 PM", "timing_resolution_status": "SELECTED"}
flag, ns = turn(PAID, **want_6_7)
assert flag is True and ns["timing"] == "4:00 PM to 5:00 PM", (flag, ns.get("timing"))      # flagged, batch untouched
assert turn(PAID, customer_intent="TEACHER_SELECTION", selected_teacher="vibha mam")[0] is True  # another teacher
assert turn(PAID, customer_intent="DAY_SELECTION", selected_days="Wednesday/Friday")[0] is True
assert turn(PAID, customer_intent="PACKAGE_SELECTION", selected_package="6 Months")[0] is True

assert turn(PAID, customer_intent="QUESTION", wants_schedule_change=True)[0] is True  # "Ap ha bolethe" after a 6-7 PM ask
assert turn(PAID, customer_intent="QUESTION", question_summary="Inquiry about class timing for Madhuri mam")[0] is False
assert turn(PAID, customer_intent="QUESTION", selected_teacher="madhuri mam")[0] is False        # her own teacher
assert turn(PAID, customer_intent="TIMING_SELECTION", selected_timing="4:00 PM to 5:00 PM")[0] is False  # her own batch

assert not turn({"course": "kathak"}, **want_6_7)[0]                                           # unpaid lead: normal selection

# Start date is fixed at payment: a new date is a change request and never reaches state (Gmail staging reads it).
flag, ns = turn(PAID, customer_intent="OTHER", preferred_start_date="1 dec")  # classifier echoing an old date on "Ok"
assert flag is False and not ns.get("preferred_start_iso"), (flag, ns.get("preferred_start_iso"))  # no loop, still frozen
flag, ns = turn(PAID, customer_intent="QUESTION", preferred_start_date="1 dec", wants_schedule_change=True)
assert flag is True and not ns.get("preferred_start_iso")                                   # real date-change request
assert turn({"course": "kathak"}, customer_intent="OTHER", preferred_start_date="1 dec")[1]["preferred_start_iso"]  # unpaid: saved

# The AI of a paid student only ever sees their own batch, so it has no other timing to offer.
from src.core.prompt import format_system_prompt
paid_prompt = format_system_prompt(dict(PAID))
assert "4:00 PM to 5:00 PM" in paid_prompt and "Customer Support" in paid_prompt
assert all(t not in paid_prompt for t in ("5:00 PM to 6:00 PM", "6:00 PM to 7:00 PM", "8:00 PM to 9:00 PM")), "paid prompt leaks other batches"
assert "6:00 PM to 7:00 PM" in format_system_prompt({"course": "kathak"})                     # unpaid lead still sees every batch

# A paid student gets the support prompt, never the sales funnel (micro-choices, start-date selling, fees).
for sales_rule in ("SALES OBJECTIVE", "mostly subah free", "FUTURE START DATE", "lock in their package", "Total Payable", "kaunsa package"):
    assert sales_rule.lower() not in paid_prompt.lower(), sales_rule
assert "student-support assistant" in paid_prompt and "SALES OBJECTIVE" in format_system_prompt({"course": "kathak"})
print("OK: paid students asking for a different slot are routed to Customer Support; own batch / questions / unpaid leads untouched")
