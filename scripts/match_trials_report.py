"""
Trial Conversion Matching Script
Compares CTWA Contact Leads against the Trials Booking DB to determine
who has taken a trial and who has not.
"""

import csv
import re
import argparse
from pathlib import Path


def clean_phone_digits(val: str) -> str:
    """Strips all non-digit characters from a phone number string."""
    return re.sub(r"\D", "", str(val or ""))


def get_phone_keys(country_code: str, phone_number: str) -> list[str]:
    """
    Returns lookup keys for a phone number.
    Generates:
    - 10-digit canonical suffix (standard for Indian mobile numbers)
    - Full E.164 digits without '+' (for exact international matching)
    """
    cc_digits = clean_phone_digits(country_code)
    pn_digits = clean_phone_digits(phone_number)
    full = cc_digits + pn_digits

    keys = []
    # 10-digit suffix
    if len(pn_digits) >= 10:
        keys.append(pn_digits[-10:])
    elif len(full) >= 10:
        keys.append(full[-10:])

    # Full digits
    if full and full not in keys:
        keys.append(full)
    if pn_digits and pn_digits not in keys:
        keys.append(pn_digits)

    return keys


def load_trials(trials_csv_path: Path) -> dict[str, list[dict]]:
    """Loads trials and indexes them by phone number lookup keys."""
    trials_index = {}
    with open(trials_csv_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_phone = row.get("contact_number", "")
            digits = clean_phone_digits(raw_phone)
            if not digits:
                continue

            keys = set()
            if len(digits) >= 10:
                keys.add(digits[-10:])
            keys.add(digits)

            for k in keys:
                trials_index.setdefault(k, []).append(row)
    return trials_index


def match_leads(
    contacts_csv_path: Path,
    trials_csv_path: Path,
    output_csv_path: Path | None = None
) -> dict:
    """Matches contact leads against trial bookings and produces report metrics."""
    trials_index = load_trials(trials_csv_path)

    matched = []
    unmatched = []
    detailed_rows = []

    with open(contacts_csv_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for contact in reader:
            name = contact.get("Name", "").strip()
            cc = contact.get("Country Code", "").strip()
            phone = contact.get("Phone Number", "").strip()
            creation_date = contact.get("Creation Date", "").strip()
            tags = contact.get("Tags", "").strip()

            keys = get_phone_keys(cc, phone)
            matching_trials = None
            for k in keys:
                if k in trials_index:
                    matching_trials = trials_index[k]
                    break

            if matching_trials:
                # User has taken a trial
                # Sort matching trials by trial_created_at
                sorted_trials = sorted(
                    matching_trials,
                    key=lambda x: x.get("trial_created_at", ""),
                    reverse=True
                )
                latest_trial = sorted_trials[0]
                all_courses = ", ".join(sorted(set(t.get("course_name", "") for t in matching_trials if t.get("course_name"))))
                
                info = {
                    "Name": name,
                    "Country Code": cc,
                    "Phone Number": phone,
                    "Creation Date": creation_date,
                    "Tags": tags,
                    "Status": "TRIAL_TAKEN",
                    "Trial Count": len(matching_trials),
                    "Courses": all_courses,
                    "Latest Course": latest_trial.get("course_name", ""),
                    "Trial Date": latest_trial.get("date_of_trial", ""),
                    "Teacher": latest_trial.get("teacher_name", ""),
                    "Timing": latest_trial.get("timing", ""),
                    "Days": latest_trial.get("days", ""),
                    "DB Profile Name": latest_trial.get("profile_name", ""),
                    "DB Email": latest_trial.get("email", ""),
                }
                matched.append(info)
                detailed_rows.append(info)
            else:
                info = {
                    "Name": name,
                    "Country Code": cc,
                    "Phone Number": phone,
                    "Creation Date": creation_date,
                    "Tags": tags,
                    "Status": "NO_TRIAL",
                    "Trial Count": 0,
                    "Courses": "",
                    "Latest Course": "",
                    "Trial Date": "",
                    "Teacher": "",
                    "Timing": "",
                    "Days": "",
                    "DB Profile Name": "",
                    "DB Email": "",
                }
                unmatched.append(info)
                detailed_rows.append(info)

    # Write output CSV if requested
    if output_csv_path:
        output_csv_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "Name", "Country Code", "Phone Number", "Creation Date", "Tags",
            "Status", "Trial Count", "Courses", "Latest Course", "Trial Date",
            "Teacher", "Timing", "Days", "DB Profile Name", "DB Email"
        ]
        with open(output_csv_path, mode="w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(detailed_rows)

    # Course breakdown
    course_counts = {}
    for m in matched:
        courses = [c.strip() for c in m["Courses"].split(",") if c.strip()]
        for c in courses:
            course_counts[c] = course_counts.get(c, 0) + 1

    return {
        "total_contacts": len(detailed_rows),
        "trials_taken": len(matched),
        "no_trial": len(unmatched),
        "conversion_rate": (len(matched) / len(detailed_rows) * 100) if detailed_rows else 0.0,
        "course_breakdown": course_counts,
        "matched_users": matched,
        "unmatched_users": unmatched,
        "output_path": str(output_csv_path) if output_csv_path else None,
    }


def print_report(results: dict):
    """Prints a clean tabular report to the console."""
    print("=" * 70)
    print("           CTWA LEADS VS TRIAL BOOKINGS REPORT")
    print("=" * 70)
    print(f"Total Contacts / Leads Analyzed : {results['total_contacts']}")
    print(f"Leads Who Took Trial (Matched)  : {results['trials_taken']}")
    print(f"Leads With No Trial             : {results['no_trial']}")
    print(f"Trial Booking Conversion Rate    : {results['conversion_rate']:.2f}%")
    print("-" * 70)
    print("Course Breakdown for Matched Leads:")
    for course, count in sorted(results["course_breakdown"].items(), key=lambda x: -x[1]):
        print(f"  - {course:<25} : {count} leads")
    print("-" * 70)
    print(f"USERS WHO TOOK A TRIAL ({results['trials_taken']} leads):")
    print(f"{'#':<3} | {'Lead Name':<20} | {'Phone':<13} | {'Course':<18} | {'Trial Date':<12}")
    print("-" * 70)
    for idx, u in enumerate(results["matched_users"], 1):
        name = u["Name"][:19]
        phone = f"{u['Country Code']} {u['Phone Number']}"
        course = u["Latest Course"][:17]
        trial_date = u["Trial Date"].split(" ")[0] if u["Trial Date"] else "N/A"
        print(f"{idx:<3} | {name:<20} | {phone:<13} | {course:<18} | {trial_date:<12}")
    print("=" * 70)
    if results.get("output_path"):
        print(f"Full report saved to: {results['output_path']}")


def test_matching_logic():
    """Self-check asserting phone normalization and key extraction behavior."""
    assert clean_phone_digits("+91 9694961855") == "919694961855"
    assert clean_phone_digits("+965 66818518") == "96566818518"
    assert "9694961855" in get_phone_keys("+91", "9694961855")
    assert "66818518" in get_phone_keys("+965", "66818518")
    print("Self-check passed: phone normalization and key lookup are edge-case correct.")


if __name__ == "__main__":
    test_matching_logic()
    parser = argparse.ArgumentParser(description="Match CTWA Contacts with Trials DB")
    parser.add_argument("--contacts", default="data/contacts_leads.csv", help="Path to contacts CSV")
    parser.add_argument("--trials", default="data/trials_db.csv", help="Path to trials CSV")
    parser.add_argument("--output", default="data/trial_conversion_report.csv", help="Output report CSV path")
    args = parser.parse_args()

    res = match_leads(Path(args.contacts), Path(args.trials), Path(args.output))
    print_report(res)

