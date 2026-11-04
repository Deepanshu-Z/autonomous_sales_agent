import re

def is_timing_candidate(raw_timing: str) -> bool:
    if not raw_timing or not isinstance(raw_timing, str):
        return False
    low = raw_timing.lower()
    if any(w in low for w in ['am', 'pm', 'baje', "o'clock"]):
        return True
    if re.search(r'\b\d{1,2}:\d{2}\b', raw_timing):
        return True
    if re.search(r'\b\d{1,2}\s*(?:to|-)\s*\d{1,2}\b', raw_timing):
        return True
    return False

tests = [
    ('deepanshupokhriyal07', False),
    ('this is my gmail: deepanshupokhriyal07', False),
    ('3pm', True),
    ('11-12', True),
    ('5:00', True),
    ('7 to 8', True),
    ('8 baje', True),
    ('iphone13', False),
    ('flat 402', False),
]

for t, expected in tests:
    res = is_timing_candidate(t)
    print(f'{t:40} -> is_timing: {res:5} (Expected: {expected})')
    assert res == expected, f'Failed for {t}'
print('ALL TIMING TESTS PASSED!')

