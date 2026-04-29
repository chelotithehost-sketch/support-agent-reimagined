"""Main entry point — Agent orchestrator, app lifecycle, CLI."""

from __future__ import annotations

import asyncio
import signal
import sys
from typing import Any

import uvicorn

from afriagent.config import settings
from afriagent.config.logging import setup_logging, get_logger
from afriagent.models import AgentResponse, InboundMessage
from afriagent.memory import MemoryManager
from afriagent.brain import Brain
from afriagent.brain.llm import create_llm_provider
from afriagent.perceiver import Perceiver
from afriagent.transmitter import Transmitter, WhatsAppAdapter, TelegramAdapter, WebchatAdapter
from afriagent.tools import ToolRegistry
from afriagent.learning import LearningEngine
from afriagent.observability import setup_telemetry, ACTIVE_CONVERSATIONS
from afriagent.models import Channel

log = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════
# Agent Orchestrator
# ══════════════════════════════════════════════════════════════════


class AfriAgent:
    """The main agent that wires everything together.

    Pipeline: InboundMessage → Perceiver → Brain → Transmitter → Customer
    """

    def __init__(self) -> None:
        # Core components
        self.memory = MemoryManager()
        self.llm = create_llm_provider()
        self.perceiver = Perceiver(self.memory, self.llm)
        self.brain = Brain(self.llm, self.memory)
        self.transmitter = Transmitter()
        self.tools = ToolRegistry()
        self.learning = LearningEngine(self.memory)

    async def start(self) -> None:
        """Initialize all components."""
        log.info("Starting AfriAgent...")

        # Connect memory tiers
        await self.memory.connect_all()

        # Register channel adapters
        if settings.twilio_account_sid:
            self.transmitter.register_adapter(Channel.WHATSAPP, WhatsAppAdapter())
            log.info("WhatsApp adapter registered")

        if settings.telegram_bot_token:
            self.transmitter.register_adapter(Channel.TELEGRAM, TelegramAdapter())
            log.info("Telegram adapter registered")

        self.transmitter.register_adapter(Channel.WEBCHAT, WebchatAdapter())

        log.info("AfriAgent started successfully")

    async def stop(self) -> None:
        """Graceful shutdown."""
        log.info("Stopping AfriAgent...")
        await self.transmitter.close_all()
        await self.tools.close_all()
        await self.memory.close_all()
        log.info("AfriAgent stopped")

    async def handle_message(self, inbound: InboundMessage) -> AgentResponse:
        """Full message handling pipeline.

        1. Perceiver: enrich and classify the inbound message
        2. Brain: generate and validate a response
        3. Transmitter: deliver the response through the channel
        4. Learning: capture the interaction for improvement
        """
        ACTIVE_CONVERSATIONS.labels(channel=inbound.channel.value).inc()

        try:
            # 1. Perceive — enrich the message
            context = await self.perceiver.process(inbound)

            # 2. Think — generate and validate response
            response = await self.brain.generate_response(context)

            # 3. Transmit — deliver through channel
            recipient = inbound.sender_id
            await self.transmitter.deliver(response, recipient)

            # 4. Learn — capture for few-shot improvement
            await self.learning.capture_interaction(context, response)

            return response

        except ValueError as e:
            # Dedup or other expected errors
            log.warning("Message handling skipped", reason=str(e))
            raise
        except Exception as e:
            log.error("Message handling failed", error=str(e), exc_info=True)
            # Try to send an error message to the customer
            try:
                error_response = AgentResponse(
                    conversation_id=f"{inbound.channel.value}:{inbound.sender_id}",
                    content=(
                        "I'm sorry, I encountered a technical issue. "
                        "A human agent will assist you shortly."
                    ),
                    channel=inbound.channel,
                    confidence=0.0,
                    validation=None,  # type: ignore[arg-type]
                    intent_handled=None,  # type: ignore[arg-type]
                    escalated=True,
                    escalate_reason=f"System error: {str(e)[:100]}",
                )
                await self.transmitter.deliver(error_response, inbound.sender_id)
            except Exception:
                pass
            raise
        finally:
            ACTIVE_CONVERSATIONS.labels(channel=inbound.channel.value).dec()


# ══════════════════════════════════════════════════════════════════
# Global Agent Singleton
# ══════════════════════════════════════════════════════════════════

_agent: AfriAgent | None = None


def get_agent() -> AfriAgent:
    """Get the global agent instance."""
    global _agent
    if _agent is None:
        _agent = AfriAgent()
    return _agent


# ══════════════════════════════════════════════════════════════════
# App Lifecycle
# ══════════════════════════════════════════════════════════════════


def create_app_with_lifecycle() -> Any:
    """Create FastAPI app with startup/shutdown hooks."""
    from afriagent.api import create_app

    app = create_app()

    @app.on_event("startup")
    async def startup() -> None:
        setup_logging()
        setup_telemetry()
        agent = get_agent()
        await agent.start()
        log.info("AfriAgent API server started", port=settings.port)

    @app.on_event("shutdown")
    async def shutdown() -> None:
        agent = get_agent()
        await agent.stop()

    return app


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════


def cli() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="AfriAgent - AI Customer Support")
    sub = parser.add_subparsers(dest="command", required=True)

    # serve
    serve_cmd = sub.add_parser("serve", help="Start the API server")
    serve_cmd.add_argument("--host", default=settings.host)
    serve_cmd.add_argument("--port", type=int, default=settings.port)
    serve_cmd.add_argument("--workers", type=int, default=settings.workers)
    serve_cmd.add_argument("--reload", action="store_true", help="Enable auto-reload")

    # migrate
    sub.add_parser("migrate", help="Run database migrations")

    # eval
    eval_cmd = sub.add_parser("eval", help="Run evaluation suite")
    eval_cmd.add_argument("--suite", default="default")

    args = parser.parse_args()

    if args.command == "serve":
        setup_logging()
        uvicorn.run(
            "afriagent.main:create_app_with_lifecycle",
            host=args.host,
            port=args.port,
            workers=args.workers if not args.reload else 1,
            reload=args.reload,
            factory=True,
        )
    elif args.command == "migrate":
        import subprocess
        subprocess.run(["alembic", "upgrade", "head"], check=True)
    elif args.command == "eval":
        log.info("Running evaluation suite", suite=args.suite)
        # TODO: Implement eval runner


if __name__ == "__main__":
    cli()
