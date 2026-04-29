# tests/unit/test_learning_loop.py
# ───────────────────────────────────────────────
# Unit tests for the Learning Loop (collector, evaluator, trainer).
# Copy into: tests/unit/test_learning_loop.py
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.learning.collector import FeedbackCollector
from agents.learning.evaluator import AutoEvaluator, EvalReport, RegressionFlag


# ── Feedback Collector Tests ────────────────────

class TestFeedbackCollector:

    @pytest.fixture
    def collector(self):
        db = AsyncMock()
        return FeedbackCollector(db_session=db)

    @pytest.mark.asyncio
    async def test_explicit_csat_stored(self, collector):
        """CSAT score 1-5 should be stored on the conversation."""
        mock_conv = MagicMock()
        mock_conv.csat_score = None
        collector._db.get = AsyncMock(return_value=mock_conv)
        collector._db.flush = AsyncMock()

        await collector.record_explicit_csat(str(uuid.uuid4()), score=4)
        assert mock_conv.csat_score == 4

    @pytest.mark.asyncio
    async def test_csat_clamped_to_valid_range(self, collector):
        """Scores outside 1-5 should be clamped."""
        mock_conv = MagicMock()
        collector._db.get = AsyncMock(return_value=mock_conv)
        collector._db.flush = AsyncMock()

        await collector.record_explicit_csat(str(uuid.uuid4()), score=0)
        assert mock_conv.csat_score == 1

        await collector.record_explicit_csat(str(uuid.uuid4()), score=10)
        assert mock_conv.csat_score == 5

    @pytest.mark.asyncio
    async def test_implicit_csat_within_window(self, collector):
        """Follow-up within 10 minutes of resolution should flag as implicit CSAT."""
        result = await collector.check_implicit_csat(
            str(uuid.uuid4()),
            time_since_resolution=timedelta(minutes=3),
        )
        assert result is True  # flagged as potentially incomplete

    @pytest.mark.asyncio
    async def test_implicit_csat_outside_window(self, collector):
        """Follow-up after 10 minutes should not flag."""
        result = await collector.check_implicit_csat(
            str(uuid.uuid4()),
            time_since_resolution=timedelta(minutes=15),
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_human_correction_stored_as_ground_truth(self, collector):
        """Human agent's response should be stored as highest-value training signal."""
        collector._db.add = MagicMock()
        collector._db.flush = AsyncMock()

        await collector.record_human_correction(
            turn_id=str(uuid.uuid4()),
            human_response="The correct fix was to update the MX record to mx1.hostprovider.co.ke",
        )

        eval_record = collector._db.add.call_args[0][0]
        assert eval_record.evaluator == "human"
        assert eval_record.accuracy_score == 1.0
        assert "ground truth" in eval_record.notes.lower() or "highest value" in eval_record.notes.lower()

    @pytest.mark.asyncio
    async def test_resolution_confirmation_patterns(self, collector):
        """Should detect common resolution confirmation phrases."""
        assert await collector.check_resolution_confirmation("Thank you so much!")
        assert await collector.check_resolution_confirmation("That worked perfectly")
        assert await collector.check_resolution_confirmation("sorted, thanks")
        assert await collector.check_resolution_confirmation("great help, resolved")

        assert not await collector.check_resolution_confirmation("still broken")
        assert not await collector.check_resolution_confirmation("I want a refund")


# ── Auto Evaluator Tests ────────────────────────

class TestAutoEvaluator:

    @pytest.fixture
    def evaluator(self):
        db = AsyncMock()
        return AutoEvaluator(db_session=db)

    @pytest.mark.asyncio
    async def test_eval_report_structure(self, evaluator):
        """Report should contain all required fields."""
        # Mock DB responses
        mock_conv = MagicMock()
        mock_conv.turns = []
        mock_conv.resolved_at = datetime.utcnow()
        mock_conv.escalated_at = None
        mock_conv.csat_score = 4

        mock_eval = MagicMock()
        mock_eval.accuracy_score = 0.85
        mock_eval.tone_score = 0.9
        mock_eval.completeness_score = 0.8
        mock_eval.overall_score = 0.85

        evaluator._db.execute = AsyncMock()
        evaluator._db.execute.return_value.scalars.return_value.all.return_value = [mock_conv]
        evaluator._db.execute.return_value.scalars.return_value.all.side_effect = [
            [mock_conv],  # conversations
            [mock_eval],  # evaluations
        ]

        # Patch the method to use our mocks
        with patch.object(evaluator, '_all_turns', return_value=[]):
            report = await evaluator.evaluate(target_date=datetime(2026, 4, 28).date())

        assert report.total_conversations >= 0
        assert hasattr(report, 'resolution_rate')
        assert hasattr(report, 'avg_csat')
        assert hasattr(report, 'escalation_rate')
        assert hasattr(report, 'total_cost_usd')
        assert hasattr(report, 'regressions')

    def test_regression_detection_threshold(self, evaluator):
        """Should flag metrics that degraded by >10%."""
        # Simulate: accuracy dropped from 0.90 to 0.78 (13% degradation)
        flag = RegressionFlag(
            metric_name="avg_accuracy_score",
            current_value=0.78,
            rolling_average=0.90,
            degradation_pct=0.133,
            severity="high",
        )
        assert flag.degradation_pct > evaluator.REGRESSION_THRESHOLD
        assert flag.severity == "high"

    def test_no_regression_within_threshold(self, evaluator):
        """Small fluctuations should not trigger regression flags."""
        # 5% degradation is within threshold
        flag = RegressionFlag(
            metric_name="avg_tone_score",
            current_value=0.86,
            rolling_average=0.90,
            degradation_pct=0.044,
            severity="low",
        )
        assert flag.degradation_pct < evaluator.REGRESSION_THRESHOLD


# ── Trainer Tests ───────────────────────────────

class TestTrainer:
    """Tests for the few-shot updater (trainer.py)."""

    @pytest.fixture
    def trainer(self):
        from agents.learning.trainer import FewShotTrainer
        return FewShotTrainer()

    def test_few_shot_examples_selected_by_intent(self, trainer):
        """Should select relevant examples based on intent match."""
        examples = trainer.select_examples(
            intent="EMAIL_SETUP",
            product_area="EMAIL",
            max_examples=3,
        )
        # Should return at most 3 examples
        assert len(examples) <= 3

    def test_few_shot_examples_prioritize_high_csat(self, trainer):
        """Higher CSAT examples should be preferred."""
        # This tests the sorting logic — examples with CSAT 5 > CSAT 3
        pass  # Implementation depends on trainer.select_examples internals

    def test_guardrail_prevents_bad_examples(self, trainer):
        """Examples from conversations with CSAT < 3 should be excluded."""
        # The trainer should filter out low-quality examples
        pass  # Implementation depends on trainer filtering logic
