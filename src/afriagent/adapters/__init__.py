"""Adapters — Channel-specific webhook handlers and message parsing."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, HTTPException
from typing import Any

from afriagent.config import settings
from afriagent.config.logging import get_logger
from afriagent.models import Channel, InboundMessage

log = get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# ══════════════════════════════════════════════════════════════════
# WhatsApp (Twilio)
# ══════════════════════════════════════════════════════════════════


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request) -> dict[str, Any]:
    """Handle incoming WhatsApp messages from Twilio.

    Twilio sends form-encoded data with fields like:
    - From: whatsapp:+254712345678
    - Body: message text
    - NumMedia: number of media attachments
    - MediaUrl0: URL of first media attachment
    """
    form = await request.form()

    sender = str(form.get("From", ""))
    body = str(form.get("Body", ""))
    num_media = int(str(form.get("NumMedia", "0")))
    media_url = str(form.get("MediaUrl0", "")) if num_media > 0 else None

    if not sender or not body:
        raise HTTPException(status_code=400, detail="Missing From or Body")

    # Extract phone number from whatsapp:+254712345678
    phone = sender.replace("whatsapp:", "")

    inbound = InboundMessage(
        channel=Channel.WHATSAPP,
        sender_id=phone,
        content=body,
        media_url=media_url,
        metadata={"twilio_sid": str(form.get("MessageSid", ""))},
    )

    # Process through the agent pipeline
    from afriagent.main import get_agent
    agent = get_agent()
    await agent.handle_message(inbound)

    # Twilio expects TwiML response (empty is fine for async processing)
    return {"status": "received"}


# ══════════════════════════════════════════════════════════════════
# Telegram
# ══════════════════════════════════════════════════════════════════


@router.post("/telegram")
async def telegram_webhook(request: Request) -> dict[str, str]:
    """Handle incoming Telegram updates.

    Telegram sends JSON with the Update object:
    {
        "update_id": 123,
        "message": {
            "message_id": 456,
            "from": {"id": 789, "first_name": "John"},
            "chat": {"id": 789, "type": "private"},
            "text": "Hello"
        }
    }
    """
    data = await request.json()

    message = data.get("message")
    if not message:
        return {"status": "no_message"}

    chat_id = str(message.get("chat", {}).get("id", ""))
    text = message.get("text", "")
    user = message.get("from", {})
    user_id = str(user.get("id", ""))

    if not chat_id or not text:
        return {"status": "incomplete"}

    inbound = InboundMessage(
        channel=Channel.TELEGRAM,
        sender_id=user_id,
        content=text,
        metadata={
            "chat_id": chat_id,
            "username": user.get("username", ""),
            "first_name": user.get("first_name", ""),
            "message_id": message.get("message_id"),
        },
    )

    from afriagent.main import get_agent
    agent = get_agent()
    await agent.handle_message(inbound)

    return {"status": "received"}


# ══════════════════════════════════════════════════════════════════
# M-Pesa Callback
# ══════════════════════════════════════════════════════════════════


@router.post("/mpesa/callback")
async def mpesa_callback(request: Request) -> dict[str, str]:
    """Handle M-Pesa payment callbacks from Safaricom.

    Callback body:
    {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": "...",
                "CheckoutRequestID": "...",
                "ResultCode": 0,
                "ResultDesc": "Success",
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": 1000},
                        {"Name": "MpesaReceiptNumber", "Value": "QHJ3R7ST9L"},
                        {"Name": "PhoneNumber", "Value": 254712345678}
                    ]
                }
            }
        }
    }
    """
    data = await request.json()
    callback = data.get("Body", {}).get("stkCallback", {})

    result_code = callback.get("ResultCode")
    checkout_id = callback.get("CheckoutRequestID", "")

    if result_code == 0:
        # Payment successful
        metadata = callback.get("CallbackMetadata", {}).get("Item", [])
        amount = next(
            (item["Value"] for item in metadata if item["Name"] == "Amount"), 0
        )
        receipt = next(
            (item["Value"] for item in metadata if item["Name"] == "MpesaReceiptNumber"),
            "",
        )
        phone = next(
            (item["Value"] for item in metadata if item["Name"] == "PhoneNumber"), 0
        )

        log.info(
            "M-Pesa payment received",
            checkout_id=checkout_id,
            amount=amount,
            receipt=receipt,
            phone=str(phone)[:8] + "***",
        )

        # TODO: Update invoice in WHMCS, notify customer via WhatsApp/Telegram
    else:
        log.warning(
            "M-Pesa payment failed",
            checkout_id=checkout_id,
            result_code=result_code,
            description=callback.get("ResultDesc"),
        )

    return {"status": "received"}


# ══════════════════════════════════════════════════════════════════
# Health check for webhooks
# ══════════════════════════════════════════════════════════════════


@router.get("/health")
async def webhook_health() -> dict[str, str]:
    return {"status": "healthy", "service": "afriagent-webhooks"}
