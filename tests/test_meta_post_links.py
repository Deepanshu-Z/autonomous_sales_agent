import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import meta_loader

def test_meta_campaign_links():
    meta_loader.clear_cache()

    test_cases = [
        ("1001432206614811_1551233887021001", "ladies_dance"),
        ("1551233887021001", "ladies_dance"),
        ("18423193069149005", "ladies_dance"),
        ("1001432206614811_1559865116157878", "yoga"),
        ("1559865116157878", "yoga"),
        ("18634005799034651", "yoga"),
        ("100064032123660_1507226361421754", "yoga"),
        ("18107014196111345", "face_yoga"),
    ]

    for identifier, expected_course in test_cases:
        cfg = meta_loader.get_post_config(identifier)
        assert cfg is not None, f"Failed to match {identifier}"
        actual = cfg["course"]
        assert actual == expected_course, f"Course mismatch for {identifier}: got {actual} expected {expected_course}"
        print(f"PASS: {identifier} -> {actual} (ad_id: {cfg.get('ad_id')}) | link: {cfg.get('post_link')}")

    print("\nALL TEST CASES PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    test_meta_campaign_links()
