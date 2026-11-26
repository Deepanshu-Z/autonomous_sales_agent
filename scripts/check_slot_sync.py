"""
Self-check: the AI's Message #2 and the timed follow-ups ask the SAME question for the SAME next slot,
and neither ever asks for something the lead already chose. Offline, no network.

    venv\\Scripts\\python scripts\\check_slot_sync.py
"""
import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.core.helpers.slot_engine import _ASKS, evaluate_enrollment_slots, followup_asks_wrong_slot, slot_question
from src.services.followup_service import get_followup_decision, get_slot_followup_message


def fake_eval(timing, teacher, days, package):
    """Shape of evaluate_enrollment_slots(), next slot chosen in the engine's own priority order."""
    missing = [n for n, v in (("Batch Timing", timing), ("Instructor Preference", teacher),
                              ("Batch Days (Tue/Thu, Wed/Fri)", days), ("Package Duration", package)) if not v]
    nxt = "TIMING" if not timing else "TEACHER" if not teacher else "DAYS" if not days else "DURATION" if not package else "PAYMENT"
    return {"next_target_slot": nxt, "missing_slots": missing,
            "timing_str": timing or "NOT SELECTED", "teacher_str": teacher or "NOT SELECTED", "package_str": package or "NOT SELECTED",
            "available_teachers_for_timing": ["Sneha Maurya", "Vibha Mittal"], "available_days": ["Tue/Thu", "Wed/Fri"],
            "settled_slots": ["Course: Kathak"] + [f"{k}: {v}" for k, v in (("Batch Timing", timing), ("Instructor", teacher),
                                                                           ("Batch Days", days), ("Package Duration", package)) if v]}


own = {"TIMING": "TIMING", "TEACHER": "TEACHER", "DAYS": "DAYS", "DURATION": "DURATION"}
n = 0
for lang in ("Hinglish", "English"):
    for timing, teacher, days, package in itertools.product((None, "6-7 PM"), (None, "Sneha"), (None, "Wed/Fri"), (None, "3 Months")):
        ev = fake_eval(timing, teacher, days, package)
        settled = {s for s, v in (("TIMING", timing), ("TEACHER", teacher), ("DAYS", days), ("DURATION", package)) if v}
        texts = [slot_question(ev, lang)] + [get_slot_followup_message(ev, c, lang) for c in range(5)]
        for t in filter(None, texts):
            n += 1
            for slot in settled:
                assert not _ASKS[slot].search(t), f"asks settled {slot}: {t!r} (state {timing},{teacher},{days},{package})"
            assert not followup_asks_wrong_slot(t, ev), f"guard would block our own question: {t!r}"
        q = slot_question(ev, lang)
        if ev["next_target_slot"] in own:  # the AI's Message #2 question is recognised as asking for exactly that slot
            assert q and _ASKS[own[ev["next_target_slot"]]].search(q), f"question does not ask {ev['next_target_slot']}: {q!r}"
        else:  # PAYMENT: nothing to ask; the timed follow-up offers the QR
            assert q is None and "QR" in get_slot_followup_message(ev, 0, lang)

# The real bad messages from the Kathak lead's chat are caught when that slot is already chosen.
ev = fake_eval("6-7 PM", "Sneha", "Wed/Fri", "3 Months")
for bad in ("Aap kaunsa package duration choose karna chahenge — 1 Month starter pack ya hamara 3 Months offer package? 😊",
            "Aapko Tue/Thu ya Wed/Fri kaunsa pair best lagega?", "Aap kaunsa batch timing select karna chahenge? 😊",
            "Aap inme se kaunse instructor ke saath seekhna chahenge — Sneha Maurya ya Vibha Mittal? 😊"):
    assert followup_asks_wrong_slot(bad, ev), bad
# ...but a statement mentioning the chosen slot is fine, and trial / paid flows are never touched.
assert not followup_asks_wrong_slot("Aapne *Wed/Fri* 6-7 PM select kiya hai, shukriya! 😊", ev)
assert not followup_asks_wrong_slot("Which batch timing suits you? 😊", {**ev, "next_target_slot": "TRIAL"})

# Real engine: Kathak lead with timing + package chosen but no instructor -> next slot is TEACHER, never package.
real = evaluate_enrollment_slots({"course": "kathak", "timing": "6:00 PM to 7:00 PM", "package": "3 Months"}, course_id="kathak")
assert real["next_target_slot"] == "TEACHER", real["next_target_slot"]
assert "instructor" in slot_question(real, "Hinglish").lower()

# Timed follow-up uses it end to end; a lead with only the course chosen still gets the normal templates.
d = get_followup_decision("x", 0, "kathak", "Kathak", False, slot_eval=real, language="Hinglish")
assert d.action == "SEND_MESSAGE" and "instructor" in d.message.lower() and "*Kathak*" in d.message, d.message
d = get_followup_decision("x", 0, "kathak", "Kathak", False, slot_eval=fake_eval(None, None, None, None), language="Hinglish")
assert d.message_id == "checkin_5m", d.message_id
d = get_followup_decision("x", 5, "kathak", "Kathak", False, slot_eval=ev, language="Hinglish")
assert not (d.message_id or "").startswith("slot_"), "final-offer step must stay the offer"
# Skipping ahead: Kathak 7 PM + Vibha chosen, batch days still open -> asking the package is wrong (real chat).
skip = evaluate_enrollment_slots({"course": "kathak", "timing": "7:00 PM to 8:00 PM", "selected_teacher": "Vibha Mittal"}, course_id="kathak")
assert skip["next_target_slot"] == "DAYS", skip["next_target_slot"]
assert followup_asks_wrong_slot("Kaunsa package duration aap choose karna chahenge — 1 Month, 3 Months, 6 Months, ya 1 Year? 😊", skip)
assert not followup_asks_wrong_slot(slot_question(skip, "Hinglish"), skip)

# Teacher grounding (real classifier output from a replay): customer typed "vibha", LLM said "Ms. Madhuri".
from src.core.intent_classifier import apply_extraction_to_state
chosen = {"course": "kathak", "stage": "TEACHER_SELECTED", "timing": "7:00 PM to 8:00 PM", "selected_timing": "7:00 PM to 8:00 PM",
          "teacher": "Vibha Mittal", "selected_teacher": "Vibha Mittal", "timing_period": "evening"}
llm = {"customer_intent": "TEACHER_SELECTION", "selected_timing": "7:00 PM to 8:00 PM", "selected_teacher": "Ms. Madhuri",
       "timing_selection_attempted": True, "timing_resolution_status": "SELECTED", "_raw_text": "vibha"}
out = apply_extraction_to_state(dict(chosen), dict(llm))
assert (out["selected_teacher"], out["timing"]) == ("Vibha Mittal", "7:00 PM to 8:00 PM"), (out["selected_teacher"], out["timing"])
# A non-teacher turn ("3") can't switch the teacher either...
out = apply_extraction_to_state(dict(chosen), {"customer_intent": "PACKAGE_SELECTION", "selected_teacher": "Ms. Madhuri",
                                               "selected_package": "3 Months", "_raw_text": "3"})
assert (out["selected_teacher"], out["timing"], out["package"]) == ("Vibha Mittal", "7:00 PM to 8:00 PM", "3 Months"), out
# ...but a real choice without a typed name ("pehli wali") and a typed switch both still work.
out = apply_extraction_to_state(dict(chosen), {"customer_intent": "TEACHER_SELECTION", "selected_teacher": "Kratika Gontiya", "_raw_text": "pehli wali"})
assert out["selected_teacher"] == "Kratika Gontiya", out["selected_teacher"]
out = apply_extraction_to_state(dict(chosen), {"customer_intent": "TEACHER_SELECTION", "selected_teacher": "Vibha Mittal", "_raw_text": "nahi sneha mam ke saath"})
assert out["selected_teacher"] == "Sneha Maurya", out["selected_teacher"]

print(f"OK - {n} messages checked, none asks a settled slot, AI and follow-ups ask the same question; teacher grounded in typed text")
