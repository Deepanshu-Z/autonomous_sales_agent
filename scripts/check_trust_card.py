"""Self-check: "About Sensationz" card goes ONCE per lead, right before the first QR; QR reminders go once per QR.
No WhatsApp / Razorpay calls are made.
Run: python scripts/check_trust_card.py
"""
import asyncio
import copy
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.core import conversation_handler as ch, kb_loader, trust_proof
import src.services.interakt as interakt
import src.services.qr_lead_summary as qls
import src.services.razorpay_service as rz
from src.services.followup_service import get_followup_decision

# ── 1. Card content (all from data, in the customer's language) ─────────────────────────────
for lang, words in {"Hinglish (Latin script)": "2007 se", "English": "Since 2007", "Hindi (Devanagari)": "2007 से"}.items():
    card = trust_proof.build_trust_card(lang, short=True)
    for must in (words, "4.6/5", "1,366", "Razorpay", "North Ex Mall", "07AALCS9864Q1Z4"):
        assert must in card, (lang, must, card)
    assert len(re.findall(r"https?://", card)) == 1, card  # one link: the QR stays the focus
assert len(re.findall(r"https?://", trust_proof.build_trust_card("English"))) > 3  # full card: every way to verify

real_rules = kb_loader.get_global_rules()
no_rating = copy.deepcopy(real_rules)
no_rating["organization"]["trust_proof"]["google_rating"] = None
trust_proof.kb_loader.get_global_rules = lambda: no_rating
assert "rating" not in trust_proof.build_trust_card("English", short=True).lower()  # missing number -> line hidden
trust_proof.kb_loader.get_global_rules = lambda: real_rules

# ── 2. Dispatch: card once, before the first QR ─────────────────────────────────────────────
sent = []


async def fake_text(phone, text, *a, **k):
    sent.append("CARD" if "Sensationz" in text and "GSTIN" in text else "TEXT")


async def fake_image(phone, url, caption=""):
    sent.append("QR")
    return True


async def fake_qr(**k):
    return {"id": f"qr_test{len(sent)}", "image_url": "https://rzp.io/rzp/test"}


async def noop(*a, **k):
    return None


async def no_sleep(*a, **k):
    return None


ch.send_text_message_async, ch.save_message_async, ch.log_message_async = fake_text, noop, noop
interakt.send_image_message_async, rz.create_dynamic_qr_code_async = fake_image, fake_qr
qls.schedule_qr_lead_summary = lambda *a, **k: None
ch.asyncio.sleep = no_sleep

COMPLETE = {"course": "kathak", "timing": "4:00 PM to 5:00 PM", "selected_timing": "4:00 PM to 5:00 PM",
            "teacher": "Ms. Madhuri", "selected_teacher": "Ms. Madhuri", "days": "Tuesday/Thursday",
            "selected_days": "Tuesday/Thursday", "days_code": "TT", "package": "3 Months",
            "selected_duration": "3 Months", "language": "Hinglish (Latin script)"}


def dispatch(state, **k):
    sent.clear()
    asyncio.run(ch.dispatch_payment_qr_async("919999000111", state, "kathak", **k))
    return list(sent)


state = dict(COMPLETE)
assert dispatch(state) == ["CARD", "QR"], sent                      # first QR: card, then QR (QR last on screen)
assert state.get("trust_card_sent_at")
assert dispatch(state, force_resend=True) == ["QR"], sent           # QR resent: no second card
assert dispatch({**COMPLETE, "trust_card_sent_at": 1.0}) == ["QR"]  # already saw the card (asked earlier): no repeat

off = copy.deepcopy(real_rules)
off["organization"]["trust_proof"]["send_with_first_qr"] = False
ch.kb_loader.get_global_rules = lambda: off
assert dispatch(dict(COMPLETE)) == ["QR"], sent                     # switch off: QR only
ch.kb_loader.get_global_rules = lambda: real_rules

# ── 3. QR reminders: once per QR even though a customer reply resets count to 0 ────────────


def nxt(sent_ids, qr_id, count=0):
    d = get_followup_decision(phone="9", count=count, course_id="kathak", course_name="kathak",
                              lead_at_qr=True, sent_ids=sent_ids, qr_id=qr_id)
    return d.message_id if d.action == "SEND_MESSAGE" else "SKIP", d.updated_sent_ids


ids = []
order = []
for _ in range(4):                       # count stays 0 every time: the customer keeps replying
    step, ids = nxt(ids, "qr_A")
    order.append(step)
assert order == ["qr_followup_5m", "qr_followup_30m", "qr_followup_2h", "SKIP"], order
assert nxt(ids, "qr_B")[0] == "qr_followup_5m"   # a NEW QR gets its own reminders

print("OK: card once before the first QR (all languages, from data); QR reminders once per QR")
