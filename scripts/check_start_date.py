"""Self-check: a customer's future start date is parsed and sent to enrollment.
Run: python scripts/check_start_date.py
"""
import asyncio
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.services import hono_staging_client as h

T = date(2026, 11, 26)  # a Thursday
cases = {
    "10 dec ss": "2026-12-10",
    "10 dec": "2026-12-10",
    "1st December": "2026-12-01",
    "from 1st dec": "2026-12-01",
    "December 10": "2026-12-10",
    "5 jan": "2027-01-05",              # past this year -> next year
    "10 December 2026": "2026-12-10",
    "next month": "2026-12-01",
    "agle mahine se": "2026-12-01",
    "next week": "2026-11-30",          # next Monday
    "agle hafte": "2026-11-30",
    "next monday": "2026-11-30",
    "agle somwar": "2026-11-30",
    "kal se": "2026-11-27",
    "from tomorrow": "2026-11-27",
    "after 10 days": "2026-12-06",
    "10 din baad": "2026-12-06",
    "15 tareekh": "2026-12-15",         # day-only, already past this month -> next month
    "28th": "2026-11-28",
    "26 nov": None,                     # today = no delay
    "20 nov": None,                     # past -> next year -> beyond cap -> start today
    "10 june": None,                    # beyond 90-day cap
    "31 feb": None,                     # invalid
    "later": None,
    "after diwali": None,
    None: None,
}
bad = {k: (h.resolve_start_date(k, T), v) for k, v in cases.items() if h.resolve_start_date(k, T) != v}
assert not bad, f"resolve_start_date mismatches (got, expected): {bad}"


async def staged_payload(start_date):
    sent = {}

    class FakeResp:
        status_code = 200
        text = "{}"

        def json(self):
            return {"success": True, "action": "STAGED", "staged_id": "x"}

    class FakeClient:
        async def post(self, url, json=None, headers=None, **k):
            sent.update(json)
            return FakeResp()

    h.get_hono_client = lambda: FakeClient()
    h.AUTOMATION_SECRET = "test"
    await h.stage_ai_enrollment_async(
        email="priya.sharma1990@gmail.com", contact="9999999999", course="yoga",
        package_duration="3 Months", start_date=start_date,
    )
    return sent


today = datetime.now().date()
future = (today + timedelta(days=14)).isoformat()
p = asyncio.run(staged_payload(future))
assert p["start_date"] == future, p
assert p["end_date"] == (today + timedelta(days=14 + 90)).isoformat(), p   # 3 months counted from the delayed start

p = asyncio.run(staged_payload((today - timedelta(days=3)).isoformat()))   # stale date from an old chat
assert p["start_date"] == today.isoformat(), p

p = asyncio.run(staged_payload(None))
assert p["start_date"] == today.isoformat(), p

print(f"OK: {len(cases)} phrasings parsed; enrollment uses delayed start and end dates")
