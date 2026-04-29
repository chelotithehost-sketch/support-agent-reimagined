"""Unit tests for the 9-layer response validator."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from afriagent.models import (
    Channel,
    ConversationContext,
    CustomerProfile,
    Intent,
    Message,
    MessageRole,
    ResponseCandidate,
    Sentiment,
    Urgency,
)
from afriagent.brain.validator import (
    relevance_gate,
    safety_filter,
    tone_checker,
    cultural_sensitivity_gate,
    completeness_gate,
    length_format_gate,
    emotional_alignment_gate,
    escalation_gate,
    ResponseValidator,
)


# ── Fixtures ──────────────────────────────────────────────────────


def make_context(
    content: str = "I need help with my bill",
    intent: Intent = Intent.BILLING,
    sentiment: Sentiment = Sentiment.NEUTRAL,
    urgency: Urgency = Urgency.MEDIUM,
    language: str = "en",
    channel: Channel = Channel.WHATSAPP,
    history: list[Message] | None = None,
) -> ConversationContext:
    return ConversationContext(
        conversation_id="test-conv-1",
        customer=CustomerProfile(id="cust-1", name="Test User"),
        current_message=Message(
            conversation_id="test-conv-1",
            channel=channel,
            role=MessageRole.CUSTOMER,
            content=content,
            language=language,
        ),
        message_history=history or [],
        detected_intent=intent,
        detected_sentiment=sentiment,
        detected_urgency=urgency,
        detected_language=language,
    )


def make_candidate(content: str, confidence: float = 0.8) -> ResponseCandidate:
    return ResponseCandidate(
        content=content,
        confidence=confidence,
        model_used="gpt-4o",
        tokens_used=100,
    )


# ── Layer 1: Relevance Gate ──────────────────────────────────────


class TestRelevanceGate:
    @pytest.mark.asyncio
    async def test_relevant_response(self):
        ctx = make_context("I need help with my billing invoice")
        candidate = make_candidate("I can help you with your invoice. Let me check your billing details.")
        result = await relevance_gate(candidate, ctx)
        assert result.passed is True
        assert result.score > 0.3

    @pytest.mark.asyncio
    async def test_irrelevant_response(self):
        ctx = make_context("I need help with my billing invoice")
        candidate = make_candidate("The weather is nice today and birds are singing in the trees.")
        result = await relevance_gate(candidate, ctx)
        assert result.score < 0.5


# ── Layer 2: Safety Filter ───────────────────────────────────────


class TestSafetyFilter:
    @pytest.mark.asyncio
    async def test_safe_response(self):
        ctx = make_context()
        candidate = make_candidate("Let me help you resolve this billing issue.")
        result = await safety_filter(candidate, ctx)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_unsafe_content(self):
        ctx = make_context()
        candidate = make_candidate("Here's how to hack into the system and bypass the password.")
        result = await safety_filter(candidate, ctx)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_sensitive_data_leak(self):
        ctx = make_context()
        candidate = make_candidate("Your card number is 4111 1111 1111 1111")
        result = await safety_filter(candidate, ctx)
        assert result.passed is False


# ── Layer 3: Tone Checker ────────────────────────────────────────


class TestToneChecker:
    @pytest.mark.asyncio
    async def test_empathetic_tone_for_frustrated(self):
        ctx = make_context(
            "This is terrible, nothing works!",
            sentiment=Sentiment.FRUSTRATED,
        )
        candidate = make_candidate("I understand your frustration and I'm sorry for the inconvenience. Let me fix this right away.")
        result = await tone_checker(candidate, ctx)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_aggressive_tone_with_frustrated(self):
        ctx = make_context(
            "This is terrible!",
            sentiment=Sentiment.FRUSTRATED,
        )
        candidate = make_candidate("You must follow the instructions. Failure to comply will result in consequences.")
        result = await tone_checker(candidate, ctx)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_neutral_tone_ok(self):
        ctx = make_context("What's my balance?")
        candidate = make_candidate("Your current balance is KSH 5,000.")
        result = await tone_checker(candidate, ctx)
        assert result.passed is True


# ── Layer 4: Cultural Sensitivity ────────────────────────────────


class TestCulturalSensitivity:
    @pytest.mark.asyncio
    async def test_culturally_appropriate(self):
        ctx = make_context()
        candidate = make_candidate("Karibu! Let me help you with your account.")
        result = await cultural_sensitivity_gate(candidate, ctx)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_culturally_insensitive(self):
        ctx = make_context()
        candidate = make_candidate("In your third-world country, this is how things work...")
        result = await cultural_sensitivity_gate(candidate, ctx)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_othering_language(self):
        ctx = make_context()
        candidate = make_candidate("You people always have these problems.")
        result = await cultural_sensitivity_gate(candidate, ctx)
        assert result.passed is False


# ── Layer 5: Completeness Gate ───────────────────────────────────


class TestCompletenessGate:
    @pytest.mark.asyncio
    async def test_billing_with_action(self):
        ctx = make_context("How do I pay?", intent=Intent.BILLING)
        candidate = make_candidate("You can pay via M-Pesa. Send to paybill 123456, account your email. Amount due is KSH 5,000.")
        result = await completeness_gate(candidate, ctx)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_billing_without_action(self):
        ctx = make_context("How do I pay?", intent=Intent.BILLING)
        candidate = make_candidate("Thank you for your inquiry.")
        result = await completeness_gate(candidate, ctx)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_technical_with_steps(self):
        ctx = make_context("My email isn't working", intent=Intent.TECHNICAL)
        candidate = make_candidate("Let me help. First, try clearing your browser cache. Then check your internet connection. If that doesn't work, try restarting your device.")
        result = await completeness_gate(candidate, ctx)
        assert result.passed is True


# ── Layer 6: Length & Format ─────────────────────────────────────


class TestLengthFormat:
    @pytest.mark.asyncio
    async def test_appropriate_whatsapp_length(self):
        ctx = make_context(channel=Channel.WHATSAPP)
        candidate = make_candidate("Your balance is KSH 5,000. You can pay via M-Pesa paybill 123456.")
        result = await length_format_gate(candidate, ctx)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_too_short(self):
        ctx = make_context()
        candidate = make_candidate("OK")
        result = await length_format_gate(candidate, ctx)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_markdown_in_whatsapp(self):
        ctx = make_context(channel=Channel.WHATSAPP)
        candidate = make_candidate("Your balance is **KSH 5,000**. Please pay via `M-Pesa`.")
        result = await length_format_gate(candidate, ctx)
        assert result.score < 1.0


# ── Layer 7: Emotional Alignment ─────────────────────────────────


class TestEmotionalAlignment:
    @pytest.mark.asyncio
    async def test_empathy_for_frustrated(self):
        ctx = make_context(sentiment=Sentiment.FRUSTRATED)
        candidate = make_candidate("I'm sorry you're experiencing this. I understand how frustrating it must be. Let me resolve this immediately.")
        result = await emotional_alignment_gate(candidate, ctx)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_dismissive_with_frustrated(self):
        ctx = make_context(sentiment=Sentiment.FRUSTRATED)
        candidate = make_candidate("You should have simply followed the instructions. It's easy.")
        result = await emotional_alignment_gate(candidate, ctx)
        assert result.passed is False


# ── Layer 8: Escalation Gate ─────────────────────────────────────


class TestEscalationGate:
    @pytest.mark.asyncio
    async def test_escalation_detected(self):
        ctx = make_context("I want to speak to a manager right now")
        candidate = make_candidate("I understand. Let me connect you with a supervisor.")
        result = await escalation_gate(candidate, ctx)
        assert len(result.suggestions) > 0

    @pytest.mark.asyncio
    async def test_no_escalation(self):
        ctx = make_context("What's my balance?")
        candidate = make_candidate("Your balance is KSH 5,000.")
        result = await escalation_gate(candidate, ctx)
        assert len(result.suggestions) == 0


# ── Full Pipeline ────────────────────────────────────────────────


class TestResponseValidator:
    @pytest.fixture
    def mock_llm(self):
        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value=MagicMock(content="CONSISTENT")
        )
        return llm

    @pytest.fixture
    def validator(self, mock_llm):
        return ResponseValidator(mock_llm)

    @pytest.mark.asyncio
    async def test_good_response_passes(self, validator):
        ctx = make_context("How do I pay my invoice?")
        candidate = make_candidate(
            "You can pay your invoice via M-Pesa. Use paybill 123456 with your email as the account reference. "
            "The amount due is KSH 5,000. Let me know if you need help with the steps."
        )
        result = await validator.validate(candidate, ctx)
        assert result.passed is True
        assert result.final_score > 0.5

    @pytest.mark.asyncio
    async def test_unsafe_response_fails(self, validator):
        ctx = make_context()
        candidate = make_candidate("Here's how to hack into the server and bypass the password protection.")
        result = await validator.validate(candidate, ctx)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_all_layers_run(self, validator):
        ctx = make_context("Hello!")
        candidate = make_candidate("Hello! How can I help you today?")
        result = await validator.validate(candidate, ctx)
        assert len(result.layers) == 9
