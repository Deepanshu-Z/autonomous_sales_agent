"""
test_whatsapp_cloud_webhook.py — Tests for /test-whatsapp-webhook and WhatsApp Cloud client.

Verifies:
1. Meta Webhook Verification Challenge (GET /test-whatsapp-webhook)
2. Delivery/Read Status Receipts (POST /test-whatsapp-webhook -> 200 acknowledged)
3. Full Meta Cloud API Ingestion (POST /test-whatsapp-webhook)
4. Simplified Raw JSON Ingestion for cURL / Postman (POST /test-whatsapp-webhook)
5. Outbound WhatsApp Cloud client behavior with unconfigured / configured tokens
"""

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

# Set dummy environment variables before importing app
os.environ["META_VERIFY_TOKEN"] = "test_meta_verify_token_123"
os.environ["WHATSAPP_TOKEN"] = "test_whatsapp_token_xyz"
os.environ["PHONE_NUMBER_ID"] = "109876543210"

from src.api import create_app
from src.services.whatsapp_cloud_client import (
    send_whatsapp_cloud_text_async,
    send_whatsapp_cloud_buttons_async,
)

app = create_app()
client = TestClient(app)


def test_meta_webhook_verification_success():
    """GET verification challenge should echo hub.challenge when token matches."""
    res = client.get(
        "/test-whatsapp-webhook?hub.mode=subscribe&hub.verify_token=test_meta_verify_token_123&hub.challenge=challenge_token_999"
    )
    assert res.status_code == 200
    assert res.text == "challenge_token_999"


def test_meta_webhook_verification_failure():
    """GET verification challenge should return 403 when token is incorrect."""
    res = client.get(
        "/test-whatsapp-webhook?hub.mode=subscribe&hub.verify_token=wrong_token&hub.challenge=challenge_token_999"
    )
    assert res.status_code == 403
    assert "Forbidden" in res.text


def test_status_event_receipt_acknowledged():
    """Delivery/read statuses from Meta must return 200 OK acknowledged."""
    status_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "1001",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "109876543210"},
                            "statuses": [
                                {
                                    "id": "wamid.HBgL...",
                                    "status": "delivered",
                                    "timestamp": "1710000000",
                                    "recipient_id": "919876543210",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }
    res = client.post("/test-whatsapp-webhook", json=status_payload)
    assert res.status_code == 200
    assert res.json().get("message") == "status_acknowledged"


@patch("src.api.routes.add_message_to_batch_async", new_callable=AsyncMock)
def test_full_meta_cloud_api_message_ingestion(mock_add_batch):
    """Full Meta Cloud API message payload must be parsed and queued under wa_{phone}."""
    cloud_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "1001",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "109876543210"},
                            "contacts": [
                                {
                                    "profile": {"name": "Test Tester"},
                                    "wa_id": "919876543210",
                                }
                            ],
                            "messages": [
                                {
                                    "from": "919876543210",
                                    "id": "wamid.HBgL...",
                                    "timestamp": "1710000000",
                                    "text": {"body": "Hi, tell me about dance classes"},
                                    "type": "text",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }

    res = client.post("/test-whatsapp-webhook", json=cloud_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["processed"][0]["phone"] == "wa_919876543210"
    assert data["processed"][0]["status"] == "enqueued"

    mock_add_batch.assert_awaited_once_with(
        phone="wa_919876543210",
        text="Hi, tell me about dance classes",
        referral={},
    )


@patch("src.api.routes.add_message_to_batch_async", new_callable=AsyncMock)
def test_simplified_raw_json_message_ingestion(mock_add_batch):
    """Simplified raw JSON (cURL / Postman) should also be ingested seamlessly."""
    raw_payload = {
        "phone": "+919811122233",
        "message": "Do you offer online yoga classes?",
    }

    res = client.post("/test-whatsapp-webhook", json=raw_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["processed"][0]["phone"] == "wa_919811122233"
    assert data["processed"][0]["status"] == "enqueued"

    mock_add_batch.assert_awaited_once_with(
        phone="wa_919811122233",
        text="Do you offer online yoga classes?",
        referral={},
    )


async def test_outbound_client_unconfigured():
    """Client handles missing credentials safely without unhandled exception."""
    with patch.dict(os.environ, {"PHONE_NUMBER_ID": "", "WHATSAPP_TOKEN": ""}):
        res = await send_whatsapp_cloud_text_async("919876543210", "Hello from bot")
        assert res.get("status") == "unconfigured_credentials"


if __name__ == "__main__":
    import asyncio
    test_meta_webhook_verification_success()
    print("✅ test_meta_webhook_verification_success passed")
    test_meta_webhook_verification_failure()
    print("✅ test_meta_webhook_verification_failure passed")
    test_status_event_receipt_acknowledged()
    print("✅ test_status_event_receipt_acknowledged passed")
    test_full_meta_cloud_api_message_ingestion()
    print("✅ test_full_meta_cloud_api_message_ingestion passed")
    test_simplified_raw_json_message_ingestion()
    print("✅ test_simplified_raw_json_message_ingestion passed")
    asyncio.run(test_outbound_client_unconfigured())
    print("✅ test_outbound_client_unconfigured passed")
    print("\n🎉 ALL TESTS PASSED SUCCESSFULLY!")
