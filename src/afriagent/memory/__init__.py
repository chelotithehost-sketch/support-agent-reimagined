"""Three-tier memory system.

Tier 1: Redis   — active session state (hot, TTL-based)
Tier 2: Postgres — episodic memory / conversation history (warm, queryable)
Tier 3: Qdrant  — cross-customer semantic patterns (cold, vector search)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as redis
from afriagent.config import settings
from afriagent.config.logging import get_logger
from afriagent.observability import MEMORY_OP, MEMORY_LATENCY

log = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════
# Tier 1 — Redis Session Store
# ══════════════════════════════════════════════════════════════════


class SessionStore:
    """Redis-backed active session state. Hot data with automatic TTL."""

    def __init__(self) -> None:
        self._pool: redis.ConnectionPool | None = None
        self._client: redis.Redis | None = None

    async def connect(self) -> None:
        self._pool = redis.ConnectionPool.from_url(
            settings.redis_url, max_connections=50, decode_responses=True
        )
        self._client = redis.Redis(connection_pool=self._pool)
        await self._client.ping()
        log.info("Redis connected", url=settings.redis_url)

    async def close(self) -> None:
        if self._pool:
            await self._pool.disconnect()

    @property
    def client(self) -> redis.Redis:
        if not self._client:
            raise RuntimeError("SessionStore not connected")
        return self._client

    async def get_session(self, conversation_id: str) -> dict[str, Any] | None:
        """Retrieve active session state."""
        with MEMORY_LATENCY.labels(tier="redis").time():
            raw = await self.client.get(f"session:{conversation_id}")
            MEMORY_OP.labels(tier="redis", operation="get").inc()

        if raw:
            return json.loads(raw)
        return None

    async def set_session(
        self, conversation_id: str, data: dict[str, Any], ttl: int | None = None
    ) -> None:
        """Store/update session state with TTL."""
        ttl = ttl or settings.redis_ttl_seconds
        with MEMORY_LATENCY.labels(tier="redis").time():
            await self.client.set(
                f"session:{conversation_id}",
                json.dumps(data, default=str),
                ex=ttl,
            )
            MEMORY_OP.labels(tier="redis", operation="set").inc()

    async def delete_session(self, conversation_id: str) -> None:
        await self.client.delete(f"session:{conversation_id}")
        MEMORY_OP.labels(tier="redis", operation="delete").inc()

    async def get_customer_state(self, customer_id: str) -> dict[str, Any] | None:
        """Get per-customer cached state (e.g., WHMCS data)."""
        raw = await self.client.get(f"customer:{customer_id}")
        if raw:
            return json.loads(raw)
        return None

    async def set_customer_state(
        self, customer_id: str, data: dict[str, Any], ttl: int = 3600
    ) -> None:
        await self.client.set(
            f"customer:{customer_id}", json.dumps(data, default=str), ex=ttl
        )

    async def acquire_lock(self, key: str, ttl: int = 30) -> bool:
        """Distributed lock for preventing duplicate processing."""
        return bool(await self.client.set(f"lock:{key}", "1", ex=ttl, nx=True))


# ══════════════════════════════════════════════════════════════════
# Tier 2 — Postgres Episodic Memory
# ══════════════════════════════════════════════════════════════════

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Float, DateTime, Text, Integer, JSON, select, func


class Base(DeclarativeBase):
    pass


class ConversationRecord(Base):
    """Persistent conversation storage."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="active")
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    satisfaction_score: Mapped[float | None] = mapped_column(Float, nullable=True)


class MessageRecord(Base):
    """Individual messages within conversations."""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(64), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    translated_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    channel: Mapped[str] = mapped_column(String(32))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LearningRecord(Base):
    """Validated interactions used for few-shot learning."""

    __tablename__ = "learning_examples"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_message: Mapped[str] = mapped_column(Text)
    agent_response: Mapped[str] = mapped_column(Text)
    intent: Mapped[str] = mapped_column(String(32))
    sentiment: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float)
    satisfaction_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EpisodicMemory:
    """Postgres-backed conversation and message persistence."""

    def __init__(self) -> None:
        self._engine = create_async_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_pool_overflow,
            echo=settings.debug,
        )
        self._session_factory = async_sessionmaker(
            self._engine, class_=AsyncSession, expire_on_commit=False
        )

    async def init_tables(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info("Postgres tables initialized")

    async def close(self) -> None:
        await self._engine.dispose()

    @property
    def session(self) -> AsyncSession:
        return self._session_factory()

    async def save_conversation(self, conv: dict[str, Any]) -> None:
        """Upsert a conversation record."""
        with MEMORY_LATENCY.labels(tier="postgres").time():
            async with self.session as s:
                record = ConversationRecord(**conv)
                await s.merge(record)
                await s.commit()
                MEMORY_OP.labels(tier="postgres", operation="save_conversation").inc()

    async def save_message(self, msg: dict[str, Any]) -> None:
        """Persist a message."""
        with MEMORY_LATENCY.labels(tier="postgres").time():
            async with self.session as s:
                record = MessageRecord(**msg)
                s.add(record)
                await s.commit()
                MEMORY_OP.labels(tier="postgres", operation="save_message").inc()

    async def get_conversation_history(
        self, conversation_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Retrieve message history for a conversation."""
        with MEMORY_LATENCY.labels(tier="postgres").time():
            async with self.session as s:
                result = await s.execute(
                    select(MessageRecord)
                    .where(MessageRecord.conversation_id == conversation_id)
                    .order_by(MessageRecord.created_at.desc())
                    .limit(limit)
                )
                rows = result.scalars().all()
                MEMORY_OP.labels(tier="postgres", operation="get_history").inc()
                return [
                    {
                        "id": r.id,
                        "role": r.role,
                        "content": r.content,
                        "channel": r.channel,
                        "created_at": r.created_at.isoformat(),
                    }
                    for r in reversed(rows)
                ]

    async def get_customer_conversations(
        self, customer_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Get recent conversations for a customer."""
        async with self.session as s:
            result = await s.execute(
                select(ConversationRecord)
                .where(ConversationRecord.customer_id == customer_id)
                .order_by(ConversationRecord.created_at.desc())
                .limit(limit)
            )
            return [
                {
                    "id": r.id,
                    "channel": r.channel,
                    "status": r.status,
                    "created_at": r.created_at.isoformat(),
                }
                for r in result.scalars().all()
            ]

    async def save_learning_example(self, example: dict[str, Any]) -> None:
        """Store a validated interaction for few-shot learning."""
        async with self.session as s:
            record = LearningRecord(**example)
            s.add(record)
            await s.commit()

    async def get_learning_examples(
        self, intent: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Retrieve high-confidence examples for an intent."""
        async with self.session as s:
            result = await s.execute(
                select(LearningRecord)
                .where(LearningRecord.intent == intent)
                .where(LearningRecord.confidence >= settings.min_confidence_for_learning)
                .order_by(LearningRecord.confidence.desc())
                .limit(limit)
            )
            return [
                {
                    "customer_message": r.customer_message,
                    "agent_response": r.agent_response,
                    "confidence": r.confidence,
                }
                for r in result.scalars().all()
            ]


# ══════════════════════════════════════════════════════════════════
# Tier 3 — Qdrant Semantic Memory
# ══════════════════════════════════════════════════════════════════

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct


class SemanticMemory:
    """Qdrant-backed cross-customer pattern matching."""

    def __init__(self) -> None:
        self._client: QdrantClient | None = None

    async def connect(self) -> None:
        self._client = QdrantClient(url=settings.qdrant_url)
        # Ensure collection exists
        collections = self._client.get_collections().collections
        if not any(c.name == settings.qdrant_collection for c in collections):
            self._client.create_collection(
                collection_name=settings.qdrant_collection,
                vectors_config=VectorParams(
                    size=settings.qdrant_vector_size, distance=Distance.COSINE
                ),
            )
            log.info("Created Qdrant collection", collection=settings.qdrant_collection)
        log.info("Qdrant connected", url=settings.qdrant_url)

    async def close(self) -> None:
        if self._client:
            self._client.close()

    @property
    def client(self) -> QdrantClient:
        if not self._client:
            raise RuntimeError("SemanticMemory not connected")
        return self._client

    async def store_pattern(
        self,
        pattern_id: str,
        vector: list[float],
        payload: dict[str, Any],
    ) -> None:
        """Store a resolution pattern with its embedding."""
        with MEMORY_LATENCY.labels(tier="qdrant").time():
            self.client.upsert(
                collection_name=settings.qdrant_collection,
                points=[
                    PointStruct(id=pattern_id, vector=vector, payload=payload)
                ],
            )
            MEMORY_OP.labels(tier="qdrant", operation="store").inc()

    async def search_similar(
        self,
        vector: list[float],
        limit: int = 5,
        score_threshold: float = 0.7,
    ) -> list[dict[str, Any]]:
        """Find similar resolution patterns by semantic similarity."""
        with MEMORY_LATENCY.labels(tier="qdrant").time():
            results = self.client.query_points(
                collection_name=settings.qdrant_collection,
                query=vector,
                limit=limit,
                score_threshold=score_threshold,
            )
            MEMORY_OP.labels(tier="qdrant", operation="search").inc()
            return [
                {
                    "id": r.id,
                    "score": r.score,
                    **(r.payload or {}),
                }
                for r in results.points
            ]


# ── Unified Memory Manager ───────────────────────────────────────


class MemoryManager:
    """Unified interface over all three memory tiers."""

    def __init__(self) -> None:
        self.session = SessionStore()
        self.episodic = EpisodicMemory()
        self.semantic = SemanticMemory()

    async def connect_all(self) -> None:
        await self.session.connect()
        await self.episodic.init_tables()
        await self.semantic.connect()
        log.info("All memory tiers connected")

    async def close_all(self) -> None:
        await self.session.close()
        await self.episodic.close()
        await self.semantic.close()
        log.info("All memory tiers disconnected")
