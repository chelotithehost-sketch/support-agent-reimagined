# Shared test fixtures for the-support-agent-speaks
# ─────────────────────────────────────────────────
# Copy into: tests/conftest.py (merge with existing fixtures)
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from agents.core.perceiver import Perception
from agents.core.reasoner import ReasoningPackage, FormatInstruction
from agents.core.validator import ResponseValidator
from agents.memory.schemas import (
    CustomerContext,
    CustomerTier,
    EmotionalState,
    Intent,
    KnowledgeChunk,
    ProductArea,
    ResolutionStep,
    ResolvedCase,
    SemanticResults,
    Urgency,
    WorkingMemory,
)


# ── Event loop ──────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ── Identity fixtures ───────────────────────────

@pytest.fixture
def sample_customer_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def sample_conversation_id() -> str:
    return str(uuid.uuid4())


# ── Memory fixtures ─────────────────────────────

@pytest.fixture
def empty_working_memory() -> WorkingMemory:
    """Fresh working memory with no conversation history."""
    return WorkingMemory(
        conversation_id=str(uuid.uuid4()),
        turns=[],
        clarifications_asked=0,
        customer_intent_history=[],
        session_started=datetime.utcnow(),
    )


@pytest.fixture
def multi_turn_working_memory() -> WorkingMemory:
    """Working memory with 3 prior turns."""
    now = datetime.utcnow()
    return WorkingMemory(
        conversation_id=str(uuid.uuid4()),
        turns=[
            {"role": "customer", "content": "My email is not working", "timestamp": (now - timedelta(minutes=5)).isoformat()},
            {"role": "agent", "content": "Can you tell me your domain name?", "timestamp": (now - timedelta(minutes=4)).isoformat()},
            {"role": "customer", "content": "it's example.co.ke", "timestamp": (now - timedelta(minutes=3)).isoformat()},
        ],
        clarifications_asked=1,
        customer_intent_history=["EMAIL_SETUP"],
        session_started=now - timedelta(minutes=5),
    )


@pytest.fixture
def basic_episodic_context() -> CustomerContext:
    """Standard customer with no red flags."""
    return CustomerContext(
        customer_id=str(uuid.uuid4()),
        tier=CustomerTier.STANDARD,
        total_tickets=3,
        open_tickets=1,
        lifetime_value_usd=150.0,
        days_as_customer=120,
        recent_csat_scores=[4, 5, 4],
        churn_risk_score=0.1,
        plan_name="Starter Hosting",
        plan_expiry=datetime.utcnow() + timedelta(days=180),
    )


@pytest.fixture
def high_value_customer_context() -> CustomerContext:
    """Long-standing customer with high LTV — should get priority routing."""
    return CustomerContext(
        customer_id=str(uuid.uuid4()),
        tier=CustomerTier.ENTERPRISE,
        total_tickets=25,
        open_tickets=0,
        lifetime_value_usd=5000.0,
        days_as_customer=730,
        recent_csat_scores=[5, 5, 4, 5],
        churn_risk_score=0.05,
        plan_name="Dedicated Server Pro",
        plan_expiry=datetime.utcnow() + timedelta(days=365),
    )


@pytest.fixture
def at_risk_customer_context() -> CustomerContext:
    """Customer showing churn signals — needs empathetic handling."""
    return CustomerContext(
        customer_id=str(uuid.uuid4()),
        tier=CustomerTier.STANDARD,
        total_tickets=8,
        open_tickets=3,
        lifetime_value_usd=200.0,
        days_as_customer=90,
        recent_csat_scores=[2, 1, 2],
        churn_risk_score=0.85,
        plan_name="Business Hosting",
        plan_expiry=datetime.utcnow() + timedelta(days=5),
    )


@pytest.fixture
def empty_semantic_results() -> SemanticResults:
    """No similar past resolutions found."""
    return SemanticResults(
        similar_resolutions=[],
        cross_customer_patterns=[],
        confidence=0.0,
    )


@pytest.fixture
def email_semantic_results() -> SemanticResults:
    """Semantic results with relevant email troubleshooting cases."""
    return SemanticResults(
        similar_resolutions=[
            ResolvedCase(
                conversation_summary="Customer couldn't send email — MX record was pointing to old provider",
                resolution_steps=["Check MX records", "Update nameservers", "Wait for propagation", "Test sending"],
                csat_score=5,
                intent=Intent.EMAIL_SETUP,
                product_area=ProductArea.EMAIL,
                resolution_time_turns=4,
            ),
            ResolvedCase(
                conversation_summary="Email bouncing — SPF record missing",
                resolution_steps=["Add SPF TXT record", "Wait 30 min", "Test delivery"],
                csat_score=4,
                intent=Intent.EMAIL_SETUP,
                product_area=ProductArea.EMAIL,
                resolution_time_turns=3,
            ),
        ],
        cross_customer_patterns=[
            "Email issues on .co.ke domains often relate to nameserver propagation delays (up to 48h)",
            "M-Pesa billing customers frequently confuse payment confirmation with service activation",
        ],
        confidence=0.75,
    )


# ── Perception fixtures ────────────────────────

@pytest.fixture
def calm_email_perception() -> Perception:
    return Perception(
        intent=Intent.EMAIL_SETUP,
        product_area=ProductArea.EMAIL,
        issue_category="email_not_sending",
        urgency=Urgency.MEDIUM,
        emotional_state=EmotionalState.CALM,
        emotional_intensity=0.3,
        domain_names=["example.co.ke"],
        confidence=0.85,
    )


@pytest.fixture
def angry_billing_perception() -> Perception:
    return Perception(
        intent=Intent.BILLING_DISPUTE,
        product_area=ProductArea.WHMCS,
        issue_category="double_charge",
        urgency=Urgency.HIGH,
        emotional_state=EmotionalState.ANGRY,
        emotional_intensity=0.95,
        frustration_indicators=["furious", "scam", "unacceptable"],
        empathy_required=True,
        contains_threat=True,
        confidence=0.95,
    )


@pytest.fixture
def confused_ssl_perception() -> Perception:
    return Perception(
        intent=Intent.SSL_ISSUE,
        product_area=ProductArea.SSL,
        issue_category="ssl_not_working",
        urgency=Urgency.MEDIUM,
        emotional_state=EmotionalState.CONFUSED,
        emotional_intensity=0.5,
        domain_names=["shop.co.ke"],
        confidence=0.70,
    )


@pytest.fixture
def anxious_downtime_perception() -> Perception:
    return Perception(
        intent=Intent.TECHNICAL_ISSUE,
        product_area=ProductArea.CPANEL,
        issue_category="site_down",
        urgency=Urgency.CRITICAL,
        emotional_state=EmotionalState.ANXIOUS,
        emotional_intensity=0.8,
        is_followup=False,
        confidence=0.90,
    )


# ── Reasoning package fixtures ─────────────────

@pytest.fixture
def basic_reasoning_package() -> ReasoningPackage:
    return ReasoningPackage(
        recommended_resolution_path=[
            ResolutionStep(step_number=1, action="Check MX records", expected_outcome="Verify correct MX entries"),
            ResolutionStep(step_number=2, action="Update nameservers if needed", expected_outcome="Point to correct provider"),
        ],
        confidence=0.8,
        needs_clarification=False,
        clarification_question=None,
        relevant_kb_chunks=[
            KnowledgeChunk(
                heading_path="Email > MX Records > Setup",
                content="MX records direct email to the correct mail server. Common hosting MX values are mx1.hostprovider.co.ke and mx2.hostprovider.co.ke.",
                source_url="https://help.hostprovider.co.ke/email/mx-records",
                relevance_score=0.92,
            ),
        ],
        similar_past_resolutions=[],
        customer_context_summary="Standard customer, 120 days, no prior email issues",
        conversation_summary="Customer reports email not sending from example.co.ke",
        tone_instruction="Professional, efficient, friendly. Match technical depth to the question.",
        format_instruction=FormatInstruction(max_words=300, use_numbered_steps=True),
        must_include=["MX record check step", "estimated propagation time"],
        must_avoid=["I cannot help you", "that's not our problem"],
        escalation_recommended=False,
        escalation_reason=None,
    )


@pytest.fixture
def angry_reasoning_package() -> ReasoningPackage:
    """Package for angry customer — empathy-first, escalation-ready."""
    return ReasoningPackage(
        recommended_resolution_path=[
            ResolutionStep(step_number=1, action="Acknowledge double charge", expected_outcome="Customer feels heard"),
            ResolutionStep(step_number=2, action="Initiate refund for duplicate", expected_outcome="Refund processed within 48h"),
        ],
        confidence=0.9,
        needs_clarification=False,
        clarification_question=None,
        relevant_kb_chunks=[],
        similar_past_resolutions=[],
        customer_context_summary="At-risk customer, recent CSAT 2/5, plan expiring in 5 days",
        conversation_summary="Customer charged twice for same invoice, very angry",
        tone_instruction="Empathy statement first. Own the problem regardless of fault. Concrete next action with time commitment. Offer escalation proactively.",
        format_instruction=FormatInstruction(max_words=200),
        must_include=["empathy statement in first sentence", "refund timeline", "escalation offer"],
        must_avoid=["company policy", "I can't help", "that's not our fault"],
        escalation_recommended=False,
        escalation_reason=None,
    )


@pytest.fixture
def low_confidence_reasoning_package() -> ReasoningPackage:
    """Package where confidence is low — should trigger clarification."""
    return ReasoningPackage(
        recommended_resolution_path=[],
        confidence=0.45,
        needs_clarification=True,
        clarification_question="I want to make sure I help with the right issue — are you having trouble logging into cPanel, or is your website not loading?",
        relevant_kb_chunks=[],
        similar_past_resolutions=[],
        customer_context_summary="New customer, first interaction",
        conversation_summary="Customer says 'nothing works' — no specific details",
        tone_instruction="Warm, patient. Use simple language.",
        format_instruction=FormatInstruction(max_words=150),
        must_include=["clarifying question"],
        must_avoid=["assumptions about the specific issue"],
        escalation_recommended=False,
        escalation_reason=None,
    )


# ── Validator fixture ───────────────────────────

@pytest.fixture
def validator() -> ResponseValidator:
    return ResponseValidator()


# ── Mock LLM client ─────────────────────────────

@pytest.fixture
def mock_llm_client() -> AsyncMock:
    """Mock LLM client that returns a canned response."""
    client = AsyncMock()
    client.chat = AsyncMock(return_value=MagicMock(
        content="I understand you're having trouble with your email. Let me help you check your MX records. Could you tell me your domain name?",
        usage=MagicMock(prompt_tokens=250, completion_tokens=50, total_tokens=300),
    ))
    return client


# ── DB session (requires running infra) ─────────

@pytest_asyncio.fixture
async def db_session():
    """Provide a test database session (rolls back after each test)."""
    from db.session import async_session_factory

    async with async_session_factory() as session:
        yield session
        await session.rollback()
