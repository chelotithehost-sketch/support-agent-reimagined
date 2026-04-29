# tests/integration/test_full_pipeline.py
# ───────────────────────────────────────────────
# Integration test: Perceiver → Router → Reasoner → Drafter → Validator → Transmitter
# Uses mocks for LLM calls; tests data flow between real component instances.
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.core.perceiver import MessagePerceiver, Perception
from agents.core.router import TaskRouter, RoutingTask
from agents.core.reasoner import ResolutionReasoner, ReasoningPackage
from agents.core.drafter import ResponseDrafter, DraftResult
from agents.core.validator import ResponseValidator, ValidationResult
from agents.core.transmitter import ResponseTransmitter
from agents.memory.schemas import (
    CustomerContext,
    CustomerTier,
    EmotionalState,
    Intent,
    ProductArea,
    Urgency,
    WorkingMemory,
    SemanticResults,
)


# ── Helpers ─────────────────────────────────────

def make_working_memory(turns: list[dict] | None = None, clarifications: int = 0) -> WorkingMemory:
    return WorkingMemory(
        conversation_id=str(uuid.uuid4()),
        turns=turns or [],
        clarifications_asked=clarifications,
        customer_intent_history=[],
        session_started=datetime.utcnow(),
    )


def make_customer_context(tier: str = "standard", churn_risk: float = 0.1) -> CustomerContext:
    return CustomerContext(
        customer_id=str(uuid.uuid4()),
        tier=CustomerTier(tier),
        total_tickets=3,
        open_tickets=1,
        lifetime_value_usd=150.0,
        days_as_customer=120,
        recent_csat_scores=[4, 5],
        churn_risk_score=churn_risk,
        plan_name="Starter Hosting",
        plan_expiry=datetime.utcnow() + timedelta(days=180),
    )


# ── Tests ───────────────────────────────────────

class TestFullPipelineHappyPath:
    """End-to-end: customer asks a clear question → gets a valid answer."""

    @pytest.mark.asyncio
    async def test_email_troubleshooting_flow(self):
        """'My email isn't sending' → perceives EMAIL_SETUP → routes → reasons → drafts → validates."""
        perceiver = MessagePerceiver()

        # 1. Perceive
        perception = await perceiver.perceive(
            "My email isn't sending from my domain example.co.ke. I'm getting bouncebacks.",
            working_memory=make_working_memory(),
        )
        assert perception.intent == Intent.EMAIL_SETUP
        assert perception.product_area == ProductArea.EMAIL
        assert "example.co.ke" in perception.domain_names
        assert perception.confidence > 0.5

        # 2. Route (mock LLM profiles)
        router = TaskRouter(profiles_path=None)
        router._models = {
            "ollama/qwen2.5:7b": MagicMock(
                id="ollama/qwen2.5:7b",
                cost_per_1k_input=0.0,
                cost_per_1k_output=0.0,
                avg_latency_ms=800,
                context_window=32000,
                supports_json_mode=False,
                capabilities=["general", "email"],
                max_urgency="critical",
                is_local=True,
                always_available=True,
            ),
        }
        router._circuit_breakers = {"ollama": MagicMock(is_open=False, failure_count=0)}

        task = RoutingTask(
            required_capabilities=["email"],
            urgency=perception.urgency,
            emotional_state=perception.emotional_state,
        )
        selection = router.route(task)
        assert selection.provider == "ollama"
        assert selection.model == "qwen2.5:7b"

        # 3. Reason
        reasoner = ResolutionReasoner()
        wm = make_working_memory(
            turns=[
                {"role": "customer", "content": "My email isn't sending from example.co.ke"},
            ],
            clarifications=0,
        )
        pkg = await reasoner.reason(
            perception=perception,
            working_memory=wm,
            episodic_context=make_customer_context(),
            semantic_results=SemanticResults(similar_resolutions=[], cross_customer_patterns=[], confidence=0.0),
        )
        assert pkg.confidence > 0.0
        assert pkg.tone_instruction  # should have tone guidance
        assert len(pkg.must_avoid) > 0  # should have constraints

        # 4. Draft (mock LLM call)
        drafter = ResponseDrafter()
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=MagicMock(
            content="I see you're having trouble sending email from example.co.ke. Here's what to check:\n\n1. Verify your MX records point to our mail servers\n2. Check that your SPF record includes our servers\n3. Wait up to 30 minutes for DNS propagation\n\nWould you like me to walk you through checking your MX records?",
            usage=MagicMock(prompt_tokens=300, completion_tokens=80, total_tokens=380),
        ))
        drafter._clients = {"ollama": mock_client}

        draft = await drafter.draft(
            customer_message="My email isn't sending from example.co.ke",
            reasoning_package=pkg,
            perception=perception,
            model_selection=selection,
        )
        assert "MX" in draft.response_text or "email" in draft.response_text.lower()
        assert draft.confidence > 0.0

        # 5. Validate
        validator = ResponseValidator()
        result = await validator.validate(
            draft=draft.response_text,
            reasoning_package=pkg,
            perception=perception,
        )
        # Should pass safety, brand, escalation gates
        safety = next(l for l in result.layers if l.layer_name == "safety")
        assert safety.passed is True
        brand = next(l for l in result.layers if l.layer_name == "brand")
        assert brand.passed is True

        # 6. Transmitter (just verify it formats correctly)
        transmitter = ResponseTransmitter()
        outbound = transmitter.prepare_outbound(
            response_text=draft.response_text,
            channel="whatsapp",
            perception=perception,
        )
        assert outbound.text  # non-empty
        assert len(outbound.text) <= 4096  # WhatsApp limit


class TestEscalationPipeline:
    """Angry customer with threat → should recommend escalation."""

    @pytest.mark.asyncio
    async def test_angry_billing_escalation(self):
        perceiver = MessagePerceiver()

        perception = await perceiver.perceive(
            "This is UNACCEPTABLE! You charged me twice! I want to speak to a manager or I'm moving to a competitor!",
            working_memory=make_working_memory(),
        )
        assert perception.emotional_state == EmotionalState.ANGRY
        assert perception.contains_threat is True
        assert perception.is_escalation_request is True

        reasoner = ResolutionReasoner()
        wm = make_working_memory(clarifications=0)
        pkg = await reasoner.reason(
            perception=perception,
            working_memory=wm,
            episodic_context=make_customer_context(churn_risk=0.9),
            semantic_results=SemanticResults(similar_resolutions=[], cross_customer_patterns=[], confidence=0.0),
        )
        assert pkg.escalation_recommended is True
        assert "empathy" in pkg.tone_instruction.lower() or "empathy" in " ".join(pkg.must_include).lower()

        validator = ResponseValidator()
        # A response missing empathy should fail emotional alignment
        bad_draft = "Please submit a ticket and we'll look into it."
        result = await validator.validate(
            draft=bad_draft,
            reasoning_package=pkg,
            perception=perception,
        )
        emotional = next(l for l in result.layers if l.layer_name == "emotional_alignment")
        # Should either fail or flag non-blocking issue
        assert not emotional.passed or "empathy" in emotional.reason.lower()


class TestClarificationPipeline:
    """Vague message → low confidence → should ask for clarification."""

    @pytest.mark.asyncio
    async def test_vague_message_triggers_clarification(self):
        perceiver = MessagePerceiver()

        perception = await perceiver.perceive(
            "nothing works",
            working_memory=make_working_memory(),
        )
        assert perception.confidence < 0.60

        reasoner = ResolutionReasoner()
        wm = make_working_memory(clarifications=0)
        pkg = await reasoner.reason(
            perception=perception,
            working_memory=wm,
            episodic_context=make_customer_context(),
            semantic_results=SemanticResults(similar_resolutions=[], cross_customer_patterns=[], confidence=0.0),
        )
        assert pkg.needs_clarification is True
        assert pkg.clarification_question is not None
        assert "?" in pkg.clarification_question


class TestMultiTurnPipeline:
    """Customer already asked one clarification → should attempt resolution now."""

    @pytest.mark.asyncio
    async def test_second_turn_attempts_resolution(self):
        perceiver = MessagePerceiver()

        wm = make_working_memory(
            turns=[
                {"role": "customer", "content": "my site is broken"},
                {"role": "agent", "content": "Can you tell me your domain?"},
                {"role": "customer", "content": "it's mysite.co.ke, getting 500 error"},
            ],
            clarifications=1,  # already asked once
        )

        perception = await perceiver.perceive(
            "it's mysite.co.ke, getting 500 error",
            working_memory=wm,
        )
        assert perception.confidence > 0.5  # more specific now
        assert "mysite.co.ke" in perception.domain_names

        reasoner = ResolutionReasoner()
        pkg = await reasoner.reason(
            perception=perception,
            working_memory=wm,
            episodic_context=make_customer_context(),
            semantic_results=SemanticResults(similar_resolutions=[], cross_customer_patterns=[], confidence=0.0),
        )
        # Even with low confidence, max clarifications already asked → attempt resolution
        assert pkg.needs_clarification is False or pkg.confidence >= 0.60


class TestValidatorRevisionLoop:
    """Validator fails → drafter revises → validator passes."""

    @pytest.mark.asyncio
    async def test_safety_failure_triggers_revision(self):
        validator = ResponseValidator()

        from agents.core.reasoner import FormatInstruction
        pkg = ReasoningPackage(
            recommended_resolution_path=[],
            confidence=0.8,
            needs_clarification=False,
            clarification_question=None,
            relevant_kb_chunks=[],
            similar_past_resolutions=[],
            customer_context_summary="Test",
            conversation_summary="Test",
            tone_instruction="Professional",
            format_instruction=FormatInstruction(max_words=300),
            must_include=[],
            must_avoid=[],
            escalation_recommended=False,
            escalation_reason=None,
        )
        perception = Perception(
            intent=Intent.TECHNICAL_ISSUE,
            product_area=ProductArea.CPANEL,
            issue_category="test",
            urgency=Urgency.LOW,
            emotional_state=EmotionalState.CALM,
            emotional_intensity=0.2,
            confidence=0.9,
        )

        # First attempt: dangerous content
        result1 = await validator.validate("Try running rm -rf / to clear the cache", pkg, perception)
        assert result1.passed is False
        assert result1.escalation_required is True

        # Second attempt: safe content
        result2 = await validator.validate(
            "You can clear your cache from cPanel → Disk Usage → Clear Cache",
            pkg, perception,
        )
        safety = next(l for l in result2.layers if l.layer_name == "safety")
        assert safety.passed is True
