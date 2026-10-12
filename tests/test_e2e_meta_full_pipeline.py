"""
test_e2e_meta_full_pipeline.py — Comprehensive End-to-End Test Suite for Meta (Instagram & Facebook)
Comments and Direct Messages (DMs).

Tests:
1. Meta Webhook Verification (hub.challenge & token verification)
2. Comments Automation:
   a. Genuine inquiry on configured target post -> public reply + private DM + state + history
   b. Negative / Complaint comment -> safety suppressed, no auto-reply
   c. Pure friend tag -> suppressed (no spam response)
   d. Non-target post comment -> ignored
   e. Self-page anti-loop guard -> ignored
3. Direct Messages (DMs) Automation:
   a. Multi-turn continuation after comment -> conversation context retained, AI response sent via Meta DM
   b. Organic DM inquiry detection -> allowed and processed
   c. Organic DM irrelevant chatter (when allow_all_dms=False) -> ignored
   d. Meta text formatting -> markdown asterisks removed
   e. Meta quick reply buttons -> routes to send_quick_replies_async
   f. Meta media/video -> routes to send_media_message_async
   g. Human agent escalation over DM -> escalates and notifies support
   h. Reset command over DM -> resets state
"""

import os
import sys
import json
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

# Fix Windows stdout encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from src.api import create_app
from src.core import meta_loader
from src.state.chat_state import get_user_state_async, reset_user_state_async, is_escalated_async
from src.state.chat_history import get_recent_history_async
from src.services.redis_client import get_redis_connection
redis_conn = get_redis_connection()

def _clear_test_history(phone: str):
    try:
        redis_conn.delete(f"history:{phone}")
    except Exception:
        pass
from src.services.interakt import send_text_message_async, send_video_message_async
from src.services.quick_reply_service import send_followup_with_buttons_async
from src.workers.tasks import process_incoming_message_async


def test_webhook_verification():
    print("\n--- [TEST 1] Webhook Verification Challenge ---")
    os.environ["META_VERIFY_TOKEN"] = "secure_verify_token_xyz"
    app = create_app()
    client = TestClient(app)

    # Valid challenge
    res = client.get("/meta/webhook?hub.mode=subscribe&hub.verify_token=secure_verify_token_xyz&hub.challenge=12345678")
    assert res.status_code == 200
    assert res.text == "12345678"

    # Invalid token
    res_bad = client.get("/meta/webhook?hub.mode=subscribe&hub.verify_token=wrong_token&hub.challenge=12345678")
    assert res_bad.status_code == 403
    print("✅ Webhook verification passed!")


def test_comment_automation_e2e():
    print("\n--- [TEST 2] Comments Automation End-to-End ---")
    os.environ["META_VERIFY_TOKEN"] = "secure_verify_token_xyz"
    os.environ["META_PAGE_ID"] = "PAGE_ID_1001"
    app = create_app()
    client = TestClient(app)

    # Clean test state for ig_user_101
    user_key = "ig_user_101"
    asyncio.run(reset_user_state_async(user_key))
    _clear_test_history(user_key)

    # Mock external Meta client network calls
    with patch("src.api.meta_routes.reply_to_comment_async", new_callable=AsyncMock) as mock_reply, \
         patch("src.api.meta_routes.send_private_reply_async", new_callable=AsyncMock) as mock_dm, \
         patch("src.api.meta_routes.get_random_delay", return_value=0.01):

        # 2a. Genuine inquiry on target post
        payload_inquiry = {
            "object": "instagram",
            "entry": [
                {
                    "id": "PAGE_ID_1001",
                    "changes": [
                        {
                            "field": "comments",
                            "value": {
                                "id": "comment_999",
                                "text": "What is the fee for face yoga?",
                                "media": {"id": "TARGET_POST_ID_HERE"},
                                "from": {"id": "user_101", "username": "ananya_singh"}
                            }
                        }
                    ]
                }
            ]
        }

        res = client.post("/meta/webhook", json=payload_inquiry)
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}

        # Allow background task to execute
        asyncio.run(asyncio.sleep(1.0))

        # Check public reply called
        assert mock_reply.called, "Public reply should have been called"
        args_reply = mock_reply.call_args[0]
        assert args_reply[0] == "comment_999"
        assert "@ananya_singh" in args_reply[1] or "ananya_singh" in args_reply[1]
        assert "*" not in args_reply[1], "Public reply must not contain markdown asterisks"

        # Check private DM called
        assert mock_dm.called, "Private DM should have been called"
        args_dm = mock_dm.call_args[0]
        assert args_dm[0] == "comment_999"
        assert len(args_dm[1]) > 0
        assert "*" not in args_dm[1], "Private DM must not contain markdown asterisks"

        # Check user state
        state = asyncio.run(get_user_state_async(user_key))
        assert state.get("is_target_ad") is True
        assert state.get("welcome_sent") is True
        assert state.get("channel") == "instagram"
        assert state.get("course") == "face_yoga"

        # Check chat history
        history = asyncio.run(get_recent_history_async(user_key))
        assert len(history) >= 2
        user_msgs = [m for m in history if m["role"] == "user"]
        ai_msgs = [m for m in history if m["role"] in ("assistant", "ai")]
        assert len(user_msgs) >= 1
        assert "What is the fee for face yoga?" in user_msgs[0]["content"]
        assert len(ai_msgs) >= 1

        print("✅ 2a. Genuine comment inquiry passed! Public reply & Private DM verified.")

        # Reset mocks
        mock_reply.reset_mock()
        mock_dm.reset_mock()

        # 2b. Complaint comment -> should be suppressed
        payload_complaint = {
            "object": "instagram",
            "entry": [
                {
                    "id": "PAGE_ID_1001",
                    "changes": [
                        {
                            "field": "comments",
                            "value": {
                                "id": "comment_998",
                                "text": "Fraud company fake class paise refund karo",
                                "media": {"id": "TARGET_POST_ID_HERE"},
                                "from": {"id": "user_102", "username": "angry_user"}
                            }
                        }
                    ]
                }
            ]
        }
        res = client.post("/meta/webhook", json=payload_complaint)
        assert res.status_code == 200
        asyncio.run(asyncio.sleep(0.5))
        assert not mock_reply.called, "Complaint must not trigger public reply"
        assert not mock_dm.called, "Complaint must not trigger private DM"
        print("✅ 2b. Complaint comment suppression passed!")

        # 2c. Pure friend tag -> should be suppressed
        payload_tag = {
            "object": "instagram",
            "entry": [
                {
                    "id": "PAGE_ID_1001",
                    "changes": [
                        {
                            "field": "comments",
                            "value": {
                                "id": "comment_997",
                                "text": "@neha_sharma @radhika123",
                                "media": {"id": "TARGET_POST_ID_HERE"},
                                "from": {"id": "user_103", "username": "tagger_user"}
                            }
                        }
                    ]
                }
            ]
        }
        res = client.post("/meta/webhook", json=payload_tag)
        assert res.status_code == 200
        asyncio.run(asyncio.sleep(0.5))
        assert not mock_reply.called, "Friend tag must not trigger public reply"
        assert not mock_dm.called, "Friend tag must not trigger private DM"
        print("✅ 2c. Pure friend tag suppression passed!")

        # 2d. Non-target post comment -> ignored
        payload_non_target = {
            "object": "instagram",
            "entry": [
                {
                    "id": "PAGE_ID_1001",
                    "changes": [
                        {
                            "field": "comments",
                            "value": {
                                "id": "comment_996",
                                "text": "What is the fee?",
                                "media": {"id": "UNCONFIGURED_POST_404"},
                                "from": {"id": "user_104", "username": "other_user"}
                            }
                        }
                    ]
                }
            ]
        }
        res = client.post("/meta/webhook", json=payload_non_target)
        assert res.status_code == 200
        asyncio.run(asyncio.sleep(0.5))
        assert not mock_reply.called
        assert not mock_dm.called
        print("✅ 2d. Non-target post comment ignored passed!")

        # 2e. Self-page anti-loop guard
        payload_self = {
            "object": "instagram",
            "entry": [
                {
                    "id": "PAGE_ID_1001",
                    "changes": [
                        {
                            "field": "comments",
                            "value": {
                                "id": "comment_995",
                                "text": "Thank you for commenting!",
                                "media": {"id": "TARGET_POST_ID_HERE"},
                                "from": {"id": "PAGE_ID_1001", "username": "our_own_page"}
                            }
                        }
                    ]
                }
            ]
        }
        res = client.post("/meta/webhook", json=payload_self)
        assert res.status_code == 200
        asyncio.run(asyncio.sleep(0.5))
        assert not mock_reply.called
        assert not mock_dm.called
        print("✅ 2e. Self-page anti-loop guard passed!")


def test_direct_messages_automation_e2e():
    print("\n--- [TEST 3] Direct Messages (DMs) Automation End-to-End ---")
    user_key = "ig_user_dm_201"
    asyncio.run(reset_user_state_async(user_key))
    _clear_test_history(user_key)

    # Pre-populate state as if user came from target post
    state = asyncio.run(get_user_state_async(user_key))
    state.update({
        "is_target_ad": True,
        "welcome_sent": True,
        "course": "face_yoga",
        "channel": "instagram",
    })
    from src.state.chat_state import save_user_state_async
    asyncio.run(save_user_state_async(user_key, state))

    with patch("src.services.meta_client.send_direct_message_async", new_callable=AsyncMock) as mock_send_dm:
        mock_send_dm.return_value = {"recipient_id": "user_dm_201", "message_id": "mid.123"}

        # 3a. DM continuation turn: User asks about batch timings
        incoming_dm = "Are there morning slots available for face yoga?"
        asyncio.run(process_incoming_message_async(user_key, incoming_dm))

        assert mock_send_dm.called, "send_direct_message_async should be called for DM"
        call_args = mock_send_dm.call_args[0]
        recipient_id, sent_msg = call_args[0], call_args[1]
        assert recipient_id == "user_dm_201"
        assert len(sent_msg) > 0
        assert "*" not in sent_msg, "Direct message reply must have markdown asterisks stripped for Meta"
        print(f"✅ 3a. Multi-turn DM continuation passed! Sample AI DM response: '{sent_msg[:120]}...'")

        # 3b. Verify history updated
        history = asyncio.run(get_recent_history_async(user_key))
        assert any("morning slots" in m.get("content", "").lower() for m in history if m["role"] == "user")
        assert any(m["role"] in ("assistant", "ai") for m in history)
        print("✅ 3b. History updated with DM exchange successfully!")


def test_organic_dm_intent_routing():
    print("\n--- [TEST 4] Organic DM Intent Routing ---")
    new_user = "ig_organic_301"
    asyncio.run(reset_user_state_async(new_user))
    _clear_test_history(new_user)

    # With allow_all_dms=False, verify course inquiry is allowed
    with patch("src.core.meta_loader.get_meta_settings", return_value={"allow_all_dms": False}), \
         patch("src.services.meta_client.send_direct_message_async", new_callable=AsyncMock) as mock_send_dm:

        mock_send_dm.return_value = {"recipient_id": "organic_301", "message_id": "mid.456"}

        # Course inquiry: "I want to learn kathak dance"
        asyncio.run(process_incoming_message_async(new_user, "I want to learn kathak dance"))
        assert mock_send_dm.called, "Organic course inquiry DM should be accepted and replied to"
        print("✅ 4a. Organic DM course inquiry accepted!")

        mock_send_dm.reset_mock()
        irrelevant_user = "ig_chitchat_302"
        asyncio.run(reset_user_state_async(irrelevant_user))
        # Non-course chatter: "What are you doing today?"
        asyncio.run(process_incoming_message_async(irrelevant_user, "What are you doing today?"))
        assert not mock_send_dm.called, "Irrelevant chatter DM should be ignored when allow_all_dms=False"
        print("✅ 4b. Irrelevant organic chatter DM ignored when allow_all_dms=False!")


def test_meta_quick_replies_and_media():
    print("\n--- [TEST 5] Meta Quick Reply Buttons & Media ---")
    test_ig_user = "ig_user_401"

    with patch("src.services.meta_client.send_quick_replies_async", new_callable=AsyncMock) as mock_qr, \
         patch("src.services.meta_client.send_media_message_async", new_callable=AsyncMock) as mock_media:

        mock_qr.return_value = {"message_id": "mid_qr"}
        mock_media.return_value = {"message_id": "mid_media"}

        # 5a. Quick Reply Buttons
        success_qr = asyncio.run(
            send_followup_with_buttons_async(test_ig_user, "Select your preferred slot:", ["Morning", "Evening"])
        )
        assert success_qr is True
        assert mock_qr.called
        recipient_id, text, buttons = mock_qr.call_args[0]
        assert recipient_id == "user_401"
        assert buttons == ["Morning", "Evening"]
        print("✅ 5a. Meta Quick Replies dispatch passed!")

        # 5b. Video / Media Message
        success_media = asyncio.run(
            send_video_message_async(test_ig_user, "https://example.com/demo.mp4", caption="Demo Video")
        )
        assert success_media is True
        assert mock_media.called
        recipient_id, media_type, url = mock_media.call_args[0]
        assert recipient_id == "user_401"
        assert media_type == "video"
        assert url == "https://example.com/demo.mp4"
        print("✅ 5b. Meta Media dispatch passed!")


def test_dm_escalation_and_reset():
    print("\n--- [TEST 6] DM Human Escalation & Reset Command ---")
    esc_user = "ig_user_501"
    asyncio.run(reset_user_state_async(esc_user))

    # Mark as target
    st = asyncio.run(get_user_state_async(esc_user))
    st["is_target_ad"] = True
    st["welcome_sent"] = True
    from src.state.chat_state import save_user_state_async
    asyncio.run(save_user_state_async(esc_user, st))

    with patch("src.services.meta_client.send_direct_message_async", new_callable=AsyncMock) as mock_send_dm:
        mock_send_dm.return_value = {"message_id": "mid_esc"}

        # 6a. User triggers human agent handoff
        asyncio.run(process_incoming_message_async(esc_user, "Can I speak to a human agent?"))
        assert mock_send_dm.called
        call_msg = mock_send_dm.call_args[0][1]
        assert "connecting you" in call_msg.lower() or "support team" in call_msg.lower()

        # Verify escalated
        assert asyncio.run(is_escalated_async(esc_user)) is True
        print("✅ 6a. DM human escalation passed!")

        # 6b. Subsequent messages while escalated should be ignored
        mock_send_dm.reset_mock()
        asyncio.run(process_incoming_message_async(esc_user, "Hello is anyone there?"))
        assert not mock_send_dm.called, "While escalated, AI should remain silent"
        print("✅ 6b. AI stays silent during escalation passed!")

        # 6c. Reset command clears escalation
        asyncio.run(process_incoming_message_async(esc_user, "reset"))
        assert mock_send_dm.called
        assert asyncio.run(is_escalated_async(esc_user)) is False
        print("✅ 6c. Reset command over DM clears escalation passed!")


if __name__ == "__main__":
    test_webhook_verification()
    test_comment_automation_e2e()
    test_direct_messages_automation_e2e()
    test_organic_dm_intent_routing()
    test_meta_quick_replies_and_media()
    test_dm_escalation_and_reset()
    print("\n" + "="*80)
    print("🏆 ALL META COMMENT & DM AUTOMATION TESTS COMPLETED SUCCESSFULLY!")
    print("="*80)
