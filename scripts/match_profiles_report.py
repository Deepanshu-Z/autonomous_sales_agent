"""
Profile Creation Matching Script
Matches CTWA contact leads against the Application Profile Creation DB
to calculate count and conversion percentage of profiles created.
"""

import csv
import re
import argparse
from pathlib import Path


def clean_digits(val: str) -> str:
    """Extracts only digits from phone string."""
    return re.sub(r"\D", "", str(val or ""))


def get_phone_keys(country_code: str, phone_number: str) -> list[str]:
    """Generates standard 10-digit suffix and full international digit keys."""
    cc_digits = clean_digits(country_code)
    pn_digits = clean_digits(phone_number)
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


def load_profiles(profiles_csv: Path) -> dict[str, list[dict]]:
    """Loads profiles and indexes them by phone keys."""
    index = {}
    with open(profiles_csv, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_contact = row.get("contact", "")
            d = clean_digits(raw_contact)
            if not d:
                continue

            keys = set()
            if len(d) >= 10:
                keys.add(d[-10:])
            keys.add(d)

            for k in keys:
                index.setdefault(k, []).append(row)
    return index


def match_profiles(
    contacts_csv: Path,
    profiles_csv: Path,
    output_csv: Path | None = None
) -> dict:
    """Matches leads to app profiles, returns summary metrics and writes CSV."""
    profiles_index = load_profiles(profiles_csv)

    matched = []
    unmatched = []
    report_rows = []

    with open(contacts_csv, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for contact in reader:
            name = contact.get("Name", "").strip()
            cc = contact.get("Country Code", "").strip()
            phone = contact.get("Phone Number", "").strip()
            created_at_lead = contact.get("Creation Date", "").strip()
            tags = contact.get("Tags", "").strip()

            keys = get_phone_keys(cc, phone)
            matching_profiles = None
            for k in keys:
                if k in profiles_index:
                    matching_profiles = profiles_index[k]
                    break

            if matching_profiles:
                # Latest profile if multiple
                sorted_p = sorted(matching_profiles, key=lambda x: x.get("created_at", ""), reverse=True)
                latest = sorted_p[0]
                row = {
                    "Name": name,
                    "Country Code": cc,
                    "Phone Number": phone,
                    "Lead Creation Date": created_at_lead,
                    "Tags": tags,
                    "Status": "PROFILE_CREATED",
                    "Profile Name": latest.get("profile_name", ""),
                    "Profile Email": latest.get("email", ""),
                    "Profile Created At": latest.get("created_at", ""),
                }
                matched.append(row)
                report_rows.append(row)
            else:
                row = {
                    "Name": name,
                    "Country Code": cc,
                    "Phone Number": phone,
                    "Lead Creation Date": created_at_lead,
                    "Tags": tags,
                    "Status": "NO_PROFILE",
                    "Profile Name": "",
                    "Profile Email": "",
                    "Profile Created At": "",
                }
                unmatched.append(row)
                report_rows.append(row)

    if output_csv:
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "Name", "Country Code", "Phone Number", "Lead Creation Date", "Tags",
            "Status", "Profile Name", "Profile Email", "Profile Created At"
        ]
        with open(output_csv, mode="w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(report_rows)

    total = len(report_rows)
    pct = (len(matched) / total * 100) if total else 0.0

    return {
        "total_leads": total,
        "profiles_created": len(matched),
        "no_profile": len(unmatched),
        "creation_rate": pct,
        "matched": matched,
        "unmatched": unmatched,
        "output_csv": str(output_csv) if output_csv else None,
    }


def print_report(res: dict):
    """Prints clean terminal summary."""
    print("=" * 70)
    print("        APP PROFILE CREATION CONVERSION REPORT")
    print("=" * 70)
    print(f"Total CTWA Leads Analyzed       : {res['total_leads']}")
    print(f"Profiles Created in App (Matched): {res['profiles_created']}")
    print(f"No Profile Created               : {res['no_profile']}")
    print(f"Profile Creation Conversion Rate : {res['creation_rate']:.2f}%")
    print("-" * 70)
    print(f"LEADS WHO CREATED PROFILE ({res['profiles_created']} users):")
    print(f"{'#':<3} | {'Lead Name':<20} | {'Phone':<14} | {'App Profile Name':<22} | {'Created At':<12}")
    print("-" * 70)
    for idx, u in enumerate(res["matched"], 1):
        name = u["Name"].encode("ascii", errors="replace").decode()[:19]
        phone = f"{u['Country Code']} {u['Phone Number']}"
        pname = u["Profile Name"].encode("ascii", errors="replace").decode()[:21]
        c_at = u["Profile Created At"][:10]
        print(f"{idx:<3} | {name:<20} | {phone:<14} | {pname:<22} | {c_at:<12}")
    print("=" * 70)
    if res.get("output_csv"):
        print(f"Full report exported to: {res['output_csv']}")


def self_test():
    assert clean_digits("+91 9694961855") == "919694961855"
    assert "9694961855" in get_phone_keys("+91", "9694961855")
    print("Self-test passed: phone matching logic verified.")


if __name__ == "__main__":
    self_test()
    parser = argparse.ArgumentParser(description="Match CTWA Contacts with Profiles DB")
    parser.add_argument("--contacts", default="data/contacts_leads.csv", help="Path to contacts CSV")
    parser.add_argument("--profiles", default="data/profiles_created_db.csv", help="Path to profiles CSV")
    parser.add_argument("--output", default="data/profile_creation_report.csv", help="Output CSV path")
    args = parser.parse_args()

    results = match_profiles(Path(args.contacts), Path(args.profiles), Path(args.output))
    print_report(results)
