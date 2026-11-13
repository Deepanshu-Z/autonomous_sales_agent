"""
followup_service.py — Factual Course-Based Follow-Up Service for Sensationz WhatsApp.

Manages the 24-hour cadence:
Step 0: +5 min
Step 1: +30 min (gap: 25 min)
Step 2: +3 hours (gap: 2.5 hours)
Step 3: +9 hours (gap: 6 hours)
Step 4: +15 hours (gap: 6 hours)
Step 5: +21 hours (gap: 6 hours) -> Offers 3 Free Live Trial Classes!
Step 6: +24 hours (gap: 3 hours) -> Final cutoff, handoff to human agent.
"""

import json
import os
import time
from typing import Optional, Dict, Any

FOLLOWUP_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "followup"
)

# Gaps in seconds to schedule the NEXT step after sending current step
# Index 0: after sending step 0 (at 5m), wait 25m (1500s) -> 30m total
# Index 1: after sending step 1 (at 30m), wait 2.5h (9000s) -> 3h total
# Index 2: after sending step 2 (at 3h), wait 6h (21600s) -> 9h total
# Index 3: after sending step 3 (at 9h), wait 6h (21600s) -> 15h total
# Index 4: after sending step 4 (at 15h), wait 6h (21600s) -> 21h total (trial offer)
# Index 5: after sending step 5 (at 21h), wait 3h (10800s) -> 24h total cutoff
NEXT_STEP_GAPS_SEC = [1500, 9000, 21600, 21600, 21600, 10800]
TOTAL_STEPS = 6  # steps 0 through 5 are messages; step 6 is agent handoff


def _normalize_course_key(course_id: Optional[str]) -> str:
    if not course_id:
        return "general"
    c = str(course_id).lower().strip()
    if "yoga" in c and "face" in c:
        return "face_yoga"
    if "yoga" in c:
        return "yoga"
    if "kathak" in c:
        return "kathak"
    if any(w in c for w in ["ladies", "bollywood", "dance"]):
        return "ladies_dance"
    return "general"


def load_course_followups(course_key: str) -> list:
    """Loads followup steps from data/followup/{course_key}.json with fallback to general."""
    fname = f"{course_key}.json"
    fpath = os.path.join(FOLLOWUP_DIR, fname)
    if not os.path.exists(fpath):
        fpath = os.path.join(FOLLOWUP_DIR, "general.json")

    try:
        with open(fpath, "r", encoding="utf-8") as fp:
            data = json.load(fp)
            return data.get("followups", [])
    except Exception as e:
        print(f"[followup_service] Error reading {fpath}: {e}")
        return []


def get_followup_for_step(course_id: Optional[str], step_index: int) -> Optional[Dict[str, Any]]:
    """Returns the followup payload dict for the given course and step index (0-5)."""
    if step_index < 0 or step_index >= TOTAL_STEPS:
        return None

    course_key = _normalize_course_key(course_id)
    steps = load_course_followups(course_key)
    if not steps:
        steps = load_course_followups("general")

    for s in steps:
        if s.get("step") == step_index:
            return s

    if steps and step_index < len(steps):
        return steps[step_index]
    return None


def get_next_gap_seconds(current_step: int) -> Optional[int]:
    """Returns seconds to wait until the next action after sending current_step."""
    if 0 <= current_step < len(NEXT_STEP_GAPS_SEC):
        return NEXT_STEP_GAPS_SEC[current_step]
    return None

