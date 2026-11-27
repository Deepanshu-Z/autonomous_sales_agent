"""Self-check: a 1-on-1 / private session request gets the Customer Support numbers and stops the bot + follow-ups.
Part 1 runs handle_incoming_message with the AI stubbed (no OpenAI / WhatsApp calls).
Part 2 asks the REAL extractor (OpenAI, < $0.01) about Hinglish / Hindi / English samples, positive and negative.
Run: python scripts/check_one_on_one.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.core import conversation_handler as ch, intent_classifier as ic
from src.core.kb_loader import format_support_executives_display

real_extract = ic.extract_intent_and_state_changes
STATE = {"phone": "919999000333", "course": "kathak", "language": "Hinglish (Latin script)"}


# ── Part 1: handler wiring ────────────────────────────────────────────────────
async def fake_classifier(text, state=None, history=None):
    return {**ic._empty_flags(), "detected_language": state["language"], "wants_one_on_one": True}


class NoLLM:
    async def ainvoke(self, messages):
        raise AssertionError("reply LLM must be skipped for a 1-on-1 request")


ic.extract_intent_and_state_changes = fake_classifier
ch._get_reply_llm = lambda: NoLLM()

numbers = format_support_executives_display()
for lang, word in (("Hinglish (Latin script)", "hamari"), ("English", "our")):
    out = asyncio.run(ch.handle_incoming_message("1-on-1 class milegi?", {**STATE, "language": lang}, []))
    st = out["new_state"]
    assert numbers in out["reply"] and word in out["reply"], (lang, out["reply"])
    assert st["is_escalated"] is True and st["next_followup_due_at"] is None, st
    assert st["_last_turn"]["wants_one_on_one"] is True  # the turn handler stops the bot on this flag
print("OK part 1: support numbers sent, reply LLM skipped, bot + follow-ups stopped")

# ── Part 2: real extractor understands the request by meaning ─────────────────
ic.extract_intent_and_state_changes = real_extract
if not (os.getenv("OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY")):
    sys.exit("SKIP part 2: no OPENAI_API_KEY")

SAMPLES = {
    "1-on-1 class milegi kya?": True,
    "Do you have one to one sessions?": True,
    "mujhe sirf mere liye alag personal class chahiye": True,
    "मुझे पर्सनल क्लास चाहिए": True,
    "private tuition available hai?": True,
    "1 to 1 kathak sikhna hai": True,
    "batch mein kitne students hote hain?": False,
    "teacher personally dhyan degi kya group class mein?": False,
    "1-2 baje wala batch hai kya?": False,
    "1 month ka package kitne ka hai": False,
    "evening batch timing kya hai": False,
}


async def run_all():
    return await asyncio.gather(*(real_extract(text, state=dict(STATE), history=[]) for text in SAMPLES))


wrong = [(text, want) for (text, want), flags in zip(SAMPLES.items(), asyncio.run(run_all()))
         if bool(flags.get("wants_one_on_one")) != want]
for text, want in wrong:
    print(f"  WRONG: {text!r} -> expected wants_one_on_one={want}")
assert not wrong, f"{len(wrong)}/{len(SAMPLES)} samples misread"
print(f"OK part 2: extractor read all {len(SAMPLES)} samples correctly")
