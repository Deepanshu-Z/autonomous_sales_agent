"""
Print specific cases cleanly without truncation.
"""
import sys
import re
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')
from src.services.supabase_client import supabase
from scripts.analyze_real_customers import is_test_phone

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

    # CASE 1: Call/Agent requests
    print("==================================================")
    print("CASE 1: USERS ASKING FOR CALL / HUMAN / AGENT")
    print("==================================================")
    call_keywords = ["call", "baat", "number", "agent", "phone", "contact", "human", "talk"]
    c = 0
    for p, msgs in by_phone.items():
        u_texts = " ".join([m['message'].lower() for m in msgs if m['role'] == 'user'])
        if any(re.search(rf"\b{k}\b", u_texts) for k in call_keywords):
            print(f"\n--- Conversation for {p} ---")
            for m in msgs:
                print(f"[{m['role'].upper()}]: {m['message']}")
            c += 1
            if c >= 3:
                break

    # CASE 3: Offline / Center / Address requests
    print("\n==================================================")
    print("CASE 3: USERS ASKING FOR OFFLINE / PHYSICAL LOCATION")
    print("==================================================")
    offline_keywords = ["offline", "location", "address", "centre", "center", "near"]
    c = 0
    for p, msgs in by_phone.items():
        u_texts = " ".join([m['message'].lower() for m in msgs if m['role'] == 'user'])
        if any(re.search(rf"\b{k}\b", u_texts) for k in offline_keywords):
            print(f"\n--- Conversation for {p} ---")
            for m in msgs:
                print(f"[{m['role'].upper()}]: {m['message']}")
            c += 1
            if c >= 3:
                break

if __name__ == '__main__':
    main()
