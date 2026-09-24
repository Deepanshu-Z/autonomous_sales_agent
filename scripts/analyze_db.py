"""
Script to deeply analyze all chat conversations in Supabase.
"""
import sys
import json
from collections import Counter, defaultdict
sys.stdout.reconfigure(encoding='utf-8')
from src.services.supabase_client import supabase

def main():
    print("Fetching chats from Supabase...")
    all_chats = []
    page = 0
    while True:
        r = supabase.table('chat_history').select('*').range(page*1000, (page+1)*1000 - 1).execute()
        data = r.data or []
        all_chats.extend(data)
        if len(data) < 1000:
            break
        page += 1

    print(f"Total messages: {len(all_chats)}")

    by_phone = defaultdict(list)
    for msg in all_chats:
        by_phone[msg['phone']].append(msg)
    for phone in by_phone:
        by_phone[phone].sort(key=lambda x: x.get('created_at') or '')

    # Fetch states
    all_states = []
    page = 0
    while True:
        r = supabase.table('user_session_state').select('*').range(page*1000, (page+1)*1000 - 1).execute()
        data = r.data or []
        all_states.extend(data)
        if len(data) < 1000:
            break
        page += 1

    state_by_phone = {s['phone']: s for s in all_states}

    print("\n========================================================")
    print("1. WHY ARE 273 USERS ESCALATED?")
    print("========================================================")
    escalated_phones = [p for p, s in state_by_phone.items() if s.get('is_escalated')]
    print(f"Total escalated phones in state: {len(escalated_phones)}")
    
    # Check trigger phrases before escalation
    escalation_triggers = []
    for p in escalated_phones:
        msgs = by_phone.get(p, [])
        user_msgs = [m['message'] for m in msgs if m['role'] == 'user']
        if user_msgs:
            escalation_triggers.append(user_msgs[-1])
    
    print("\nLast user message in escalated conversations (top 20):")
    for msg, count in Counter(escalation_triggers).most_common(20):
        print(f"  [{count:2d}] {repr(msg[:90])}")

    print("\n========================================================")
    print("2. 'Tum fraud ho' PHENOMENON")
    print("========================================================")
    fraud_phones = [p for p, msgs in by_phone.items() if any('Tum fraud ho' in (m.get('message') or '') for m in msgs)]
    print(f"Total phones with 'Tum fraud ho': {len(fraud_phones)}")
    if fraud_phones:
        p = fraud_phones[0]
        print(f"Sample conversation ({p}):")
        for m in by_phone[p]:
            print(f"  {m['role'].upper()}: {m['message'][:120]}")

    print("\n========================================================")
    print("3. DROP-OFF AT TIMING_SELECTED (141 USERS)")
    print("========================================================")
    timing_phones = [p for p, s in state_by_phone.items() if s.get('stage') == 'TIMING_SELECTED']
    print(f"Total phones in TIMING_SELECTED: {len(timing_phones)}")
    # Look at what the bot said to them, and what the user said last
    last_assistant_msgs_timing = []
    last_user_msgs_timing = []
    for p in timing_phones[:50]:
        msgs = by_phone.get(p, [])
        if msgs:
            u_msgs = [m['message'] for m in msgs if m['role'] == 'user']
            a_msgs = [m['message'] for m in msgs if m['role'] == 'assistant']
            if u_msgs:
                last_user_msgs_timing.append(u_msgs[-1])
            if a_msgs:
                last_assistant_msgs_timing.append(a_msgs[-1])

    print("\nSample full conversations of users stuck at TIMING_SELECTED:")
    for p in timing_phones[:3]:
        print(f"\n--- Conversation for {p} ---")
        for m in by_phone.get(p, []):
            print(f"  [{m['role'].upper()}]: {m['message']}")

    print("\n========================================================")
    print("4. DROP-OFF AT NEW (187 USERS)")
    print("========================================================")
    new_phones = [p for p, s in state_by_phone.items() if s.get('stage') == 'NEW']
    print(f"Total phones in NEW: {len(new_phones)}")
    print("Sample conversations stuck at NEW:")
    for p in new_phones[:3]:
        print(f"\n--- Conversation for {p} ---")
        for m in by_phone.get(p, []):
            print(f"  [{m['role'].upper()}]: {m['message']}")

    print("\n========================================================")
    print("5. PACKAGE_SELECTED -> APP DOWNLOAD / PROFILE CREATION DROP-OFF")
    print("========================================================")
    pkg_phones = [p for p, s in state_by_phone.items() if s.get('stage') in ('PACKAGE_SELECTED', 'APP_LINK_SENT', 'ENROLL_CONFIRMED', 'COUPON_SENT')]
    print(f"Total phones that selected package or beyond: {len(pkg_phones)}")
    for p in pkg_phones[:3]:
        print(f"\n--- Conversation for {p} (stage={state_by_phone[p].get('stage')}) ---")
        for m in by_phone.get(p, []):
            print(f"  [{m['role'].upper()}]: {m['message']}")

if __name__ == '__main__':
    main()
