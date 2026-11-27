"""Self-check: switching course never loses a lead's unpaid choices; switching back restores them.
Real case 919117376261: Kathak 4-5 PM, Wed/Fri, Ms. Madhuri, 6 Months, QR sent -> asked about Bollywood -> all wiped.
Runs the real handle_incoming_message with both AI calls stubbed (no OpenAI / WhatsApp calls).
Run: python scripts/check_course_switch.py
"""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.core import conversation_handler as ch, intent_classifier as ic

NEXT_FLAGS = {}


async def fake_classifier(text, state=None, history=None):
    return {**ic._empty_flags(), "detected_language": "Hinglish (Latin script)", **NEXT_FLAGS}


class FakeLLM:
    async def ainvoke(self, messages):
        return SimpleNamespace(content='{"reply": "ok", "followup": null}')


ic.extract_intent_and_state_changes = fake_classifier
ch._get_reply_llm = lambda: FakeLLM()


def turn(state, **flags):
    NEXT_FLAGS.clear()
    NEXT_FLAGS.update(flags)
    return asyncio.run(ch.handle_incoming_message("msg", dict(state), []))["new_state"]


KATHAK_AT_QR = {"phone": "919999000222", "course": "kathak", "timing": "4:00 PM to 5:00 PM",
                "selected_timing": "4:00 PM to 5:00 PM", "teacher": "Ms. Madhuri", "selected_teacher": "Ms. Madhuri",
                "days": "Wednesday/Friday", "selected_days": "Wednesday/Friday", "days_code": "WF",
                "package": "6 Months", "selected_duration": "6 Months", "fee": 2950, "stage": "PAYMENT_QR_SENT",
                "qr_id": "qr_test", "language": "Hinglish (Latin script)"}

# Her exact turn: a QUESTION naming another course keeps Kathak and every choice (the classifier still says ladies_dance).
asked = turn(KATHAK_AT_QR, customer_intent="QUESTION", selected_course="ladies_dance")
assert asked["course"] == "kathak" and asked["timing"] == "4:00 PM to 5:00 PM" and asked["package"] == "6 Months", asked
# A lead with no choices yet still switches on a question ("yoga ki timing?" from a Kathak-ad lead).
assert turn({"phone": "919999000223", "course": "kathak"}, customer_intent="QUESTION", selected_course="yoga")["course"] == "yoga"

# Real switch to Ladies Dance: the new course starts clean, Kathak's choices are kept aside.
dance = turn(KATHAK_AT_QR, customer_intent="COURSE", selected_course="ladies_dance")
assert dance["course"] == "ladies_dance" and not dance.get("timing") and not dance.get("package"), dance
assert dance["course_selections"]["kathak"]["selected_timing"] == "4:00 PM to 5:00 PM"

# Back to Kathak: every choice and the QR stage come back exactly, even when the classifier echoes an old slot
# (live replay: "nahi kathak hi theek hai" came with selected_package "6 Months").
for echo in ({}, {"selected_package": "6 Months"}):
    back = turn(dance, customer_intent="COURSE_SELECTION", selected_course="kathak", **echo)
    for k in ("timing", "selected_teacher", "days", "days_code", "package", "fee", "stage"):
        assert back.get(k) == KATHAK_AT_QR[k], (echo, k, back.get(k))

print("OK: course switch keeps unpaid choices per course and restores them on return")
