"""Unit tests for memory layer."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from afriagent.models import LearningExample, Intent, Sentiment


class TestSessionStore:
    """Test Redis session store (using fakeredis for unit tests)."""

    @pytest.fixture
    def store(self):
        from afriagent.memory import SessionStore
        s = SessionStore()
        # Mock the client for unit tests
        s._client = AsyncMock()
        return s

    @pytest.mark.asyncio
    async def test_get_session_returns_none_when_empty(self, store):
        store._client.get = AsyncMock(return_value=None)
        result = await store.get_session("conv-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_session_stores_json(self, store):
        store._client.set = AsyncMock()
        await store.set_session("conv-1", {"key": "value"})
        store._client.set.assert_called_once()
        call_args = store._client.set.call_args
        assert "session:conv-1" in call_args[0]

    @pytest.mark.asyncio
    async def test_acquire_lock_success(self, store):
        store._client.set = AsyncMock(return_value=True)
        result = await store.acquire_lock("dedup-key")
        assert result is True

    @pytest.mark.asyncio
    async def test_acquire_lock_failure(self, store):
        store._client.set = AsyncMock(return_value=None)
        result = await store.acquire_lock("dedup-key")
        assert result is False


class TestEpisodicMemory:
    """Test Postgres episodic memory (mocked)."""

    @pytest.fixture
    def memory(self):
        from afriagent.memory import EpisodicMemory
        m = EpisodicMemory.__new__(EpisodicMemory)
        m._session_factory = MagicMock()
        return m

    @pytest.mark.asyncio
    async def test_get_history_calls_session(self, memory):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        memory._session_factory.return_value = mock_session

        result = await memory.get_conversation_history("conv-1", limit=10)
        assert isinstance(result, list)


class TestLearningExample:
    def test_model_creation(self):
        example = LearningExample(
            conversation_id="conv-1",
            customer_message="How do I pay?",
            agent_response="Use M-Pesa paybill 123456",
            intent=Intent.BILLING,
            sentiment=Sentiment.NEUTRAL,
            confidence=0.9,
        )
        assert example.intent == Intent.BILLING
        assert example.confidence == 0.9
        assert example.id is not None
