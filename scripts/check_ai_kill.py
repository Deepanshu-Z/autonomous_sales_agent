"""Self-check: a lead killed from the CRM Urgency page gets no AI reply and no follow-up, even when it sends
the tester commands (#bot, reset) or re-clicks an ad, until resume_ai(). Offline: fake Redis, no Supabase,
WhatsApp sends fail the check.
Run: python scripts/check_ai_kill.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.state import chat_state as cs
from src.workers import tasks


class FakeRedis:
    def __init__(self):
        self.kv, self.h = {}, {}

    def mget(self, keys): return [self.kv.get(k) for k in keys]
    def setex(self, k, ttl, v): self.kv[k] = v
    def pipeline(self): return self
    def execute(self): return []
    def hset(self, name, key, value): self.h.setdefault(name, {})[key] = value
    def hexists(self, name, key): return key in self.h.get(name, {})
    def hdel(self, name, key): self.h.get(name, {}).pop(key, None)
    def hgetall(self, name): return dict(self.h.get(name, {}))


async def _noop(*a, **k): return None


async def _no_send(*a, **k): raise AssertionError("a killed lead must not get any WhatsApp message")


cs.redis_conn, cs.supabase = FakeRedis(), None
tasks.check_target_ad = lambda *a, **k: (True, "test")
tasks.log_lead_action = lambda **k: None
tasks.save_message_async = tasks.log_message_async = _noop
tasks.get_recent_history_async = lambda *a, **k: _noop()
tasks.send_text_message_async = _no_send

PHONE = "+91 99990 00444"
cs.save_user_state("919999000444", {**cs.get_default_state("919999000444"), "course": "yoga", "next_followup_due_at": 1})

info = cs.kill_ai(PHONE, by="check", agent="agent@example.com")
st = cs.get_user_state("919999000444")
assert cs.is_ai_killed("wa_919999000444") and cs.is_ai_killed("9999000444"), "kill must match every phone format"
assert st["is_escalated"] is True and st["next_followup_due_at"] is None and st["assigned_agent_email"] == "agent@example.com", st
assert cs.get_killed_leads() == {"919999000444": info}

# Tester commands and a new ad click (which auto-unescalate a normal escalation) must not revive it.
for text, referral in (("#bot", None), ("reset", None), ("hi, more info?", {"ad_id": "120245204108950010"})):
    asyncio.run(tasks._execute_pipeline_async("919999000444", text, referral=referral))
    assert cs.is_ai_killed("919999000444"), text

cs.resume_ai("9999000444")
assert not cs.is_ai_killed("919999000444") and cs.get_user_state("919999000444")["is_escalated"] is False
print("OK: killed lead stays silent through #bot / reset / ad re-click, no follow-up armed; resume re-enables it")
