import sys, os, json
sys.path.insert(0, os.path.abspath("."))
import asyncio
from dotenv import load_dotenv
load_dotenv()

from src.core import intent_classifier
from src.core.helpers.slot_engine import evaluate_enrollment_slots
from src.state.chat_history import get_recent_history
from src.state.chat_state import get_user_state

async def test_lead_1():
    phone = "917780358487"
    st = get_user_state(phone)
    hist = get_recent_history(phone, 20)
    
    # Target message:
    # "Pahele mai 1 month dekhna chahungi kaise seekhna hai agar sab set hogaya toh uske baad 6 months ka package lungi"
    
    # Find the index of this message in history
    idx = -1
    for i, m in enumerate(hist):
        if "Pahele mai 1 month" in (m.get("content") or ""):
            idx = i
            break
            
    print(f"Message index in history: {idx}")
    hist_before = hist[:idx] if idx != -1 else hist
    
    user_text = "Pahele mai 1 month dekhna chahungi kaise seekhna hai agar sab set hogaya toh uske baad 6 months ka package lungi"
    
    flags = await intent_classifier.extract_intent_and_state_changes(user_text, state=st, history=hist_before)
    print("\n--- Extractor Flags ---")
    print("customer_intent:", flags.get("customer_intent"))
    print("intent:", flags.get("intent"))
    print("selected_package:", flags.get("selected_package"))
    print("selected_timing:", flags.get("selected_timing"))
    print("requests_qr:", flags.get("requests_qr"))
    print("wants_trial:", flags.get("wants_trial"))
    print("question_summary:", flags.get("question_summary"))

    # Let's check state changes
    slot_eval = evaluate_enrollment_slots(st, course_id="ladies_dance")
    print("\nSlot Eval:")
    print("is_complete:", slot_eval.get("is_complete"))
    print("missing_slots:", slot_eval.get("missing_slots"))
    print("settled_slots:", slot_eval.get("settled_slots"))
    
    clean_followup = "Kya aap payment ke liye tayaar hain ya koi aur sawaal hai? 😊"
    import re
    is_asking_slot = bool(
        not slot_eval.get("is_complete")
        or (
            clean_followup and re.search(
                r"\b(which timing|which batch|which specific timing|which instructor|which package duration|choose a package|select a package|kisme se|kaunsa batch|kaunsi timing|batch days|which days|kaunse din|kaunsa pair|pair chahenge|tuesday|thursday|wednesday|friday|saturday|sunday)\b",
                clean_followup,
                re.IGNORECASE,
            )
        )
    )
    print("is_asking_slot:", is_asking_slot)

    user_asked_qr = bool(
        flags.get("requests_qr")
        or flags.get("intent") == "PAYMENT_REQUEST"
        or flags.get("customer_intent") == "PAYMENT_REQUEST"
    )
    print("user_asked_qr:", user_asked_qr)
    
    has_valid_fee = True
    all_prerequisites_met = bool(slot_eval.get("is_complete") and has_valid_fee)
    is_client_paid = False

    is_payment_step = bool(
        not is_client_paid
        and all_prerequisites_met
        and (user_asked_qr or not is_asking_slot)
        and (
            user_asked_qr
            or (not st.get("payment_claimed") and (
                st.get("stage") in ["PACKAGE_SELECTED", "TIMING_SELECTED", "TEACHER_SELECTED", "COUPON_SENT", "PAYMENT_PENDING", "CHECKOUT"]
                or flags.get("intent") in ["PACKAGE_SELECTION", "TIMING_SELECTION", "TEACHER_SELECTION", "PAYMENT_REQUEST"]
            ))
        )
    )
    print("is_payment_step:", is_payment_step)
    
    # Check dispatch_payment_qr_async guards:
    print("\n--- dispatch_payment_qr_async internal guards ---")
    force_resend = user_asked_qr
    pkg = st.get("package") or st.get("selected_duration")
    print("force_resend:", force_resend)
    print("st.get('qr_code_sent'):", st.get("qr_code_sent"))
    print("st.get('last_qr_package'):", st.get("last_qr_package"))
    anti_spam_blocked = (not force_resend and st.get("qr_code_sent") and st.get("last_qr_package") == pkg)
    print("anti_spam_blocked:", anti_spam_blocked)

asyncio.run(test_lead_1())
