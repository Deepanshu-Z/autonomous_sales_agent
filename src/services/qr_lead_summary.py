"""
qr_lead_summary.py — one-paragraph LLM summary of a lead's chat, taken each time a NEW QR is sent.

Called fire-and-forget from conversation_handler.dispatch_payment_qr_async (once per created QR, never per
message). Never blocks or breaks the QR flow: every failure is logged and swallowed. One row per lead in
`lead_summaries` (key = last 10 digits of the phone); each new QR replaces it with the up-to-date summary.

Manual check (prints, writes nothing):  venv\\Scripts\\python -m src.services.qr_lead_summary <phone>
"""
import asyncio
import logging
import os
import re
import sys
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from src.services.supabase_client import supabase
from src.state.chat_state import get_phone_variants

logger = logging.getLogger(__name__)

MODEL = os.getenv("OPENAI_EXTRACTOR_MODEL") or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
MAX_MSGS = 40  # last N chat messages fed to the LLM
TIMEOUT_SEC = 60
_llm = None
_tasks: set = set()  # strong refs so fire-and-forget tasks aren't garbage-collected

SYSTEM = """You summarise a WhatsApp sales chat between the Sensationz bot and a lead who was just sent a payment QR.
Write ONE concise paragraph (3-5 sentences, under 90 words, plain English, no bullets, no phone number) covering:
what course/batch/package they wanted, their questions or objections (price, timing, trust, family approval...),
what they last said and how interested they seem, and the best next step for a human to close them.
Chat is Hinglish/English; bot template messages (welcome, offer cards) are noise. State only what the chat supports.
Never suggest free trial classes as the next step."""


def _key(phone: str) -> str:
    return re.sub(r"\D", "", str(phone or ""))[-10:]


def _chat(phone: str) -> list:
    return (supabase.table("chat_history").select("role,message,created_at")
            .in_("phone", get_phone_variants(phone)).order("created_at").execute().data or [])


async def summarise(facts: dict, msgs: list) -> str:
    global _llm
    _llm = _llm or ChatOpenAI(model=MODEL, api_key=os.getenv("OPENAI_API_KEY"), temperature=0.2, max_retries=2)
    lines = [f"{'Lead' if m['role'] == 'user' else 'Bot'}: {(m['message'] or '')[:300]}" for m in msgs[-MAX_MSGS:]]
    known = ", ".join(f"{k}={v}" for k, v in facts.items() if v)
    resp = await _llm.ainvoke([SystemMessage(content=SYSTEM),
                               HumanMessage(content=f"Known facts: {known}\n\nChat (oldest first):\n" + "\n".join(lines))])
    return resp.content.strip()


async def _run(phone: str, facts: dict) -> None:
    try:
        msgs = await asyncio.to_thread(_chat, phone)
        if not any(m["role"] == "user" for m in msgs):
            return
        text = await asyncio.wait_for(summarise(facts, msgs), TIMEOUT_SEC)
        row = {"phone": _key(phone), "summary": text, "last_msg_at": msgs[-1]["created_at"], "msg_count": len(msgs),
               "model": MODEL, "updated_at": datetime.now(timezone.utc).isoformat()}
        # Each new QR replaces the summary, so it always reflects the chat up to the latest QR
        await asyncio.to_thread(lambda: supabase.table("lead_summaries")
                                .upsert(row, on_conflict="phone").execute())
    except Exception as e:
        logger.warning(f"[qr_lead_summary] failed for {phone} (non-fatal): {e}")


def schedule_qr_lead_summary(phone: str, state: dict) -> None:
    """Fire-and-forget. Call once per QR, right after it is delivered."""
    if supabase is None:
        return
    facts = {k: state.get(k) for k in ("course", "teacher", "timing", "package", "payable_amount", "stage")}
    t = asyncio.create_task(_run(phone, facts))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    from src.state.chat_state import get_user_state
    sys.stdout.reconfigure(encoding="utf-8")
    p = sys.argv[1]
    st = get_user_state(p) or {}
    print(asyncio.run(summarise({k: st.get(k) for k in ("course", "teacher", "timing", "package", "payable_amount", "stage")}, _chat(p))))
