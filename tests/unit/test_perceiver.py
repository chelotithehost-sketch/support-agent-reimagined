"""Unit tests for the Perceiver pipeline."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from afriagent.models import Channel, InboundMessage, Intent, Sentiment, Urgency
from afriagent.perceiver import (
    Perceiver,
    detect_language,
    classify_intent,
    detect_sentiment,
    detect_urgency,
)


# ── Language Detection ────────────────────────────────────────────


class TestDetectLanguage:
    def test_english(self):
        assert detect_language("Hello, I need help with my account") == "en"

    def test_swahili(self):
        assert detect_language("Habari, nina tatizo na akaunti yangu") == "sw"

    def test_french(self):
        assert detect_language("Bonjour, j'ai un problème avec mon compte") == "fr"

    def test_empty(self):
        assert detect_language("") == "en"


# ── Intent Classification ────────────────────────────────────────


class TestClassifyIntent:
    def test_billing(self):
        assert classify_intent("I need to pay my invoice") == Intent.BILLING
        assert classify_intent("How much is my bill?") == Intent.BILLING
        assert classify_intent("Nataka kulipa bili yangu") == Intent.BILLING

    def test_technical(self):
        assert classify_intent("My server is down") == Intent.TECHNICAL
        assert classify_intent("I'm getting an error on my website") == Intent.TECHNICAL
        assert classify_intent("Tatizo na barua pepe") == Intent.TECHNICAL

    def test_sales(self):
        assert classify_intent("I want to upgrade my plan") == Intent.SALES
        assert classify_intent("What features do you offer?") == Intent.SALES

    def test_escalation(self):
        assert classify_intent("I want to speak to a manager") == Intent.ESCALATION
        assert classify_intent("This is unacceptable, get me a human") == Intent.ESCALATION

    def test_greeting(self):
        assert classify_intent("Hello!") == Intent.GREETING
        assert classify_intent("Habari") == Intent.GREETING

    def test_general(self):
        assert classify_intent("What's the weather like?") == Intent.GENERAL


# ── Sentiment Detection ──────────────────────────────────────────


class TestDetectSentiment:
    def test_positive(self):
        assert detect_sentiment("Great service, thank you!") == Sentiment.POSITIVE
        assert detect_sentiment("Nzuri sana, asante!") == Sentiment.POSITIVE

    def test_negative(self):
        assert detect_sentiment("This is terrible service") == Sentiment.NEGATIVE

    def test_frustrated(self):
        assert detect_sentiment(
            "I've been trying to get help again and again, this is terrible and useless"
        ) == Sentiment.FRUSTRATED

    def test_neutral(self):
        assert detect_sentiment("Can you check my account balance?") == Sentiment.NEUTRAL


# ── Urgency Detection ────────────────────────────────────────────


class TestDetectUrgency:
    def test_critical(self):
        assert detect_urgency("We had a security breach", Sentiment.NEGATIVE) == Urgency.CRITICAL

    def test_high(self):
        assert detect_urgency("Our production server is down", Sentiment.NEGATIVE) == Urgency.HIGH

    def test_high_from_frustration(self):
        assert detect_urgency("I'm very unhappy", Sentiment.FRUSTRATED) == Urgency.HIGH

    def test_low(self):
        assert detect_urgency("Hello there", Sentiment.POSITIVE) == Urgency.LOW


# ── Perceiver Integration ────────────────────────────────────────


class TestPerceiver:
    @pytest.fixture
    def mock_memory(self):
        memory = AsyncMock()
        memory.session.acquire_lock = AsyncMock(return_value=True)
        memory.session.get_customer_state = AsyncMock(return_value=None)
        memory.session.set_customer_state = AsyncMock()
        memory.session.set_session = AsyncMock()
        memory.episodic.get_conversation_history = AsyncMock(return_value=[])
        memory.episodic.save_message = AsyncMock()
        memory.semantic.search_similar = AsyncMock(return_value=[])
        return memory

    @pytest.fixture
    def mock_llm(self):
        llm = AsyncMock()
        llm.embed = AsyncMock(return_value=[0.1] * 1536)
        llm.generate = AsyncMock(return_value=MagicMock(content="Hello!"))
        return llm

    @pytest.fixture
    def perceiver(self, mock_memory, mock_llm):
        return Perceiver(mock_memory, mock_llm)

    @pytest.mark.asyncio
    async def test_process_basic_message(self, perceiver):
        inbound = InboundMessage(
            channel=Channel.WHATSAPP,
            sender_id="+254712345678",
            content="Hello, I need help with my bill",
        )

        context = await perceiver.process(inbound)

        assert context.current_message.content == "Hello, I need help with my bill"
        assert context.detected_intent in (Intent.BILLING, Intent.GREETING)
        assert context.detected_language == "en"
        assert context.customer.phone == "+254712345678"

    @pytest.mark.asyncio
    async def test_process_swahili_message(self, perceiver):
        inbound = InboundMessage(
            channel=Channel.WHATSAPP,
            sender_id="+254712345678",
            content="Habari, nina tatizo na akaunti yangu",
        )

        context = await perceiver.process(inbound)

        assert context.detected_language == "sw"
        # Should have attempted translation
        perceiver.llm.generate.assert_called()

    @pytest.mark.asyncio
    async def test_dedup_rejects_duplicate(self, perceiver, mock_memory):
        mock_memory.session.acquire_lock = AsyncMock(return_value=False)

        inbound = InboundMessage(
            channel=Channel.WHATSAPP,
            sender_id="+254712345678",
            content="Hello",
        )

        with pytest.raises(ValueError, match="Duplicate"):
            await perceiver.process(inbound)
