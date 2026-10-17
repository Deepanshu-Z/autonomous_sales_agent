"""
quick_reply_service.py — Independent WhatsApp Quick Reply Buttons Service.

Sends interactive quick-reply buttons (max 3 buttons, max 20 chars each) via Interakt.
If deleted or discontinued, conversation_handler automatically falls back to plain text.
"""

import httpx
from typing import List, Optional
from src.services.interakt import (
    _split_phone,
    _headers,
    _get_async_client,
    BASE_URL,
    send_text_message_async,
)


async def send_followup_with_buttons_async(
    phone: str,
    followup_text: str,
    buttons: Optional[List[str]] = None,
) -> bool:
    """
    Sends followup message with interactive WhatsApp quick reply buttons.
    - Max 3 buttons allowed by WhatsApp.
    - Max 20 characters per button title.
    Falls back to standard text message if buttons are empty or if the API call fails.
    """
    valid_buttons = [str(b).strip()[:20] for b in (buttons or []) if str(b).strip()][:3]

    clean_phone = str(phone or "").strip()
    if clean_phone.startswith("ig_") or clean_phone.startswith("fb_"):
        from src.services.meta_client import send_quick_replies_async
        from src.core.meta_loader import format_for_meta
        clean_text = format_for_meta(followup_text)
        recipient_id = clean_phone.split("_", 1)[1]
        channel = "facebook" if clean_phone.startswith("fb_") else "instagram"
        try:
            res = await send_quick_replies_async(recipient_id, clean_text, valid_buttons, channel=channel)
            if isinstance(res, dict) and "error" not in res:
                return True
        except Exception as e:
            print(f"[quick_reply_service] Meta quick replies error for {clean_phone}: {e}")
        await send_text_message_async(clean_phone, clean_text)
        return False

    if clean_phone.startswith("wa_"):
        from src.services.whatsapp_cloud_client import send_whatsapp_cloud_buttons_async
        recipient_id = clean_phone.split("_", 1)[1]
        try:
            res = await send_whatsapp_cloud_buttons_async(recipient_id, followup_text, valid_buttons)
            if isinstance(res, dict) and "error" not in res:
                return True
        except Exception as e:
            print(f"[quick_reply_service] WhatsApp Cloud buttons error for {clean_phone}: {e}")
        await send_text_message_async(clean_phone, followup_text)
        return False

    if not valid_buttons:
        # No buttons requested — send as plain text
        await send_text_message_async(phone, followup_text)
        return True

    country_code, number = _split_phone(phone)
    payload = {
        "countryCode": country_code,
        "phoneNumber": number,
        "type": "InteractiveButton",
        "data": {
            "message": {
                "type": "button",
                "body": {"text": followup_text},
                "action": {
                    "buttons": [
                        {
                            "type": "reply",
                            "reply": {
                                "id": f"btn_{i+1}",
                                "title": title,
                            },
                        }
                        for i, title in enumerate(valid_buttons)
                    ]
                },
            }
        },
    }

    try:
        client = _get_async_client()
        response = await client.post(
            f"{BASE_URL}/message/",
            headers=_headers(),
            json=payload,
        )
        if response.status_code in (200, 201):
            return True
        else:
            print(f"[quick_reply_service] InteractiveButton returned {response.status_code} ({response.text}), falling back to text.")
            await send_text_message_async(phone, followup_text)
            return False
    except Exception as e:
        print(f"[quick_reply_service] Error sending buttons: {e}, falling back to text.")
        await send_text_message_async(phone, followup_text)
        return False
