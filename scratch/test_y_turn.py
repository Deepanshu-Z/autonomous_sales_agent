import sys, os, json
sys.path.insert(0, os.path.abspath('.'))
import asyncio
from dotenv import load_dotenv
load_dotenv()

from src.core import intent_classifier
from src.core.helpers.slot_engine import evaluate_enrollment_slots
from src.state.chat_history import get_recent_history
from src.state.chat_state import get_user_state

async def test_turn():
    phone = "919811134113"
    st = get_user_state(phone)
    hist = get_recent_history(phone, 15)
    
    # Message before 'Y' was assistant asking:
    # "Kya aap package ko lock karne ke liye aage badhna chahenge? 😊"
    # User replied: "Y"
    
    flags = await intent_classifier.extract_intent_and_state_changes("Y", state=st, history=hist[:-2])
    print("=== Extractor flags for 'Y' ===")
    print("customer_intent:", flags.get("customer_intent"))
    print("intent:", flags.get("intent"))
    print("selected_package:", flags.get("selected_package"))
    print("selected_timing:", flags.get("selected_timing"))
    print("requests_qr:", flags.get("requests_qr"))
    print("wants_trial:", flags.get("wants_trial"))

    # Let's check is_payment_step condition:
    last_turn = flags
    user_asked_qr = bool(
        last_turn.get("requests_qr")
        or last_turn.get("intent") == "PAYMENT_REQUEST"
        or flags.get("customer_intent") == "PAYMENT_REQUEST"
    )
    
    slot_eval = evaluate_enrollment_slots(st, course_id="ladies_dance")
    print("slot_eval is_complete:", slot_eval.get("is_complete"))
    
    # Check is_asking_slot
    followup_msg = "Kya aapko payment karne mein koi madad chahiye? 😊"
    import re
    is_asking_slot = bool(
        not slot_eval.get("is_complete")
        or (
            followup_msg and re.search(
                r"\b(which timing|which batch|which specific timing|which instructor|which package duration|choose a package|select a package|kisme se|kaunsa batch|kaunsi timing|batch days|which days|kaunse din|kaunsa pair|pair chahenge|tuesday|thursday|wednesday|friday|saturday|sunday)\b",
                followup_msg,
                re.IGNORECASE,
            )
        )
    )
    print("is_asking_slot:", is_asking_slot)
    
    all_prerequisites_met = slot_eval.get("is_complete")
    is_client_paid = False
    
    is_payment_step = bool(
        not is_client_paid
        and all_prerequisites_met
        and (user_asked_qr or not is_asking_slot)
        and (
            user_asked_qr
            or (not st.get("payment_claimed") and (
                st.get("stage") in ["PACKAGE_SELECTED", "TIMING_SELECTED", "TEACHER_SELECTED", "COUPON_SENT", "PAYMENT_PENDING", "CHECKOUT"]
                or last_turn.get("intent") in ["PACKAGE_SELECTION", "TIMING_SELECTION", "TEACHER_SELECTION", "PAYMENT_REQUEST"]
            ))
        )
    )
    print("user_asked_qr:", user_asked_qr)
    print("last_turn intent:", last_turn.get("intent"))
    print("st stage in list:", st.get("stage") in ["PACKAGE_SELECTED", "TIMING_SELECTED", "TEACHER_SELECTED", "COUPON_SENT", "PAYMENT_PENDING", "CHECKOUT"])
    print("is_payment_step:", is_payment_step)

asyncio.run(test_turn())
