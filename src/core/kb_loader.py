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


def get_valid_timings(course_id: Optional[str] = None) -> List[str]:
    """Dynamically extracts valid batch timings straight from the course JSON — never a hand-typed list."""
    if not course_id:
        return []
    data = get_course_data(course_id)
    batches = data.get("batch_timings", {}).get("batches", [])
    valid_timings = []
    for b in batches:
        t = b.get("time_12h", "")
        m = re.match(r"(\d{1,2}:\d{2}\s*(?:AM|PM))\s*(?:to|-|–)\s*(\d{1,2}:\d{2}\s*(?:AM|PM))", t, re.IGNORECASE)
        if m:
            start, end = m.group(1).strip(), m.group(2).strip()
            sp, ep = start.split(), end.split()
            canonical = f"{sp[0]}–{ep[0]} {ep[1].upper()}" if sp[1].upper() == ep[1].upper() else f"{start}–{end}"
            valid_timings.append(canonical)
        elif t:
            valid_timings.append(t)
    return valid_timings


def get_valid_packages(course_id: Optional[str] = None) -> Dict[str, str]:
    """Dynamically extracts package names + formatted fee strings from the course JSON."""
    if not course_id:
        return {}
    data = get_course_data(course_id)
    packages = data.get("pricing", {}).get("packages", [])
    valid_pkgs = {}
    for p in packages:
        duration = p.get("duration")
        if not duration:
            continue
        base = p.get("base_price")
        offer = p.get("offer_price")
        coupon = p.get("coupon_code")
        if base is not None and offer is not None:
            valid_pkgs[duration] = f"₹{base:,} (Offer Price: ₹{offer:,})"
        elif base is not None and coupon:
            valid_pkgs[duration] = f"₹{base:,} (Coupon: {coupon})"
        elif base is not None:
            valid_pkgs[duration] = f"₹{base:,}"
        elif offer is not None:
            valid_pkgs[duration] = f"Offer Price: ₹{offer:,}"
    return valid_pkgs


def get_pricing_and_timings_summary(course_id: str) -> Dict[str, Any]:
    """Compact packages+timings for CROSS-course reference blocks — no coupon
    codes, no teachers/syllabus/policies. Used when a course is relevant but
    isn't the active one this turn."""
    data = get_course_data(course_id)
    entry = get_catalog_entry(course_id) or {}
    return {
        "name": data.get("course_overview", {}).get("name", entry.get("name", course_id.title())),
        "packages": get_valid_packages(course_id),
        "timings": get_valid_timings(course_id),
    }


def get_course_coupons(course_id: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Dynamically extracts coupon codes per package duration — zero hardcoded codes."""
    if not course_id:
        return {}
    data = get_course_data(course_id)
    packages = data.get("pricing", {}).get("packages", [])
    cid_upper = course_id.upper().replace(" ", "_")
    coupons = {}
    for pkg in packages:
        duration = pkg.get("duration")
        if not duration:
            continue
        offer = pkg.get("offer_price")
        base = pkg.get("base_price", 0)
        coupons[duration] = {
            "code": pkg.get("coupon_code") or f"{cid_upper}{offer if offer is not None else base}",
            "base_price": base,
            "offer_price": offer if offer is not None else base,
            "duration": duration,
        }
    return coupons


def format_batch_timings_display(course_id: Optional[str] = None) -> str:
    if not course_id:
        return ""
    data = get_course_data(course_id)
    batches = data.get("batch_timings", {}).get("batches", [])
    slots: Dict[str, List[str]] = {}
    for b in batches:
        slots.setdefault(b.get("slot", "Available").strip().title(), []).append(b.get("time_12h", ""))
    if not slots:
        return "• Flexible batch timings available — check the app/website for the current schedule."
    return "\n".join(f"• {slot}: " + ", ".join(t for t in times if t) for slot, times in slots.items())


def get_course_gst_percentage(course_id: Optional[str] = None) -> int:
    """Dynamically retrieves the official GST percentage for the course (e.g. 18 for Kathak, 5 for Yoga/Face Yoga)."""
    if not course_id:
        return 5
    data = get_course_data(course_id)
    pricing = data.get("pricing", {})
    if pricing.get("gst_percentage") is not None:
        return int(pricing["gst_percentage"])
    entry = get_catalog_entry(course_id)
    if entry and entry.get("gst_percentage") is not None:
        return int(entry["gst_percentage"])
    return 5


def format_package_fees_display(course_id: Optional[str] = None) -> str:
    if not course_id:
        return ""
    pkgs = get_valid_packages(course_id)
    if not pkgs:
        return ""
    gst_pct = get_course_gst_percentage(course_id)
    lines = [f"• {pkg}: {fee}" for pkg, fee in pkgs.items()]
    lines.append(f"\n*Note:* {gst_pct}% GST will be applicable at checkout on the app/website.")
    return "\n".join(lines)


def format_demo_videos_display(course_id: Optional[str] = None) -> str:
    if not course_id:
        return ""
    data = get_course_data(course_id)
    demos = data.get("trial_class", {}).get("demo_videos", {})
    return "\n".join(f"• Trainer {name}: {url}" for name, url in demos.items() if url)


def format_app_links_display() -> str:
    platforms = get_global_rules().get("access_platforms", {})
    return (
        f"📱 Android: {platforms.get('android_app', '')}\n"
        f"🍎 iOS: {platforms.get('ios_app', '')}\n"
        f"💻 Website / PC / Laptop: {platforms.get('website_portal', '')}"
    )


def get_course_bonus_tip(course_id: Optional[str] = None) -> Optional[Dict[str, str]]:
    """Returns the pre-filled text bonus tip for the course from course JSON, if defined."""
    cid = (course_id or "yoga").lower().strip()
    data = get_course_data(cid)
    return data.get("bonus_tip")


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


def get_course_bonus_tip(course_id: Optional[str] = None) -> Optional[str]:
    """Retrieves the prefilled bonus tip content for a given course directly from course JSON.
    Zero hardcoded values, dynamic per course.
    """
    if not course_id:
        return None
    data = get_course_data(course_id)
    bonus = data.get("bonus_tip")
    if isinstance(bonus, dict):
        return bonus.get("content")
    elif isinstance(bonus, str):
        return bonus
    return None


def get_course_conversion_hook(course_id: Optional[str] = None, seed: Optional[int] = None) -> Optional[str]:
    """Returns a random course fact from the conversion_hooks list for use as a persuasion drip.
    Zero hardcoded values — dynamically read from course JSON.
    Args:
        course_id: The course identifier (e.g. 'yoga', 'kathak', 'face_yoga').
        seed: Optional integer seed for deterministic selection (e.g. based on message count).
    """
    if not course_id:
        return None
    import random
    data = get_course_data(course_id)
    hooks = data.get("conversion_hooks")
    if not isinstance(hooks, list) or not hooks:
        return None
    if seed is not None:
        rng = random.Random(seed)
        return rng.choice(hooks)
    return random.choice(hooks)
