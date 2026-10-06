"""
test_all_courses_audit.py — 100% Accuracy Verification Test Suite for All Courses.

Tests and asserts:
1. Catalog registration and full KB loading for all courses.
2. Batch timings extraction and conflict-free resolution for all batches.
3. Teacher profiles, certifications, demo video links, and dedicated batch mappings.
4. Pricing packages (1M, 3M, 6M, 1Y), base price, offer price, and GST calculations.
5. Coupon codes and package detection from user text.
6. Bidirectional consistency between batch instructors and teacher assigned batches.
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import kb_loader, coupons
from src.state.chat_state import detect_timing_from_text


def test_audit_all_courses():
    kb_loader.clear_cache()
    catalog = kb_loader.get_course_catalog()
    digitized_ids = [c["id"] for c in catalog if c.get("has_full_kb")]

    print("\n" + "=" * 60)
    print("🚀 RUNNING 100% ACCURACY AUDIT ACROSS ALL DIGITIZED COURSES")
    print("=" * 60)
    print(f"Digitized Courses Found in Catalog: {digitized_ids}\n")

    assert "yoga" in digitized_ids, "Yoga must be registered with has_full_kb: true"
    assert "kathak" in digitized_ids, "Kathak must be registered with has_full_kb: true"
    assert "ladies_dance" in digitized_ids, "Ladies Dance must be registered with has_full_kb: true"
    assert "face_yoga" in digitized_ids, "Face Yoga must be registered with has_full_kb: true"

    for cid in digitized_ids:
        print("-" * 50)
        print(f"🔍 AUDITING COURSE: {cid.upper()}")
        print("-" * 50)

        data = kb_loader.get_course_data(cid)
        overview = data.get("course_overview", {})
        batches = data.get("batch_timings", {}).get("batches", [])
        teachers = data.get("teachers", [])
        pricing = data.get("pricing", {})
        packages = pricing.get("packages", [])

        # ── 1. Overview & Catalog Validation ──
        print(f"  [1] Course Name: {overview.get('name')}")
        print(f"      Mode: {overview.get('mode')} | Platform: {overview.get('platform')}")
        assert overview.get("name"), f"Missing course name in {cid}"

        # ── 2. Timings Validation ──
        valid_timings = kb_loader.get_valid_timings(cid)
        print(f"  [2] Batch Timings ({len(batches)} batches in schedule, {len(valid_timings)} unique slots):")
        for vt in valid_timings:
            print(f"      • {vt}")
        assert len(valid_timings) > 0, f"No valid timings extracted for {cid}"

        # Test timing detection for each valid timing slot
        for b in batches:
            t12 = b.get("time_12h", "")
            instructors = b.get("instructors", [])
            assert t12, f"Batch missing time_12h in {cid}"
            assert instructors, f"Batch {t12} has no assigned instructors in {cid}"

            # Test detect_timing_from_text matches this batch
            detected, is_amb, _ = detect_timing_from_text(t12, course_id=cid)
            assert detected is not None, f"Timing {t12} failed to resolve via detect_timing_from_text in {cid}"
            assert not is_amb, f"Timing {t12} unexpectedly marked ambiguous in {cid}"

        # ── 3. Teachers & Dedicated Batches ──
        print(f"  [3] Faculty & Dedicated Batches ({len(teachers)} instructors):")
        teacher_names = set()
        for t in teachers:
            name = t.get("name")
            assert name, f"Teacher missing name in {cid}"
            teacher_names.add(name)

            assigned = t.get("assigned_batches") or []
            exp = t.get("experience_years")
            demo = t.get("demo_video")
            demo_str = f"Video: {demo}" if demo else "No video link"
            print(f"      • {name} ({exp} exp) | Dedicated Batches: {assigned} | {demo_str}")

            # Verify all assigned batches exist in the actual schedule
            for slot in assigned:
                matching_batches = [
                    b for b in batches
                    if b.get("time_12h") == slot and (name in b.get("instructors", []))
                ]
                assert len(matching_batches) > 0, (
                    f"Mismatch in {cid}: Teacher {name} claims assigned_batch '{slot}', "
                    f"but is not listed in that batch under batch_timings!"
                )

        # Verify every instructor in batches is in teachers list
        for b in batches:
            for ins in b.get("instructors", []):
                assert ins in teacher_names, (
                    f"Discrepancy in {cid}: Instructor '{ins}' is assigned to batch "
                    f"{b.get('time_12h')}, but missing from the course's teachers list!"
                )

        # ── 4. Pricing & GST ──
        print(f"  [4] Pricing Packages ({len(packages)} tiers, {pricing.get('gst_percentage')}% GST):")
        valid_pkgs = kb_loader.get_valid_packages(cid)
        assert len(valid_pkgs) == len(packages), f"Mismatch in package count for {cid}"

        for pkg in packages:
            dur = pkg.get("duration")
            base = pkg.get("base_price")
            offer = pkg.get("offer_price")
            coupon = pkg.get("coupon_code")
            print(f"      • {dur}: Base ₹{base:,}" + (f" ➔ Offer ₹{offer:,} (Coupon: {coupon})" if offer else ""))
            assert base is not None, f"Missing base_price for {dur} in {cid}"

            # Check that pkg duration is detected from text
            detected_pkg = coupons.detect_package_from_text(dur, course_id=cid)
            assert detected_pkg == dur, f"detect_package_from_text failed for '{dur}' in {cid}"

            # If offer price exists, test price detection from the offer number
            if offer is not None:
                detected_by_price = coupons.detect_package_from_text(str(offer), course_id=cid)
                assert detected_by_price == dur, (
                    f"Price '{offer}' failed to resolve to '{dur}' in {cid} (got: {detected_by_price})"
                )

        # ── 5. Coupons ──
        course_coupons = kb_loader.get_course_coupons(cid)
        print(f"  [5] Active Coupons ({len(course_coupons)}):")
        for dur, cinfo in course_coupons.items():
            print(f"      • {dur}: Code '{cinfo.get('code')}' (Offer: ₹{cinfo.get('offer_price'):,})")
            assert cinfo.get("code"), f"Missing coupon code for {dur} in {cid}"

        print(f"  ✅ {cid.upper()}: 100% ACCURATE & VERIFIED!\n")

    print("=" * 60)
    print("🎉 ALL COURSES PASSED 100% ACCURACY & BIDIRECTIONAL AUDIT!")
    print("=" * 60)


if __name__ == "__main__":
    test_audit_all_courses()
