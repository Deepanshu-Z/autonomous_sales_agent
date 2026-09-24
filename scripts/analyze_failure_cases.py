"""
Deeper qualitative and quantitative breakdown of real customer conversations.
"""
import sys
import re
from collections import Counter, defaultdict
sys.stdout.reconfigure(encoding='utf-8')
from src.services.supabase_client import supabase

def is_test_phone(p: str) -> bool:
    if not p:
        return True
    p_clean = p.replace("+", "").strip()
    return (
        p_clean.startswith("99999") or
        p_clean.startswith("9199999") or
        p_clean == "test_user" or
        p_clean.startswith("12345") or
        len(p_clean) < 8
    )

def main():
    all_chats = []
    page = 0
    while True:
        r = supabase.table('chat_history').select('*').range(page*1000, (page+1)*1000 - 1).execute()
        data = r.data or []
        all_chats.extend(data)
        if len(data) < 1000:
            break
        page += 1

    real_chats = [m for m in all_chats if not is_test_phone(m.get('phone'))]
    by_phone = defaultdict(list)
    for m in real_chats:
        by_phone[m['phone']].append(m)
    for p in by_phone:
        by_phone[p].sort(key=lambda x: x.get('created_at') or '')

    all_states = []
    page = 0
    while True:
        r = supabase.table('user_session_state').select('*').range(page*1000, (page+1)*1000 - 1).execute()
        data = r.data or []
        all_states.extend(data)
        if len(data) < 1000:
            break
        page += 1

    real_states = {s['phone']: s for s in all_states if not is_test_phone(s.get('phone'))}

    print("=================================================================")
    print("CATEGORIZING ISSUES ACROSS ALL %d REAL CUSTOMER CHATS" % len(by_phone))
    print("=================================================================")

    # Issue 1: How many users asked for a CALL / HUMAN / AGENT?
    call_agent_phones = []
    call_keywords = ["call", "baat", "number", "agent", "phone", "contact", "human", "calling", "talk"]
    for p, msgs in by_phone.items():
        u_texts = " ".join([m['message'].lower() for m in msgs if m['role'] == 'user'])
        if any(re.search(rf"\b{k}\b", u_texts) for k in call_keywords):
            call_agent_phones.append(p)
    print(f"Users asking for Call/Agent/Phone/Talk: {len(call_agent_phones)} ({(len(call_agent_phones)/len(by_phone))*100:.1f}%)")

    # Issue 2: How many asked about TRIAL / FREE TRIAL / DEMO?
    trial_phones = []
    trial_keywords = ["trial", "demo", "free", "sample"]
    for p, msgs in by_phone.items():
        u_texts = " ".join([m['message'].lower() for m in msgs if m['role'] == 'user'])
        if any(re.search(rf"\b{k}\b", u_texts) for k in trial_keywords):
            trial_phones.append(p)
    print(f"Users asking for Trial / Demo / Free: {len(trial_phones)} ({(len(trial_phones)/len(by_phone))*100:.1f}%)")

    # Issue 3: How many asked about FEES / PRICE / DISCOUNT / COST?
    fee_phones = []
    fee_keywords = ["fee", "fees", "price", "kitna", "cost", "charge", "charges", "paisa", "rupaye", "rs", "₹", "discount", "offer"]
    for p, msgs in by_phone.items():
        u_texts = " ".join([m['message'].lower() for m in msgs if m['role'] == 'user'])
        if any(re.search(rf"\b{k}\b", u_texts) for k in fee_keywords):
            fee_phones.append(p)
    print(f"Users asking for Fees / Price / Cost / Discount: {len(fee_phones)} ({(len(fee_phones)/len(by_phone))*100:.1f}%)")

    # Issue 4: How many users got stuck on "Done" or coupon activation?
    coupon_phones = []
    for p, msgs in by_phone.items():
        u_texts = " ".join([m['message'].lower() for m in msgs if m['role'] == 'user'])
        if "coupon" in u_texts or "done" in u_texts or "code" in u_texts:
            coupon_phones.append(p)
    print(f"Users asking about Coupon / Code / saying Done: {len(coupon_phones)} ({(len(coupon_phones)/len(by_phone))*100:.1f}%)")

    # Issue 5: How many users said offline / location / centre / address?
    offline_phones = []
    offline_keywords = ["offline", "location", "address", "centre", "center", "near", "kahan", "place"]
    for p, msgs in by_phone.items():
        u_texts = " ".join([m['message'].lower() for m in msgs if m['role'] == 'user'])
        if any(re.search(rf"\b{k}\b", u_texts) for k in offline_keywords):
            offline_phones.append(p)
    print(f"Users asking about Offline / Location / Address / Center: {len(offline_phones)} ({(len(offline_phones)/len(by_phone))*100:.1f}%)")

    # Issue 6: Language / Script friction
    # Let's inspect Hindi/Hinglish inquiries
    hindi_phones = []
    for p, msgs in by_phone.items():
        for m in msgs:
            if m['role'] == 'user' and re.search(r'[\u0900-\u097F]', m['message']):
                hindi_phones.append(p)
                break
    print(f"Users sending native Devanagari Hindi: {len(hindi_phones)}")

    # Let's inspect 10 concrete real user conversations showing different failure modes
    print("\n=================================================================")
    print("DEEP DIVE: 8 CONCRETE REAL USER FAILURE CASES")
    print("=================================================================")
    
    # 1. Ask for call / human
    print("\n--- CASE 1: USER ASKING FOR A CALL / HUMAN ---")
    for p in call_agent_phones[:2]:
        print(f"\nUser {p}:")
        for m in by_phone[p]:
            print(f"  [{m['role'].upper()}]: {m['message']}")

    # 2. Trial class request handling
    print("\n--- CASE 2: USER ASKING FOR TRIAL CLASS ---")
    for p in trial_phones[:2]:
        if p not in call_agent_phones:
            print(f"\nUser {p}:")
            for m in by_phone[p]:
                print(f"  [{m['role'].upper()}]: {m['message']}")

    # 3. Offline / Location request handling
    print("\n--- CASE 3: USER ASKING FOR OFFLINE / LOCATION ---")
    for p in offline_phones[:2]:
        print(f"\nUser {p}:")
        for m in by_phone[p]:
            print(f"  [{m['role'].upper()}]: {m['message']}")

    # 4. Long conversation where user engaged heavily but didn't convert
    print("\n--- CASE 4: HIGH ENGAGEMENT (5+ USER TURNS) BUT DROPPED OFF ---")
    high_engagement = [p for p, msgs in by_phone.items() if len([m for m in msgs if m['role'] == 'user']) >= 5]
    print(f"Total users with 5+ user messages: {len(high_engagement)}")
    for p in high_engagement[:2]:
        print(f"\nUser {p}:")
        for m in by_phone[p]:
            print(f"  [{m['role'].upper()}]: {m['message']}")

if __name__ == '__main__':
    main()
