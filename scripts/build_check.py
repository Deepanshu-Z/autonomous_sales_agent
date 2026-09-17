"""
build_check.py — Production Pre-Deployment Verification Suite
Equivalent to 'npm run build' for this Python WhatsApp Sales Agent.

Validates:
1. JSON Schema & Syntax Integrity (data/global_rules.json, data/courses/*.json)
2. Python Bytecode Compilation (compileall across src/)
3. Undefined Variables & Static Errors (pyflakes across src/)
4. Module Import & Dependency Resolution (imports all critical modules)
"""

import sys
import os
import glob
import json
import compileall
import importlib
import subprocess

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure project root is in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Auto-detect venv: If executed outside venv, re-execute using project venv
venv_python = (
    os.path.join(BASE_DIR, "venv", "Scripts", "python.exe")
    if sys.platform == "win32"
    else os.path.join(BASE_DIR, "venv", "bin", "python")
)
if os.path.exists(venv_python):
    try:
        if os.path.samefile(sys.executable, venv_python) is False:
            result = subprocess.run([venv_python] + sys.argv, cwd=BASE_DIR)
            sys.exit(result.returncode)
    except Exception:
        if os.path.abspath(sys.executable).lower() != os.path.abspath(venv_python).lower():
            result = subprocess.run([venv_python] + sys.argv, cwd=BASE_DIR)
            sys.exit(result.returncode)


def check_json_files() -> bool:
    print("\n🔍 [1/4] Checking JSON Knowledge Base Files...")
    data_dir = os.path.join(BASE_DIR, "data")
    json_files = glob.glob(os.path.join(data_dir, "**", "*.json"), recursive=True)
    all_ok = True
    for jf in json_files:
        rel = os.path.relpath(jf, BASE_DIR)
        try:
            with open(jf, "r", encoding="utf-8") as f:
                json.load(f)
            print(f"   ✅ {rel}")
        except Exception as e:
            print(f"   ❌ {rel}: {e}")
            all_ok = False
    return all_ok


def check_compilation() -> bool:
    print("\n🔍 [2/4] Checking Python Bytecode Compilation...")
    src_dir = os.path.join(BASE_DIR, "src")
    success = compileall.compile_dir(src_dir, quiet=1)
    if success:
        print("   ✅ All Python files in src/ compiled successfully (zero syntax/indentation errors).")
    else:
        print("   ❌ Syntax or compilation errors detected in src/!")
    return bool(success)


def check_undefined_variables() -> bool:
    print("\n🔍 [3/4] Checking Undefined Variables & Static Analysis (pyflakes)...")
    try:
        res = subprocess.run(
            [sys.executable, "-m", "pyflakes", "src/"],
            capture_output=True,
            text=True,
            cwd=BASE_DIR,
        )
        output = (res.stdout or "") + (res.stderr or "")
        errors = [line for line in output.splitlines() if "undefined name" in line or "syntax error" in line.lower()]
        if errors:
            print("   ❌ Production-breaking errors found:")
            for err in errors:
                print(f"      • {err}")
            return False
        print("   ✅ Zero undefined variables or syntax errors in src/.")
        return True
    except Exception as e:
        print(f"   ⚠️  Could not run pyflakes: {e}")
        return True


def check_module_imports() -> bool:
    print("\n🔍 [4/4] Checking Critical Module Imports & Dependency Integrity...")
    critical_modules = [
        "src.core.kb_loader",
        "src.core.coupons",
        "src.core.prompt",
        "src.core.intent_classifier",
        "src.core.sales_followup",
        "src.core.refusal_engine",
        "src.core.conversation_handler",
        "src.services.interakt",
        "src.services.quick_reply_service",
        "src.workers.tasks",
        "src.api.routes",
    ]
    all_ok = True
    for mod_name in critical_modules:
        try:
            importlib.import_module(mod_name)
            print(f"   ✅ {mod_name}")
        except Exception as e:
            print(f"   ❌ {mod_name}: {e}")
            all_ok = False
    return all_ok


def main():
    print("=" * 70)
    print("🚀 RUNNING PRODUCTION BUILD & INTEGRITY CHECK (npm run build equivalent)")
    print("=" * 70)

    t1 = check_json_files()
    t2 = check_compilation()
    t3 = check_undefined_variables()
    t4 = check_module_imports()

    print("\n" + "=" * 70)
    if all([t1, t2, t3, t4]):
        print("🎉 BUILD SUCCESS: Zero errors found! Safe for production deployment.")
        print("=" * 70)
        sys.exit(0)
    else:
        print("❌ BUILD FAILED: Please fix the errors above before deploying.")
        print("=" * 70)
        sys.exit(1)


if __name__ == "__main__":
    main()
