"""
kb_loader.py — Knowledge Base Loader & Merger for Sensationz AI Assistant.

Three data layers:
  1. data/global_rules.json    -> organization, access platforms, universal
                                   payment/course policies, support/escalation.
                                   Identical for every course, loaded once.
  2. data/course_catalog.json  -> every course Sensationz offers, with a
                                   has_full_kb flag marking which ones have a
                                   dedicated data/courses/<id>.json file.
  3. data/courses/<id>.json    -> full structured KB for has_full_kb courses.
                                   Course-specific fields only.

get_course_data() / get_course_prompt_str() return global_rules + course data
MERGED into one payload. This merge was previously missing, which meant the
assistant had zero knowledge of studio address, payment policy, or escalation
rules — that's fixed here.

full_kb_course_ids() replaces hand-typed course lists in prompt.py: add a
course file + flip has_full_kb to true in course_catalog.json, and every
downstream prompt section picks it up automatically.
"""

import os
import json
import re
from typing import Dict, Any, List, Optional

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DATA_DIR = os.path.join(_BASE_DIR, "data")
_COURSES_DIR = os.path.join(_DATA_DIR, "courses")

_COURSES_CACHE: Dict[str, Dict[str, Any]] = {}
_PROMPT_STR_CACHE: Dict[str, str] = {}
_GLOBALS_CACHE: Optional[Dict[str, Any]] = None
_CATALOG_CACHE: Optional[List[Dict[str, Any]]] = None


def clear_cache() -> None:
    """Call after editing data files during local dev / hot reload."""
    global _GLOBALS_CACHE, _CATALOG_CACHE
    _COURSES_CACHE.clear()
    _PROMPT_STR_CACHE.clear()
    _GLOBALS_CACHE = None
    _CATALOG_CACHE = None


def get_global_rules() -> Dict[str, Any]:
    """Loads shared studio addresses, apps, and universal policies."""
    global _GLOBALS_CACHE
    if _GLOBALS_CACHE is not None:
        return _GLOBALS_CACHE
    path = os.path.join(_DATA_DIR, "global_rules.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                _GLOBALS_CACHE = json.load(f)
                return _GLOBALS_CACHE
        except Exception as e:
            print(f"[kb_loader] Error loading global_rules.json: {e}")
    _GLOBALS_CACHE = {}
    return _GLOBALS_CACHE


def get_course_catalog() -> List[Dict[str, Any]]:
    """Loads registry of all available courses for multi-course routing."""
    global _CATALOG_CACHE
    if _CATALOG_CACHE is not None:
        return _CATALOG_CACHE
    path = os.path.join(_DATA_DIR, "course_catalog.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                _CATALOG_CACHE = json.load(f)
                return _CATALOG_CACHE
        except Exception as e:
            print(f"[kb_loader] Error loading course_catalog.json: {e}")
    _CATALOG_CACHE = []
    return _CATALOG_CACHE


def get_catalog_entry(course_id: str) -> Optional[Dict[str, Any]]:
    cid = (course_id or "").lower().strip()
    # Check id OR aliases (e.g. 'zumba' -> matches 'fitness' catalog entry)
    return next(
        (c for c in get_course_catalog() 
         if c.get("id") == cid or cid in [a.lower() for a in c.get("aliases", [])]), 
        None
    )


def full_kb_course_ids(exclude: Optional[str] = None) -> List[str]:
    """Every course_id with has_full_kb == true, optionally excluding one."""
    exclude_cid = (exclude or "").lower().strip()
    entry = get_catalog_entry(exclude_cid)
    if entry:
        exclude_cid = entry.get("id", exclude_cid)
    return [c["id"] for c in get_course_catalog() if c.get("has_full_kb") and c["id"] != exclude_cid]


def course_exists(course_id: str) -> bool:
    """True if the course has a dedicated KB file OR at least a catalog entry."""
    if not course_id:
        return False
    cid = course_id.lower().strip()
    if get_catalog_entry(cid) is not None:
        return True
    return os.path.exists(os.path.join(_COURSES_DIR, f"{cid}.json"))


def _load_course_file(course_id: str) -> Optional[Dict[str, Any]]:
    if not course_id:
        return None
    cid = course_id.lower().strip()
    if cid in _COURSES_CACHE:
        return _COURSES_CACHE[cid]

    # Check catalog entry for explicit kb_file first
    catalog_entry = get_catalog_entry(cid)
    candidates = []
    if catalog_entry and catalog_entry.get("kb_file"):
        candidates.append(os.path.join(_BASE_DIR, catalog_entry["kb_file"]))
        candidates.append(os.path.join(_DATA_DIR, catalog_entry["kb_file"]))
    candidates.extend([
        os.path.join(_COURSES_DIR, f"{cid}.json"),
        os.path.join(_COURSES_DIR, f"{cid.replace(' ', '_')}.json"),
        os.path.join(_COURSES_DIR, f"{cid.replace('_', '')}.json"),
    ])

    for path in candidates:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                _COURSES_CACHE[cid] = data
                return data
            except Exception as e:
                print(f"[kb_loader] Error reading {path}: {e}")
                return None
    return None


def get_course_data(course_id: str = "") -> Dict[str, Any]:
    """
    Returns the MERGED payload for a course: global_rules fields + either
    the full course file (if has_full_kb) or a lightweight catalog-derived
    stub. Every other function in this module reads from here, so nothing
    downstream needs to special-case whether a course has a dedicated file.
    """
    raw_cid = (course_id or "").lower().strip()
    g = get_global_rules()
    catalog_entry = get_catalog_entry(raw_cid)
    if not catalog_entry and raw_cid:
        detected = detect_course_from_text(raw_cid)
        if detected:
            catalog_entry = get_catalog_entry(detected)
    cid = catalog_entry.get("id", raw_cid) if catalog_entry else raw_cid

    shared = {
        "organization": g.get("organization", {}),
        "access_platforms": g.get("access_platforms", {}),
        "universal_payment_policies": g.get("universal_payment_policies", {}),
        "universal_course_policies": g.get("universal_course_policies", {}),
        "universal_enrollment_flow": g.get("universal_enrollment_flow", {}),
        "support_and_escalation": g.get("support_and_escalation", {}),
        "ai_behavior_rules": g.get("ai_behavior_rules", {}),
        "other_courses": [
            {"id": c["id"], "name": c["name"]}
            for c in get_course_catalog() if c["id"] != cid
        ],
    }

    has_full_kb = bool(catalog_entry and catalog_entry.get("has_full_kb", False))
    global_trials = g.get("universal_course_policies", {}).get("trial_classes", {})
    if cid and has_full_kb:
        course_file = _load_course_file(cid)
        if course_file is not None:
            merged_course = dict(course_file)
            if global_trials:
                course_trial = dict(merged_course.get("trial_class") or {})
                for k, v in global_trials.items():
                    if k not in course_trial or not course_trial[k]:
                        course_trial[k] = v
                merged_course["trial_class"] = course_trial
            return {**shared, **merged_course}

    if catalog_entry is not None:
        name = catalog_entry.get("name", cid.title())
        return {
            **shared,
            "trial_class": global_trials,
            "course_overview": {
                "name": name,
                "description": catalog_entry.get("description", ""),
                "note": (
                    f"We DO offer {name}. Full syllabus, batch timings, and per-package fees are not yet "
                    "digitized for this course. Confirm Sensationz offers it, then direct the customer to "
                    "the Sensationz App/website or *agent* for the exact schedule and pricing. NEVER state "
                    "this course isn't offered, and never invent timings or fees for it."
                ),
            },
        }

    # Unknown or unselected course — do NOT silently fall back to yoga!
    catalog_courses = [c.get("name", c.get("id", "").title()) for c in get_course_catalog()]
    return {
        **shared,
        "course_overview": {
            "name": "Course Not Selected",
            "available_courses": catalog_courses,
            "instruction": (
                "The customer has not selected a specific course yet, or asked about a course not in our catalog. "
                "DO NOT assume, invent, or default to Yoga, Kathak, or any specific course! "
                "Politely ask the customer which course they are interested in from our official offerings: "
                f"{', '.join(catalog_courses)}."
            ),
        },
    }


def _redact_coupon_codes(data: Any) -> Any:
    """Recursively strips coupon_code fields so a redeemable code can never
    leak into the prompt before profile creation is confirmed."""
    if isinstance(data, dict):
        return {k: _redact_coupon_codes(v) for k, v in data.items() if k != "coupon_code"}
    if isinstance(data, list):
        return [_redact_coupon_codes(item) for item in data]
    return data


def get_course_prompt_str(course_id: Optional[str] = None, minified: bool = True, include_coupons: bool = True) -> str:
    """Merged global + course JSON, ready for system-prompt injection."""
    cid = (course_id or "").lower().strip()
    cache_key = f"{cid}_{minified}_{include_coupons}"
    if cache_key in _PROMPT_STR_CACHE:
        return _PROMPT_STR_CACHE[cache_key]

    data = get_course_data(cid)
    if not include_coupons:
        data = _redact_coupon_codes(data)

    prompt_str = (
        json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        if minified else json.dumps(data, indent=2, ensure_ascii=False)
    )
    _PROMPT_STR_CACHE[cache_key] = prompt_str
    return prompt_str


# ── Re-exported Schedule & Timing Helpers (from src.core.helpers.schedule_engine) ──
from src.core.helpers.schedule_engine import (
    classify_time_period,
    get_available_timings,
    get_valid_timings,
    get_teachers_for_slot,
    get_slots_for_teacher,
    get_course_time_periods,
    format_batch_timings_display,
    format_teacher_batch_schedule,
)

# ── Re-exported Pricing & Package Helpers (from src.core.pricing_engine) ──
from src.core.pricing_engine import (
    get_course_gst_percentage,
    get_valid_packages,
    get_package_by_duration,
    calculate_exact_fee,
    get_course_coupons,
    format_package_fees_display,
    get_pricing_and_timings_summary,
    format_order_summary,
    get_order_summary_details,
    generate_order_summary,
)



def format_demo_videos_display(course_id: Optional[str] = None) -> str:
    if not course_id:
        return ""
    data = get_course_data(course_id)
    demos = dict(data.get("trial_class", {}).get("demo_videos") or {})
    # Check teachers list if not in trial_class.demo_videos
    if not demos:
        for t in data.get("teachers", []):
            name = t.get("name")
            v = t.get("demo_video")
            if name and v:
                demos[name] = v

    if demos:
        lines = [f"• {name}: {url}" for name, url in demos.items() if url]
        teachers = [t.get("name") for t in data.get("teachers", []) if t.get("name")]
        no_demo = [t for t in teachers if t not in demos]
        if no_demo:
            lines.append(f"(Note: Recorded demo videos are not available for: {', '.join(no_demo)}; clients can attend live interactive trial classes with them).")
        return "\n".join(lines)

    note = data.get("trial_class", {}).get("demo_videos_note")
    if note:
        return note

    teachers = [t.get("name") for t in data.get("teachers", []) if t.get("name")]
    if teachers:
        return (
            f"Recorded demo videos are currently not available for {', '.join(teachers)}. "
            "Clients can book and attend up to 3 free live interactive trial classes on the Sensationz App to experience their teaching style firsthand."
        )
    return "Recorded demo videos are currently not available for this course. Clients can attend up to 3 free live interactive trial classes on the Sensationz App."


def format_app_links_display() -> str:
    platforms = get_global_rules().get("access_platforms", {})
    return (
        f"📱 Android: {platforms.get('android_app', '')}\n"
        f"🍎 iOS: {platforms.get('ios_app', '')}\n"
        f"💻 Website / PC / Laptop: {platforms.get('website_portal', '')}"
    )


# ── Fallback-only utilities ─────────────────────────────────────────────
# Deterministic keyword/fuzzy matching. These exist SOLELY as a resilience
# fallback for when the semantic LLM extractor (intent_classifier.py) fails
# or times out — never as the primary decision path. intent_classifier.py
# calls these itself, inside its own except block, ONLY on extraction
# failure. If the classifier succeeds, these never run and never override
# its result.

def detect_course_from_text_fallback(text: str, default_course: Optional[str] = None) -> Optional[str]:
    """Deterministic keyword course-name matcher against catalog aliases. FALLBACK ONLY."""
    if not text:
        return default_course

    q = re.sub(r"[\s\-_]+", " ", text).lower().strip()

    catalog = get_course_catalog()

    # Separate candidates into multi-word and single-word, prioritizing longest multi-word first
    multi_word_candidates = []
    single_word_candidates = []
    for item in catalog:
        cid = item.get("id")
        for alias in item.get("aliases", []) + [cid]:
            if not alias:
                continue
            alias_clean = re.sub(r"[\s\-_]+", " ", str(alias)).lower().strip()
            if " " in alias_clean:
                multi_word_candidates.append((alias_clean, cid))
            else:
                single_word_candidates.append((alias_clean, cid))

    # Sort multi-word candidates by length descending (e.g. "face yoga" before "yoga")
    multi_word_candidates.sort(key=lambda x: len(x[0]), reverse=True)

    # 1. Multi-word alias matches across the catalog (longest first, with word boundaries)
    for alias_clean, cid in multi_word_candidates:
        pattern = r"\b" + re.escape(alias_clean) + r"\b"
        if re.search(pattern, q):
            return cid

    # 2. Single-word exact matches with word boundaries (zero hardcoded stop words)
    words = set(re.findall(r"\b[a-zA-Z0-9_]+\b", q))
    for alias_clean, cid in single_word_candidates:
        if alias_clean in words:
            return cid

    return default_course


def is_app_link_request_fallback(text: str) -> bool:
    """Deterministic keyword check. FALLBACK ONLY."""
    t = (text or "").lower()
    return any(w in t for w in ["app link", "download link", "play store", "app store", "send link", "link bhejo", "link do"])


# Backward-compatible alias for existing test suites and modules
detect_course_from_text = detect_course_from_text_fallback

# Backward-compatible helper re-exports from src.core.helpers
from src.core.helpers.schedule_engine import (
    classify_time_period,
    get_available_timings,
    get_valid_timings,
    get_teachers_for_slot,
    get_slots_for_teacher,
    get_course_time_periods,
    format_batch_timings_display,
    format_teacher_batch_schedule,
    get_course_instructors,
    resolve_canonical_teacher,
)

from src.core.pricing_engine import (
    get_course_gst_percentage,
    get_valid_packages,
    get_package_by_duration,
    calculate_exact_fee,
    get_course_coupons,
    format_package_fees_display,
    get_pricing_and_timings_summary,
    format_order_summary,
    get_order_summary_details,
    generate_order_summary,
)