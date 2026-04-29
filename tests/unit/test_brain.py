"""Unit tests for the Brain (response generation)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from afriagent.models import (
    Channel,
    ConversationContext,
    CustomerProfile,
    Intent,
    Message,
    MessageRole,
    Sentiment,
    Urgency,
    ValidationResult,
    ValidationLayer,
)
from afriagent.brain import Brain
from afriagent.brain.llm import LLMResponse


def make_context(
    content: str = "I need help with my bill",
    intent: Intent = Intent.BILLING,
    sentiment: Sentiment = Sentiment.NEUTRAL,
    language: str = "en",
) -> ConversationContext:
    return ConversationContext(
        conversation_id="test-conv-1",
        customer=CustomerProfile(id="cust-1", name="Test User"),
        current_message=Message(
            conversation_id="test-conv-1",
            channel=Channel.WHATSAPP,
            role=MessageRole.CUSTOMER,
            content=content,
            language=language,
        ),
        detected_intent=intent,
        detected_sentiment=sentiment,
        detected_urgency=Urgency.MEDIUM,
        detected_language=language,
    )


class TestBrain:
    @pytest.fixture
    def mock_llm(self):
        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value=LLMResponse(
                content="You can pay your invoice via M-Pesa. Use paybill 123456.",
                model="gpt-4o",
                provider="openai",
                tokens_input=50,
                tokens_output=30,
            )
        )
        llm.embed = AsyncMock(return_value=[0.1] * 1536)
        return llm

    @pytest.fixture
    def mock_memory(self):
        memory = AsyncMock()
        memory.session.get_session = AsyncMock(return_value={})
        memory.session.set_session = AsyncMock()
        memory.episodic.save_message = AsyncMock()
        memory.episodic.get_learning_examples = AsyncMock(return_value=[])
        memory.semantic.store_pattern = AsyncMock()
        memory.semantic.search_similar = AsyncMock(return_value=[])
        return memory

    @pytest.fixture
    def brain(self, mock_llm, mock_memory):
        return Brain(mock_llm, mock_memory)

    @pytest.mark.asyncio
    async def test_generate_response_basic(self, brain, mock_llm):
        ctx = make_context("How do I pay my invoice?")

        # Mock validator to always pass
        brain.validator.validate = AsyncMock(
            return_value=ValidationResult(
                passed=True,
                final_score=0.85,
                layers=[],
                issues=[],
            )
        )

        response = await brain.generate_response(ctx)

        assert response.content == "You can pay your invoice via M-Pesa. Use paybill 123456."
        assert response.confidence == 0.85
        assert response.conversation_id == "test-conv-1"
        assert response.channel == Channel.WHATSAPP
        mock_llm.generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_response_with_translation(self, brain, mock_llm):
        ctx = make_context(
            "Nataka kulipa bili yangu",
            language="sw",
        )

        # First call: generate response, second call: translate
        mock_llm.generate = AsyncMock(
            side_effect=[
                LLMResponse(
                    content="You can pay via M-Pesa paybill 123456.",
                    model="gpt-4o",
                    provider="openai",
                ),
                LLMResponse(
                    content="Unaweza kulipa kupitia M-Pesa paybill 123456.",
                    model="gpt-4o",
                    provider="openai",
                ),
            ]
        )

        brain.validator.validate = AsyncMock(
            return_value=ValidationResult(passed=True, final_score=0.8, layers=[], issues=[])
        )

        response = await brain.generate_response(ctx)
        assert "M-Pesa" in response.content
        assert mock_llm.generate.call_count == 2  # generate + translate

    @pytest.mark.asyncio
    async def test_regeneration_on_validation_failure(self, brain, mock_llm):
        ctx = make_context()

        # First validation fails, second passes
        brain.validator.validate = AsyncMock(
            side_effect=[
                ValidationResult(
                    passed=False,
                    final_score=0.3,
                    layers=[],
                    issues=["Response too short"],
                ),
                ValidationResult(
                    passed=True,
                    final_score=0.75,
                    layers=[],
                    issues=[],
                ),
            ]
        )

        response = await brain.generate_response(ctx)
        assert response.confidence == 0.75
        # LLM called twice (original + regeneration)
        assert mock_llm.generate.call_count >= 2

    @pytest.mark.asyncio
    async def test_escalation_detected(self, brain, mock_llm):
        ctx = make_context("I want to speak to a manager")

        brain.validator.validate = AsyncMock(
            return_value=ValidationResult(
                passed=True,
                final_score=0.7,
                layers=[
                    ValidationLayer(
                        layer_name="escalation_gate",
                        passed=True,
                        score=0.5,
                        suggestions=["Customer requesting escalation"],
                    )
                ],
                issues=[],
            )
        )

        response = await brain.generate_response(ctx)
        assert response.escalated is True

    @pytest.mark.asyncio
    async def test_system_prompt_includes_intent(self, brain, mock_llm):
        ctx = make_context(intent=Intent.TECHNICAL)

        brain.validator.validate = AsyncMock(
            return_value=ValidationResult(passed=True, final_score=0.8, layers=[], issues=[])
        )

        await brain.generate_response(ctx)

        # Check that the system prompt includes technical context
        call_args = mock_llm.generate.call_args
        messages = call_args[0][0]
        system_msg = next(m for m in messages if m["role"] == "system")
        assert "TECHNICAL CONTEXT" in system_msg["content"] or "troubleshooting" in system_msg["content"].lower()

    @pytest.mark.asyncio
    async def test_history_included(self, brain, mock_llm):
        history = [
            Message(
                conversation_id="test-conv-1",
                channel=Channel.WHATSAPP,
                role=MessageRole.CUSTOMER,
                content="Hello",
            ),
            Message(
                conversation_id="test-conv-1",
                channel=Channel.WHATSAPP,
                role=MessageRole.AGENT,
                content="Hi! How can I help?",
            ),
        ]
        ctx = make_context("I have a billing question")
        ctx.message_history = history

        brain.validator.validate = AsyncMock(
            return_value=ValidationResult(passed=True, final_score=0.8, layers=[], issues=[])
        )

        await brain.generate_response(ctx)

        call_args = mock_llm.generate.call_args
        messages = call_args[0][0]
        user_messages = [m for m in messages if m["role"] == "user"]
        assert len(user_messages) >= 2  # history + current
