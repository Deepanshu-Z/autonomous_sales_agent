import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""
full_test.py — Concurrent load test with round-robin verification.

Simulates N users (half target, half non-target) sending messages
to the test webhook concurrently. Verifies:
  - Target/non-target filtering
  - AI response latency
  - Round-robin agent assignment (Agent1/Agent2 alternating)
  - Different phones process concurrently (not serialized)

Usage:
    python full_test.py                    # 10 users (default)
    python full_test.py 20                 # 20 users
    python full_test.py 20 http://host:8000/test-webhook

Exit code: 0 = all pass, 1 = some failed
"""

import sys
import os
import time
import json
import asyncio
import httpx
from dotenv import load_dotenv

load_dotenv()

DEFAULT_WEBHOOK_URL = "http://localhost:8000/test-webhook"
NUM_USERS = int(sys.argv[1]) if len(sys.argv) > 1 else 10
WEBHOOK_URL = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_WEBHOOK_URL
MAX_RESPONSE_TIME = 15.0

TARGET_AD_ID = os.getenv("TARGET_AD_ID", "PLACEHOLDER_AD_ID")
# TARGET_MESSAGE_TEXT = os.getenv("TARGET_MESSAGE_TEXT", "Hello! Can I get more info on Yoga classes?")

from redis_client import get_redis_connection
redis_conn = get_redis_connection()

# TARGET_MESSAGE_TEXT = os.getenv("TARGET_MESSAGE_TEXT", "Hello! Can I get more info on Yoga classes?")

# New: pool of messages for TARGET users (still same ad, different text per user)
TARGET_MESSAGE_TEXT = os.getenv("TARGET_MESSAGE_TEXT", "Hello! Can I get more info on Yoga classes?")

# Pool for TARGET users (same ad, varied opening text)
TARGET_SAMPLE_MESSAGES = [
    TARGET_MESSAGE_TEXT,
    "Hi, interested in the yoga classes",
    "What are the fees for yoga?",
    "Can you tell me more about the batch timings?",
    "I saw your ad, please share details",
    "Yoga classes info please",
    "Hello, I want to join yoga",
    "More info on this please",
    "Details of yoga classes?",
    "Interested, please share info",
]

# Pool for NON-TARGET users (wrong ad, generic chatter)
SAMPLE_MESSAGES = [
    "Hello! Can I get more info on Yoga classes?",
    "What are the fees?",
    "Do you have a demo class video?",
    "Other timings",
    "5 6 pm",
    "3 months",
    "Is this online live on Zoom?",
    "Yes",
    "Teacher details kya hai?",
    "1 year package",
    "Hi",
]



# def generate_payload(user_id: int) -> tuple:
#     phone = f"919800{user_id:06d}"

#     if user_id <= NUM_USERS // 2:
#         # TARGET users — both ad ID and text match
#         return {
#             "type": "message_received",
#             "data": {
#                 "customer": {"country_code": "91", "phone_number": phone},
#                 "message": {
#                     "message": TARGET_MESSAGE_TEXT,
#                     "referral": {
#                         "source_id": TARGET_AD_ID,
#                         "source_type": "ad",
#                         "source_url": f"https://fb.com/l.php?ad_id={TARGET_AD_ID}",
#                     },
#                 },
#             },
#         }, phone, TARGET_MESSAGE_TEXT, True
#     else:
#         # NON-TARGET users — wrong ad
#         msg = SAMPLE_MESSAGES[(user_id - 1) % len(SAMPLE_MESSAGES)]
#         return {
#             "type": "message_received",
#             "data": {
#                 "customer": {"country_code": "91", "phone_number": phone},
#                 "message": {
#                     "message": msg,
#                     "referral": {
#                         "source_id": "999999_wrong_ad",
#                         "source_type": "ad",
#                         "source_url": "https://fb.com/l.php?ad_id=999999_wrong_ad",
#                     },
#                 },
#             },
#         }, phone, msg, False

# def generate_payload(user_id: int) -> tuple:
#     phone = f"919800{user_id:06d}"

#     if user_id <= NUM_USERS // 2:
#         # TARGET users — same ad ID, but message text now varies per user
#         msg = TARGET_SAMPLE_MESSAGES[(user_id - 1) % len(TARGET_SAMPLE_MESSAGES)]
#         return {
#             "type": "message_received",
#             "data": {
#                 "customer": {"country_code": "91", "phone_number": phone},
#                 "message": {
#                     "message": msg,
#                     "referral": {
#                         "source_id": TARGET_AD_ID,
#                         "source_type": "ad",
#                         "source_url": f"https://fb.com/l.php?ad_id={TARGET_AD_ID}",
#                     },
#                 },
#             },
#         }, phone, msg, True
#     else:
#         # NON-TARGET users — wrong ad
#         msg = SAMPLE_MESSAGES[(user_id - 1) % len(SAMPLE_MESSAGES)]
#         return {
#             "type": "message_received",
#             "data": {
#                 "customer": {"country_code": "91", "phone_number": phone},
#                 "message": {
#                     "message": msg,
#                     "referral": {
#                         "source_id": "999999_wrong_ad",
#                         "source_type": "ad",
#                         "source_url": "https://fb.com/l.php?ad_id=999999_wrong_ad",
#                     },
#                 },
#             },
#         }, phone, msg, False

def generate_payload(user_id: int) -> tuple:
    phone = f"919800{user_id:06d}"
    msg = SAMPLE_MESSAGES[(user_id - 1) % len(SAMPLE_MESSAGES)]

    if user_id <= NUM_USERS // 2:
        # TARGET users — correct ad ID, message text now varies too
        return {
            "type": "message_received",
            "data": {
                "customer": {"country_code": "91", "phone_number": phone},
                "message": {
                    "message": msg,
                    "referral": {
                        "source_id": TARGET_AD_ID,
                        "source_type": "ad",
                        "source_url": f"https://fb.com/l.php?ad_id={TARGET_AD_ID}",
                    },
                },
            },
        }, phone, msg, True
    else:
        # NON-TARGET users — wrong ad
        return {
            "type": "message_received",
            "data": {
                "customer": {"country_code": "91", "phone_number": phone},
                "message": {
                    "message": msg,
                    "referral": {
                        "source_id": "999999_wrong_ad",
                        "source_type": "ad",
                        "source_url": "https://fb.com/l.php?ad_id=999999_wrong_ad",
                    },
                },
            },
        }, phone, msg, False




async def send_webhook(client: httpx.AsyncClient, user_id: int) -> dict:
    payload, phone, message_text, is_target = generate_payload(user_id)
    sent_at = time.time()
    try:
        response = await client.post(
            WEBHOOK_URL, json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30.0,
        )
        latency_ms = (time.time() - sent_at) * 1000
        print(f"  [webhook] User {user_id:2d} ({phone}) -> HTTP {response.status_code} ({latency_ms:.0f}ms)")
        return {
            "user_id": user_id, "phone": phone, "message": message_text,
            "is_target": is_target, "status_code": response.status_code,
            "enqueue_ms": round(latency_ms, 1), "sent_at": sent_at,
        }
    except Exception as e:
        latency_ms = (time.time() - sent_at) * 1000
        print(f"  [webhook] User {user_id:2d} ({phone}) -> ERROR: {e} ({latency_ms:.0f}ms)")
        return {
            "user_id": user_id, "phone": phone, "message": message_text,
            "is_target": is_target, "status_code": None,
            "enqueue_ms": round(latency_ms, 1), "sent_at": sent_at,
            "error": str(e),
        }


def check_ai_response(phone: str) -> dict | None:
    key = f"history:{phone}"
    try:
        cached = redis_conn.lrange(key, 0, -1)
        if cached:
            for item in reversed(cached):
                turn = json.loads(item)
                if turn.get("role") == "assistant":
                    return turn
    except Exception:
        pass
    return None


async def wait_for_ai_responses(target_results: list, timeout: float = 90.0) -> dict:
    pending = {r["phone"]: r["sent_at"] for r in target_results}
    completed = {}
    start = time.time()

    while pending and (time.time() - start) < timeout:
        newly_done = []
        for phone, sent_at in pending.items():
            ai = check_ai_response(phone)
            if ai:
                e2e_time = time.time() - sent_at
                server_lat = ai.get("response_time_sec")
                final_lat = server_lat if (server_lat and server_lat > 0) else round(e2e_time, 2)
                completed[phone] = {
                    "reply": ai.get("content", ""),
                    "latency_sec": final_lat,
                }
                newly_done.append(phone)

        for phone in newly_done:
            del pending[phone]

        if newly_done:
            elapsed = time.time() - start
            done = len(completed)
            total = done + len(pending)
            print(f"  Progress: {done}/{total} AI replies received ({elapsed:.1f}s)")

        if pending:
            await asyncio.sleep(0.5)

    return completed


def check_round_robin():
    """Check the Redis round-robin counter to verify agent distribution."""
    try:
        counter = redis_conn.get("agent_round_robin_counter")
        if counter:
            count = int(counter)
            agent1 = os.getenv("PRIORITY_AGENT_EMAIL_ANOTHER_1", "Agent1")
            agent2 = os.getenv("PRIORITY_AGENT_EMAIL_ANOTHER_2", "Agent2")
            a1_count = (count + 1) // 2  # odd positions
            a2_count = count // 2  # even positions
            return {
                "total_assignments": count,
                "agent1": agent1,
                "agent1_count": a1_count,
                "agent2": agent2,
                "agent2_count": a2_count,
            }
    except Exception:
        pass
    return None

async def run_test():
    print("=" * 70)
    print(f"  CONCURRENT LOAD TEST - {NUM_USERS} Simultaneous Users")
    print(f"  Webhook: {WEBHOOK_URL}")
    print(f"  Pass threshold: <{MAX_RESPONSE_TIME}s per user")
    print("=" * 70)

    # Reset round-robin counter for clean test
    try:
        redis_conn.delete("agent_round_robin_counter")
        print("  Round-robin counter reset.")
    except Exception:
        pass

    # Clean up test user histories from previous runs
    for i in range(1, NUM_USERS + 1):
        phone = f"919800{i:06d}"
        try:
            redis_conn.delete(f"history:{phone}")
            redis_conn.delete(f"user_state:{phone}")
            redis_conn.delete(f"batch:{phone}")
            redis_conn.delete(f"batch_trigger:{phone}")
            redis_conn.delete(f"batch_referral:{phone}")
            
            # Pre-populate is_target_ad for simulated target users
            if i <= NUM_USERS // 2:
                redis_conn.set(f"user_state:{phone}", json.dumps({
                    "phone": phone,
                    "stage": "NEW",
                    "timing": None,
                    "package": None,
                    "fee": None,
                    "app_installed": False,
                    "profile_created": False,
                    "coupon_sent": False,
                    "is_escalated": False,
                    "is_target_ad": True,
                    "low_confidence_count": 0
                }))
        except Exception:
            pass
    print(f"  Cleaned up {NUM_USERS} test user states and pre-populated target flags.\n")

    # Phase 1: Fire all webhooks
    print(f"  PHASE 1: Firing {NUM_USERS} webhooks simultaneously...")
    t_start = time.time()

    limits = httpx.Limits(max_keepalive_connections=NUM_USERS, max_connections=NUM_USERS + 10)
    async with httpx.AsyncClient(limits=limits) as client:
        tasks = [send_webhook(client, i + 1) for i in range(NUM_USERS)]
        results = await asyncio.gather(*tasks)

    webhook_time = time.time() - t_start
    ok_count = sum(1 for r in results if r.get("status_code") == 200)
    print(f"\n  Webhooks: {ok_count}/{NUM_USERS} HTTP 200 in {webhook_time:.2f}s")

    if ok_count == 0:
        print("  ERROR: No webhooks accepted. Is the server running?")
        print("    python start.py      (single worker)")
        print("    python run_workers.py 20  (multi-worker)")
        sys.exit(1)

    # Phase 2: Wait for AI responses (target users only)
    target_results = [r for r in results if r["is_target"] and r.get("status_code") == 200]
    non_target_results = [r for r in results if not r["is_target"]]

    print(f"\n  PHASE 2: Waiting for {len(target_results)} target users to get AI replies...")
    ai_responses = await wait_for_ai_responses(target_results, timeout=90.0)

    total_time = time.time() - t_start

    # Phase 3: Results
    print("\n" + "=" * 70)
    print("  TEST RESULTS")
    print("=" * 70)

    print(f"\n{'User':>4} | {'Phone':>14} | {'Target':>6} | {'Status':>12} | {'Enqueue':>8} | Message")
    print("-" * 90)

    all_pass = True
    latencies = []

    for r in sorted(results, key=lambda x: x["user_id"]):
        user_id = r["user_id"]
        phone = r["phone"]
        is_target = r["is_target"]

        if is_target:
            ai = ai_responses.get(phone)
            if ai and ai.get("reply"):
                lat = ai["latency_sec"]
                latencies.append(lat)
                passed = lat <= MAX_RESPONSE_TIME
                status = f"OK {lat:.1f}s" if passed else f"SLOW {lat:.1f}s"
                if not passed:
                    all_pass = False
            else:
                status = "TIMEOUT"
                all_pass = False
        else:
            ai = ai_responses.get(phone)
            if ai:
                status = "LEAKED!"
                all_pass = False
            else:
                status = "IGNORED(ok)"

        msg_display = r["message"][:30] + "..." if len(r["message"]) > 30 else r["message"]
        print(f"{user_id:4d} | {phone:>14} | {'YES' if is_target else 'NO':>6} | {status:>12} | {r['enqueue_ms']:6.0f}ms | {msg_display}")

    # Summary
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    print(f"  Total users          : {NUM_USERS}")
    print(f"  Target users         : {len(target_results)}")
    print(f"  Non-target users     : {len(non_target_results)}")
    print(f"  AI replies received  : {len(ai_responses)}/{len(target_results)}")
    print(f"  Total wall-clock time: {total_time:.2f}s")

    if latencies:
        print(f"  Fastest reply        : {min(latencies):.2f}s")
        print(f"  Slowest reply        : {max(latencies):.2f}s")
        print(f"  Average reply        : {sum(latencies)/len(latencies):.2f}s")
        under = sum(1 for l in latencies if l <= MAX_RESPONSE_TIME)
        print(f"  Under {MAX_RESPONSE_TIME}s           : {under}/{len(latencies)}")

    # Round-robin verification
    print("\n" + "-" * 70)
    print("  ROUND-ROBIN AGENT ASSIGNMENT")
    print("-" * 70)
    rr = check_round_robin()
    if rr:
        print(f"  Total assignments    : {rr['total_assignments']}")
        print(f"  {rr['agent1']}: ~{rr['agent1_count']} assignments")
        print(f"  {rr['agent2']}: ~{rr['agent2_count']} assignments")
        diff = abs(rr['agent1_count'] - rr['agent2_count'])
        if diff <= 1:
            print(f"  Distribution         : BALANCED (diff={diff})")
        else:
            print(f"  Distribution         : UNEVEN (diff={diff}) -- check logs")
    else:
        print("  Could not read round-robin counter from Redis.")

    print("=" * 70)

    if all_pass and len(ai_responses) == len(target_results):
        print("  ALL TESTS PASSED - Bot is deployment-ready!")
        sys.exit(0)
    else:
        print("  SOME TESTS FAILED - Review results above.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_test())
