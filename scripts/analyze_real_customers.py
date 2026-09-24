"""
Comprehensive Analysis of REAL Customer Chats from Supabase.
Filters out test phones (99999*, test_user, etc.).
"""
import sys
import json
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
    print("Fetching all chat history...")
    all_chats = []
    page = 0
    while True:
        r = supabase.table('chat_history').select('*').range(page*1000, (page+1)*1000 - 1).execute()
        data = r.data or []
        all_chats.extend(data)
        if len(data) < 1000:
            break
        page += 1

    print(f"Total raw messages: {len(all_chats)}")

    # Filter out test messages
    real_chats = [m for m in all_chats if not is_test_phone(m.get('phone'))]
    print(f"Total real customer messages: {len(real_chats)}")

    by_phone = defaultdict(list)
    for m in real_chats:
        by_phone[m['phone']].append(m)
    for p in by_phone:
        by_phone[p].sort(key=lambda x: x.get('created_at') or '')

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

    real_states = [s for s in all_states if not is_test_phone(s.get('phone'))]
    state_by_phone = {s['phone']: s for s in real_states}

    print("\n========================================================")
    print("REAL CUSTOMER FUNNEL METRICS (Total Real Users: %d)" % len(real_states))
    print("========================================================")
    stage_counts = Counter(s.get('stage') for s in real_states)
    for st, cnt in stage_counts.most_common():
        pct = (cnt / len(real_states)) * 100
        print(f"  Stage {st:<20}: {cnt:4d} ({pct:5.1f}%)")

    escalated_count = sum(1 for s in real_states if s.get('is_escalated'))
    app_installed_count = sum(1 for s in real_states if s.get('app_installed'))
    profile_created_count = sum(1 for s in real_states if s.get('profile_created'))
    coupon_sent_count = sum(1 for s in real_states if s.get('coupon_sent'))
    assigned_count = sum(1 for s in real_states if s.get('already_assigned'))

    print(f"\n  Escalated           : {escalated_count:4d} ({(escalated_count/len(real_states))*100:5.1f}%)")
    print(f"  Already Assigned    : {assigned_count:4d} ({(assigned_count/len(real_states))*100:5.1f}%)")
    print(f"  App Installed       : {app_installed_count:4d} ({(app_installed_count/len(real_states))*100:5.1f}%)")
    print(f"  Profile Created     : {profile_created_count:4d} ({(profile_created_count/len(real_states))*100:5.1f}%)")
    print(f"  Coupon Sent         : {coupon_sent_count:4d} ({(coupon_sent_count/len(real_states))*100:5.1f}%)")

    followup_counts = Counter(s.get('follow_up_count') for s in real_states)
    print("\nFollow-up counts for real users:")
    for f_cnt, cnt in followup_counts.most_common():
        print(f"  {f_cnt} followups: {cnt} ({(cnt/len(real_states))*100:5.1f}%)")

    print("\n========================================================")
    print("CONVERSATION TURNS & LENGTH (Real Conversations: %d)" % len(by_phone))
    print("========================================================")
    turn_counts = [len(msgs) for msgs in by_phone.values()]
    user_turn_counts = [len([m for m in msgs if m['role'] == 'user']) for msgs in by_phone.values()]
    print(f"Avg total messages per conversation: {sum(turn_counts)/len(turn_counts):.1f}")
    print(f"Avg user messages per conversation : {sum(user_turn_counts)/len(user_turn_counts):.1f}")

    user_turns_dist = Counter(user_turn_counts)
    print("\nDistribution of User Message Count:")
    for ut in sorted(user_turns_dist.keys())[:15]:
        print(f"  User sent {ut:2d} message(s): {user_turns_dist[ut]:3d} conversations ({(user_turns_dist[ut]/len(by_phone))*100:5.1f}%)")

    print("\n========================================================")
    print("FIRST USER MESSAGE (Top 15 Ad Templates / Inquiries)")
    print("========================================================")
    first_msgs = []
    for p, msgs in by_phone.items():
        u_msgs = [m for m in msgs if m['role'] == 'user']
        if u_msgs:
            first_msgs.append(u_msgs[0]['message'])
    for msg, cnt in Counter(first_msgs).most_common(15):
        print(f"  [{cnt:3d}] {repr(msg[:80])}")

    print("\n========================================================")
    print("ANALYSIS OF DROP-OFFS & WHY USERS STOP RESPONDING")
    print("========================================================")
    
    # 1-turn wonders: Users who sent exactly 1 message and never replied again
    single_turn_phones = [p for p, msgs in by_phone.items() if len([m for m in msgs if m['role'] == 'user']) == 1]
    print(f"\nUsers who sent exactly 1 message and ghosted: {len(single_turn_phones)} ({(len(single_turn_phones)/len(by_phone))*100:5.1f}%)")
    
    # What was the bot's response to them?
    bot_first_replies = []
    for p in single_turn_phones:
        a_msgs = [m for m in by_phone[p] if m['role'] == 'assistant']
        if a_msgs:
            bot_first_replies.append(a_msgs[0]['message'])
    
    print("\nSample 1-turn conversations (User message -> Bot response -> User ghosts):")
    for p in single_turn_phones[:5]:
        msgs = by_phone[p]
        u = [m['message'] for m in msgs if m['role'] == 'user']
        a = [m['message'] for m in msgs if m['role'] == 'assistant']
        print(f"\n--- User {p} ---")
        print(f"USER: {u[0] if u else 'N/A'}")
        if a:
            print(f"BOT 1: {a[0][:150]}...")
            if len(a) > 1:
                print(f"BOT 2: {a[1][:150]}...")
        # Check state
        st = state_by_phone.get(p, {})
        print(f"State: stage={st.get('stage')}, followups={st.get('follow_up_count')}, escalated={st.get('is_escalated')}")

    # Inspect users who reached 2-4 turns and then dropped off
    multi_turn_dropoffs = [p for p, msgs in by_phone.items() if 2 <= len([m for m in msgs if m['role'] == 'user']) <= 4]
    print(f"\nUsers who exchanged 2-4 user messages and then dropped off: {len(multi_turn_dropoffs)}")
    print("\nSample 2-4 turn drop-off conversations:")
    for p in multi_turn_dropoffs[:5]:
        print(f"\n--- User {p} ---")
        for m in by_phone[p]:
            print(f"  [{m['role'].upper()}]: {m['message']}")
        st = state_by_phone.get(p, {})
        print(f"  -> State: stage={st.get('stage')}, followups={st.get('follow_up_count')}, escalated={st.get('is_escalated')}")

    # Inspect users who reached COUPON_SENT or APP_LINK_SENT
    print("\n========================================================")
    print("SUCCESSFUL OR NEAR-CONVERSION SESSIONS")
    print("========================================================")
    advanced_phones = [p for p, s in state_by_phone.items() if s.get('coupon_sent') or s.get('stage') in ('COUPON_SENT', 'APP_LINK_SENT', 'ENROLL_CONFIRMED')]
    print(f"Real users who reached coupon / app link / enroll confirmed: {len(advanced_phones)}")
    for p in advanced_phones[:5]:
        print(f"\n--- User {p} (stage={state_by_phone[p].get('stage')}) ---")
        for m in by_phone.get(p, []):
            print(f"  [{m['role'].upper()}]: {m['message']}")

if __name__ == '__main__':
    main()
