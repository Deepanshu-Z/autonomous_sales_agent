"""
Comprehensive Full-Funnel Conversion Matching Script
Matches CTWA contact leads against:
1. Profile Creation DB
2. Trial Bookings DB
3. Completed Payments DB

Outputs exact counts, percentages, and detailed lead-level status across the entire funnel.
"""

import csv
import re
import sys
import argparse
from pathlib import Path
from collections import defaultdict

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass


def clean_phone_digits(val: str) -> str:
    """Strips all non-digit characters from a phone number string."""
    return re.sub(r"\D", "", str(val or ""))


def get_phone_keys(country_code: str, phone_number: str) -> list[str]:
    """
    Returns canonical lookup keys for a phone number:
    - 10-digit suffix
    - Full digits without '+'
    - Raw phone number digits
    """
    cc_digits = clean_phone_digits(country_code)
    pn_digits = clean_phone_digits(phone_number)
    full = cc_digits + pn_digits

    keys = []
    if len(pn_digits) >= 10:
        keys.append(pn_digits[-10:])
    elif len(full) >= 10:
        keys.append(full[-10:])

    if full and full not in keys:
        keys.append(full)
    if pn_digits and pn_digits not in keys:
        keys.append(pn_digits)

    return keys


def extract_record_keys(phone_str: str) -> list[str]:
    """Generates phone lookup keys for a single contact string."""
    digits = clean_phone_digits(phone_str)
    if not digits:
        return []
    keys = []
    if len(digits) >= 10:
        keys.append(digits[-10:])
    if digits not in keys:
        keys.append(digits)
    return keys


def load_dataset_index(csv_path: Path, phone_cols: list[str]) -> tuple[dict[str, list[dict]], list[dict]]:
    """Loads a CSV file and indexes rows by normalized phone keys."""
    index = defaultdict(list)
    rows = []
    if not csv_path.exists():
        return index, rows

    with open(csv_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
            # Find phone from possible columns
            phone_val = ""
            for col in phone_cols:
                if col in row and row[col]:
                    phone_val = row[col]
                    break
            
            keys = extract_record_keys(phone_val)
            for k in keys:
                index[k].append(row)
    return index, rows


def run_full_funnel_match(
    contacts_csv: Path,
    profiles_csv: Path,
    trials_csv: Path,
    payments_csv: Path,
    output_report_csv: Path | None = None
) -> dict:
    # 1. Load Indexes
    profiles_index, all_profiles = load_dataset_index(profiles_csv, ["contact", "contact_number", "contact_no", "phone"])
    trials_index, all_trials = load_dataset_index(trials_csv, ["contact_number", "contact", "contact_no", "phone"])
    payments_index, all_payments = load_dataset_index(payments_csv, ["contact_no", "contact_number", "contact", "phone"])

    # 2. Iterate over Contacts / Leads
    leads = []
    with open(contacts_csv, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            leads.append(row)

    matched_profiles = []
    matched_trials = []
    matched_payments = []
    detailed_rows = []

    for lead in leads:
        name = lead.get("Name", "").strip()
        cc = lead.get("Country Code", "").strip()
        phone = lead.get("Phone Number", "").strip()
        created_at = lead.get("Creation Date", "").strip()
        tags = lead.get("Tags", "").strip()

        keys = get_phone_keys(cc, phone)

        # Profile check
        p_matches = []
        for k in keys:
            if k in profiles_index:
                p_matches = profiles_index[k]
                break

        # Trial check
        t_matches = []
        for k in keys:
            if k in trials_index:
                t_matches = trials_index[k]
                break

        # Payment check
        pay_matches = []
        for k in keys:
            if k in payments_index:
                pay_matches = payments_index[k]
                break

        has_profile = len(p_matches) > 0
        has_trial = len(t_matches) > 0
        has_payment = len(pay_matches) > 0

        latest_p = sorted(p_matches, key=lambda x: x.get("created_at", ""), reverse=True)[0] if has_profile else {}
        latest_t = sorted(t_matches, key=lambda x: x.get("trial_created_at", ""), reverse=True)[0] if has_trial else {}
        latest_pay = sorted(pay_matches, key=lambda x: x.get("date_of_payment", ""), reverse=True)[0] if has_payment else {}

        detail = {
            "Lead Name": name,
            "Country Code": cc,
            "Phone Number": phone,
            "Lead Created At": created_at,
            "Tags": tags,
            "Has Profile": "YES" if has_profile else "NO",
            "Profile Name": latest_p.get("profile_name", ""),
            "Profile Email": latest_p.get("email", ""),
            "Profile Created At": latest_p.get("created_at", ""),
            "Has Trial": "YES" if has_trial else "NO",
            "Trial Count": len(t_matches),
            "Trial Courses": ", ".join(sorted(set(t.get("course_name", "") for t in t_matches if t.get("course_name")))),
            "Latest Trial Date": latest_t.get("date_of_trial", ""),
            "Teacher Name": latest_t.get("teacher_name", ""),
            "Timing": latest_t.get("timing", ""),
            "Has Paid": "YES" if has_payment else "NO",
            "Payment Count": len(pay_matches),
            "Paid Courses": ", ".join(sorted(set(p.get("course_name", "") for p in pay_matches if p.get("course_name")))),
            "Total Paid Amount": sum(float(p.get("grand_total") or 0) for p in pay_matches),
            "Payment Dates": ", ".join(p.get("date_of_payment", "") for p in pay_matches),
            "Coupon Codes": ", ".join(p.get("coupon_code", "") for p in pay_matches if p.get("coupon_code")),
            "Counsellor": ", ".join(p.get("counsellor_name", "") for p in pay_matches if p.get("counsellor_name")),
        }
        detailed_rows.append(detail)

        if has_profile:
            matched_profiles.append((lead, latest_p))
        if has_trial:
            matched_trials.append((lead, latest_t, t_matches))
        if has_payment:
            matched_payments.append((lead, latest_pay, pay_matches))

    # Export to CSV if requested
    if output_report_csv:
        output_report_csv.parent.mkdir(parents=True, exist_ok=True)
        if detailed_rows:
            fieldnames = list(detailed_rows[0].keys())
            with open(output_report_csv, mode="w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(detailed_rows)

    return {
        "total_leads": len(leads),
        "total_input_profiles": len(all_profiles),
        "total_input_trials": len(all_trials),
        "total_input_payments": len(all_payments),
        "matched_profiles_count": len(matched_profiles),
        "matched_trials_count": len(matched_trials),
        "matched_payments_count": len(matched_payments),
        "matched_profiles": matched_profiles,
        "matched_trials": matched_trials,
        "matched_payments": matched_payments,
        "detailed_rows": detailed_rows,
        "output_report_csv": str(output_report_csv) if output_report_csv else None,
    }


def print_funnel_report(title: str, results: dict):
    total = results["total_leads"]
    prof_cnt = results["matched_profiles_count"]
    trial_cnt = results["matched_trials_count"]
    pay_cnt = results["matched_payments_count"]

    prof_pct = (prof_cnt / total * 100) if total else 0.0
    trial_pct = (trial_cnt / total * 100) if total else 0.0
    pay_pct = (pay_cnt / total * 100) if total else 0.0

    print("\n" + "=" * 80)
    print(f"        {title.upper()}")
    print("=" * 80)
    print(f"Total CTWA Leads Analyzed            : {total}")
    print(f"Input Records in Batch               : Profiles: {results['total_input_profiles']} | Trials: {results['total_input_trials']} | Payments: {results['total_input_payments']}")
    print("-" * 80)
    print(f"1. Leads Who Created App Profile     : {prof_cnt:<5} ({prof_pct:.2f}% conversion)")
    print(f"2. Leads Who Booked / Took Trial     : {trial_cnt:<5} ({trial_pct:.2f}% conversion)")
    print(f"3. Leads Who Completed Payment       : {pay_cnt:<5} ({pay_pct:.2f}% conversion)")
    print("=" * 80)

    # 1. Profiles details
    if prof_cnt > 0:
        print(f"\n[+] LEADS WHO CREATED APP PROFILE ({prof_cnt} users):")
        print(f"{'#':<3} | {'Lead Name':<18} | {'Phone':<14} | {'App Profile Name':<20} | {'Created At':<12}")
        print("-" * 75)
        for idx, (l, p) in enumerate(results["matched_profiles"], 1):
            name = l["Name"][:17]
            phone = f"{l['Country Code']} {l['Phone Number']}"
            pname = p.get("profile_name", "")[:19]
            c_at = p.get("created_at", "")[:10]
            print(f"{idx:<3} | {name:<18} | {phone:<14} | {pname:<20} | {c_at:<12}")

    # 2. Trials details
    if trial_cnt > 0:
        print(f"\n[+] LEADS WHO BOOKED / TOOK A TRIAL ({trial_cnt} users):")
        print(f"{'#':<3} | {'Lead Name':<18} | {'Phone':<14} | {'Course':<20} | {'Teacher':<15} | {'Trial Date':<12}")
        print("-" * 88)
        for idx, (l, t, all_t) in enumerate(results["matched_trials"], 1):
            name = l["Name"][:17]
            phone = f"{l['Country Code']} {l['Phone Number']}"
            course = t.get("course_name", "")[:19]
            teacher = t.get("teacher_name", "")[:14]
            t_date = t.get("date_of_trial", "").split(" ")[0]
            print(f"{idx:<3} | {name:<18} | {phone:<14} | {course:<20} | {teacher:<15} | {t_date:<12}")

    # 3. Payments details
    if pay_cnt > 0:
        print(f"\n[+] LEADS WHO COMPLETED PAYMENT ({pay_cnt} users):")
        print(f"{'#':<3} | {'Lead Name':<18} | {'Phone':<14} | {'Course':<18} | {'Amount':<8} | {'Counsellor':<14} | {'Coupon':<10}")
        print("-" * 92)
        for idx, (l, p, all_p) in enumerate(results["matched_payments"], 1):
            name = l["Name"][:17]
            phone = f"{l['Country Code']} {l['Phone Number']}"
            course = p.get("course_name", "")[:17]
            amt = str(p.get("grand_total", ""))
            counsellor = p.get("counsellor_name", "")[:13]
            coupon = p.get("coupon_code", "")[:9]
            print(f"{idx:<3} | {name:<18} | {phone:<14} | {course:<18} | {amt:<8} | {counsellor:<14} | {coupon:<10}")

    if results.get("output_report_csv"):
        print(f"\nFull lead-by-lead CSV report exported to: {results['output_report_csv']}")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Match CTWA Contacts with Full Funnel Data")
    parser.add_argument("--contacts", default="data/contacts_leads.csv", help="Path to contacts CSV")
    parser.add_argument("--profiles", required=True, help="Path to profiles CSV")
    parser.add_argument("--trials", required=True, help="Path to trials CSV")
    parser.add_argument("--payments", required=True, help="Path to payments CSV")
    parser.add_argument("--output", default="data/full_funnel_report.csv", help="Output report CSV path")
    parser.add_argument("--title", default="FULL-FUNNEL CONVERSION REPORT", help="Report Title")
    args = parser.parse_args()

    res = run_full_funnel_match(
        Path(args.contacts),
        Path(args.profiles),
        Path(args.trials),
        Path(args.payments),
        Path(args.output) if args.output else None
    )
    print_funnel_report(args.title, res)
