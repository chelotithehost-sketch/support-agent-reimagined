"""Transmitter — Multi-channel response delivery.

Takes a validated AgentResponse and delivers it through the
appropriate channel adapter (WhatsApp, Telegram, Webchat).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import httpx

from afriagent.config import settings
from afriagent.config.logging import get_logger
from afriagent.models import AgentResponse, Channel
from afriagent.observability import get_tracer

log = get_logger(__name__)
tracer = get_tracer(__name__)


# ══════════════════════════════════════════════════════════════════
# Base Adapter
# ══════════════════════════════════════════════════════════════════


class ChannelAdapter(ABC):
    """Abstract base for channel delivery adapters."""

    @abstractmethod
    async def send(self, recipient: str, message: str, **kwargs: Any) -> bool:
        """Send a message. Returns True on success."""
        ...

    @abstractmethod
    async def send_media(self, recipient: str, media_url: str, caption: str = "") -> bool:
        """Send media (image, document)."""
        ...


# ══════════════════════════════════════════════════════════════════
# WhatsApp (Twilio)
# ══════════════════════════════════════════════════════════════════


class WhatsAppAdapter(ChannelAdapter):
    """Twilio WhatsApp Business API adapter."""

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=30)
        self.account_sid = settings.twilio_account_sid
        self.auth_token = settings.twilio_auth_token
        self.from_number = settings.twilio_whatsapp_number
        self.base_url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"

    async def send(self, recipient: str, message: str, **kwargs: Any) -> bool:
        """Send WhatsApp message via Twilio."""
        with tracer.start_as_current_span("whatsapp.send") as span:
            span.set_attribute("recipient", recipient[:10] + "...")

            # Ensure WhatsApp prefix
            to = recipient if recipient.startswith("whatsapp:") else f"whatsapp:{recipient}"

            try:
                resp = await self._http.post(
                    self.base_url,
                    auth=(self.account_sid, self.auth_token),
                    data={
                        "From": self.from_number,
                        "To": to,
                        "Body": message,
                    },
                )
                resp.raise_for_status()
                log.info("WhatsApp message sent", recipient=to[:20], status=resp.status_code)
                return True
            except Exception as e:
                log.error("WhatsApp send failed", error=str(e), recipient=to[:20])
                return False

    async def send_media(self, recipient: str, media_url: str, caption: str = "") -> bool:
        """Send media message via WhatsApp."""
        to = recipient if recipient.startswith("whatsapp:") else f"whatsapp:{recipient}"
        try:
            resp = await self._http.post(
                self.base_url,
                auth=(self.account_sid, self.auth_token),
                data={
                    "From": self.from_number,
                    "To": to,
                    "Body": caption,
                    "MediaUrl": media_url,
                },
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            log.error("WhatsApp media send failed", error=str(e))
            return False

    async def close(self) -> None:
        await self._http.aclose()


# ══════════════════════════════════════════════════════════════════
# Telegram
# ══════════════════════════════════════════════════════════════════


class TelegramAdapter(ChannelAdapter):
    """Telegram Bot API adapter."""

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=30)
        self.token = settings.telegram_bot_token
        self.base_url = f"https://api.telegram.org/bot{self.token}"

    async def send(self, recipient: str, message: str, **kwargs: Any) -> bool:
        """Send Telegram message."""
        with tracer.start_as_current_span("telegram.send") as span:
            span.set_attribute("chat_id", recipient)

            try:
                resp = await self._http.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        "chat_id": recipient,
                        "text": message,
                        "parse_mode": kwargs.get("parse_mode", ""),
                    },
                )
                resp.raise_for_status()
                log.info("Telegram message sent", chat_id=recipient)
                return True
            except Exception as e:
                log.error("Telegram send failed", error=str(e), chat_id=recipient)
                return False

    async def send_media(self, recipient: str, media_url: str, caption: str = "") -> bool:
        """Send photo/document via Telegram."""
        try:
            resp = await self._http.post(
                f"{self.base_url}/sendPhoto",
                json={
                    "chat_id": recipient,
                    "photo": media_url,
                    "caption": caption,
                },
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            log.error("Telegram media send failed", error=str(e))
            return False

    async def close(self) -> None:
        await self._http.aclose()


# ══════════════════════════════════════════════════════════════════
# Webchat (in-process, no external call)
# ══════════════════════════════════════════════════════════════════


class WebchatAdapter(ChannelAdapter):
    """Webchat adapter — responses are returned directly via API."""

    async def send(self, recipient: str, message: str, **kwargs: Any) -> bool:
        # For webchat, sending is handled by the API response
        log.debug("Webchat response queued", recipient=recipient)
        return True

    async def send_media(self, recipient: str, media_url: str, caption: str = "") -> bool:
        return True


# ══════════════════════════════════════════════════════════════════
# Transmitter Orchestrator
# ══════════════════════════════════════════════════════════════════


class Transmitter:
    """Routes validated responses to the correct channel adapter."""

    def __init__(self) -> None:
        self.adapters: dict[Channel, ChannelAdapter] = {}
        self._pending_webchat: dict[str, str] = {}  # conversation_id → response

    def register_adapter(self, channel: Channel, adapter: ChannelAdapter) -> None:
        self.adapters[channel] = adapter
        log.info("Adapter registered", channel=channel.value)

    async def deliver(self, response: AgentResponse, recipient: str) -> bool:
        """Deliver response through the appropriate channel."""
        with tracer.start_as_current_span("transmitter.deliver") as span:
            span.set_attribute("channel", response.channel.value)

            adapter = self.adapters.get(response.channel)
            if not adapter:
                log.error("No adapter for channel", channel=response.channel.value)
                return False

            # For webchat, just store the response
            if response.channel == Channel.WEBCHAT:
                self._pending_webchat[response.conversation_id] = response.content
                return True

            # For messaging channels, send directly
            success = await adapter.send(recipient, response.content)

            if success:
                log.info(
                    "Response delivered",
                    channel=response.channel.value,
                    conversation_id=response.conversation_id,
                    confidence=response.confidence,
                )
            else:
                log.error(
                    "Delivery failed",
                    channel=response.channel.value,
                    conversation_id=response.conversation_id,
                )

            return success

    def get_webchat_response(self, conversation_id: str) -> str | None:
        """Retrieve pending webchat response."""
        return self._pending_webchat.pop(conversation_id, None)

    async def close_all(self) -> None:
        for adapter in self.adapters.values():
            if hasattr(adapter, "close"):
                await adapter.close()  # type: ignore[misc]
