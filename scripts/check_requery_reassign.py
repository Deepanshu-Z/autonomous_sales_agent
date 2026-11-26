"""Self-check: an ad re-inquiry moves a chat held by a sales agent back to the Supreme Owner.
Run: python scripts/check_requery_reassign.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.workers import tasks

PHONE = "919999999999"
AD = {"ad_id": "120251036740780010"}


async def run(state, text, referral, escalated):
    assigned = []
    saved = dict(state)

    async def get_state(_):
        return dict(saved)

    async def save_state(_, s):
        saved.clear()
        saved.update(s)

    async def assign(_, email):
        assigned.append(email)
        return True

    async def noop(*a, **k):
        return None

    async def is_esc(_):
        return escalated

    async def history(_):
        return []

    tasks.get_user_state_async = get_state
    tasks.save_user_state_async = save_state
    tasks.assign_chat_to_agent_async = assign
    tasks.is_escalated_async = is_esc
    tasks.get_recent_history_async = history
    tasks.handle_ai_reply_async = noop
    tasks.save_message_async = noop
    tasks.log_message_async = noop
    tasks.check_target_ad = lambda *a, **k: (True, "test")
    tasks.log_lead_action = lambda **k: None
    import src.state.chat_state as cs
    cs.clear_escalation_async = noop

    await tasks._execute_pipeline_async(PHONE, text, referral)
    return assigned, saved


async def main():
    owner = tasks.OWNER_EMAIL
    agent_state = {"already_assigned": True, "assigned_agent_email": "agent@x.com", "is_escalated": True}

    # Escalated lead with a sales agent clicks the ad again -> back to owner
    assigned, saved = await run(agent_state, "Can I get more info?", AD, escalated=True)
    assert assigned == [owner], assigned
    assert saved["assigned_agent_email"] == owner, saved

    # Not escalated, but still held by a sales agent, new ad click -> back to owner
    assigned, _ = await run({**agent_state, "is_escalated": False}, "hi", AD, escalated=False)
    assert assigned == [owner], assigned

    # Normal follow-up message (no ad) from an agent-held lead -> untouched
    assigned, _ = await run({**agent_state, "is_escalated": False}, "what is the fee", None, escalated=False)
    assert assigned == [], assigned

    # Already with owner -> no redundant Interakt call
    assigned, _ = await run({"already_assigned": True, "assigned_agent_email": owner}, "hi", AD, escalated=False)
    assert assigned == [], assigned

    # Brand-new lead -> owner (unchanged behaviour)
    assigned, _ = await run({}, "hi", AD, escalated=False)
    assert assigned == [owner], assigned

    print("OK: ad re-inquiry reassigns to Supreme Owner")


asyncio.run(main())
