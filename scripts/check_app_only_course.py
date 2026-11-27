"""Self-check: a course with no data (catalog has_full_kb false, e.g. Kids Bollywood Dance) is never sold in chat.
No slots / invented defaults, a short redirect-to-app prompt, links once, no sales follow-ups. No AI calls.
Run: python scripts/check_app_only_course.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.core import kb_loader
from src.core.conversation_handler import _should_allow_app_links
from src.core.helpers.slot_engine import evaluate_enrollment_slots, slot_question
from src.core.intent_classifier import apply_extraction_to_state
from src.core.pricing_engine import apply_week_pass
from src.core.prompt import format_system_prompt
from src.services.followup_service import get_followup_decision

KIDS = {"course": "kids_bollywood_dance", "language": "Hinglish (Latin script)"}
assert kb_loader.is_app_only("kids_bollywood_dance") and not kb_loader.is_app_only("kathak")

# No slots, nothing to ask, no invented "Monday to Friday", never complete (so no QR).
ev = evaluate_enrollment_slots(KIDS)
assert ev["next_target_slot"] == "APP" and not ev["is_complete"] and ev["days"] is None and slot_question(ev) is None, ev
assert evaluate_enrollment_slots({"course": "kathak"})["next_target_slot"] == "TIMING"          # full courses unchanged

# A timing / package the customer names is not saved for this course; a 1-week-pass wish is not sold either.
s = apply_extraction_to_state(dict(KIDS), {"customer_intent": "TIMING_SELECTION", "selected_timing": "4:00 PM to 5:00 PM",
                                           "selected_package": "1 Month", "timing_resolution_status": "SELECTED"})
assert not s.get("timing") and not s.get("package"), (s.get("timing"), s.get("package"))
s = dict(KIDS)
apply_week_pass(s, {"wants_pass": True})
assert not s.get("package"), s.get("package")

# Redirect prompt: only the catalog facts, no sales funnel; first time vs already redirected.
first = format_system_prompt(dict(KIDS))
assert "Kids Bollywood Dance" in first and "Fun, high-energy" in first and "First redirect" in first
for sales in ("SALES OBJECTIVE", "mostly subah free", "Monday to Friday\n", "Total Payable", "kaunsa package"):
    assert sales not in first, sales
assert "ALREADY" in format_system_prompt({**KIDS, "app_links_sent": True})
assert "SALES OBJECTIVE" in format_system_prompt({"course": "kathak"})                          # full courses unchanged

# Links go out automatically once, then not again unless asked.
lt = {"intent": "QUESTION"}
assert _should_allow_app_links(text="fees kitni hai", state=dict(KIDS), last_turn=lt, history=[]) is True
assert _should_allow_app_links(text="fees kitni hai", state={**KIDS, "app_links_sent": True}, last_turn=lt, history=[]) is False

# No sales follow-ups for this course; full courses still get theirs.
d = get_followup_decision(phone="9", count=0, course_id="kids_bollywood_dance", course_name="Kids Bollywood Dance", lead_at_qr=False)
assert d.action == "SKIP", d
assert get_followup_decision(phone="9", count=0, course_id="kathak", course_name="Kathak", lead_at_qr=False).action != "SKIP"

print("OK: app-only courses redirect to the app once, never offer slots / fees / QR, and get no sales follow-ups")
