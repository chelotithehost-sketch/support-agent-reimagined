"""Tests for models."""

import pytest
from datetime import datetime, timezone

from afriagent.models import (
    Channel,
    ConversationContext,
    ConversationStatus,
    CustomerProfile,
    InboundMessage,
    Intent,
    Message,
    MessageRole,
    ResponseCandidate,
    Sentiment,
    Urgency,
    ValidationResult,
    ValidationLayer,
    AgentResponse,
    LearningExample,
    Conversation,
)


class TestMessage:
    def test_create_message(self):
        msg = Message(
            conversation_id="conv-1",
            channel=Channel.WHATSAPP,
            role=MessageRole.CUSTOMER,
            content="Hello!",
        )
        assert msg.id is not None
        assert msg.content == "Hello!"
        assert msg.channel == Channel.WHATSAPP
        assert msg.timestamp is not None

    def test_message_with_metadata(self):
        msg = Message(
            conversation_id="conv-1",
            channel=Channel.TELEGRAM,
            role=MessageRole.AGENT,
            content="Hi there!",
            metadata={"chat_id": "12345"},
        )
        assert msg.metadata["chat_id"] == "12345"


class TestInboundMessage:
    def test_create_inbound(self):
        inbound = InboundMessage(
            channel=Channel.WHATSAPP,
            sender_id="+254712345678",
            content="Help me",
        )
        assert inbound.sender_id == "+254712345678"
        assert inbound.media_url is None

    def test_inbound_with_media(self):
        inbound = InboundMessage(
            channel=Channel.WHATSAPP,
            sender_id="+254712345678",
            content="Look at this",
            media_url="https://example.com/image.jpg",
        )
        assert inbound.media_url == "https://example.com/image.jpg"


class TestCustomerProfile:
    def test_defaults(self):
        profile = CustomerProfile(id="cust-1")
        assert profile.name == ""
        assert profile.active_services == []
        assert profile.tags == []

    def test_with_data(self):
        profile = CustomerProfile(
            id="cust-1",
            name="John Doe",
            email="john@example.com",
            whmcs_client_id=42,
            preferred_language="sw",
        )
        assert profile.name == "John Doe"
        assert profile.whmcs_client_id == 42


class TestConversationContext:
    def test_create_context(self):
        ctx = ConversationContext(
            conversation_id="conv-1",
            customer=CustomerProfile(id="cust-1"),
            current_message=Message(
                conversation_id="conv-1",
                channel=Channel.WHATSAPP,
                role=MessageRole.CUSTOMER,
                content="Hello",
            ),
        )
        assert ctx.detected_intent == Intent.GENERAL
        assert ctx.detected_sentiment == Sentiment.NEUTRAL


class TestValidationResult:
    def test_passing_validation(self):
        result = ValidationResult(
            passed=True,
            final_score=0.85,
            layers=[
                ValidationLayer(layer_name="safety", passed=True, score=1.0),
                ValidationLayer(layer_name="relevance", passed=True, score=0.7),
            ],
        )
        assert result.passed is True
        assert len(result.layers) == 2

    def test_failing_validation(self):
        result = ValidationResult(
            passed=False,
            final_score=0.3,
            issues=["Unsafe content"],
        )
        assert result.passed is False
        assert "Unsafe content" in result.issues


class TestEnums:
    def test_channel_values(self):
        assert Channel.WHATSAPP.value == "whatsapp"
        assert Channel.TELEGRAM.value == "telegram"

    def test_intent_values(self):
        assert Intent.BILLING.value == "billing"
        assert Intent.ESCALATION.value == "escalation"

    def test_sentiment_values(self):
        assert Sentiment.FRUSTRATED.value == "frustrated"
