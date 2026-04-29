"""FastAPI application — API routes, health checks, and webchat."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from afriagent.config import settings
from afriagent.config.logging import get_logger
from afriagent.models import Channel, InboundMessage

log = get_logger(__name__)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="AfriAgent",
        description="AI-powered customer support agent for African businesses",
        version="0.1.0",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routes
    app.include_router(_health_router())
    app.include_router(_chat_router())
    app.include_router(_admin_router())

    # Register webhook routes
    from afriagent.adapters import router as webhook_router
    app.include_router(webhook_router)

    return app


# ══════════════════════════════════════════════════════════════════
# Health Routes
# ══════════════════════════════════════════════════════════════════


def _health_router() -> Any:
    from fastapi import APIRouter

    router = APIRouter(tags=["health"])

    @router.get("/health")
    async def health() -> dict[str, Any]:
        """Basic health check."""
        return {
            "status": "healthy",
            "service": "afriagent",
            "version": "0.1.0",
            "env": settings.env.value,
        }

    @router.get("/health/detailed")
    async def detailed_health() -> dict[str, Any]:
        """Detailed health check with component status."""
        from afriagent.main import get_agent

        agent = get_agent()
        checks: dict[str, str] = {}

        # Redis
        try:
            await agent.memory.session.client.ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "error"

        # Postgres
        try:
            async with agent.memory.episodic.session as s:
                await s.execute("SELECT 1")
            checks["postgres"] = "ok"
        except Exception:
            checks["postgres"] = "error"

        # Qdrant
        try:
            agent.memory.semantic.client.get_collections()
            checks["qdrant"] = "ok"
        except Exception:
            checks["qdrant"] = "error"

        all_ok = all(v == "ok" for v in checks.values())
        return {
            "status": "healthy" if all_ok else "degraded",
            "checks": checks,
        }

    return router


# ══════════════════════════════════════════════════════════════════
# Chat Routes (Webchat + API)
# ══════════════════════════════════════════════════════════════════


class ChatRequest(BaseModel):
    message: str
    customer_id: str = "web-visitor"
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    response: str
    conversation_id: str
    confidence: float
    escalated: bool = False


def _chat_router() -> Any:
    from fastapi import APIRouter

    router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

    @router.post("/", response_model=ChatResponse)
    async def chat(req: ChatRequest) -> ChatResponse:
        """Send a message and get a response (webchat / API)."""
        from afriagent.main import get_agent

        agent = get_agent()

        inbound = InboundMessage(
            channel=Channel.WEBCHAT,
            sender_id=req.customer_id,
            content=req.message,
            metadata={"conversation_id": req.conversation_id} if req.conversation_id else {},
        )

        try:
            response = await agent.handle_message(inbound)
            return ChatResponse(
                response=response.content,
                conversation_id=response.conversation_id,
                confidence=response.confidence,
                escalated=response.escalated,
            )
        except Exception as e:
            log.error("Chat error", error=str(e))
            raise HTTPException(status_code=500, detail="Internal error")

    @router.get("/history/{conversation_id}")
    async def history(conversation_id: str) -> dict[str, Any]:
        """Get conversation history."""
        from afriagent.main import get_agent

        agent = get_agent()
        messages = await agent.memory.episodic.get_conversation_history(conversation_id)
        return {"conversation_id": conversation_id, "messages": messages}

    return router


# ══════════════════════════════════════════════════════════════════
# Admin Routes
# ══════════════════════════════════════════════════════════════════


def _admin_router() -> Any:
    from fastapi import APIRouter

    router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

    @router.get("/stats")
    async def stats() -> dict[str, Any]:
        """Get agent statistics."""
        from afriagent.main import get_agent

        agent = get_agent()
        learning_stats = await agent.learning.get_stats()

        return {
            "learning": learning_stats,
            "active_conversations": len(
                [s for s in [] if s]  # Placeholder
            ),
        }

    @router.post("/conversations/{conversation_id}/escalate")
    async def escalate(conversation_id: str, reason: str = "manual") -> dict[str, str]:
        """Manually escalate a conversation."""
        from afriagent.main import get_agent

        agent = get_agent()
        session = await agent.memory.session.get_session(conversation_id)
        if session:
            session["escalated"] = True
            session["escalate_reason"] = reason
            await agent.memory.session.set_session(conversation_id, session)

        return {"status": "escalated", "conversation_id": conversation_id}

    return router
