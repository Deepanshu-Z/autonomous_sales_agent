"""
test_meta_automation.py — Self-contained test suite for Meta (Instagram/Facebook) automation.
Validates:
1. Zero hardcoding: meta_loader reads settings and target posts dynamically.
2. Filtering: only configured post IDs trigger actions; non-target posts are ignored.
3. Abuse/Complaint guarding: negative comments are filtered out.
4. Formatting: clean formatting without WhatsApp asterisks for Instagram/FB.
5. Webhook routes: verification challenge and event routing.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json
import asyncio
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from fastapi.testclient import TestClient

from src.core import meta_loader
from src.workers.tasks import is_complaint_or_refund, check_target_ad
from src.api import create_app

def test_meta_config_and_loader():
    # 1. Settings load correctly
    settings = meta_loader.get_meta_settings()
    assert "graph_api_base" in settings
    assert "graph_api_version" in settings
    assert settings["delay_min"] <= settings["delay_max"]

    # 2. Match target post vs non-target post
    target_post = meta_loader.get_post_config("TARGET_POST_ID_HERE")
    assert target_post is not None
    assert target_post["course"] == "face_yoga"

    non_target_post = meta_loader.get_post_config("RANDOM_POST_999999")
    assert non_target_post is None

    # 3. Rotating public reply formatting
    reply = meta_loader.get_random_public_reply(target_post, username="priya_sharma")
    assert "priya_sharma" in reply
    assert "{" not in reply and "}" not in reply  # No unresolved placeholders

    # 4. Text formatter for Meta
    raw_text = "### Welcome to Yoga!\n*Price:* ₹600\n* Monday to Friday\n**Certified teachers**"
    cleaned = meta_loader.format_for_meta(raw_text)
    assert "*" not in cleaned  # WhatsApp asterisks stripped
    assert "#" not in cleaned  # Markdown headers cleaned
    print("✅ test_meta_config_and_loader passed!")


def test_complaint_guard_on_comments():
    positive_comment = "What are the timings for this class?"
    complaint_comment = "This is a fraud company paise wapas karo"
    refund_comment = "I want a refund for my payment"

    assert is_complaint_or_refund(positive_comment) is False
    assert is_complaint_or_refund(complaint_comment) is True
    assert is_complaint_or_refund(refund_comment) is True
    print("✅ test_complaint_guard_on_comments passed!")


def test_referral_matching_with_meta_post():
    referral = {"source_id": "TARGET_POST_ID_HERE"}
    is_target, reason = check_target_ad("Interested in yoga", referral_data=referral)
    assert is_target is True
    assert "TARGET_POST_ID_HERE" in reason

    non_target_referral = {"source_id": "UNREGISTERED_POST_123"}
    is_target_bad, reason_bad = check_target_ad("Hi", referral_data=non_target_referral)
    assert is_target_bad is False
    print("✅ test_referral_matching_with_meta_post passed!")


def test_webhook_routes():
    os.environ["META_VERIFY_TOKEN"] = "test_secret_token_123"
    app = create_app()
    client = TestClient(app)

    # 1. Challenge verification GET
    res = client.get("/meta/webhook?hub.mode=subscribe&hub.verify_token=test_secret_token_123&hub.challenge=987654")
    assert res.status_code == 200
    assert res.text == "987654"

    # Bad token rejection
    bad_res = client.get("/meta/webhook?hub.mode=subscribe&hub.verify_token=wrong_token&hub.challenge=987654")
    assert bad_res.status_code == 403

    # 2. Non-target comment POST (should return 200 ok, but perform no action)
    payload_non_target = {
        "object": "instagram",
        "entry": [
            {
                "id": "17841400",
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "id": "c_123",
                            "text": "Nice video!",
                            "media": {"id": "NON_TARGET_MEDIA_999"},
                            "from": {"id": "u_456", "username": "somebody"}
                        }
                    }
                ]
            }
        ]
    }
    res_post = client.post("/meta/webhook", json=payload_non_target)
    assert res_post.status_code == 200
    assert res_post.json() == {"status": "ok"}
    print("✅ test_webhook_routes passed!")


def test_dynamic_crisp_comment_reply():
    target_post = meta_loader.get_post_config("TARGET_POST_ID_HERE")
    reply = asyncio.run(meta_loader.generate_crisp_comment_reply_async("What are the fees?", "sneha_r", target_post))
    assert isinstance(reply, str)
    assert len(reply) > 0
    assert "@sneha_r" in reply or "sneha_r" in reply
    assert "*" not in reply  # No markdown formatting
    print(f"✅ test_dynamic_crisp_comment_reply passed! Sample output: '{reply}'")


def test_comment_intent_evaluator():
    target_post = meta_loader.get_post_config("TARGET_POST_ID_HERE")

    # 1. Genuine inquiry comment
    inquiry_res = asyncio.run(
        meta_loader.evaluate_and_generate_comment_actions_async(
            "What is the fee for face yoga classes?", "priya_s", target_post
        )
    )
    assert inquiry_res["should_reply"] is True
    assert inquiry_res["intent"] == "INQUIRY"
    assert len(inquiry_res["public_reply"]) > 0
    assert len(inquiry_res["private_dm"]) > 0
    assert "*" not in inquiry_res["public_reply"]
    assert "*" not in inquiry_res["private_dm"]

    # 2. Complaint / negative comment
    complaint_res = asyncio.run(
        meta_loader.evaluate_and_generate_comment_actions_async(
            "This is fraud, I paid and never got the class link", "angry_user", target_post
        )
    )
    assert complaint_res["should_reply"] is False
    assert complaint_res["intent"] in ("COMPLAINT", "TROLL_OR_ABUSIVE")
    assert complaint_res["public_reply"] == ""
    assert complaint_res["private_dm"] == ""

    # 3. Pure friend tag comment
    tag_res = asyncio.run(
        meta_loader.evaluate_and_generate_comment_actions_async(
            "@neha @anjali", "rahul_k", target_post
        )
    )
    assert tag_res["should_reply"] is False
    assert tag_res["intent"] == "SPAM_OR_IRRELEVANT"
    assert tag_res["public_reply"] == ""
    assert tag_res["private_dm"] == ""
    print("✅ test_comment_intent_evaluator passed!")


def test_organic_dm_intent_detector():
    # Course inquiry signals should qualify
    assert meta_loader.is_course_intent_dm("What are the batch timings for yoga?") is True
    assert meta_loader.is_course_intent_dm("Face yoga fee kitna hai?") is True
    assert meta_loader.is_course_intent_dm("I want to join dance classes") is True

    # Generic or irrelevant chit-chat should not qualify
    assert meta_loader.is_course_intent_dm("Hi how are you?") is False
    assert meta_loader.is_course_intent_dm("Nice picture") is False
    print("✅ test_organic_dm_intent_detector passed!")


if __name__ == "__main__":
    test_meta_config_and_loader()
    test_complaint_guard_on_comments()
    test_referral_matching_with_meta_post()
    test_webhook_routes()
    test_dynamic_crisp_comment_reply()
    test_comment_intent_evaluator()
    test_organic_dm_intent_detector()
    print("\n🎉 ALL META AUTOMATION & INTENT DETECTOR TESTS PASSED SUCCESSFULLY!")
