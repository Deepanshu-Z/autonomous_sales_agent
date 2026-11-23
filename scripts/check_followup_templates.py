"""
Self-check: QR / trial / doubt follow-up templates keep the general.json cadence, 5 variations per
stage, unique ids, and none of the lines that are forbidden for that client type.
Run: venv\\Scripts\\python.exe scripts\\check_followup_templates.py
"""
import glob
import json
import os
import re

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "followup")
load = lambda name: json.load(open(os.path.join(DIR, name), encoding="utf-8"))

general_stages = list(load("general.json")["stages"])
EXPECTED = ["5m"] + general_stages  # 5m check-in + the exact general cadence

# Lines that must never appear for that client type (our own copy, so a word list is fine here).
FORBIDDEN = {
    "qr_followup.json": r"\btrial\b|limited seats?|offer khatam|otp (bhej|share kar)|card number (bhej|share)",
    "trials_followup.json": r"\bqr\b|fees?\b|package|₹|kaunsa time|timing bata|book kar doon|confirm karoon|slot (hold|confirm|book)",
    "doubt_followup.json": r"limited seats?|offer khatam|jaldi kijiye|last chance|refund milega|paise wapas|\b\d{2,3},\d{3}\+? students|4\.8",
}

all_ids = []
for f in glob.glob(os.path.join(DIR, "*.json")):
    for variations in json.load(open(f, encoding="utf-8")).get("stages", {}).values():
        all_ids += [v["id"] for v in variations]
assert len(all_ids) == len(set(all_ids)), "duplicate follow-up ids across files"

for name, pattern in FORBIDDEN.items():
    stages = load(name)["stages"]
    assert list(stages) == EXPECTED, (name, list(stages))
    for key, variations in stages.items():
        assert len(variations) == 5, (name, key, len(variations))
        for v in variations:
            assert v["id"] and v["message"].strip(), (name, v)
            assert not v.get("is_final_trial_offer"), (name, v["id"])
            hit = re.search(pattern, v["message"], re.IGNORECASE)
            assert not hit, f"{name} {v['id']}: forbidden '{hit.group(0)}'"

print(f"OK: 3 follow-up templates valid ({len(EXPECTED)} stages x 5 variations each)")
