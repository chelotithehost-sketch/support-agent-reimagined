"""Unit tests for the Transmitter."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from afriagent.models import Channel, AgentResponse, ValidationResult, Intent
from afriagent.transmitter import (
    Transmitter,
    WhatsAppAdapter,
    TelegramAdapter,
    WebchatAdapter,
)


def make_response(
    content: str = "Test response",
    channel: Channel = Channel.WHATSAPP,
    conversation_id: str = "conv-1",
    confidence: float = 0.8,
) -> AgentResponse:
    return AgentResponse(
        conversation_id=conversation_id,
        content=content,
        channel=channel,
        confidence=confidence,
        validation=ValidationResult(passed=True, final_score=confidence, layers=[]),
        intent_handled=Intent.BILLING,
    )


class TestWebchatAdapter:
    @pytest.mark.asyncio
    async def test_send_returns_true(self):
        adapter = WebchatAdapter()
        result = await adapter.send("user-1", "Hello")
        assert result is True

    @pytest.mark.asyncio
    async def test_send_media_returns_true(self):
        adapter = WebchatAdapter()
        result = await adapter.send_media("user-1", "http://example.com/img.png", "caption")
        assert result is True


class TestTransmitter:
    @pytest.fixture
    def transmitter(self):
        t = Transmitter()
        t.register_adapter(Channel.WEBCHAT, WebchatAdapter())
        return t

    @pytest.mark.asyncio
    async def test_webchat_delivery(self, transmitter):
        response = make_response(channel=Channel.WEBCHAT, conversation_id="conv-123")
        result = await transmitter.deliver(response, "user-1")
        assert result is True

        # Should be retrievable
        pending = transmitter.get_webchat_response("conv-123")
        assert pending == "Test response"

    @pytest.mark.asyncio
    async def test_webchat_response_consumed_on_read(self, transmitter):
        response = make_response(channel=Channel.WEBCHAT, conversation_id="conv-456")
        await transmitter.deliver(response, "user-1")

        # First read returns the response
        assert transmitter.get_webchat_response("conv-456") == "Test response"
        # Second read returns None (consumed)
        assert transmitter.get_webchat_response("conv-456") is None

    @pytest.mark.asyncio
    async def test_missing_adapter_fails(self):
        transmitter = Transmitter()
        # Don't register any adapters
        response = make_response(channel=Channel.TELEGRAM)
        result = await transmitter.deliver(response, "user-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_whatsapp_delivery(self):
        mock_adapter = AsyncMock()
        mock_adapter.send = AsyncMock(return_value=True)

        transmitter = Transmitter()
        transmitter.register_adapter(Channel.WHATSAPP, mock_adapter)

        response = make_response(channel=Channel.WHATSAPP)
        result = await transmitter.deliver(response, "+254712345678")

        assert result is True
        mock_adapter.send.assert_called_once_with("+254712345678", "Test response")
