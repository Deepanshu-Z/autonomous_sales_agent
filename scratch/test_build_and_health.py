"""
scratch/test_build_and_health.py — Full Repository Build & Sanity Verification Suite.
"""

import os
import sys
import glob
import json
import py_compile

# Add project root to path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

def test_1_python_syntax():
    print("--- [1/6] Checking Python Syntax & Compilation Across Repository ---")
    py_files = glob.glob(os.path.join(ROOT, "src", "**", "*.py"), recursive=True)
    py_files += [
        os.path.join(ROOT, "main.py"),
        os.path.join(ROOT, "start.py"),
        os.path.join(ROOT, "run_workers.py"),
    ]
    for path in py_files:
        py_compile.compile(path, doraise=True)
    print(f"  [PASS] {len(py_files)} Python files compiled cleanly with 0 syntax errors.")


def test_2_json_integrity():
    print("--- [2/6] Checking JSON Knowledge Base & Follow-Up Files Integrity ---")
    json_dirs = [
        os.path.join(ROOT, "data", "courses"),
        os.path.join(ROOT, "data", "followup"),
    ]
    checked = 0
    for jdir in json_dirs:
        for fpath in glob.glob(os.path.join(jdir, "*.json")):
            with open(fpath, "r", encoding="utf-8") as fp:
                data = json.load(fp)
                assert isinstance(data, dict), f"Expected dict in {fpath}"
                checked += 1
    print(f"  [PASS] {checked} JSON files validated and parsed cleanly.")


def test_3_followup_pairs():
    print("--- [3/6] Checking Follow-Up 5-Pair Variations per Course ---")
    courses = ["yoga", "ladies_dance", "kathak", "face_yoga", "general"]
    expected_stages = ["30m", "3h", "6h_step3", "6h_step4", "6h_step5_trials"]
    for c in courses:
        fpath = os.path.join(ROOT, "data", "followup", f"{c}.json")
        assert os.path.exists(fpath), f"Missing followup file for {c}"
        with open(fpath, "r", encoding="utf-8") as fp:
            d = json.load(fp)
            stages = d.get("stages", {})
            for stage in expected_stages:
                vars_list = stages.get(stage, [])
                assert len(vars_list) == 5, f"{c} stage {stage} does not have exactly 5 variations (has {len(vars_list)})"
    print(f"  [PASS] All 5 courses have exactly 5 variations per timestamp (125 total messages).")


def test_4_module_imports():
    print("--- [4/6] Checking Critical Module Imports ---")
    critical_modules = [
        "src.state.chat_state",
        "src.core.kb_loader",
        "src.core.prompt",
        "src.core.intent_classifier",
        "src.core.refusal_engine",
        "src.core.conversation_handler",
        "src.services.followup_service",
        "src.workers.whatsapp_followup_worker",
        "src.api.routes",
        "src.api.meta_routes",
        "main",
    ]
    for mod in critical_modules:
        __import__(mod)
        print(f"  [PASS] {mod}")


def test_5_state_fields():
    print("--- [5/6] Checking Chat State Default Structure ---")
    from src.state.chat_state import get_default_state
    st = get_default_state("wa_0000000000")
    required_keys = [
        "phone", "stage", "timing", "package", "fee", "course",
        "selected_timing", "selected_teacher", "days", "preferred_start_date",
        "payment_status", "is_escalated", "wants_trial"
    ]
    for k in required_keys:
        assert k in st, f"Missing required state key: {k}"
    print(f"  [PASS] Session state initializes all required fields including 'preferred_start_date'.")


def test_6_fastapi_app():
    print("--- [6/6] Checking FastAPI App Routes ---")
    from main import app
    route_count = len(app.routes)
    assert route_count > 0, "No routes registered on FastAPI app!"
    route_names = [getattr(r, "path", getattr(r, "name", str(r))) for r in app.routes]
    print(f"  Found {route_count} route entries: {route_names}")
    print("  [PASS] FastAPI app initialized successfully with routes.")


def main():
    print("==========================================================")
    print(" RUNNING FULL BUILD & HEALTH CHECK ")
    print("==========================================================\n")
    test_1_python_syntax()
    test_2_json_integrity()
    test_3_followup_pairs()
    test_4_module_imports()
    test_5_state_fields()
    test_6_fastapi_app()
    print("\n==========================================================")
    print(" >>> BUILD STATUS: SUCCESS (All 6/6 test stages passed) <<<")
    print("==========================================================")


if __name__ == "__main__":
    main()
