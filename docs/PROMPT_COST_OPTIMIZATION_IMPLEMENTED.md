# Prompt Cost Optimization — Implementation Report

Date: 2026-11-19

## Summary

The first safe optimization pass is implemented.

The reply prompt now places stable course data before dynamic customer state. This improves provider-side prefix-cache reuse without changing the LangChain message structure, conversation history selection, state merge, payment logic, timing logic, or reply JSON parsing.

A compact intent-extractor prompt is also available behind a feature flag, but it is disabled by default because it needs replay testing before it can safely replace the existing high-detail extractor prompt.

## Files changed by this implementation

### `src/core/prompt.py`

Changed the system-prompt order:

```text
Global policy
Course data
Dynamic customer session context
Language/current-turn instructions
```

Previously, dynamic `state_context` appeared before the large course-data block. This meant per-turn state changes could prevent the course-data suffix from being reused by prefix caching.

No business rules, course data, coupon protections, payment restrictions, or output schema were removed.

### `src/core/intent_classifier.py`

Added `EXTRACTOR_SYSTEM_PROMPT_COMPACT` and feature-flag selection:

```text
PROMPT_OPTIMIZATION_V2=true
```

The default is `false`, so the existing extractor prompt remains active unless explicitly enabled for a canary.

The existing normalization and `apply_extraction_to_state()` logic was not changed. This preserves compatibility with all downstream state consumers.

### `.env.example`

Documented the optional extractor canary flag:

```text
PROMPT_OPTIMIZATION_V2=False
```

The real `.env` file was not changed.

### `scripts/test_prompt_optimization.py`

Added a no-network contract check that verifies:

- Course data appears before dynamic state.
- Runtime placeholders are fully rendered.
- Dynamic payment state is not in the static prefix.
- Both course prompts contain the expected sections.
- Prompt token sizes are printed for comparison.

### `docs/PROMPT_COST_OPTIMIZATION.md`

Contains the full implementation plan, safety constraints, rollout plan, and deferred work.

## Files intentionally not changed

These areas were deliberately left untouched to avoid changing the AI brain or business behavior:

- `src/core/helpers/schedule_engine.py`
- `src/core/pricing_engine.py`
- `src/core/coupons.py`
- `apply_extraction_to_state()` behavior
- Payment verification
- Refusal and escalation behavior
- `src/state/chat_history.py`
- Conversation-history limits

## Validation performed

Passed:

```text
scripts/test_prompt_optimization.py
python -m py_compile src/core/prompt.py src/core/intent_classifier.py scripts/test_prompt_optimization.py
scripts/build_check.py
```

Diagnostic prompt sizes from the local contract check:

```text
Existing extractor system prompt: 4,636 tokens
Compact extractor system prompt:    343 tokens
Reply prompt, Yoga:               10,454 tokens
Reply prompt, Kathak:             10,492 tokens
Reply static prefix, Yoga:         9,502 tokens
```

The compact extractor prompt is intentionally not enabled by default. A 343-token prompt is a large reduction and therefore has a higher risk of losing nuanced behavior. It must be replay-tested against real cases before activation.

## Existing issues observed during validation

The existing timing self-check currently fails independently of these changes:

```text
Expected bare 07:00 with no period to be AMBIGUOUS.
Actual schedule resolver result: SELECTED, 7:00 AM to 8:00 AM.
```

No schedule-engine file was changed by this implementation.

`git diff --check` also reports trailing whitespace in an existing user-modified line in `src/core/kb_loader.py`. That line was preserved and not altered as part of this task.

## Current rollout state

Active now:

- Reply prompt static/dynamic reordering
- Prompt-order contract test

Available but disabled:

- Compact extractor prompt via `PROMPT_OPTIMIZATION_V2=true`

The recommended next step is to replay real conversations covering timing ambiguity, payment/QR requests, coupons, trial flow, language switching, complaints, stop messaging, teacher conflicts, and course switching. Enable the compact extractor only after those state transitions match the current extractor.
