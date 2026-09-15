import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.intent_classifier import extract_intent_and_state_changes, apply_extraction_to_state

# Simulate the exact scenario: Telugu first, then Hinglish, then English
cases = [
    ("ఉపాధ్యాయులు ఎవరు?",         "Telugu (Telugu script)"),   # non-ASCII: keep Telugu
    ("kya m 2 batch join kr skta hu ?", "Hinglish (Latin script)"),  # ASCII: must be Hinglish even if state has Telugu
    ("ok what if I dont lke the classes ??", "English"),              # ASCII English
]

state = {'course': 'yoga', 'stage': 'NEW', 'language': 'Telugu (Telugu script)'}  # stale Telugu in state

for msg, expected in cases:
    flags = asyncio.run(extract_intent_and_state_changes(msg, state, []))
    flags['_raw_text'] = msg
    new_s = apply_extraction_to_state(state, flags)
    classifier_lang = flags.get("detected_language")
    stored_lang = new_s.get("language")
    
    # Simulate conversation_handler's _detect_lang_for_prompt
    def _detect_lang_for_prompt(m, cl):
        stripped = (m or "").strip()
        if stripped and all(ord(c) < 256 for c in stripped):
            if cl and "english" in cl.lower() and "hinglish" not in cl.lower():
                return "English"
            return "Hinglish (Latin script)"
        return cl or ""
    prompt_lang = _detect_lang_for_prompt(msg, classifier_lang)
    if not prompt_lang:
        prompt_lang = new_s.get("language") or state.get("language") or ""
    
    status = "PASS" if prompt_lang == expected else "FAIL"
    print(f"[{status}] MSG: {msg[:40].encode('ascii', errors='replace').decode()!r}")
    print(f"   classifier: {classifier_lang!r}")
    print(f"   prompt_lang: {prompt_lang!r}  (expected: {expected!r})")
    print()
    # Update state for next turn (simulate persistence)
    state = new_s
