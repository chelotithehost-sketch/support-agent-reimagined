"""Perceiver — Multi-channel intake with enrichment.

Responsibilities:
1. Receive raw messages from channel adapters
2. Detect language and translate if needed
3. Classify intent, sentiment, urgency
4. Load customer context from WHMCS
5. Retrieve conversation history (Redis + Postgres)
6. Find similar resolution patterns (Qdrant)
7. Assemble full ConversationContext for the Brain
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from afriagent.config import settings
from afriagent.config.logging import get_logger
from afriagent.models import (
    Channel,
    ConversationContext,
    CustomerProfile,
    InboundMessage,
    Intent,
    Message,
    MessageRole,
    Sentiment,
    Urgency,
)
from afriagent.memory import MemoryManager
from afriagent.brain.llm import BaseLLMProvider
from afriagent.observability import REQUEST_COUNT, get_tracer
from afriagent.perceiver.language import detect_language as _detect_language_advanced

log = get_logger(__name__)
tracer = get_tracer(__name__)

# ── Language Detection ────────────────────────────────────────────
# Now uses the dedicated language.py module with Sheng support.

def detect_language(text: str) -> str:
    """Detect language using the advanced detector with Sheng awareness."""
    return _detect_language_advanced(text)


# ── Intent Classification ────────────────────────────────────────

INTENT_KEYWORDS: dict[Intent, list[str]] = {
    Intent.BILLING: [
        "bill", "invoice", "payment", "charge", "subscription", "renew",
        "mpesa", "pay", "cost", "price", "fee", "amount", "malipo",
        "deni", "lipa",
    ],
    Intent.TECHNICAL: [
        "down", "error", "not working", "slow", "issue", "bug", "crash",
        "timeout", "connection", "server", "hosting", "domain", "ssl",
        "email", "dns", "tatizo", "shida",
    ],
    Intent.SALES: [
        "buy", "upgrade", "plan", "feature", "demo", "trial", "new",
        "subscribe", "package", "offer", "discount", "nunua",
    ],
    Intent.ESCALATION: [
        "manager", "supervisor", "human", "person", "complaint",
        "unacceptable", "terrible", "worst", "angry", "furious",
    ],
    Intent.GREETING: [
        "hello", "hi", "hey", "good morning", "good afternoon",
        "habari", "jambo", "mambo", "salut", "bonjour",
    ],
}


def classify_intent(text: str) -> Intent:
    """Rule-based intent classification with keyword matching."""
    lower = text.lower()
    scores: dict[Intent, int] = {}

    for intent, keywords in INTENT_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in lower)
        if score > 0:
            scores[intent] = score

    if not scores:
        return Intent.GENERAL

    return max(scores, key=scores.get)  # type: ignore[arg-type]


# ── Sentiment Detection ──────────────────────────────────────────

NEGATIVE_MARKERS = [
    "angry", "frustrated", "terrible", "worst", "horrible", "awful",
    "unacceptable", "disappointed", "annoyed", "furious", "hate",
    "stupid", "useless", "scam", "fraud", "rip off", "mbaya",
]
POSITIVE_MARKERS = [
    "great", "excellent", "amazing", "love", "perfect", "wonderful",
    "fantastic", "helpful", "thanks", "thank you", "nzuri", "asante",
    "sawa",
]
FRUSTRATED_MARKERS = [
    "again", "already", "still", "how many times", "keep telling",
    "nobody", "bado", "tena", "bila mafanikio",
]


def detect_sentiment(text: str) -> Sentiment:
    """Simple sentiment detection."""
    lower = text.lower()

    neg = sum(1 for m in NEGATIVE_MARKERS if m in lower)
    pos = sum(1 for m in POSITIVE_MARKERS if m in lower)
    frust = sum(1 for m in FRUSTRATED_MARKERS if m in lower)

    if frust >= 2 or neg >= 3:
        return Sentiment.FRUSTRATED
    if neg > pos:
        return Sentiment.NEGATIVE
    if pos > neg:
        return Sentiment.POSITIVE
    return Sentiment.NEUTRAL


# ── Urgency Detection ────────────────────────────────────────────

URGENCY_HIGH = [
    "urgent", "emergency", "asap", "immediately", "critical",
    "production down", "server down", "can't access", "locked out",
    "haraka", "dharura",
]
URGENCY_CRITICAL = [
    "data loss", "security breach", "hacked", "compromised",
    "money lost", "unauthorized", "ransomware",
]


def detect_urgency(text: str, sentiment: Sentiment) -> Urgency:
    """Determine message urgency."""
    lower = text.lower()

    if any(m in lower for m in URGENCY_CRITICAL):
        return Urgency.CRITICAL
    if any(m in lower for m in URGENCY_HIGH):
        return Urgency.HIGH
    if sentiment == Sentiment.FRUSTRATED:
        return Urgency.HIGH
    if sentiment == Sentiment.NEGATIVE:
        return Urgency.MEDIUM
    return Urgency.LOW


# ── Perceiver Pipeline ───────────────────────────────────────────


class Perceiver:
    """Multi-channel intake with full enrichment pipeline."""

    def __init__(
        self,
        memory: MemoryManager,
        llm: BaseLLMProvider,
    ) -> None:
        self.memory = memory
        self.llm = llm

    async def process(self, inbound: InboundMessage) -> ConversationContext:
        """Full Perceiver pipeline: raw message → enriched context."""
        with tracer.start_as_current_span("perceiver.process") as span:
            span.set_attribute("channel", inbound.channel.value)

            # 1. Dedup check
            conversation_id = inbound.metadata.get(
                "conversation_id", f"{inbound.channel.value}:{inbound.sender_id}"
            )
            lock_key = f"dedup:{inbound.channel.value}:{inbound.sender_id}:{hash(inbound.content)}"
            if not await self.memory.session.acquire_lock(lock_key, ttl=10):
                log.warning("Duplicate message detected", sender=inbound.sender_id)
                raise ValueError("Duplicate message")

            # 2. Language detection
            language = detect_language(inbound.content)
            translated = None
            if language != "en":
                translated = await self._translate(inbound.content, language, "en")

            # 3. NLU enrichment
            intent = classify_intent(translated or inbound.content)
            sentiment = detect_sentiment(inbound.content)
            urgency = detect_urgency(inbound.content, sentiment)

            REQUEST_COUNT.labels(channel=inbound.channel.value, intent=intent.value).inc()

            # 4. Load customer profile
            customer = await self._load_customer(inbound.sender_id, inbound.channel)

            # 5. Load conversation history
            history = await self._load_history(conversation_id, customer.id)

            # 6. Vector search for similar patterns
            similar = await self._find_similar(inbound.content, intent)

            # 7. Build message object
            message = Message(
                conversation_id=conversation_id,
                channel=inbound.channel,
                role=MessageRole.CUSTOMER,
                content=inbound.content,
                language=language,
                translated_content=translated,
            )

            # 8. Persist message
            await self.memory.episodic.save_message({
                "id": message.id,
                "conversation_id": conversation_id,
                "role": "customer",
                "content": inbound.content,
                "translated_content": translated,
                "language": language,
                "channel": inbound.channel.value,
                "created_at": message.timestamp,
            })

            # 9. Update session state
            await self.memory.session.set_session(conversation_id, {
                "customer_id": customer.id,
                "channel": inbound.channel.value,
                "last_message": inbound.content,
                "intent": intent.value,
                "sentiment": sentiment.value,
                "urgency": urgency.value,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })

            context = ConversationContext(
                conversation_id=conversation_id,
                customer=customer,
                current_message=message,
                message_history=history,
                detected_intent=intent,
                detected_sentiment=sentiment,
                detected_urgency=urgency,
                detected_language=language,
                similar_patterns=similar,
            )

            log.info(
                "Perceiver complete",
                conversation_id=conversation_id,
                intent=intent.value,
                sentiment=sentiment.value,
                urgency=urgency.value,
                language=language,
            )

            return context

    async def _load_customer(
        self, sender_id: str, channel: Channel
    ) -> CustomerProfile:
        """Load or create customer profile."""
        # Check cache
        cached = await self.memory.session.get_customer_state(sender_id)
        if cached:
            return CustomerProfile(**cached)

        # TODO: Load from WHMCS API if configured
        profile = CustomerProfile(
            id=sender_id,
            phone=sender_id if channel == Channel.WHATSAPP else "",
        )

        # Cache for 1 hour
        await self.memory.session.set_customer_state(
            sender_id, profile.model_dump(), ttl=3600
        )
        return profile

    async def _load_history(
        self, conversation_id: str, customer_id: str
    ) -> list[Message]:
        """Load message history from Redis (hot) or Postgres (warm)."""
        # Try Redis first
        session = await self.memory.session.get_session(conversation_id)
        if session:
            log.debug("Session found in Redis", conversation_id=conversation_id)

        # Load from Postgres
        records = await self.memory.episodic.get_conversation_history(
            conversation_id, limit=20
        )
        return [
            Message(
                conversation_id=conversation_id,
                channel=Channel(r.get("channel", "webchat")),
                role=MessageRole(r["role"]),
                content=r["content"],
            )
            for r in records
        ]

    async def _find_similar(
        self, text: str, intent: Intent
    ) -> list[dict[str, Any]]:
        """Find similar resolution patterns via vector search."""
        try:
            vector = await self.llm.embed(text)
            return await self.memory.semantic.search_similar(
                vector, limit=3, score_threshold=0.75
            )
        except Exception as e:
            log.warning("Vector search failed", error=str(e))
            return []

    async def _translate(
        self, text: str, source_lang: str, target_lang: str
    ) -> str:
        """Translate text using LLM. Lightweight fallback for non-critical paths."""
        try:
            resp = await self.llm.generate([
                {
                    "role": "system",
                    "content": f"Translate the following from {source_lang} to {target_lang}. "
                    "Return ONLY the translation, nothing else.",
                },
                {"role": "user", "content": text},
            ])
            return resp.content
        except Exception:
            return text  # Graceful fallback
