"""
followup_service.py — Unified Follow-Up Service for Sensationz WhatsApp.

SINGLE POINT OF ENTRY:
Use the toggle `USE_LEGACY_FOLLOWUP` below (or .env `LEGACY_FOLLOWUP=false/true`)
to switch between:
  1. LEGACY MODE (True):
     - Step 0 (+5 min of silence): Friendly check-in reminder
     - Step 1 (+30 min of silence): Handoff to human agent in Interakt

  2. 24-HOUR AI VALUE MODE (False):
     - Cadence: 5m -> 30m -> 3h -> 6h -> 6h -> 6h (Total ~24 hours from last user message)
     - 5 Message Variations for EACH of the 5 Timestamps:
         * 30 mins: 5 distinct company & course fact messages
         * 3 hours: 5 distinct company & course fact messages
         * 6 hours (step 3): 5 distinct company & course fact messages
         * 6 hours (step 4): 5 distinct company & course fact messages
         * 6 hours (step 5): 5 distinct variations of the 3 Free Live Trial Classes offer!
     - Step 6 (~23.5h continuous silence): Final handoff to human agent in Interakt (inside Meta 24h window).
"""

import json
import os
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

# =========================================================================
# FOLLOW-UP SYSTEM MODE TOGGLE (SINGLE POINT OF ENTRY)
# True  = Legacy Mode (5-min check-in -> 30-min agent handoff in Interakt)
# False = 24-Hour AI Mode (5-min check-in -> 30m/3h/6h/6h/6h facts & trials -> 24h agent)
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

# Gaps in seconds for 24-Hour mode strictly anchored to silence from last message:
# Step 0 (at 5m silence): wait 30m (1800s) -> triggers 30m message
# Step 1 (at 30m silence): wait 3h (10800s) -> triggers 3h message
# Step 2 (at 3h silence): wait 6h (21600s) -> triggers 6h_step3 message
# Step 3 (at 6h silence): wait 6h (21600s) -> triggers 6h_step4 message
# Step 4 (at 6h silence): wait 6h (21600s) -> triggers 6h_step5_trials message (trial offer)
# Step 5 (at 6h silence): wait 2h (7200s) -> triggers ~23.5h agent handoff inside Meta 24h window
GAPS_24H_SEC: Dict[int, int] = {
    0: 1800,   # 30 minutes after 5m message
    1: 10800,  # 3 hours after 30m message
    2: 21600,  # 6 hours after 3h message
    3: 21600,  # 6 hours after 6h_step3 message
    4: 21600,  # 6 hours after 6h_step4 message (delivers trial offer)
    5: 7200,   # 2 hours after trial offer (total ~23.5h silence -> agent handoff inside Meta window)
}

# Gap in seconds for Legacy mode (Step 0 -> Step 1):
GAP_LEGACY_SEC: int = 1800  # 30 minutes

STAGE_KEYS: Dict[int, str] = {
    1: "30m",
    2: "3h",
    3: "6h_step3",
    4: "6h_step4",
    5: "6h_step5_trials",
}


@dataclass
class FollowupDecision:
    action: str                     # "SEND_MESSAGE" | "ESCALATE_TO_AGENT" | "SKIP"
    message: Optional[str] = None   # Text message to send to lead
    agent_email: Optional[str] = None # Interakt agent email if escalating
    next_count: int = 0             # Next follow_up_count to save in DB/state
    next_gap_seconds: Optional[int] = None # Seconds until next action
    step_label: str = ""            # Label for logging
    is_trial_offer: bool = False    # True if 3 Free Trials offer
    message_id: Optional[str] = None # ID of the specific message variation sent
    updated_sent_ids: Optional[List[str]] = None # Updated list of sent message IDs


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


def load_course_json(course_key: str) -> dict:
    """Loads course followup configuration from data/followup/{course_key}.json."""
    fname = f"{course_key}.json"
    fpath = os.path.join(FOLLOWUP_DIR, fname)
    if not os.path.exists(fpath):
        fpath = os.path.join(FOLLOWUP_DIR, "general.json")

    try:
        with open(fpath, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception as e:
        print(f"[followup_service] Error reading {fpath}: {e}")
        return {}


def get_variation_for_stage(
    course_id: Optional[str],
    step_index: int,
    sent_ids: List[str],
) -> Optional[Dict[str, Any]]:
    """
    Selects the next unseen variation from the 5 message variations for this stage.
    If all 5 have already been sent, it safely wraps around to variation 0.
    """
    stage_key = STAGE_KEYS.get(step_index)
    if not stage_key:
        return None

    course_key = _normalize_course_key(course_id)
    cdata = load_course_json(course_key)
    stages = cdata.get("stages", {})
    variations = stages.get(stage_key, [])

    if not variations:
        # Fallback to general course if specific course stage missing
        gdata = load_course_json("general")
        variations = gdata.get("stages", {}).get(stage_key, [])

    if not variations:
        return None

    # Pick the first variation whose ID hasn't been sent yet
    for v in variations:
        vid = v.get("id")
        if vid and vid not in sent_ids:
            return v

    # If all 5 variations for this timestamp were already seen, wrap around to index 0
    return variations[0]


def get_followup_decision(
    phone: str,
    count: int,
    course_id: Optional[str],
    course_name: Optional[str],
    lead_at_qr: bool,
    sent_ids: Optional[List[str]] = None,
    sent_steps: Optional[Any] = None,
) -> FollowupDecision:
    """
    Core single-point-of-entry decision engine.
    Inspects lead state, current count, and active mode (Legacy vs 24-Hour AI),
    and returns a clean FollowupDecision with exact message variations.
    """
    from src.services.agent_handoff import get_next_agent_email

    sent_ids = list(sent_ids or [])
    if sent_steps and isinstance(sent_steps, list):
        for s in sent_steps:
            if isinstance(s, str) and s not in sent_ids:
                sent_ids.append(s)

    # -------------------------------------------------------------
    # 1. LEGACY MODE (5 min check-in -> 30 min agent handoff)
    # -------------------------------------------------------------
    if is_legacy_mode():
        if count == 0:
            msg = get_checkin_message(course_name)
            new_sent = list(set(sent_ids + ["legacy_5m"]))
            return FollowupDecision(
                action="SEND_MESSAGE",
                message=msg,
                next_count=1,
                next_gap_seconds=GAP_LEGACY_SEC,
                step_label="5-min reminder (Legacy)",
                message_id="legacy_5m",
                updated_sent_ids=new_sent,
            )
        else:
            if lead_at_qr:
                return FollowupDecision(
                    action="SKIP",
                    next_count=1,
                    next_gap_seconds=None,
                    step_label="QR lead — skipping agent handoff, AI stays active",
                    updated_sent_ids=sent_ids,
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
                    updated_sent_ids=sent_ids,
                )

    # -------------------------------------------------------------
    # 2. 24-HOUR AI VALUE MODE (5m -> 30m -> 3h -> 6h -> 6h -> 6h trials -> 24h agent)
    # -------------------------------------------------------------
    if count == 0:
        # Step 0 (+5 min of silence from last user message)
        msg = get_checkin_message(course_name)
        new_sent = list(set(sent_ids + ["checkin_5m"]))
        return FollowupDecision(
            action="SEND_MESSAGE",
            message=msg,
            next_count=1,
            next_gap_seconds=GAPS_24H_SEC.get(0, 1800),
            step_label="5-min check-in (24h Mode)",
            message_id="checkin_5m",
            updated_sent_ids=new_sent,
        )

    if 1 <= count <= 5:
        # Pull the next unseen variation from the 5 variations for this stage
        var = get_variation_for_stage(course_id, count, sent_ids)
        if var and var.get("message"):
            vid = var.get("id", f"step_{count}")
            msg = var["message"]
            gap = GAPS_24H_SEC.get(count, 21600)
            is_trial = bool(var.get("is_final_trial_offer", False))
            stage_name = STAGE_KEYS.get(count, f"step_{count}")
            new_sent = list(set(sent_ids + [vid]))

            return FollowupDecision(
                action="SEND_MESSAGE",
                message=msg,
                next_count=count + 1,
                next_gap_seconds=gap,
                step_label=f"24h [{stage_name}] ({vid})",
                is_trial_offer=is_trial,
                message_id=vid,
                updated_sent_ids=new_sent,
            )
        else:
            count = 6

    # Step >= 6 (Full 24 hours of continuous silence completed -> Human Agent Handoff)
    if lead_at_qr:
        return FollowupDecision(
            action="SKIP",
            next_count=7,
            next_gap_seconds=None,
            step_label="24h cutoff for QR lead — skipping agent handoff, AI stays active",
            updated_sent_ids=sent_ids,
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
        updated_sent_ids=sent_ids,
    )
