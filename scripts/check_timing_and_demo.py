"""Self-check for two live bugs, using the exact classifier outputs from their failing runs (AI stubbed, no API calls):
- 917018156374: "Get ₹ 15" (a price) was read as 3 PM -> "unavailable timing" -> saved Yoga batch wiped after the QR.
- 916304721630: "need demo class" was read as a 1-on-1 request -> support numbers + bot stopped.
Run: python scripts/check_timing_and_demo.py
"""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.core import intent_classifier as ic
from src.core.prompt import format_system_prompt

NEXT = {}


class FakeLLM:
    async def ainvoke(self, messages):
        return SimpleNamespace(content=json.dumps(NEXT))


ic._get_classifier_llm = lambda: FakeLLM()


def classify(text, state, **model_out):
    NEXT.clear()
    NEXT.update({"detected_language": "English", **model_out})
    flags = asyncio.run(ic.extract_intent_and_state_changes(text, dict(state), []))
    flags["_raw_text"] = text
    return flags, ic.apply_extraction_to_state(dict(state), flags)


YOGA_AT_QR = {"course": "yoga", "timing": "5:00 PM to 6:00 PM", "selected_timing": "5:00 PM to 6:00 PM",
              "timing_period": "Evening", "teacher": "Mradula", "selected_teacher": "Mradula", "package": "1 Month",
              "selected_duration": "1 Month", "stage": "PACKAGE_SELECTED", "qr_id": "qr_x", "language": "English"}
KEEP = ("timing", "selected_teacher", "package")

# 1. A price the model did not read as a time never becomes a timing (real failing output: QUESTION, no time fields).
flags, s = classify("Get ₹ 15", YOGA_AT_QR, customer_intent="QUESTION", question_summary="Inquiry about the ₹15 charge")
assert flags.get("timing_resolution_status") in (None, "NONE") and not flags.get("unoffered_timing_mentioned"), flags
assert all(s.get(k) == YOGA_AT_QR[k] for k in KEEP), {k: s.get(k) for k in KEEP}
# ...while a real time the model did read still resolves (applying it to state is then the teacher-conflict flow's job).
flags, _ = classify("6 to 7 pm", YOGA_AT_QR, customer_intent="TIMING_SELECTION", requested_start_24h="18:00",
                    requested_end_24h="19:00")
assert flags.get("selected_timing") == "6:00 PM to 7:00 PM" and flags.get("timing_resolution_status") == "SELECTED", flags

# 2. A time that exists in no batch never erases a saved batch; the reply is told the batch stays.
unavailable = {"customer_intent": "QUESTION", "timing_selection_attempted": True, "timing_resolution_status": "UNAVAILABLE",
               "unoffered_type": "TIMING", "unoffered_value": "3 baje", "unoffered_timing_mentioned": "3 baje"}
s = ic.apply_extraction_to_state(dict(YOGA_AT_QR), dict(unavailable))
assert all(s.get(k) == YOGA_AT_QR[k] for k in KEEP), {k: s.get(k) for k in KEEP}
assert "SAVED BATCH STAYS" in format_system_prompt(s)
# Without a saved batch, nothing changes: still nothing selected.
s = ic.apply_extraction_to_state({"course": "yoga"}, dict(unavailable))
assert not s.get("timing") and "Timing is NOT SELECTED" in format_system_prompt(s)

# 3. "need demo class" (real failing output: 1-on-1 + trial) is a demo request, not 1-on-1; real 1-on-1 still is.
flags, _ = classify("need demo class", {"course": "ladies_dance"}, customer_intent="ONE_ON_ONE_REQUEST",
                    wants_one_on_one=True, wants_trial=True)
assert not flags.get("wants_one_on_one") and flags.get("customer_intent") != "ONE_ON_ONE_REQUEST", flags
flags, _ = classify("personal class chahiye sirf mere liye", {"course": "ladies_dance"},
                    customer_intent="ONE_ON_ONE_REQUEST", wants_one_on_one=True)
assert flags.get("wants_one_on_one") is True
# After the pass was offered, the model repeats wants_trial: a real 1-on-1 request must still count (live replay).
flags, _ = classify("1 to 1 personal class milegi?", {"course": "ladies_dance", "pass_interest": True, "package": "1 Week Pass"},
                    customer_intent="ONE_ON_ONE_REQUEST", wants_one_on_one=True, wants_trial=True)
assert flags.get("wants_one_on_one") is True, flags

print("OK: prices never become timings, unavailable times never erase a saved batch, demo requests are not 1-on-1")
