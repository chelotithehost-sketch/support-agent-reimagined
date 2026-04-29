"""Pydantic models — the shared vocabulary across all layers."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────


class Channel(str, Enum):
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    WEBCHAT = "webchat"


class MessageRole(str, Enum):
    CUSTOMER = "customer"
    AGENT = "agent"
    SYSTEM = "system"


class ConversationStatus(str, Enum):
    ACTIVE = "active"
    WAITING = "waiting"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class Intent(str, Enum):
    BILLING = "billing"
    TECHNICAL = "technical"
    SALES = "sales"
    GENERAL = "general"
    ESCALATION = "escalation"
    GREETING = "greeting"
    COMPLAINT = "complaint"


class Sentiment(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    FRUSTRATED = "frustrated"


class Urgency(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ── Core Message ──────────────────────────────────────────────────


class Message(BaseModel):
    """A single message in a conversation."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    conversation_id: str
    channel: Channel
    role: MessageRole
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Enrichment fields (populated by Perceiver)
    language: str | None = None
    translated_content: str | None = None


class InboundMessage(BaseModel):
    """Raw message from a channel adapter before Perceiver processing."""

    channel: Channel
    sender_id: str  # phone number or telegram user id
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    media_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Customer Context ──────────────────────────────────────────────


class CustomerProfile(BaseModel):
    """Persistent customer data from WHMCS + conversation history."""

    id: str
    name: str = ""
    email: str = ""
    phone: str = ""
    whmcs_client_id: int | None = None
    active_services: list[dict[str, Any]] = Field(default_factory=list)
    open_tickets: list[dict[str, Any]] = Field(default_factory=list)
    lifetime_value: float = 0.0
    preferred_language: str = "en"
    tags: list[str] = Field(default_factory=list)
    last_interaction: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ConversationContext(BaseModel):
    """Full context assembled by Perceiver for the Brain."""

    conversation_id: str
    customer: CustomerProfile
    current_message: Message
    message_history: list[Message] = Field(default_factory=list)
    detected_intent: Intent = Intent.GENERAL
    detected_sentiment: Sentiment = Sentiment.NEUTRAL
    detected_urgency: Urgency = Urgency.MEDIUM
    detected_language: str = "en"
    business_context: dict[str, Any] = Field(default_factory=dict)
    similar_patterns: list[dict[str, Any]] = Field(default_factory=list)


# ── Response ──────────────────────────────────────────────────────


class ResponseCandidate(BaseModel):
    """A candidate response before validation."""

    content: str
    confidence: float = 0.0
    model_used: str = ""
    tokens_used: int = 0
    reasoning: str = ""


class ValidationLayer(BaseModel):
    """Result from a single validation layer."""

    layer_name: str
    passed: bool
    score: float = 0.0
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    processing_time_ms: float = 0.0


class ValidationResult(BaseModel):
    """Aggregated result from all 9 validation layers."""

    passed: bool
    final_score: float
    layers: list[ValidationLayer] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    processing_time_ms: float = 0.0


class AgentResponse(BaseModel):
    """Final validated response ready for delivery."""

    conversation_id: str
    content: str
    channel: Channel
    confidence: float
    validation: ValidationResult
    intent_handled: Intent
    escalated: bool = False
    escalate_reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Conversation ──────────────────────────────────────────────────


class Conversation(BaseModel):
    """A full conversation thread."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    customer_id: str
    channel: Channel
    status: ConversationStatus = ConversationStatus.ACTIVE
    messages: list[Message] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None
    satisfaction_score: float | None = None


# ── Learning ──────────────────────────────────────────────────────


class LearningExample(BaseModel):
    """A validated interaction used for few-shot learning."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    conversation_id: str
    customer_message: str
    agent_response: str
    intent: Intent
    sentiment: Sentiment
    confidence: float
    satisfaction_score: float | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
