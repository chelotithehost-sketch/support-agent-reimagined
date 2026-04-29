"""Unit tests for the coordinator module."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from afriagent.models import ConversationContext, DispatchPlan, DispatchStep
from afriagent.coordinator import CoordinatorBrain
from afriagent.coordinator.dispatcher import (
    dispatch,
    _fallback_intent,
    _fallback_language,
    _build_fallback_plan,
)
from afriagent.coordinator.replanner import (
    StepResult,
    should_replan,
    should_escalate,
    get_next_provider,
    replan,
    MAX_REPLAN_CYCLES,
    CONFIDENCE_THRESHOLD,
)
from afriagent.coordinator.prompts import (
    build_system_prompt,
    get_few_shot_messages,
    FEW_SHOT_EXAMPLES,
)
from afriagent.coordinator.model import generate_json, reset_model


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def mock_context():
    """Minimal ConversationContext for testing."""
    from afriagent.models import (
        CustomerProfile, Message, Channel, MessageRole,
        Intent, Sentiment, Urgency,
    )
    from datetime import datetime, timezone

    customer = CustomerProfile(id="test-123", name="Test User")
    message = Message(
        conversation_id="conv-1",
        channel=Channel.WHATSAPP,
        role=MessageRole.CUSTOMER,
        content="I need help with my invoice",
    )
    return ConversationContext(
        conversation_id="conv-1",
        customer=customer,
        current_message=message,
        detected_intent=Intent.BILLING,
        detected_sentiment=Sentiment.NEUTRAL,
        detected_urgency=Urgency.MEDIUM,
        detected_language="en",
    )


@pytest.fixture
def tool_registry():
    return {
        "check_invoice": {
            "description": "Check invoice status",
            "requires": ["client_id"],
            "latency_profile": "medium",
        },
        "mpesa_push": {
            "description": "Initiate M-Pesa payment",
            "requires": ["phone", "amount"],
            "latency_profile": "medium",
        },
    }


# ── Fallback dispatch tests ──────────────────────────────────────


class TestFallbackIntent:
    def test_billing_keywords(self):
        assert _fallback_intent("I need to pay my invoice") == "billing"
        assert _fallback_intent("mpesa payment failed") == "billing"

    def test_outage_keywords(self):
        assert _fallback_intent("my website is down") == "outage"
        assert _fallback_intent("email not working") == "outage"

    def test_hostile_keywords(self):
        assert _fallback_intent("you scammers I want a refund") == "hostile"

    def test_unclear(self):
        assert _fallback_intent("help me") == "unclear"
        assert _fallback_intent("hello") == "unclear"


class TestFallbackLanguage:
    def test_sheng(self):
        assert _fallback_language("sasa buda, niaje?") == "sheng"
        assert _fallback_language("maze poa") == "sheng"

    def test_swahili(self):
        assert _fallback_language("habari yako, asante sana") == "sw"

    def test_english(self):
        assert _fallback_language("I need help with my domain") == "en"


class TestFallbackPlan:
    def test_builds_valid_plan(self, mock_context):
        plan = _build_fallback_plan(mock_context)
        assert isinstance(plan, DispatchPlan)
        assert plan.intent in ("billing", "outage", "general", "hostile", "unclear")
        assert 1 <= plan.urgency <= 5
        assert len(plan.steps) >= 1
        assert 0.0 <= plan.confidence <= 1.0

    def test_hostile_gets_ticket_step(self, mock_context):
        mock_context.current_message.content = "you scammers refund now"
        plan = _build_fallback_plan(mock_context)
        tools = [s.tool for s in plan.steps]
        assert "create_support_ticket" in tools


# ── CoordinatorBrain tests ───────────────────────────────────────


class TestCoordinatorBrain:
    @pytest.mark.asyncio
    async def test_dispatch_returns_plan(self, mock_context, tool_registry):
        brain = CoordinatorBrain(tool_registry=tool_registry)
        plan = await brain.dispatch(mock_context)
        assert isinstance(plan, DispatchPlan)
        assert plan.confidence > 0

    @pytest.mark.asyncio
    async def test_dispatch_with_llm(self, mock_context, tool_registry):
        """When coordinator LLM is available, it should be used."""
        mock_result = {
            "intent": "billing",
            "urgency": 3,
            "language": "en",
            "steps": [{"tool": "check_invoice", "llm_provider": None, "params": {}}],
            "confidence": 0.9,
            "reasoning": "Billing inquiry",
        }
        with patch("afriagent.coordinator.dispatcher.generate_json", return_value=mock_result):
            brain = CoordinatorBrain(tool_registry=tool_registry)
            plan = await brain.dispatch(mock_context)
            assert plan.intent == "billing"
            assert plan.confidence == 0.9

    @pytest.mark.asyncio
    async def test_dispatch_fallback_on_llm_failure(self, mock_context, tool_registry):
        """When coordinator LLM returns None, fallback should be used."""
        with patch("afriagent.coordinator.dispatcher.generate_json", return_value=None):
            brain = CoordinatorBrain(tool_registry=tool_registry)
            plan = await brain.dispatch(mock_context)
            assert isinstance(plan, DispatchPlan)
            assert plan.confidence >= 0.4  # Fallback confidence

    @pytest.mark.asyncio
    async def test_replan_returns_plan(self, mock_context, tool_registry):
        brain = CoordinatorBrain(tool_registry=tool_registry)
        prev_result = StepResult(
            step=DispatchStep(tool="check_invoice", llm_provider=None, params={}),
            content="",
            confidence=0.3,
            success=False,
            error="API timeout",
        )
        plan = await brain.replan(mock_context, prev_result, replan_count=0)
        assert isinstance(plan, DispatchPlan)


# ── Replanner tests ──────────────────────────────────────────────


class TestReplanner:
    def test_should_replan_low_confidence(self):
        result = StepResult(
            step=DispatchStep(tool="test", llm_provider=None, params={}),
            confidence=0.3,
        )
        assert should_replan(result, replan_count=0) is True

    def test_should_not_replan_high_confidence(self):
        result = StepResult(
            step=DispatchStep(tool="test", llm_provider=None, params={}),
            confidence=0.8,
        )
        assert should_replan(result, replan_count=0) is False

    def test_should_not_replan_max_cycles(self):
        result = StepResult(
            step=DispatchStep(tool="test", llm_provider=None, params={}),
            confidence=0.3,
        )
        assert should_replan(result, replan_count=MAX_REPLAN_CYCLES) is False

    def test_should_escalate_max_cycles(self):
        result = StepResult(
            step=DispatchStep(tool="test", llm_provider=None, params={}),
            confidence=0.3,
        )
        assert should_escalate(result, replan_count=MAX_REPLAN_CYCLES) is True

    def test_should_not_escalate_normal(self):
        result = StepResult(
            step=DispatchStep(tool="test", llm_provider=None, params={}),
            confidence=0.8,
        )
        assert should_escalate(result, replan_count=0) is False

    def test_get_next_provider_skips_circuit_broken(self):
        health = {"openai": {"status": "circuit_open"}, "anthropic": {"status": "healthy"}}
        with patch("afriagent.coordinator.replanner.settings") as mock_settings:
            mock_settings.llm_providers = ["openai", "anthropic"]
            result = get_next_provider("openai", health)
            assert result == "anthropic"

    def test_get_next_provider_all_healthy(self):
        health = {"openai": {"status": "healthy"}, "anthropic": {"status": "healthy"}}
        with patch("afriagent.coordinator.replanner.settings") as mock_settings:
            mock_settings.llm_providers = ["openai", "anthropic"]
            result = get_next_provider("openai", health)
            assert result == "anthropic"

    @pytest.mark.asyncio
    async def test_replan_max_cycles_returns_escalation(self, mock_context, tool_registry):
        result = StepResult(
            step=DispatchStep(tool="test", llm_provider=None, params={}),
            confidence=0.3,
            success=False,
        )
        plan = await replan(
            mock_context, result, replan_count=MAX_REPLAN_CYCLES,
            tool_registry=tool_registry, self_model_state={}, provider_health={},
        )
        assert plan.escalate is True


# ── Prompts tests ────────────────────────────────────────────────


class TestPrompts:
    def test_build_system_prompt_includes_tools(self, tool_registry):
        prompt = build_system_prompt(tool_registry, {}, {})
        assert "check_invoice" in prompt
        assert "mpesa_push" in prompt

    def test_build_system_prompt_includes_health(self, tool_registry):
        health = {"openai": {"status": "healthy", "avg_latency_ms": 500, "error_streak": 0}}
        prompt = build_system_prompt(tool_registry, {}, health)
        assert "openai" in prompt
        assert "healthy" in prompt

    def test_few_shot_messages_count(self):
        messages = get_few_shot_messages()
        # Each example produces 2 messages (user + assistant)
        assert len(messages) == len(FEW_SHOT_EXAMPLES) * 2

    def test_few_shot_messages_format(self):
        messages = get_few_shot_messages()
        for msg in messages:
            assert "role" in msg
            assert "content" in msg
            assert msg["role"] in ("user", "assistant")


# ── Model tests ──────────────────────────────────────────────────


class TestModel:
    def test_reset_model(self):
        reset_model()
        # After reset, model should be None
        from afriagent.coordinator.model import _model_instance
        assert _model_instance is None

    def test_generate_json_returns_none_without_model(self):
        reset_model()
        with patch("afriagent.coordinator.model._check_availability", return_value=False):
            result = generate_json("test prompt", "test system")
            assert result is None
