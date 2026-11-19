# Prompt Cost Optimization Implementation

Status: proposed implementation plan

This document describes the code changes required to reduce repeated LLM input tokens while preserving the existing counsellor behavior, state transitions, payment safeguards, coupon rules, and sales funnel.

## Why this change is necessary

The usage report shows that input tokens, not output tokens, are driving cost:

- Input tokens: approximately 188.8M
- Output tokens: approximately 3.4M
- Cached input tokens: approximately 128.8M
- Uncached input tokens: approximately 60.0M
- Cache-read ratio: approximately 68.2%

Prompt caching is already active. The problem is that very large prompts are still sent repeatedly, and a large portion of the input remains uncached. Provider-side prompt caching is prefix-based, so changing customer-specific content near the beginning of a prompt can prevent the large static suffix from being reused.

The application currently makes an extractor call for most messages and then a reply-generation call. Refusal or complaint turns may make an additional refusal-engine call. This makes repeated prompt size expensive even when the final reply is short.

The goal is not to remove the counsellor's knowledge or safety rules. The goal is to arrange and deduplicate the existing information so that:

1. Stable content appears first and can be cached.
2. Customer-specific content appears last.
3. The same fact is not sent in several different formats.
4. Deterministic Python logic continues to own state, timing, pricing, and payment decisions.

## Current request flow

```text
Incoming customer message
    -> conversation_handler.handle_incoming_message()
    -> intent_classifier.extract_intent_and_state_changes()
    -> intent_classifier.apply_extraction_to_state()
    -> payment/course/timing/state normalization
    -> prompt.format_system_prompt()
    -> reply LLM with last two history messages
    -> JSON reply parsing
    -> escalation, stop-messaging, or refusal handling
```

Important existing boundaries:

- [`src/core/conversation_handler.py`](../src/core/conversation_handler.py) orchestrates extraction, state merge, reply generation, and follow-up behavior.
- [`src/core/intent_classifier.py`](../src/core/intent_classifier.py) extracts structured intent and contains normalization and state-merge logic.
- [`src/core/prompt.py`](../src/core/prompt.py) builds the large reply system prompt.
- [`src/core/kb_loader.py`](../src/core/kb_loader.py) loads and locally caches course data strings.
- [`src/state/chat_history.py`](../src/state/chat_history.py) stores recent conversation history.

The cache in `kb_loader.py` only avoids rebuilding JSON locally. It is not the same as provider-side prompt caching.

## Required prompt layout

The reply prompt should be assembled in this order:

```text
GLOBAL_STATIC_POLICY
COURSE_STATIC_DATA
DYNAMIC_CUSTOMER_STATE
LANGUAGE_AND_CURRENT_TURN_OVERRIDES
```

The final request should continue using one `SystemMessage` during the first rollout. The content can be assembled from separate sections without changing the message roles or history behavior.

The stable prefix should contain:

- Persona and tone
- JSON response contract
- Global sales funnel rules
- Payment and coupon safety rules
- Trial and enrolled-student rules
- WhatsApp formatting rules
- Global catalog names

The course-static block should contain:

- Course JSON
- Course title
- Course-specific GST information
- Course-specific availability and “not offered” rules
- Static teacher and timing information when it is not already present in the course JSON

The dynamic tail should contain:

- Current course state
- Selected timing, teacher, days, and package
- Payment and trial status
- Coupon/profile status
- Missing funnel slots
- Current intent and current-turn instructions
- Detected language and script rule
- Conversation continuity data

Customer name, payment status, coupon state, and conversation summary must not appear before the stable prefix.

## Implementation phases

### Phase 0: Baseline measurement

Files:

- `src/core/intent_classifier.py`
- `src/core/conversation_handler.py`
- `src/core/refusal_engine.py`
- New: `scripts/check_prompt_budgets.py`

Changes:

1. Record input tokens, cached input tokens, uncached input tokens, and output tokens for each LLM call.
2. Label each call as `extractor`, `reply`, or `refusal`.
3. Record course ID, prompt version, and static/dynamic prompt hashes.
4. Do not record customer message content in cost telemetry.
5. Measure the real percentage of short confirmation messages instead of assuming a percentage.

Why:

Without a baseline, a lower cache ratio or a behavior regression could be mistaken for an optimization. This phase changes observability only.

Acceptance criteria:

- Existing behavior is unchanged.
- Existing build checks still pass.
- Prompt sizes can be measured separately for extractor and reply calls.

### Phase 1: Split the reply prompt without deleting rules

Primary file:

- `src/core/prompt.py`

Changes:

1. Keep the existing policy text intact initially.
2. Extract the current template into logical sections:
   - global static policy
   - course-static data
   - dynamic state
   - language suffix
3. Move the dynamic state section below the course data.
4. Keep coupon codes redacted from course data using the existing `include_coupons=False` behavior.
5. Keep `format_system_prompt()` as a compatibility wrapper so existing callers do not break.

Why:

The current template places dynamic `state_context` before the large course-data block. Reordering the same information gives the provider a stable prefix and makes the large course block cacheable for repeated requests.

Important limitation:

Do not move the entire existing `state_context` blindly. It contains both static course information and customer-specific state. It must be divided first.

### Phase 2: Use the reordered prompt in reply generation

Primary file:

- `src/core/conversation_handler.py`

Changes:

1. Continue calling the existing prompt builder.
2. Continue sending one `SystemMessage`.
3. Continue using the existing last-two-message reply history.
4. Change only the assembled order of system-prompt sections.

Do not change in this phase:

- Reply JSON parsing
- Follow-up behavior
- Conversation-history slicing
- State persistence
- Payment verification
- Refusal handling

Why:

This isolates cache optimization from conversational behavior. If the output changes, it is easier to identify whether the cause is prompt ordering rather than a changed state machine.

### Phase 3: Remove duplicated reply data

Primary file:

- `src/core/prompt.py`

Changes, in this order:

1. If timing and teacher data already exists in `COURSE_DATA`, stop repeating the same full data in dynamic state.
2. Replace verbose state paragraphs with compact structured state where the rule itself is already present in the static policy.
3. Include app, trial, payment, and support links only when the current intent requires them.
4. Keep current-turn safety instructions for payment, coupon, trial, timing, and teacher conflicts.
5. Consolidate repeated rules only after replay testing proves that the remaining single rule is sufficient.

Why:

The current reply prompt contains the same business facts in course JSON, formatted summaries, and turn-specific prose. Removing duplicate representations reduces both cached and uncached token volume without removing the underlying knowledge.

### Phase 4: Reduce the intent detector safely

Primary file:

- `src/core/intent_classifier.py`

Changes:

1. Keep the existing extractor prompt as the rollback version.
2. Add a compact extractor prompt behind a feature flag.
3. Ask the model for canonical intent and essential entities instead of every alias boolean.
4. Preserve high-risk outputs:
   - payment claim
   - payment request
   - stop messaging
   - complaint/refund
   - human-agent request
   - phone request
   - trial request
   - unoffered entity
5. Reconstruct legacy alias fields in the existing normalization section.
6. Leave `apply_extraction_to_state()` unchanged initially.

Examples of safe compatibility mapping:

```text
PAYMENT_REQUEST  -> requests_qr = true
APP_LINK_REQUEST -> wants_app_link = true
STOP_MESSAGING   -> wants_stop_messaging = true
AGENT_REQUEST    -> requests_human_agent = true
```

Why:

The current extractor asks the model for multiple fields representing the same intent. Python already synchronizes these fields. A smaller model output contract reduces prompt and parsing complexity while preserving the state shape expected by downstream code.

Do not remove the timing rules first. Timing ambiguity, bare hours, unoffered slots, and AM/PM handling are safety-sensitive and should be reduced only after replay coverage exists.

### Phase 5: Conversation summary review

Primary files:

- `src/core/intent_classifier.py`
- `src/core/prompt.py`

The extractor currently generates a conversation summary on every turn, and the reply prompt injects it into dynamic state.

Do not delete it immediately. First measure whether `_last_turn` and structured state already contain the same continuity information. If they do, generate a summary only after meaningful events such as:

- refusal or objection
- timing conflict
- course change
- future start-date selection
- major funnel transition

Why:

This can reduce recurring dynamic tokens, but removing it blindly may make the assistant repeat questions or mishandle an objection.

### Phase 6: Regression testing

New check:

- `scripts/test_prompt_contract.py`

Required checks:

1. Global static prefix is identical for different customer states.
2. Course-static prefix changes only when course ID changes.
3. Customer state appears only after the dynamic marker.
4. Coupon codes do not appear in the static course block.
5. Required JSON output instructions remain present.
6. Payment and trial safeguards remain present.
7. Existing state fields are still produced after extraction normalization.

Replay cases must include:

- Course selection
- Morning/evening selection
- Bare hour such as “7 baje”
- Unoffered timing
- Teacher conflict
- Package selection
- Premature QR request
- Valid QR request
- Trial request
- Coupon request
- Paid-student support
- Future start date
- Course switching
- English, Hinglish, and native scripts
- Complaint/refund
- Stop messaging
- Human-agent request
- Short replies such as “haan”, “yes”, “done”, and “ok”

Run the existing checks as well:

```text
scripts/build_check.py
scripts/test_timing_resolver.py
scripts/test_language_fix.py
scripts/test_button_extract.py
```

### Phase 7: Canary rollout

Use a feature flag such as:

```text
PROMPT_OPTIMIZATION_V2=false
```

Roll out gradually and compare:

- Cache-read ratio
- Uncached input tokens
- Total input tokens
- Latency
- Timing mistakes
- Payment/QR mistakes
- Coupon leaks
- Stop-messaging failures
- Escalation failures
- Trial completion and conversion

Rollback should require only disabling the flag.

## Changes deliberately deferred

### History limit

Do not change `HISTORY_CACHE_LIMIT` in the first implementation. The reply path already sends approximately the last two messages, and the extractor already limits its transcript. Changing Redis retention may have little effect on actual LLM prompt size.

### Affirmative fast paths

Do not treat `haan`, `yes`, `done`, or `ok` as universal confirmations. Their meaning depends on the previous pending question. A fast path should only be considered later when a trusted state such as `pending_question=timing_period` exists.

## Files expected to change

Initial implementation:

- `src/core/prompt.py`
- `src/core/conversation_handler.py`
- `src/core/intent_classifier.py`
- `scripts/check_prompt_budgets.py`
- `scripts/test_prompt_contract.py`

Files intentionally protected during the first rollout:

- `src/core/helpers/schedule_engine.py`
- `src/core/pricing_engine.py`
- `src/core/coupons.py`
- Payment verification code
- `apply_extraction_to_state()` behavior
- Refusal and escalation behavior
- `src/state/chat_history.py`

## Success criteria

The first release is successful only if:

1. The static prompt prefix is stable and cache reuse improves.
2. Total input tokens decrease.
3. Existing state transitions remain correct.
4. Payment, coupon, timing, language, trial, and opt-out safeguards do not regress.
5. The optimized version can be disabled immediately.

The expected result is a safer 30–50% input-token reduction from prompt restructuring and deduplication. A 60–75% cost reduction should not be promised until actual production measurements show that entire LLM calls can be safely skipped.
