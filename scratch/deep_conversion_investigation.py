"""
Comprehensive deep-dive analysis on real data to find exact root cause of 1% conversion rate.
"""
import sys
import re
from collections import Counter, defaultdict
sys.stdout.reconfigure(encoding='utf-8')
from src.services.supabase_client import supabase

def is_test_phone(p: str) -> bool:
    if not p:
        return True
    p_clean = re.sub(r'\D', '', str(p))
    return (
        p_clean.startswith("99999") or
        p_clean.startswith("9199999") or
        p_clean == "test_user" or
        p_clean.startswith("12345") or
        len(p_clean) < 8
    )

def fetch_all(table_name):
    all_data = []
    page = 0
    limit = 1000
    while True:
        r = supabase.table(table_name).select('*').range(page * limit, (page + 1) * limit - 1).execute()
        data = r.data or []
        all_data.extend(data)
        if len(data) < limit:
            break
        page += 1
    return all_data

def main():
    print("Fetching all chat_history...")
    all_chats = fetch_all('chat_history')
    print(f"Total chats in DB: {len(all_chats)}")

    print("Fetching all user_session_state...")
    all_states = fetch_all('user_session_state')
    print(f"Total user states in DB: {len(all_states)}")

    # Filter out test users
    real_chats = [m for m in all_chats if not is_test_phone(m.get('phone'))]
    real_states = [s for s in all_states if not is_test_phone(s.get('phone'))]

    print(f"Real user chats: {len(real_chats)}")
    print(f"Real user states: {len(real_states)}")

    # Group chats by phone
    chats_by_phone = defaultdict(list)
    for m in real_chats:
        chats_by_phone[m['phone']].append(m)
    for p in chats_by_phone:
        chats_by_phone[p].sort(key=lambda x: x.get('created_at') or '')

    state_by_phone = {s['phone']: s for s in real_states}

    # 1. Funnel Stages Breakdown
    stage_counts = Counter(s.get('stage') for s in real_states)
    print("\n" + "="*70)
    print("1. REAL USER FUNNEL BREAKDOWN (user_session_state)")
    print("="*70)
    total_real = len(real_states)
    for stage, count in stage_counts.most_common():
        print(f"  {stage:<25} : {count:5d} ({count/total_real*100:5.1f}%)")

    # 2. Escalation Breakdown
    escalated_states = [s for s in real_states if s.get('is_escalated')]
    print("\n" + "="*70)
    print("2. ESCALATION GRAVEYARD")
    print("="*70)
    print(f"Total Real Users Escalated: {len(escalated_states)} / {total_real} ({len(escalated_states)/total_real*100:.1f}%)")

    # Check how many escalated users sent messages AFTER being escalated!
    abandoned_after_escalation = []
    agent_replied_count = 0
    bot_ignored_count = 0

    for s in escalated_states:
        phone = s['phone']
        msgs = chats_by_phone.get(phone, [])
        # find the index where assistant sent escalation message
        esc_idx = None
        for i, m in enumerate(msgs):
            if m.get('role') == 'assistant' and "Connecting you with our team now" in (m.get('message') or ''):
                esc_idx = i
                break
        if esc_idx is not None and esc_idx < len(msgs) - 1:
            later_msgs = msgs[esc_idx+1:]
            later_user_msgs = [m for m in later_msgs if m.get('role') == 'user']
            later_agent_msgs = [m for m in later_msgs if m.get('role') in ('agent', 'human')]
            if later_user_msgs:
                abandoned_after_escalation.append((phone, later_user_msgs))
            if later_agent_msgs:
                agent_replied_count += 1

    print(f"Escalated users who reached back out trying to talk/pay: {len(abandoned_after_escalation)}")
    print(f"Escalations where a human agent actually sent a message: {agent_replied_count}")

    # 3. Followup counts
    followup_counts = Counter(s.get('follow_up_count') for s in real_states)
    print("\nFollowup count distribution:")
    for f_cnt, count in sorted(followup_counts.items(), key=lambda x: str(x[0])):
        print(f"  follow_up_count = {f_cnt}: {count} users")

    # 4. Conversion Analysis: How many users actually reached Payment / Checkout?
    # Users who got COUPON_SENT or beyond
    coupons = [s for s in real_states if s.get('coupon_sent') or s.get('stage') == 'COUPON_SENT']
    profiles = [s for s in real_states if s.get('profile_created')]
    print("\n" + "="*70)
    print("3. BOT CONVERSION MILESTONES")
    print("="*70)
    print(f"Real leads in DB                       : {total_real}")
    print(f"Bot flagged profile_created = True      : {len(profiles)} ({len(profiles)/total_real*100:.1f}%)")
    print(f"Bot flagged coupon_sent / stage COUPON  : {len(coupons)} ({len(coupons)/total_real*100:.1f}%)")

    # 5. Intent vs Friction: What did users actually want?
    print("\n" + "="*70)
    print("4. USER INTENT ANALYSIS")
    print("="*70)
    user_intents = Counter()
    for phone, msgs in chats_by_phone.items():
        all_user_text = " ".join(m.get('message', '').lower() for m in msgs if m.get('role') == 'user')
        if any(w in all_user_text for w in ['fee', 'price', 'kitna', 'cost', 'charge', 'rs', '₹']):
            user_intents['Asked Fees/Price'] += 1
        if any(w in all_user_text for w in ['time', 'timing', 'batch', 'subah', 'sham', 'morning', 'evening', 'am', 'pm']):
            user_intents['Asked Timings/Batches'] += 1
        if any(w in all_user_text for w in ['trial', 'demo', 'free', 'sample']):
            user_intents['Asked Free Trial/Demo'] += 1
        if any(w in all_user_text for w in ['pay', 'payment', 'link', 'kare', 'admission', 'gpay', 'phonepe', 'qr', 'upi']):
            user_intents['Asked Payment/Payment Link'] += 1
        if any(w in all_user_text for w in ['call', 'baat', 'number', 'phone', 'contact']):
            user_intents['Asked for Phone Call/Talk'] += 1
        if any(w in all_user_text for w in ['weight', 'pcos', 'period', 'belly', 'diet', 'back pain']):
            user_intents['Asked Health/Pain/Weight Issue'] += 1
        if any(w in all_user_text for w in ['app', 'download', 'play store', 'link']):
            user_intents['Asked for App Link'] += 1
        if any(w in all_user_text for w in ['done', 'complete', 'ho gaya']):
            user_intents['Said Done/Complete'] += 1

    for intent, count in user_intents.most_common():
        print(f"  {intent:<35}: {count:5d} ({count/len(chats_by_phone)*100:5.1f}%)")

    # 6. Detailed examples of users who wanted to pay / try but dropped out
    print("\n" + "="*70)
    print("5. SAMPLES OF HIGH-INTENT DROPOFFS")
    print("="*70)
    sample_count = 0
    for phone, msgs in abandoned_after_escalation:
        user_texts = [m['message'] for m in msgs]
        if any(w in " ".join(user_texts).lower() for w in ['pay', 'start', 'timing', 'admission', 'ok', 'yes', 'form', 'morning']):
            sample_count += 1
            print(f"\n[Case {sample_count}] Phone: {phone}")
            print(f"  State: stage={state_by_phone.get(phone, {}).get('stage')}, follow_up_count={state_by_phone.get(phone, {}).get('follow_up_count')}")
            for m in chats_by_phone[phone][-8:]:
                print(f"    [{m['role'].upper()}]: {m['message'][:120]}")
            if sample_count >= 5:
                break

if __name__ == '__main__':
    main()
