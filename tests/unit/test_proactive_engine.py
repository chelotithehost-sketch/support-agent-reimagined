# tests/unit/test_proactive_engine.py
# ───────────────────────────────────────────────
# Unit tests for the Proactive Support Engine (signal monitor + trigger rules).
# Copy into: tests/unit/test_proactive_engine.py
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.proactive.signal_monitor import SignalMonitor, Signal, SignalType
from agents.proactive.trigger_rules import TriggerRule, TriggerEngine, OutreachAction


# ── Signal Monitor Tests ────────────────────────

class TestSignalMonitor:

    @pytest.fixture
    def monitor(self):
        db = AsyncMock()
        return SignalMonitor(db_session=db)

    @pytest.mark.asyncio
    async def test_uptime_degradation_signal(self, monitor):
        """Should detect when uptime drops below 99.5%."""
        # Mock: server has been down for 15 minutes
        monitor._check_uptime = AsyncMock(return_value=Signal(
            signal_type=SignalType.UPTIME_DEGRADATION,
            severity="high",
            metadata={"uptime_pct": 98.2, "downtime_minutes": 15},
            affected_customers=["cust-001", "cust-002"],
        ))

        signals = await monitor.collect_signals()
        assert len(signals) == 1
        assert signals[0].signal_type == SignalType.UPTIME_DEGRADATION
        assert signals[0].severity == "high"

    @pytest.mark.asyncio
    async def test_payment_failure_spike(self, monitor):
        """Should detect when payment failures exceed normal rate."""
        monitor._check_payment_failures = AsyncMock(return_value=Signal(
            signal_type=SignalType.PAYMENT_FAILURE,
            severity="medium",
            metadata={"failure_count": 12, "normal_rate": 2, "spike_multiplier": 6.0},
            affected_customers=["cust-003"],
        ))

        signals = await monitor.collect_signals()
        assert signals[0].metadata["spike_multiplier"] > 3.0

    @pytest.mark.asyncio
    async def test_ticket_velocity_spike(self, monitor):
        """Should detect unusual ticket volume."""
        monitor._check_ticket_velocity = AsyncMock(return_value=Signal(
            signal_type=SignalType.TICKET_VELOCITY,
            severity="medium",
            metadata={"tickets_last_hour": 45, "normal_hourly": 8},
            affected_customers=[],
        ))

        signals = await monitor.collect_signals()
        assert signals[0].metadata["tickets_last_hour"] > signals[0].metadata["normal_hourly"] * 3

    @pytest.mark.asyncio
    async def test_negative_csat_cluster(self, monitor):
        """Should detect when multiple customers give low CSAT in short window."""
        monitor._check_csat = AsyncMock(return_value=Signal(
            signal_type=SignalType.NEGATIVE_CSAT,
            severity="high",
            metadata={"avg_csat": 1.8, "count": 5, "window_hours": 2},
            affected_customers=["cust-010", "cust-011", "cust-012"],
        ))

        signals = await monitor.collect_signals()
        assert signals[0].metadata["avg_csat"] < 2.0

    @pytest.mark.asyncio
    async def test_churn_risk_detection(self, monitor):
        """Should detect customers at risk of churning."""
        monitor._check_churn_risk = AsyncMock(return_value=Signal(
            signal_type=SignalType.CHURN_RISK,
            severity="critical",
            metadata={"risk_score": 0.92, "signals": ["3 open tickets", "CSAT 1/5", "plan expires in 3 days"]},
            affected_customers=["cust-050"],
        ))

        signals = await monitor.collect_signals()
        assert signals[0].metadata["risk_score"] > 0.8

    @pytest.mark.asyncio
    async def test_no_signals_returns_empty(self, monitor):
        """When everything is normal, return empty list."""
        monitor._check_uptime = AsyncMock(return_value=None)
        monitor._check_payment_failures = AsyncMock(return_value=None)
        monitor._check_ticket_velocity = AsyncMock(return_value=None)
        monitor._check_csat = AsyncMock(return_value=None)
        monitor._check_churn_risk = AsyncMock(return_value=None)
        monitor._check_security = AsyncMock(return_value=None)
        monitor._check_repeated_issues = AsyncMock(return_value=None)

        signals = await monitor.collect_signals()
        assert len(signals) == 0


# ── Trigger Engine Tests ────────────────────────

class TestTriggerEngine:

    @pytest.fixture
    def engine(self):
        return TriggerEngine()

    def test_uptime_signal_triggers_proactive_outreach(self, engine):
        """Uptime degradation should trigger outreach to affected customers."""
        signal = Signal(
            signal_type=SignalType.UPTIME_DEGRADATION,
            severity="high",
            metadata={"uptime_pct": 98.0, "downtime_minutes": 20},
            affected_customers=["cust-001"],
        )

        actions = engine.evaluate(signal)
        assert len(actions) > 0
        assert any(a.action == OutreachAction.PROACTIVE_MESSAGE for a in actions)
        assert any("cust-001" in a.target_customers for a in actions)

    def test_churn_risk_triggers_empathetic_outreach(self, engine):
        """Churn risk should trigger personalized retention outreach."""
        signal = Signal(
            signal_type=SignalType.CHURN_RISK,
            severity="critical",
            metadata={"risk_score": 0.9, "signals": ["plan expiring", "low CSAT"]},
            affected_customers=["cust-050"],
        )

        actions = engine.evaluate(signal)
        assert len(actions) > 0
        outreach = next(a for a in actions if a.action == OutreachAction.PROACTIVE_MESSAGE)
        assert "retention" in outreach.template_name.lower() or "check-in" in outreach.template_name.lower()

    def test_payment_failure_triggers_reminder(self, engine):
        """Payment failure should trigger a billing reminder."""
        signal = Signal(
            signal_type=SignalType.PAYMENT_FAILURE,
            severity="medium",
            metadata={"failure_count": 3},
            affected_customers=["cust-003"],
        )

        actions = engine.evaluate(signal)
        assert any(a.action == OutreachAction.BILLING_REMINDER for a in actions)

    def test_security_event_triggers_alert(self, engine):
        """Security event should trigger immediate alert."""
        signal = Signal(
            signal_type=SignalType.SECURITY_EVENT,
            severity="critical",
            metadata={"event_type": "brute_force", "attempts": 50},
            affected_customers=["cust-020"],
        )

        actions = engine.evaluate(signal)
        assert any(a.action == OutreachAction.SECURITY_ALERT for a in actions)

    def test_low_severity_signal_no_outreach(self, engine):
        """Low severity signals should not trigger customer-facing outreach."""
        signal = Signal(
            signal_type=SignalType.TICKET_VELOCITY,
            severity="low",
            metadata={"tickets_last_hour": 10, "normal_hourly": 8},
            affected_customers=[],
        )

        actions = engine.evaluate(signal)
        # Should log internally but not reach out to customers
        assert all(a.action != OutreachAction.PROACTIVE_MESSAGE for a in actions)
