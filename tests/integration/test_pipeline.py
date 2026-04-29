"""Integration tests — require Redis, Postgres, Qdrant running.

Run with: pytest -m integration
"""

import pytest
import asyncio

from afriagent.models import Channel, InboundMessage


@pytest.mark.integration
class TestEndToEndPipeline:
    """Full pipeline integration tests.

    These require running services:
    - Redis on localhost:6379
    - Postgres on localhost:5432
    - Qdrant on localhost:6333
    """

    @pytest.fixture
    async def agent(self):
        from afriagent.main import AfriAgent
        a = AfriAgent()
        await a.start()
        yield a
        await a.stop()

    @pytest.mark.asyncio
    async def test_full_message_flow(self, agent):
        """Test complete message processing pipeline."""
        inbound = InboundMessage(
            channel=Channel.WEBCHAT,
            sender_id="test-user-1",
            content="Hello, I need help with my hosting account",
        )

        response = await agent.handle_message(inbound)

        assert response is not None
        assert response.content
        assert response.conversation_id
        assert response.confidence > 0

    @pytest.mark.asyncio
    async def test_billing_flow(self, agent):
        """Test billing-related message flow."""
        inbound = InboundMessage(
            channel=Channel.WEBCHAT,
            sender_id="test-user-2",
            content="I need to pay my invoice of 5000 KSH via M-Pesa",
        )

        response = await agent.handle_message(inbound)

        assert response.intent_handled.value in ("billing", "general")
        assert response.content

    @pytest.mark.asyncio
    async def test_technical_flow(self, agent):
        """Test technical support message flow."""
        inbound = InboundMessage(
            channel=Channel.WEBCHAT,
            sender_id="test-user-3",
            content="My website is showing a 500 error and I can't access my email",
        )

        response = await agent.handle_message(inbound)

        assert response.content
        assert response.confidence > 0

    @pytest.mark.asyncio
    async def test_escalation_flow(self, agent):
        """Test escalation detection."""
        inbound = InboundMessage(
            channel=Channel.WEBCHAT,
            sender_id="test-user-4",
            content="I've been waiting for 3 days, this is terrible service, I want to speak to a manager",
        )

        response = await agent.handle_message(inbound)

        assert response.escalated is True or "manager" in response.content.lower()
