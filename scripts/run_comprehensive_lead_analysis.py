"""
Run Comprehensive Lead & Funnel Conversion Analysis
Matches the uploaded October 10-15 data against:
1. CTWA Master Leads (data/contacts_leads.csv)
2. Supabase Live CRM / Sales Bot Leads (user_session_state & chat_history)
3. Direct Cohort Funnel (within the provided dataset)
"""

import csv
import re
import sys
from pathlib import Path
from collections import defaultdict

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from src.services.supabase_client import supabase


def clean(v):
    return re.sub(r'\D', '', str(v or ''))


def get_keys(phone):
    d = clean(phone)
    if not d:
        return []
    keys = []
    if len(d) >= 10:
        keys.append(d[-10:])
    if d not in keys:
        keys.append(d)
    return keys


def load_csv(path):
    with open(path, mode="r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main():
    print("=" * 80)
    print("      COMPREHENSIVE MULTI-CHANNEL CONVERSION MATCHING REPORT")
    print("=" * 80)

    # 1. Load Uploaded Datasets
    profiles = load_csv("data/new_profiles_oct15.csv")
    trials = load_csv("data/new_trials_oct15.csv")
    payments = load_csv("data/new_payments_oct15.csv")
    ctwa_leads = load_csv("data/contacts_leads.csv")

    print(f"\n[+] Datasets Loaded:")
    print(f"    - CTWA Master Contacts (contacts_leads.csv) : {len(ctwa_leads)} leads")
    print(f"    - New Profiles Created (Oct 10 - Oct 15)     : {len(profiles)} records")
    print(f"    - New Trials Booked    (Oct 11 - Oct 15)     : {len(trials)} records")
    print(f"    - New Payments Completed(Oct 10 - Oct 15)    : {len(payments)} records")

    # Index Uploaded Datasets
    prof_by_key = defaultdict(list)
    for p in profiles:
        for k in get_keys(p.get("contact")):
            prof_by_key[k].append(p)

    trials_by_key = defaultdict(list)
    for t in trials:
        for k in get_keys(t.get("contact_number")):
            trials_by_key[k].append(t)

    payments_by_key = defaultdict(list)
    for pay in payments:
        for k in get_keys(pay.get("contact_no")):
            payments_by_key[k].append(pay)

    # -------------------------------------------------------------
    # SECTION 1: MATCHING AGAINST CTWA MASTER LEADS (contacts_leads.csv)
    # -------------------------------------------------------------
    ctwa_matched_profiles = []
    ctwa_matched_trials = []
    ctwa_matched_payments = []

    for lead in ctwa_leads:
        cc = lead.get("Country Code", "")
        phone = lead.get("Phone Number", "")
        keys = []
        cc_d = clean(cc)
        pn_d = clean(phone)
        full = cc_d + pn_d
        if len(pn_d) >= 10:
            keys.append(pn_d[-10:])
        elif len(full) >= 10:
            keys.append(full[-10:])
        if full and full not in keys:
            keys.append(full)
        if pn_d and pn_d not in keys:
            keys.append(pn_d)

        # Check Profiles
        for k in keys:
            if k in prof_by_key:
                ctwa_matched_profiles.append((lead, prof_by_key[k]))
                break

        # Check Trials
        for k in keys:
            if k in trials_by_key:
                ctwa_matched_trials.append((lead, trials_by_key[k]))
                break

        # Check Payments
        for k in keys:
            if k in payments_by_key:
                ctwa_matched_payments.append((lead, payments_by_key[k]))
                break

    print("\n" + "=" * 80)
    print("  1. MATCH AGAINST CTWA MASTER LEADS (contacts_leads.csv - 629 leads)")
    print("=" * 80)
    print(f"Profiles Created in App : {len(ctwa_matched_profiles)} / 629 ({len(ctwa_matched_profiles)/len(ctwa_leads)*100:.2f}%)")
    print(f"Trials Booked / Taken   : {len(ctwa_matched_trials)} / 629 ({len(ctwa_matched_trials)/len(ctwa_leads)*100:.2f}%)")
    print(f"Payments Completed      : {len(ctwa_matched_payments)} / 629 ({len(ctwa_matched_payments)/len(ctwa_leads)*100:.2f}%)")

    if ctwa_matched_profiles:
        print("\n  [+] Profiles Matched:")
        for idx, (lead, ps) in enumerate(ctwa_matched_profiles, 1):
            p = ps[0]
            print(f"      {idx}. Lead: {lead['Name']} ({lead['Phone Number']}) -> App Profile: {p.get('profile_name')} (Created: {p.get('created_at')[:10]})")

    if ctwa_matched_trials:
        print("\n  [+] Trials Matched:")
        for idx, (lead, ts) in enumerate(ctwa_matched_trials, 1):
            t = ts[0]
            print(f"      {idx}. Lead: {lead['Name']} ({lead['Phone Number']}) -> Course: {t.get('course_name')} | Teacher: {t.get('teacher_name')} | Trial Date: {t.get('date_of_trial')[:10]}")

    if ctwa_matched_payments:
        print("\n  [+] Payments Matched:")
        for idx, (lead, pays) in enumerate(ctwa_matched_payments, 1):
            pay = pays[0]
            print(f"      {idx}. Lead: {lead['Name']} ({lead['Phone Number']}) -> Paid: Rs.{pay.get('grand_total')} | Course: {pay.get('course_name')}")

    # -------------------------------------------------------------
    # SECTION 2: MATCHING AGAINST LIVE CRM SALES BOT LEADS (Supabase)
    # -------------------------------------------------------------
    print("\n" + "=" * 80)
    print("  2. MATCH AGAINST LIVE CRM / SALES BOT LEADS (Supabase user_session_state)")
    print("=" * 80)
    all_bot_users = {}
    page = 0
    while True:
        r = supabase.table('user_session_state').select('*').range(page*1000, (page+1)*1000 - 1).execute()
        data = r.data or []
        for d in data:
            p = clean(d.get('phone'))
            for k in get_keys(p):
                all_bot_users[k] = d
        if len(data) < 1000:
            break
        page += 1

    total_bot_leads = len(all_bot_users)

    crm_matched_profiles = []
    seen_prof_leads = set()
    for p in profiles:
        for k in get_keys(p.get("contact")):
            if k in all_bot_users and k not in seen_prof_leads:
                seen_prof_leads.add(k)
                crm_matched_profiles.append((p, all_bot_users[k]))
                break

    crm_matched_trials = []
    seen_trial_leads = set()
    for t in trials:
        for k in get_keys(t.get("contact_number")):
            if k in all_bot_users and k not in seen_trial_leads:
                seen_trial_leads.add(k)
                crm_matched_trials.append((t, all_bot_users[k]))
                break

    crm_matched_payments = []
    seen_pay_leads = set()
    for pay in payments:
        for k in get_keys(pay.get("contact_no")):
            if k in all_bot_users:
                crm_matched_payments.append((pay, all_bot_users[k]))
                break

    print(f"Total Unique Phone Keys in CRM DB: {total_bot_leads}")
    print(f"App Profiles Created Matched    : {len(crm_matched_profiles)} / {len(profiles)} uploaded profiles")
    print(f"Trials Booked / Taken Matched   : {len(crm_matched_trials)} / {len(trials)} uploaded trials")
    print(f"Payments Completed Matched      : {len(crm_matched_payments)} / {len(payments)} uploaded payments")

    if crm_matched_payments:
        print("\n  [+] Paying Customers who came directly from CRM / Sales Bot:")
        total_bot_rev = sum(float(p[0].get("grand_total") or 0) for p in crm_matched_payments)
        for idx, (pay, bot) in enumerate(crm_matched_payments, 1):
            print(f"      {idx}. {pay.get('profile_name')} ({pay.get('contact_no')})")
            print(f"         • Course       : {pay.get('course_name')}")
            print(f"         • Amount Paid  : Rs. {pay.get('grand_total')} on {pay.get('date_of_payment')}")
            print(f"         • Coupon Used  : {pay.get('coupon_code') or 'None'}")
            print(f"         • Counsellor   : {pay.get('counsellor_name') or 'Direct/Bot'}")
            print(f"         • Bot Stage    : {bot.get('stage')}")
        print(f"      Total Bot-Attributed Revenue: Rs. {total_bot_rev:.2f}")

    # -------------------------------------------------------------
    # SECTION 3: DIRECT FUNNEL OF THE UPLOADED COHORT (Oct 10 - Oct 15)
    # -------------------------------------------------------------
    print("\n" + "=" * 80)
    print("  3. DIRECT COHORT FUNNEL (Within the Uploaded Oct 10 - Oct 15 Datasets)")
    print("=" * 80)
    unique_paying_contacts = {}
    for pay in payments:
        p_phone = clean(pay.get("contact_no"))
        p_key = p_phone[-10:] if len(p_phone) >= 10 else p_phone
        unique_paying_contacts.setdefault(p_key, []).append(pay)

    unique_paying_users_count = len(unique_paying_contacts)
    total_gmv = sum(float(p.get("grand_total") or 0) for p in payments)

    # Paying users who created profiles
    paying_with_profile = 0
    paying_with_trials = 0

    print(f"Total Profiles Created in Batch : {len(profiles)}")
    print(f"Total Trials Booked in Batch    : {len(trials)}")
    print(f"Total Transactions in Batch     : {len(payments)} (from {unique_paying_users_count} unique users)")
    print(f"Total Payment GMV in Batch      : Rs. {total_gmv:,.2f}")
    print("-" * 80)
    print(f"{'#':<3} | {'Customer Name':<18} | {'Phone':<14} | {'Course Paid':<22} | {'Amount':<8} | {'Profile?':<8} | {'Trial?'}")
    print("-" * 80)
    for idx, (pkey, pays) in enumerate(unique_paying_contacts.items(), 1):
        first_pay = pays[0]
        name = first_pay.get("profile_name", "")[:17]
        phone = first_pay.get("contact_no", "")
        course = first_pay.get("course_name", "")[:21]
        amt = sum(float(p.get("grand_total") or 0) for p in pays)
        
        has_p = pkey in prof_by_key
        has_t = pkey in trials_by_key
        if has_p: paying_with_profile += 1
        if has_t: paying_with_trials += 1

        p_str = "YES" if has_p else "NO"
        t_str = "YES" if has_t else "NO"
        print(f"{idx:<3} | {name:<18} | {phone:<14} | {course:<22} | Rs.{amt:<5.0f} | {p_str:<8} | {t_str}")

    print("-" * 80)
    print(f"Paying Users who also created Profile in this Batch : {paying_with_profile} / {unique_paying_users_count} ({paying_with_profile/unique_paying_users_count*100:.1f}%)")
    print(f"Paying Users who also booked Trial in this Batch     : {paying_with_trials} / {unique_paying_users_count} ({paying_with_trials/unique_paying_users_count*100:.1f}%)")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
