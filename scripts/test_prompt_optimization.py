"""Small no-network check for prompt ordering and size reporting."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def token_count(text: str) -> int:
    try:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception:
        # ponytail: approximate fallback only for local diagnostics; use tiktoken
        # when exact provider/model accounting is required.
        return len(text.split())


def main() -> None:
    from src.core.intent_classifier import (
        EXTRACTOR_SYSTEM_PROMPT,
        EXTRACTOR_SYSTEM_PROMPT_COMPACT,
    )
    from src.core.prompt import format_system_prompt

    yoga_prompt = format_system_prompt(
        {"stage": "NEW", "course": "yoga", "language": "English"},
        course_id="yoga",
        detected_language="English",
    )
    kathak_prompt = format_system_prompt(
        {"stage": "NEW", "course": "kathak", "language": "English"},
        course_id="kathak",
        detected_language="English",
    )

    course_marker = "5. OFFICIAL COURSE DATA"
    dynamic_marker = "6. ACTIVE CUSTOMER SESSION CONTEXT"
    assert yoga_prompt.index(course_marker) < yoga_prompt.index(dynamic_marker)
    assert "{state_context}" not in yoga_prompt
    assert "{course_data}" not in yoga_prompt
    assert course_marker in kathak_prompt
    assert dynamic_marker in kathak_prompt

    static_prefix = yoga_prompt.split(dynamic_marker, 1)[0]
    assert "- Payment Status: UNPAID" not in static_prefix

    print(f"extractor_system_tokens={token_count(EXTRACTOR_SYSTEM_PROMPT)}")
    print(f"extractor_compact_system_tokens={token_count(EXTRACTOR_SYSTEM_PROMPT_COMPACT)}")
    print(f"reply_yoga_tokens={token_count(yoga_prompt)}")
    print(f"reply_kathak_tokens={token_count(kathak_prompt)}")
    print(f"reply_static_prefix_tokens={token_count(static_prefix)}")
    print("prompt optimization contract: PASS")


if __name__ == "__main__":
    main()
