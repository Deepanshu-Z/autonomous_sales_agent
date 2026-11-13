"""
followup_service.py — Unified Follow-Up Service for Sensationz WhatsApp.

SINGLE POINT OF ENTRY:
Use the toggle `USE_LEGACY_FOLLOWUP` below (or .env `LEGACY_FOLLOWUP=false/true`)
to switch between:
  1. LEGACY MODE (True):
     - Step 0 (+5 min of silence): Friendly check-in reminder
     - Step 1 (+30 min of silence): Handoff to human agent in Interakt

  2. 24-HOUR AI VALUE MODE (False):
     - Step 0 (+5 min): Friendly check-in reminder
     - Step 1 (+30 min): Fact #1 from course JSON
     - Step 2 (+3 hours): Fact #2 from course JSON
     - Step 3 (+9 hours): Fact #3 from course JSON
     - Step 4 (+15 hours): Fact #4 from course JSON
     - Step 5 (+21 hours): 🎁 3 Free Live Trial Classes offer
     - Step 6 (+24 hours): Final handoff to human agent in Interakt
"""

import json
import os
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

# =========================================================================
# FOLLOW-UP SYSTEM MODE TOGGLE (SINGLE POINT OF ENTRY)
# True  = Legacy Mode (5-min check-in -> 30-min agent handoff)
# False = 24-Hour AI Mode (5-min check-in -> 30m/3h/9h/15h facts -> 21h trial -> 24h agent)
# =========================================================================
USE_LEGACY_FOLLOWUP: bool = True


def is_legacy_mode() -> bool:
    """Returns True for Legacy Mode (5m -> 30m agent), False for 24-Hour AI Mode."""
    env_val = os.getenv("LEGACY_FOLLOWUP")
    if env_val is not None:
        return env_val.strip().lower() in ("true", "1", "yes")
    return USE_LEGACY_FOLLOWUP


FOLLOWUP_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "followup"
)

# Gaps in seconds for 24-Hour mode:
# Step 0 (at 5m): wait 25m (1500s) -> 30m total
# Step 1 (at 30m): wait 2.5h (9000s) -> 3h total
# Step 2 (at 3h): wait 6h (21600s) -> 9h total
# Step 3 (at 9h): wait 6h (21600s) -> 15h total
# Step 4 (at 15h): wait 6h (21600s) -> 21h total (trial offer)
# Step 5 (at 21h): wait 3h (10800s) -> 24h total (handoff)
GAPS_24H_SEC: Dict[int, int] = {
    0: 1500,
    1: 9000,
    2: 21600,
    3: 21600,
    4: 21600,
    5: 10800,
}

# Gap in seconds for Legacy mode (Step 0 -> Step 1):
GAP_LEGACY_SEC: int = 1800  # 30 minutes


@dataclass
class FollowupDecision:
    action: str                     # "SEND_MESSAGE" | "ESCALATE_TO_AGENT" | "SKIP"
    message: Optional[str] = None   # Text message to send to lead
    agent_email: Optional[str] = None # Interakt agent email if escalating
    next_count: int = 0             # Next follow_up_count to save in DB/state
    next_gap_seconds: Optional[int] = None # Seconds until next action
    step_label: str = ""            # Label for logging
    is_trial_offer: bool = False    # True if 3 Free Trials offer
    updated_sent_steps: Optional[List[int]] = None # Updated list of sent steps


def get_total_steps() -> int:
    """Returns the total number of follow-up steps before agent handoff."""
    return 1 if is_legacy_mode() else 6


def get_checkin_message(course_name: Optional[str] = None) -> str:
    """Returns the friendly check-in message sent at Step 0 (+5 min of silence)."""
    if course_name:
        clean = course_name.replace("_", " ").strip().title()
        return (
            f"Hi again! 😊 Just checking in — were you still interested in "
            f"knowing more about our {clean} classes? Happy to help whenever you're ready!"
        )
    return (
        "Hi again! 😊 Just checking in — were you still interested in "
        "exploring our classes? Happy to help whenever you're ready!"
    )


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
    """Returns the followup payload dict for the given course and step index (1-5)."""
    course_key = _normalize_course_key(course_id)
    steps = load_course_followups(course_key)
    if not steps:
        steps = load_course_followups("general")

    for s in steps:
        if s.get("step") == step_index:
            return s
    return None


def get_followup_decision(
    phone: str,
    count: int,
    course_id: Optional[str],
    course_name: Optional[str],
    lead_at_qr: bool,
    sent_steps: Optional[List[int]] = None,
) -> FollowupDecision:
    """
    Core single-point-of-entry decision engine.
    Inspects lead state, current count, and active mode (Legacy vs 24-Hour AI),
    and returns a clean FollowupDecision.
    """
    from src.services.agent_handoff import get_next_agent_email

    sent_steps = list(sent_steps or [])

    # -------------------------------------------------------------
    # 1. LEGACY MODE (5 min check-in -> 30 min agent handoff)
    # -------------------------------------------------------------
    if is_legacy_mode():
        if count == 0:
            # Step 0 (+5 min): Send standard check-in, schedule agent handoff in 30 mins (1800s)
            msg = get_checkin_message(course_name)
            new_sent = list(set(sent_steps + [0]))
            return FollowupDecision(
                action="SEND_MESSAGE",
                message=msg,
                next_count=1,
                next_gap_seconds=GAP_LEGACY_SEC,
                step_label="5-min reminder (Legacy)",
                updated_sent_steps=new_sent,
            )
        else:
            # Step 1 (+30 min):
            if lead_at_qr:
                # QR leads stay with owner & AI, skip agent escalation
                return FollowupDecision(
                    action="SKIP",
                    next_count=1,
                    next_gap_seconds=None,
                    step_label="QR lead — skipping agent handoff, AI stays active",
                    updated_sent_steps=sent_steps,
                )
            else:
                agent, _ = get_next_agent_email()
                msg = "Connecting you with our team now. Someone will be with you shortly! 🙏"
                return FollowupDecision(
                    action="ESCALATE_TO_AGENT",
                    message=msg,
                    agent_email=agent,
                    next_count=2,
                    next_gap_seconds=None,
                    step_label="30-min agent escalation (Legacy)",
                    updated_sent_steps=sent_steps,
                )

    # -------------------------------------------------------------
    # 2. 24-HOUR AI VALUE MODE (5m -> 30m -> 3h -> 9h -> 15h -> 21h trial -> 24h agent)
    # -------------------------------------------------------------
    if count == 0:
        # Step 0 (+5 min of silence)
        if 0 not in sent_steps:
            msg = get_checkin_message(course_name)
            new_sent = list(set(sent_steps + [0]))
            return FollowupDecision(
                action="SEND_MESSAGE",
                message=msg,
                next_count=1,
                next_gap_seconds=GAPS_24H_SEC.get(0, 1500),
                step_label="5-min check-in (24h Mode)",
                updated_sent_steps=new_sent,
            )
        else:
            # Step 0 was already sent earlier in session and user engaged.
            # Advance directly to the next unseen factual step (1-5)
            step_to_send = next((s for s in range(1, 6) if s not in sent_steps), None)
            if step_to_send is None:
                # All facts 1-5 already seen -> handoff to agent
                count = 6
            else:
                count = step_to_send

    if 1 <= count <= 5:
        # Anti-loop check: ensure this step hasn't already been sent
        step_to_send = count
        if step_to_send in sent_steps:
            step_to_send = next((s for s in range(count, 6) if s not in sent_steps), None)

        if step_to_send is None or step_to_send > 5:
            count = 6
        else:
            fact = get_followup_for_step(course_id, step_to_send)
            if fact and fact.get("message"):
                msg = fact["message"]
                gap = GAPS_24H_SEC.get(step_to_send, 21600)
                is_trial = bool(fact.get("is_final_trial_offer", False))
                lbl = fact.get("delay_label", f"step {step_to_send}")
                new_sent = list(set(sent_steps + [step_to_send]))
                return FollowupDecision(
                    action="SEND_MESSAGE",
                    message=msg,
                    next_count=step_to_send + 1,
                    next_gap_seconds=gap,
                    step_label=f"24h {lbl} (Fact #{step_to_send})",
                    is_trial_offer=is_trial,
                    updated_sent_steps=new_sent,
                )
            else:
                count = 6

    # Step >= 6 (+24 hours total completed)
    if lead_at_qr:
        return FollowupDecision(
            action="SKIP",
            next_count=7,
            next_gap_seconds=None,
            step_label="24h cutoff for QR lead — skipping agent handoff, AI stays active",
            updated_sent_steps=sent_steps,
        )

    agent, _ = get_next_agent_email()
    msg = "Connecting you with our team now. Someone will be with you shortly! 🙏"
    return FollowupDecision(
        action="ESCALATE_TO_AGENT",
        message=msg,
        agent_email=agent,
        next_count=7,
        next_gap_seconds=None,
        step_label="24-hour final agent escalation",
        updated_sent_steps=sent_steps,
    )
