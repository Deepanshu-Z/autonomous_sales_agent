"""
check_leads_in_prod_db.py — Comprehensive Lead Profile & Trial Verification Script.

Fetches all unique phone numbers from the sales agent database (Supabase)
and verifies their profile creation and trial status against the production
PostgreSQL application database.

Outputs:
1. Executive Summary (Profile Created, Not Created, Trials Taken)
2. Tabular breakdown with Phone, Profile Status, Trial Status, Name, and Email
3. Optional CSV export (--csv <path>)
"""

import sys
import os
import argparse
import asyncio
from typing import Dict, Any, List, Set

# Ensure project root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Configure UTF-8 output for Windows console
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from src.services.supabase_client import supabase
from src.services.app_db_verifier import (
    _get_pool,
    normalize_phone_last10,
    PROD_APP_USERS_TABLE,
    PROD_APP_PHONE_COLUMN,
)


def fetch_all_unique_supabase_phones() -> Set[str]:
    """
    Fetches all distinct phone numbers from both 'user_session_state' and 'chat_history'
    in Supabase to ensure complete coverage of every lead.
    """
    if supabase is None:
        raise RuntimeError("Supabase client is not initialized. Check SUPABASE_URL and SUPABASE_KEY in .env.")

    all_phones = set()
    tables_to_query = ["user_session_state", "chat_history"]

    for table in tables_to_query:
        page = 0
        page_size = 1000
        while True:
            try:
                res = (
                    supabase.table(table)
                    .select("phone")
                    .range(page * page_size, (page + 1) * page_size - 1)
                    .execute()
                )
                data = res.data or []
                for row in data:
                    raw_phone = row.get("phone")
                    if raw_phone:
                        clean_raw = str(raw_phone).strip()
                        if clean_raw:
                            all_phones.add(clean_raw)

                if len(data) < page_size:
                    break
                page += 1
            except Exception as e:
                print(f"[warning] Error querying Supabase table '{table}' (page {page}): {e}")
                break

    return all_phones


async def verify_against_prod_db(clean_phones: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Performs high-performance batch query on PostgreSQL RDS to check profile
    and trial status for all normalized 10-digit phone numbers in one network roundtrip.
    """
    pool = await _get_pool()
    if pool is None:
        raise RuntimeError("Production App DB pool could not be initialized. Check PROD_APP_DB_URL in .env.")

    query = f"""
        SELECT DISTINCT ON (clean_phone)
            p.id AS profile_id,
            p.full_name,
            p.email,
            p.created_at AS profile_created_at,
            RIGHT(regexp_replace(p.{PROD_APP_PHONE_COLUMN}, '\\D', '', 'g'), 10) AS clean_phone,
            COUNT(t.id) AS trial_count,
            MAX(t.trial_date) AS latest_trial_date
        FROM "{PROD_APP_USERS_TABLE}" p
        LEFT JOIN "trial" t ON t."profileId" = p.id
        WHERE RIGHT(regexp_replace(p.{PROD_APP_PHONE_COLUMN}, '\\D', '', 'g'), 10) = ANY($1::text[])
          AND (p.full_name IS NULL OR (p.full_name NOT ILIKE 'deleted@%' AND p.full_name NOT ILIKE 'deleted_%'))
        GROUP BY p.id, p.full_name, p.email, p.created_at, clean_phone
        ORDER BY clean_phone, p.created_at DESC NULLS LAST;
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, clean_phones)

    prod_map = {}
    for r in rows:
        prod_map[r["clean_phone"]] = dict(r)

    return prod_map


async def run_audit(args):
    print("=" * 110)
    print(" 🔍 SENSATIONZ LEAD VERIFICATION: SUPABASE (WHATSAPP) <-> PROD RDS (APP)")
    print("=" * 110)

    # 1. Fetch raw phones from Supabase
    print("⏳ [1/3] Fetching all unique lead numbers from Supabase...")
    raw_phones = fetch_all_unique_supabase_phones()
    print(f"   -> Found {len(raw_phones)} total raw numbers across 'user_session_state' and 'chat_history'.")

    # 2. Normalize and map to 10-digit phone keys
    norm_to_raw: Dict[str, List[str]] = {}
    invalid_numbers: List[str] = []
    for raw in raw_phones:
        norm = normalize_phone_last10(raw)
        if norm:
            norm_to_raw.setdefault(norm, []).append(raw)
        else:
            invalid_numbers.append(raw)

    valid_10_digit_phones = sorted(list(norm_to_raw.keys()))
    print(f"   -> Cleaned into {len(valid_10_digit_phones)} valid 10-digit mobile numbers.")
    if invalid_numbers:
        print(f"   -> (Ignored {len(invalid_numbers)} test/invalid formats e.g. {invalid_numbers[:3]})")

    # 3. Query Prod DB
    print("\n⏳ [2/3] Querying Production App DB (PostgreSQL) for Profiles & Trial Bookings...")
    prod_map = await verify_against_prod_db(valid_10_digit_phones)
    print(f"   -> Matches checked against '{PROD_APP_USERS_TABLE}' & 'trial' tables.")

    # 4. Aggregate results
    profiles_with_trials = []
    profiles_without_trials = []
    no_profiles = []

    for norm_phone in valid_10_digit_phones:
        raw_list = norm_to_raw[norm_phone]
        display_phone = raw_list[0] if raw_list else norm_phone
        
        if norm_phone in prod_map:
            p_data = prod_map[norm_phone]
            trial_cnt = p_data.get("trial_count") or 0
            record = {
                "phone": display_phone,
                "norm_phone": norm_phone,
                "profile_created": "YES",
                "trial_taken": "YES" if trial_cnt > 0 else "NO",
                "trial_count": trial_cnt,
                "name": p_data.get("full_name") or "-",
                "email": p_data.get("email") or "-",
                "profile_date": str(p_data.get("profile_created_at") or "")[:10] or "-",
            }
            if trial_cnt > 0:
                profiles_with_trials.append(record)
            else:
                profiles_without_trials.append(record)
        else:
            record = {
                "phone": display_phone,
                "norm_phone": norm_phone,
                "profile_created": "NO",
                "trial_taken": "NO",
                "trial_count": 0,
                "name": "-",
                "email": "-",
                "profile_date": "-",
            }
            no_profiles.append(record)

    total_leads = len(valid_10_digit_phones)
    total_profiles = len(profiles_with_trials) + len(profiles_without_trials)
    total_no_profiles = len(no_profiles)
    total_trials = len(profiles_with_trials)

    pct_profile = (total_profiles / total_leads * 100) if total_leads else 0
    pct_no_profile = (total_no_profiles / total_leads * 100) if total_leads else 0
    pct_trials_of_profiles = (total_trials / total_profiles * 100) if total_profiles else 0
    pct_trials_of_total = (total_trials / total_leads * 100) if total_leads else 0

    # 5. Print Executive Summary
    print("\n" + "=" * 110)
    print(" 📊 EXECUTIVE AUDIT SUMMARY")
    print("=" * 110)
    print(f" Total Unique Valid Leads from WhatsApp DB : {total_leads:>6}")
    print(f"  ├──  Profile Created in App             : {total_profiles:>6}  ({pct_profile:5.1f}% of all leads)")
    print(f"  │    ├──  Trial Taken                   : {total_trials:>6}  ({pct_trials_of_profiles:5.1f}% of profiles | {pct_trials_of_total:4.1f}% of total leads)")
    print(f"  │    └── ❌ No Trial Taken               : {len(profiles_without_trials):>6}  ({(100 - pct_trials_of_profiles):5.1f}% of profiles)")
    print(f"  └── ❌ No Profile Created                : {total_no_profiles:>6}  ({pct_no_profile:5.1f}% of all leads)")
    print("=" * 110)

    # 6. Detailed Table Output
    all_records = profiles_with_trials + profiles_without_trials + no_profiles

    # Filter if requested
    records_to_print = all_records
    if args.filter == "created":
        records_to_print = profiles_with_trials + profiles_without_trials
    elif args.filter == "trials":
        records_to_print = profiles_with_trials
    elif args.filter == "no-trials":
        records_to_print = profiles_without_trials
    elif args.filter == "no-profile":
        records_to_print = no_profiles

    print(f"\n📋 DETAILED LIST ({len(records_to_print)} records displayed, filter: '{args.filter}'):")
    print("-" * 110)
    header = f"{'#':<5} | {'PHONE NUMBER':<14} | {'PROFILE?':<8} | {'TRIAL?':<6} | {'TRIALS':<6} | {'NAME':<24} | {'EMAIL'}"
    print(header)
    print("-" * 110)

    # Limit printing in console if too many, unless --all flag is passed
    limit = None if args.all else (args.limit or 150)
    for idx, r in enumerate(records_to_print[:limit], 1):
        name = r["name"][:24] if r["name"] else "-"
        email = r["email"] if r["email"] else "-"
        print(f"{idx:<5} | {r['phone']:<14} | {r['profile_created']:<8} | {r['trial_taken']:<6} | {r['trial_count']:<6} | {name:<24} | {email}")

    if limit and len(records_to_print) > limit:
        print("-" * 110)
        print(f"... and {len(records_to_print) - limit} more records. Use --all or --limit <n> to print more, or --csv <filename> to export.")
    print("-" * 110)

    # 7. Optional CSV export
    if args.csv:
        import csv
        with open(args.csv, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "phone",
                    "norm_phone",
                    "profile_created",
                    "trial_taken",
                    "trial_count",
                    "name",
                    "email",
                    "profile_date",
                ],
            )
            writer.writeheader()
            for r in all_records:
                writer.writerow(r)
        print(f"\n💾 Saved full lead status to CSV: {args.csv}")


def main():
    parser = argparse.ArgumentParser(description="Check all WhatsApp leads against Prod DB for profile and trial status.")
    parser.add_argument(
        "--filter",
        choices=["all", "created", "trials", "no-trials", "no-profile"],
        default="all",
        help="Filter which rows to print in table (default: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of rows to print to console (default: 100). Use --all to print all.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Print all rows to console without truncation limit.",
    )
    parser.add_argument(
        "--csv",
        type=str,
        default=None,
        help="Optional file path to export all results to CSV (e.g. --csv leads_audit.csv)",
    )

    args = parser.parse_args()
    asyncio.run(run_audit(args))


if __name__ == "__main__":
    main()
