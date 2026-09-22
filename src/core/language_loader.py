"""
language_loader.py — Centralized Language Configuration & Matching Loader.

Loads data/languages.json to dynamically provide:
- Supported languages, display names, and button labels
- First-turn language selection prompt and buttons
- Robust alias matching for incoming user text or button clicks
"""

import os
import json
from typing import Dict, Any, List, Optional

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LANGUAGES_FILE = os.path.join(_BASE_DIR, "data", "languages.json")

_LANG_CACHE: Optional[Dict[str, Any]] = None


def clear_cache() -> None:
    """Clears language cache (useful during testing or hot reloading)."""
    global _LANG_CACHE
    _LANG_CACHE = None


def get_language_config() -> Dict[str, Any]:
    """Loads and caches configuration from data/languages.json."""
    global _LANG_CACHE
    if _LANG_CACHE is not None:
        return _LANG_CACHE

    if os.path.exists(_LANGUAGES_FILE):
        try:
            with open(_LANGUAGES_FILE, "r", encoding="utf-8") as f:
                _LANG_CACHE = json.load(f)
                return _LANG_CACHE
        except Exception as e:
            print(f"[language_loader] Error loading {_LANGUAGES_FILE}: {e}")

    _LANG_CACHE = {
        "default_language": "English",
        "prompt_message": "Welcome to Sensationz! 🙏\n\nPlease select your preferred language:\nकृपया अपनी पसंदीदा भाषा चुनें:",
        "languages": [
            {
                "id": "english",
                "name": "English",
                "button_label": "English",
                "aliases": ["english", "eng", "en", "1"]
            },
            {
                "id": "hindi",
                "name": "Hindi",
                "button_label": "हिन्दी (Hindi)",
                "aliases": ["hindi", "हिन्दी", "हिंदी", "hi", "2"]
            }
        ]
    }
    return _LANG_CACHE


def get_default_language() -> str:
    """Returns the default language name configured in data/languages.json."""
    cfg = get_language_config()
    return cfg.get("default_language", "English")


def get_language_prompt() -> Dict[str, Any]:
    """Returns the initial language selection prompt message and quick-reply buttons."""
    cfg = get_language_config()
    prompt = cfg.get("prompt_message", "Welcome to Sensationz! 🙏\nPlease select your preferred language:")
    configured_buttons = cfg.get("prompt_buttons")
    if configured_buttons:
        buttons = configured_buttons
    else:
        buttons = [lang.get("button_label", lang.get("name")) for lang in cfg.get("languages", [])][:3]
    return {
        "message": prompt,
        "buttons": buttons
    }


def match_language(text: str) -> Optional[str]:
    """
    Matches user input or button text against configured languages and aliases.
    Returns the canonical language name (e.g. 'English', 'Hindi', 'Hinglish') or None.
    """
    if not text:
        return None

    cleaned = text.strip().lower()
    cfg = get_language_config()

    for lang in cfg.get("languages", []):
        name = lang.get("name", "").lower()
        lid = lang.get("id", "").lower()
        btn = lang.get("button_label", "").lower()
        aliases = [a.lower() for a in lang.get("aliases", [])]

        if cleaned in [name, lid, btn] or cleaned in aliases:
            return lang.get("name")

        # Partial matching for button clicks like "हिन्दी (Hindi)"
        if name and (name == cleaned or cleaned.startswith(name)):
            return lang.get("name")
        if btn and (btn == cleaned or cleaned in btn or btn in cleaned):
            return lang.get("name")

    return None


def get_agent_button(language: Optional[str] = None) -> str:
    """
    Returns the 'Talk to Agent' button label for the specified language.
    Strict rule:
    - Hindi -> 'एजेंट से बात'
    - Hinglish -> 'Agent se baat'
    - For users other than Hindi and Hinglish, always use English: 'Talk to Agent'
    """
    if not language:
        return "Talk to Agent"

    lang_lower = str(language).lower().strip()
    cfg = get_language_config()

    for lang in cfg.get("languages", []):
        lid = lang.get("id", "").lower()
        lname = lang.get("name", "").lower()
        if (lid in lang_lower or lname in lang_lower) and lid in ("hindi", "hinglish"):
            return lang.get("agent_button", "Talk to Agent")

    return "Talk to Agent"


def get_all_agent_buttons() -> set:
    """Returns a set of all recognized agent button labels in lowercase."""
    cfg = get_language_config()
    labels = {"talk to agent", "agent"}
    for lang in cfg.get("languages", []):
        btn = lang.get("agent_button")
        if btn:
            labels.add(btn.strip().lower())
    return labels
