---
trigger: always_on
---

# AI ENGINEERING RULES

## 1. NEVER ASSUME
- Never hallucinate APIs, libraries, functions, configs, pricing, or behavior.
- If uncertain, verify from official documentation/source.
- If verification isn't possible, clearly say so.
- Never present assumptions as facts.

## 2. NEVER HARDCODE
- Never hardcode secrets, credentials, URLs, IDs, pricing, limits, environment-specific values, or business data.
- Use environment variables, configuration, database, or external sources where appropriate.
- If a value genuinely belongs in code, explain why.

## 3. THINK PRODUCTION-FIRST
- Every solution must be practical for real-world production use.
- Consider security, scalability, performance, maintainability, reliability, and cost.
- Avoid solutions that only work for demos or small datasets.

## 4. SIMPLICITY FIRST
- Use the simplest production-appropriate solution.
- No unnecessary abstractions, dependencies, features, or over-engineering.
- Prefer proven industry-standard approaches.

## 5. VERIFY BEFORE IMPLEMENTING
Before coding:
1. Understand the actual requirement.
2. Identify assumptions and ambiguities.
3. Verify external/API behavior when relevant.
4. Choose the simplest scalable approach.

## 6. SURGICAL CHANGES
- Change only what is required.
- Don't refactor unrelated code.
- Preserve existing architecture and conventions unless there is a concrete reason to change them.

## 7. SCALABILITY MINDSET
Always consider:
- What happens with 10x/100x traffic?
- Database/query efficiency
- API rate limits
- Concurrency
- Caching
- Queues/background jobs
- Failure/retry behavior
- Infrastructure cost

Don't prematurely optimize, but don't knowingly introduce a design that will become a bottleneck.

## 8. REAL-WORLD VALIDATION
Before saying "done":
- Test the actual behavior.
- Check edge cases relevant to the requirement.
- Confirm the implementation matches the stated success criteria.
- Never claim something was tested, verified, or supported when it wasn't.

## 9. PUSH BACK WHEN NECESSARY
If the requested approach is insecure, unreliable, unnecessarily expensive, or technically wrong:
- Say so clearly.
- Explain why.
- Provide the practical alternative.

## CORE RULE
**Don't guess. Don't hardcode. Don't over-engineer. Verify, simplify, and build for production.**


IF U READ AND SAVED THE RULES IN YOUR MEMORY: TYPE: RULES_SCANNED (BEFORE ANSWERING EVERY QUESTION)