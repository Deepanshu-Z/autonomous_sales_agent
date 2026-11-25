"""
chat_cli.py — WhatsApp-in-the-terminal for the Sensationz sales bot.

Runs the REAL conversation pipeline (intent classifier -> state -> prompt -> reply -> QR dispatch ->
payment fulfilment -> Hono staging payload) for one fake customer, and prints what WhatsApp would show:
text bubbles, QR images (with caption), videos, agent handoffs.

Nothing leaves your machine except the OpenAI calls (the AI is real, so it costs tokens):
  • WhatsApp / Interakt sends ........ printed here instead of sent
  • Customer state + chat history .... in memory only (Redis / Supabase are NOT touched)
  • Hono backend staging ............. payload printed, never POSTed
  • Razorpay QR ...................... simulated by default (pass --real-qr to create real Razorpay QRs)

Usage:
    python chat_cli.py                      # fresh fake customer 919999000001
    python chat_cli.py --phone 919876543210
    python chat_cli.py --real-qr            # create REAL Razorpay QRs (uses your .env keys!)

In-chat commands:
    pay [email]   simulate the customer paying the latest QR (Razorpay webhook) -> prints Hono payload
    state         show everything the bot remembers about this customer
    reset         brand-new customer (wipes memory + history)
    exit / q      quit
"""

import argparse
import asyncio
import copy
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv()

ap = argparse.ArgumentParser(add_help=True)
ap.add_argument("--phone", default="919999000001", help="fake customer number (default 919999000001)")
ap.add_argument("--real-qr", action="store_true", help="create REAL Razorpay QR codes (default: simulated)")
ARGS = ap.parse_args()
PHONE = "".join(c for c in ARGS.phone if c.isdigit())

# ── count real AI calls (cost visibility) ───────────────────────────────────────
from langchain_openai import ChatOpenAI

LLM_CALLS = {"n": 0}
_orig_ainvoke = ChatOpenAI.ainvoke


async def _counted(self, *a, **k):
    LLM_CALLS["n"] += 1
    return await _orig_ainvoke(self, *a, **k)


ChatOpenAI.ainvoke = _counted

# ── import the real pipeline BEFORE patching so every module is loaded ──────────
import src.state.chat_state as cs
import src.core.conversation_handler as ch
import src.core.intent_classifier  # noqa: F401
import src.services.razorpay_service as rz
import src.services.hono_staging_client as hsc
import src.services.interakt  # noqa: F401
import src.services.payment_ledger  # noqa: F401
from src.core.pricing_engine import WEEK_PASS  # noqa: F401


# ── in-memory stand-ins (no Redis / Supabase writes) ────────────────────────────
class _Pipe:
    def __init__(s, r): s.r = r
    def setex(s, k, t, v): s.r.d[k] = v; return s
    def execute(s): return []
    def __getattr__(s, n): return lambda *a, **k: s


class _Redis:
    def __init__(s): s.d = {}
    def get(s, k): return s.d.get(k)
    def set(s, k, v, *a, **kw): s.d[k] = v; return True
    def setex(s, k, t, v): s.d[k] = v; return True
    def mget(s, ks): return [s.d.get(k) for k in ks]
    def delete(s, *ks): [s.d.pop(k, None) for k in ks]; return 1
    def pipeline(s, *a, **k): return _Pipe(s)
    def __getattr__(s, n): return lambda *a, **k: None


cs.redis_conn = _Redis()
cs.supabase = None

STORE = {}      # phone -> state
OUT = []        # outbound WhatsApp events for the current turn
QRS = []        # QR creations (kwargs) — newest last
STAGED = []     # bodies that would have been POSTed to Hono
HISTORY = []    # chat history handed to the handler


def _fresh_state():
    st = cs.get_default_state(PHONE)
    st["is_target_ad"] = True
    return st


async def _get_state(phone): return copy.deepcopy(STORE.get(phone) or _fresh_state())
async def _save_state(phone, state, *a, **k): STORE[phone] = copy.deepcopy(state)
async def _reset_state(phone, *a, **k): STORE.pop(phone, None)
async def _text(phone, msg, *a, **k): OUT.append(("text", msg)); return True
async def _image(phone, url, caption="", *a, **k): OUT.append(("image", url, caption)); return True
async def _video(phone, url, caption="", *a, **k): OUT.append(("video", url, caption)); return True
async def _noop(*a, **k): return None


async def _handoff(phone, *a, **k):
    OUT.append(("handoff", k.get("escalation_reason")))


async def _fake_qr(**kw):
    n = len(QRS) + 1
    QRS.append(dict(kw, _simulated=True))
    return {"id": f"qr_SIM{n:03d}", "image_url": f"https://rzp.io/rzp/SIM{n:03d}", "amount": kw.get("amount")}


_real_create_qr = rz.create_dynamic_qr_code_async


async def _recording_real_qr(**kw):
    res = await _real_create_qr(**kw)
    QRS.append(dict(kw, _simulated=False, _qr_id=(res or {}).get("id")))
    return res


class _Resp:
    status_code = 200
    def json(self): return {"data": {"action": "STAGED", "staged_id": "cli-sim"}, "message": "ok"}


class _FakeHono:
    async def post(self, url, headers=None, json=None, timeout=None):
        STAGED.append(json); return _Resp()


def _trigger_staging(payload, phone):
    asyncio.get_event_loop().create_task(hsc.stage_ai_enrollment_async(**payload))


def install_patches():
    names = {
        "get_user_state_async": _get_state, "save_user_state_async": _save_state,
        "reset_user_state_async": _reset_state, "clear_escalation_async": _noop, "mark_escalated": _noop,
        "send_text_message_async": _text, "send_image_message_async": _image, "send_video_message_async": _video,
        "save_message_async": _noop, "log_message_async": _noop, "execute_agent_handoff_async": _handoff,
        "schedule_payment_record": lambda *a, **k: None, "schedule_email_attach": lambda *a, **k: None,
        "log_lead_action": lambda *a, **k: None,
    }
    for mod in list(sys.modules.values()):
        if mod and getattr(mod, "__name__", "").startswith("src."):
            for n, f in names.items():
                if hasattr(mod, n):
                    setattr(mod, n, f)
    rz.create_dynamic_qr_code_async = _recording_real_qr if ARGS.real_qr else _fake_qr
    hsc.get_hono_client = lambda: _FakeHono()
    hsc.AUTOMATION_SECRET = "cli-sim"
    hsc.trigger_async_staging = _trigger_staging


install_patches()

# Must run AFTER the imports above: project modules call load_dotenv() when imported and would put it back.
if not ARGS.real_qr:
    os.environ.pop("TEST_QR_AMOUNT", None)  # .env forces every QR to Rs 1; show the real amounts instead

# ── terminal rendering ──────────────────────────────────────────────────────────
W = 68


def _bubble(who, text):
    print(f"\n{who}")
    for line in str(text).split("\n"):
        print("   " + line)


def render(events):
    for ev in events:
        kind = ev[0]
        if kind == "text":
            _bubble("🤖 Sensationz", ev[1])
        elif kind == "image":
            _bubble("🖼️  Sensationz sent an IMAGE (QR code)", f"{ev[1]}\n{(ev[2] or '').strip()}")
        elif kind == "video":
            _bubble("🎬 Sensationz sent a VIDEO", f"{ev[1]}\n{(ev[2] or '').strip()}")
        elif kind == "handoff":
            _bubble("🙋 Handed over to a human agent", ev[1] or "")


STATE_KEYS = ("course", "stage", "timing", "teacher", "days", "package", "fee", "payment_status", "student_email")


def status_line():
    st = STORE.get(PHONE, {})
    bits = [f"{k}={st.get(k)}" for k in STATE_KEYS if st.get(k) not in (None, "", False)]
    return "   📦 " + " | ".join(bits) if bits else "   📦 (new customer)"


def print_qr_info(new_qrs):
    for q in new_qrs:
        sim = "SIMULATED — not scannable" if q.get("_simulated") else "REAL Razorpay QR"
        print(f"\n💳 QR created ({sim}): {q.get('duration')} — ₹{q.get('amount')} | "
              f"{q.get('timing')} | {q.get('teacher')} | {q.get('days')}")


async def customer_turn(text):
    OUT.clear()
    q0, calls0, t0 = len(QRS), LLM_CALLS["n"], time.perf_counter()
    try:
        await ch._handle_conversation_turn_unlocked(PHONE, text, HISTORY)
    except Exception as e:  # surface, never hide
        import traceback
        print(f"\n💥 ERROR in bot pipeline: {type(e).__name__}: {e}")
        print(traceback.format_exc()[-900:])
        return
    texts = [o[1] for o in OUT if o[0] == "text"]
    HISTORY.append({"role": "user", "content": text, "message": text})
    for t in texts:
        HISTORY.append({"role": "assistant", "content": t, "message": t})
    render(OUT)
    print_qr_info(QRS[q0:])
    print(f"\n{status_line()}")
    print(f"   ⚡ {time.perf_counter() - t0:.1f}s | AI calls this message: {LLM_CALLS['n'] - calls0} "
          f"(total {LLM_CALLS['n']})")


async def simulate_payment(email):
    if not QRS:
        print("⚠️  No QR has been created yet — chat until the bot sends a QR, then type: pay")
        return
    q = QRS[-1]
    amount = float(q.get("amount") or 0)
    seq = len(QRS)
    qr_id = q.get("_qr_id") or f"qr_SIM{seq:03d}"
    event = {"event": "payment.captured", "payload": {"payment": {"entity": {
        "id": f"pay_CLI{seq:03d}{int(time.time()) % 100000}", "status": "captured", "amount": int(round(amount * 100)),
        "method": "upi", "email": email, "contact": "+" + PHONE, "order_id": qr_id,
        "notes": {"source": "whatsapp_sales_agent", "phone": PHONE, "course_id": q.get("course_id"),
                  "duration": q.get("duration"), "timing": q.get("timing"), "teacher": q.get("teacher"),
                  "days": q.get("days"), "qr_id": qr_id}}}}}
    OUT.clear()
    STAGED.clear()
    print(f"\n💰 Simulating Razorpay payment: ₹{amount:g} for '{q.get('duration')}' by {email} ...")
    try:
        res = await rz.fulfill_razorpay_payment_async(event, send_notification=True, ledger_source=None)
    except Exception as e:
        import traceback
        print(f"💥 payment fulfilment error: {type(e).__name__}: {e}\n{traceback.format_exc()[-700:]}")
        return
    await asyncio.sleep(0.6)  # let the background staging task finish
    render(OUT)
    print(f"\n✅ fulfilment result: {json.dumps({k: res.get(k) for k in ('status', 'duration', 'amount_inr', 'stage')}, default=str)}")
    if STAGED:
        print("📤 Payload that WOULD be sent to the Hono backend (not actually sent):")
        print(json.dumps(STAGED[-1], indent=2, ensure_ascii=False))
    else:
        print("ℹ️  Nothing was staged to Hono (no valid email on the payment).")
    print(f"\n{status_line()}")


def show_state():
    st = STORE.get(PHONE)
    if not st:
        print("(no state yet — say hi first)")
        return
    print("\n📊 What the bot remembers:")
    for k in ("course", "stage", "language", "timing", "teacher", "days", "package", "fee", "payment_status",
              "student_email", "pass_interest", "last_qr_package", "last_qr_amount", "qr_ids_sent",
              "staged_enrollment", "is_escalated"):
        print(f"   • {k}: {st.get(k)}")


def banner():
    print("=" * W)
    print("  📱 SENSATIONZ — WHATSAPP IN THE TERMINAL (real bot pipeline)")
    print("=" * W)
    print(f"  Fake customer: +{PHONE}   |   QR mode: {'REAL Razorpay QR' if ARGS.real_qr else 'simulated (safe)'}")
    print("  Memory & history: in-memory only   |   Hono: payload printed, never sent")
    print("  Commands: pay [email] · state · reset · exit")
    if ARGS.real_qr and os.getenv("TEST_QR_AMOUNT"):
        print(f"  ⚠️  TEST_QR_AMOUNT={os.getenv('TEST_QR_AMOUNT')} is set in .env → real QRs will be that amount, not the package price")
    if ARGS.real_qr and str(os.getenv("RAZORPAY_KEY_ID", "")).startswith("rzp_live"):
        print("  ⚠️  Razorpay key is LIVE: any QR you pay with a real UPI app is real money")
    print("=" * W)


async def main():
    banner()
    while True:
        try:
            raw = (await asyncio.to_thread(input, "\n👤 You: ")).strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye!")
            return
        if not raw:
            continue
        low = raw.lower()
        if low in ("exit", "quit", "q"):
            print("Bye!")
            return
        if low == "reset":
            STORE.pop(PHONE, None)
            HISTORY.clear()
            QRS.clear()
            STAGED.clear()
            print("🔄 Brand-new customer. (AI call counter keeps running.)")
            continue
        if low == "state":
            show_state()
            continue
        if low == "pay" or low.startswith("pay "):
            email = raw[4:].strip() or "test.student@gmail.com"
            await simulate_payment(email)
            continue
        await customer_turn(raw)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBye!")
