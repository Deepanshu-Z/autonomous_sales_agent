"""
Self-check: a lead promised an agent is always really assigned (fakes only: no Interakt / DB).
Run: venv\\Scripts\\python.exe scripts\\check_agent_handoff.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services import agent_handoff as ah
from src.services.interakt import ASSIGNED, AGENT_NOT_FOUND, CUSTOMER_NOT_FOUND, NOT_INTERAKT_CHANNEL, ASSIGN_FAILED

# 0. The 5 agents Interakt rejects (Nov 2026 logs) are out of the rotation
REJECTED = {"sensationzdance252@gmail.com", "sensationzperforming92180@gmail.com", "sensationzdance97@gmail.com",
            "dsensationz40@gmail.com", "sensationzarub@gmail.com"}
agents = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data", "agents.json"), encoding="utf-8"))
assert all(not a["active"] for a in agents if a["email"] in REJECTED)
assert not REJECTED & set(ah.AGENT_POOL), "rejected agents must not be in the live rotation"


# ── Fakes ─────────────────────────────────────────────────────────────────────
class FakeRedis:
    def __init__(self):
        self.store, self.counter = {}, 0

    def incr(self, key):
        self.counter += 1
        return self.counter

    def setex(self, key, ttl, value):
        self.store[key] = value

    def exists(self, key):
        return key in self.store


GOOD, GHOST, OTHER, OWNER = "good@x.com", "ghost@x.com", "other@x.com", "owner@x.com"
events: list = []
interakt_behaviour: dict = {}


async def fake_assign(phone, agent):
    events.append(("assign", agent))
    return interakt_behaviour.get(agent, ASSIGNED)


async def fake_send(phone, text):
    events.append(("send", text[:30]))


async def fake_mark(phone):
    events.append(("escalate", phone))


async def fake_clear(phone):
    events.append(("unescalate", phone))


async def fake_get_state(phone):
    return {"language": "English"}


async def fake_save(phone, state):
    events.append(("save", state.get("assigned_agent_email")))


async def noop(*a, **k):
    return None


def reset(pool, behaviour):
    ah.redis_conn = FakeRedis()
    ah.ALL_AGENTS = [{"email": e, "name": e} for e in pool]
    ah.AGENT_POOL = list(pool)
    ah.OWNER_EMAIL = OWNER
    interakt_behaviour.clear()
    interakt_behaviour.update(behaviour)
    events.clear()


ah.assign_chat_to_agent_detailed_async = fake_assign
ah.send_text_message_async, ah.mark_escalated_async, ah.clear_escalation_async = fake_send, fake_mark, fake_clear
ah.get_user_state_async, ah.save_user_state_async = fake_get_state, fake_save
ah.save_message_async, ah.log_message_async = noop, noop
import src.state.chat_state as chat_state
chat_state.is_protected_from_agent_assignment = lambda state: (False, "")
import src.core.lead_logger as lead_logger
lead_logger.log_lead_action = lambda **kw: None

assign = lambda pref=None, phone="919999999999": asyncio.run(ah.assign_lead_to_available_agent_async(phone, pref))

# 1. Agent Interakt doesn't know -> marked unavailable, next agent assigned in the same call
reset([GHOST, GOOD], {GHOST: AGENT_NOT_FOUND})
assert assign() == GOOD and not ah.is_agent_available(GHOST)
assert assign() == GOOD and ("assign", GHOST) not in events[2:], "unavailable agent skipped afterwards"

# 2. Previously assigned agent who is no longer active is never reused
reset([GOOD], {})
assert assign(pref=GHOST) == GOOD and ("assign", GHOST) not in events

# 3. Every agent rejected -> owner fallback; owner rejected too -> None
reset([GHOST, OTHER], {GHOST: AGENT_NOT_FOUND, OTHER: ASSIGN_FAILED})
assert assign() == OWNER
reset([GHOST], {GHOST: AGENT_NOT_FOUND, OWNER: ASSIGN_FAILED})
assert assign() is None

# 4. Customer unknown to Interakt -> nobody can be assigned, owner not spammed
reset([GOOD], {GOOD: CUSTOMER_NOT_FOUND})
assert assign() is None and ("assign", OWNER) not in events

# 5. Instagram / Facebook: no Interakt assignment, chosen agent returned
reset([GOOD], {GOOD: NOT_INTERAKT_CHANNEL})
assert assign(phone="ig_123") == GOOD

# 6. Full handoff: "connecting" is sent ONLY after a successful assignment
reset([GHOST, GOOD], {GHOST: AGENT_NOT_FOUND})
ok = asyncio.run(ah.execute_agent_handoff_async("919999999999", "Connecting you to our team member", "test"))
kinds = [e[0] for e in events]
assert ok is True and kinds.index("assign") < kinds.index("send"), events
assert ("save", GOOD) in events and ("unescalate", "919999999999") not in events

# 7. Nobody can be assigned -> no "connecting" promise, AI re-enabled, support numbers sent
reset([GHOST], {GHOST: AGENT_NOT_FOUND, OWNER: ASSIGN_FAILED})
ok = asyncio.run(ah.execute_agent_handoff_async("919999999999", "Connecting you to our team member", "test"))
sent = [e[1] for e in events if e[0] == "send"]
assert ok is False and ("unescalate", "919999999999") in events
assert sent and not any("Connecting" in s for s in sent) and "busy" in sent[0], sent

print("OK: agent handoff checks passed")
