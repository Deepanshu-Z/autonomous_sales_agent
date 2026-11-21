"""
One-off repair for paid clients whose saved Gmail was mis-extracted by the old sanitizer
(none@gmail.com, account@gmail.com, koi@gmail.com, gmailid-xxx@gmail.com, ...).

For every PAID client it re-runs the NEW sanitizer over the client's own chat messages:
  UPDATE -> saved email is wrong and the client did type a valid one: relink to it
            (reuses /api/sync-email logic: saves state + re-stages the enrollment to Hono)
  CLEAR  -> saved email is wrong and the client never typed one: remove it and set
            awaiting_email_fulfillment, so the bot asks for the Gmail on their next message
Emails that came from Razorpay (never typed in chat) are left alone.

Dry run by default. MUST run ON THE SERVER after deploying the sanitizer fix: the server
reads state from its own Redis first, so writes made from another machine get overwritten.
    python scripts/fix_bad_student_emails.py           # preview
    python scripts/fix_bad_student_emails.py --apply   # write
"""
import os, re, sys, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from supabase import create_client
from src.core.helpers.email_sanitizer import sanitize_email

sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))


def plan():
    rows = sb.table("user_session_state").select("phone,last_topic").ilike("last_topic", "%PAY:PAID%").execute().data or []
    actions = []
    for r in rows:
        m = re.search(r"EMAIL:([^|]+)", r.get("last_topic") or "")
        if not m:
            continue
        stored = m.group(1).strip().lower()
        msgs = [(h["message"] or "") for h in (sb.table("chat_history").select("message").eq("phone", r["phone"])
                .eq("role", "user").order("created_at").execute().data or [])]
        typed = [e for e in (sanitize_email(x) for x in msgs) if e]
        latest = typed[-1] if typed else None
        local = stored.split("@")[0]
        stored_typed = any(stored in x.lower() for x in msgs)
        local_as_bare_word = any(re.search(rf"(?<![\w.@-]){re.escape(local)}(?![\w@])", x.lower()) for x in msgs)

        bad = (
            sanitize_email(stored) != stored                   # none@, gmailid-xxx@ ...
            or (latest and latest != stored)                   # client typed a different valid email
            or (not stored_typed and local_as_bare_word)       # 'account'/'koi' guessed from a sentence
        )
        if bad:
            actions.append((r["phone"], stored, ("UPDATE", latest) if latest else ("CLEAR", None)))
    return actions


async def apply(actions):
    from src.api.routes import sync_customer_email_endpoint, SyncEmailPayload
    from src.state.chat_state import phone_concurrency_lock, get_user_state_async, save_user_state_async
    from src.services.payment_ledger import attach_student_email_async
    for phone, stored, (action, new) in actions:
        if action == "UPDATE":
            res = await sync_customer_email_endpoint(SyncEmailPayload(phone=phone, email=new))
            print(f"  ..{phone[-4:]} UPDATE -> {new}: success={res.get('success')} staged={res.get('staged')}")
        else:
            async with phone_concurrency_lock(phone):
                st = await get_user_state_async(phone)
                st.update(student_email=None, staged_enrollment=False, awaiting_email_fulfillment=True)
                await save_user_state_async(phone, st)
            print(f"  ..{phone[-4:]} CLEARED (bot will ask for Gmail on next message)")
        # Awaited (not fire-and-forget): this short-lived script's loop would cancel background tasks.
        await attach_student_email_async(phone, new)


if __name__ == "__main__":
    acts = plan()
    print(f"{len(acts)} paid client(s) with a wrong saved email:")
    for phone, stored, (action, new) in acts:
        print(f"  ..{phone[-4:]}  {stored:45} -> {action} {new or ''}")
    if "--apply" in sys.argv and acts:
        asyncio.run(apply(acts))
    elif acts:
        print("\nDry run only. Re-run with --apply ON THE SERVER to write.")
