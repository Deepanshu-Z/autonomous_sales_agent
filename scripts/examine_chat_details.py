"""
Detailed analysis of call/agent requests and escalations.
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

    call_keywords = ["call", "baat", "number", "agent", "phone", "contact", "human", "talk"]
    c = 0
    for p, msgs in by_phone.items():
        u_texts = " ".join([m['message'].lower() for m in msgs if m['role'] == 'user'])
        if any(re.search(rf"\b{k}\b", u_texts) for k in call_keywords):
            print("==================================================")
            print(f"Call/Agent request for {p}")
            print("==================================================")
            for m in msgs:
                print(f"[{m['role'].upper()}]: {m['message']}")
            c += 1
            if c >= 3:
                break

if __name__ == '__main__':
    main()
