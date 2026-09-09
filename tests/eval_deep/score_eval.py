"""Score test_result.txt against questions.py must_include / must_not_include."""

from __future__ import annotations

import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from questions import CONVERSATIONS, INDEPENDENT

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_LOG = os.path.join(ROOT, "test_result.txt")


def parse_log(path: str) -> dict:
    text = open(path, encoding="utf-8").read()
    pairs = {}
    for m in re.finditer(r"^Q(\d+): (.*?)\nA\1: (.*?)(?=\nQ\d+: |\Z)", text, re.S | re.M):
        pairs[int(m.group(1))] = {"q": m.group(2).strip(), "a": m.group(3).strip()}
    return pairs


def check(answer: str, must_include, must_not_include):
    al = answer.lower()
    missing = [s for s in (must_include or []) if s.lower() not in al]
    leaked = [s for s in (must_not_include or []) if s.lower() in al]
    return missing, leaked


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LOG
    pairs = parse_log(path)
    rows = []
    n = 0
    for item in INDEPENDENT:
        n += 1
        rows.append((n, item["id"], item.get("tags", []), item.get("must_include"), item.get("must_not_include"), item["text"][:60]))
    for conv in CONVERSATIONS:
        for i, turn in enumerate(conv["turns"], 1):
            n += 1
            rows.append((n, f"{conv['id']}.t{i}", ["convo"] + [conv["id"]], turn.get("must_include"), turn.get("must_not_include"), turn["text"][:60]))

    fail = []
    pass_n = 0
    skip = 0
    for qn, qid, tags, must_i, must_not, preview in rows:
        rec = pairs.get(qn)
        if not rec:
            skip += 1
            fail.append((qn, qid, tags, ["NO_ANSWER_IN_LOG"], [], preview))
            continue
        a = rec["a"]
        if a.startswith("[EVAL ERROR]"):
            skip += 1
            fail.append((qn, qid, tags, ["EVAL_ERROR"], [], preview))
            continue
        missing, leaked = check(a, must_i, must_not)
        if missing or leaked:
            fail.append((qn, qid, tags, missing, leaked, preview))
        else:
            pass_n += 1

    total = len(rows)
    scored = total - skip
    print(f"Scored {pass_n}/{total} fully passing checks ({skip} missing/error). Failures: {len(fail)}")
    print("--- FAIL DETAIL ---")
    for qn, qid, tags, missing, leaked, preview in fail:
        print(f"Q{qn} [{qid}] tags={tags}")
        print(f"  {preview}")
        if missing:
            print(f"  missing: {missing}")
        if leaked:
            print(f"  leaked: {leaked}")
    print("--- TAG FAIL COUNTS ---")
    from collections import Counter
    c = Counter()
    for _, _, tags, missing, leaked, _ in fail:
        for t in tags:
            c[t] += 1
    for t, k in c.most_common():
        print(f"  {t}: {k}")


if __name__ == "__main__":
    main()
