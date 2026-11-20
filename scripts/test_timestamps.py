"""Self-check: qr_sent_at is set-once and updated_at only moves on lead activity.

Run: python scripts/test_timestamps.py   (from local_auto/, no network needed)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.state import chat_state as cs

captured = []


class _Q:
    def __init__(self): self.data = []
    def __getattr__(self, _): return lambda *a, **k: self
    def upsert(self, row, **k): captured.append(dict(row)); return self
    def execute(self): return self


class _SB:
    def table(self, _): return _Q()


cs.supabase = _SB()
cs.redis_conn = type("R", (), {"pipeline": lambda self: (_ for _ in ()).throw(Exception("no redis"))})()

# 1. Bot-side save of a QR'd lead whose state lacks both keys (cached / payment_id path):
#    must NOT write either column, so the DB keeps its original values.
cs.save_user_state("919000000001", {"stage": "PACKAGE_SELECTED", "qr_id": "qr_ABC", "payment_id": "pay_X"})
row = captured[-1]
assert "qr_sent_at" not in row, f"qr_sent_at re-stamped on bot save: {row.get('qr_sent_at')}"
assert "updated_at" not in row, f"updated_at moved on bot save: {row.get('updated_at')}"
assert row["is_qr_sent"] is True

# 2. Existing qr_sent_at is written back unchanged on later saves.
fixed = "2026-11-01T10:00:00+00:00"
st = {"stage": "PACKAGE_SELECTED", "qr_code_sent": True, "qr_sent_at": fixed}
cs.save_user_state("919000000001", st)
cs.save_user_state("919000000001", st)
assert captured[-1]["qr_sent_at"] == fixed

# 3. Lead message stamps updated_at; qr_sent_at stays fixed.
cs.mark_lead_activity(st)
cs.save_user_state("919000000001", st)
assert captured[-1]["updated_at"] == st["updated_at"] != fixed
assert captured[-1]["qr_sent_at"] == fixed

print("OK: qr_sent_at fixed, updated_at only on lead activity")
