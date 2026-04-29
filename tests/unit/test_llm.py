"""Unit tests for LLM providers."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from afriagent.brain.llm import CircuitBreaker, CircuitState, LLMResponse


class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = CircuitBreaker(fail_threshold=3, reset_seconds=60)
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request() is True

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(fail_threshold=3, reset_seconds=60)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.allow_request() is False

    def test_resets_on_success(self):
        cb = CircuitBreaker(fail_threshold=3, reset_seconds=60)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.fail_count == 0

    def test_half_open_after_reset(self):
        import time
        cb = CircuitBreaker(fail_threshold=2, reset_seconds=0)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        time.sleep(0.01)
        assert cb.allow_request() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_closes_from_half_open_on_success(self):
        import time
        cb = CircuitBreaker(fail_threshold=2, reset_seconds=0)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.01)
        cb.allow_request()  # Move to HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED


class TestLLMResponse:
    def test_response_model(self):
        resp = LLMResponse(
            content="Hello",
            model="gpt-4o",
            provider="openai",
            tokens_input=10,
            tokens_output=5,
            latency_ms=150.0,
        )
        assert resp.content == "Hello"
        assert resp.model == "gpt-4o"
        assert resp.provider == "openai"
        assert resp.tokens_input + resp.tokens_output == 15
