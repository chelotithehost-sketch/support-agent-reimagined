"""Unit tests for the self-model module."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from afriagent.self_model.state import SelfModelState, DEFAULT_STATE
from afriagent.self_model.updater import SelfModelUpdater, TurnMetrics


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite database path."""
    return str(tmp_path / "test_self_model.db")


@pytest.fixture
def state(tmp_db):
    """Fresh SelfModelState with temp database."""
    return SelfModelState(db_path=tmp_db)


@pytest.fixture
def updater(state):
    """SelfModelUpdater with test state."""
    return SelfModelUpdater(state=state)


# ── SelfModelState tests ─────────────────────────────────────────


class TestSelfModelState:
    def test_init_creates_db(self, tmp_db):
        state = SelfModelState(db_path=tmp_db)
        assert os.path.exists(tmp_db)

    def test_get_state_returns_default(self, state):
        result = state.get_state()
        assert "tool_reliability" in result
        assert "provider_health" in result
        assert "intent_accuracy_by_domain" in result
        assert "learned_failure_patterns" in result
        assert "peak_hours" in result
        assert "last_updated" in result

    def test_write_state(self, state):
        new_state = state.get_state()
        new_state["tool_reliability"]["whmcs"] = 0.95
        state.write_state(new_state)

        read_back = state.get_state()
        assert read_back["tool_reliability"]["whmcs"] == 0.95

    def test_update_tool_reliability_ema(self, state):
        # Initial score should be 1.0 (default)
        state.update_tool_reliability("whmcs", success=True, alpha=0.1)
        result = state.get_state()
        # EMA: 0.1 * 1.0 + 0.9 * 1.0 = 1.0
        assert result["tool_reliability"]["whmcs"] == 1.0

        # Failure should reduce score
        state.update_tool_reliability("whmcs", success=False, alpha=0.1)
        result = state.get_state()
        # EMA: 0.1 * 0.0 + 0.9 * 1.0 = 0.9
        assert result["tool_reliability"]["whmcs"] == pytest.approx(0.9, abs=0.01)

    def test_update_provider_health_success(self, state):
        state.update_provider_health("openai", success=True, latency_ms=500.0)
        result = state.get_state()
        health = result["provider_health"]["openai"]
        assert health["status"] == "healthy"
        assert health["error_streak"] == 0
        assert health["avg_latency_ms"] == pytest.approx(500.0, abs=1.0)

    def test_update_provider_health_failure_streak(self, state):
        # 3 failures should set status to degraded
        for _ in range(3):
            state.update_provider_health("openai", success=False, latency_ms=500.0)
        result = state.get_state()
        assert result["provider_health"]["openai"]["status"] == "degraded"
        assert result["provider_health"]["openai"]["error_streak"] == 3

    def test_update_provider_health_5_failures_circuit_open(self, state):
        for _ in range(5):
            state.update_provider_health("openai", success=False, latency_ms=500.0)
        result = state.get_state()
        assert result["provider_health"]["openai"]["status"] == "circuit_open"

    def test_update_provider_health_recovery(self, state):
        # Fail then succeed
        state.update_provider_health("openai", success=False, latency_ms=500.0)
        state.update_provider_health("openai", success=True, latency_ms=400.0)
        result = state.get_state()
        assert result["provider_health"]["openai"]["status"] == "healthy"
        assert result["provider_health"]["openai"]["error_streak"] == 0

    def test_update_intent_accuracy(self, state):
        state.update_intent_accuracy("billing", correct=True, alpha=0.1)
        result = state.get_state()
        # Should be close to default (0.85) with one correct update
        assert result["intent_accuracy_by_domain"]["billing"] > 0.84

    def test_add_failure_pattern(self, state):
        state.add_failure_pattern("mpesa_timeout_peak_hours_18_21")
        result = state.get_state()
        assert "mpesa_timeout_peak_hours_18_21" in result["learned_failure_patterns"]

    def test_add_failure_pattern_no_duplicates(self, state):
        state.add_failure_pattern("test_pattern")
        state.add_failure_pattern("test_pattern")
        result = state.get_state()
        assert result["learned_failure_patterns"].count("test_pattern") == 1

    def test_failure_pattern_max_50(self, state):
        for i in range(55):
            state.add_failure_pattern(f"pattern_{i}")
        result = state.get_state()
        assert len(result["learned_failure_patterns"]) <= 50

    def test_get_provider_health_dict(self, state):
        state.update_provider_health("openai", success=True, latency_ms=500.0)
        health = state.get_provider_health_dict()
        assert "openai" in health
        assert health["openai"]["status"] == "healthy"

    def test_get_tool_reliability_dict(self, state):
        state.update_tool_reliability("whmcs", success=True)
        reliability = state.get_tool_reliability_dict()
        assert "whmcs" in reliability


# ── SelfModelUpdater tests ───────────────────────────────────────


class TestSelfModelUpdater:
    @pytest.mark.asyncio
    async def test_update_tool_metrics(self, updater, state):
        metrics = TurnMetrics(
            tool_used="whmcs",
            tool_success=True,
            llm_provider="openai",
            llm_latency_ms=800.0,
            llm_success=True,
            validation_score=0.85,
            detected_intent="billing",
        )
        await updater._update(metrics)

        result = state.get_state()
        assert "whmcs" in result["tool_reliability"]
        assert "openai" in result["provider_health"]

    @pytest.mark.asyncio
    async def test_update_failure_pattern(self, updater, state):
        metrics = TurnMetrics(
            tool_used="mpesa",
            tool_success=False,
            validation_score=0.3,
            detected_intent="billing",
        )
        await updater._update(metrics)

        result = state.get_state()
        # Low score + tool used should create a failure pattern
        assert len(result["learned_failure_patterns"]) > 0

    @pytest.mark.asyncio
    async def test_update_no_tool(self, updater, state):
        """Updates without a tool should still work."""
        metrics = TurnMetrics(
            llm_provider="openai",
            llm_latency_ms=500.0,
            validation_score=0.8,
        )
        await updater._update(metrics)
        # Should not crash

    def test_get_state(self, updater):
        result = updater.get_state()
        assert isinstance(result, dict)
        assert "tool_reliability" in result

    def test_get_provider_health(self, updater):
        result = updater.get_provider_health()
        assert isinstance(result, dict)
