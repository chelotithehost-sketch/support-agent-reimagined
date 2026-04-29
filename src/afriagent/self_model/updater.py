"""Self-model updater — background task that updates state after each conversation turn.

Must NEVER run synchronously in the hot path. Always dispatched as an asyncio background task.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from afriagent.config.logging import get_logger
from afriagent.self_model.state import SelfModelState

log = get_logger(__name__)

# EMA smoothing factor
ALPHA = 0.1


@dataclass
class TurnMetrics:
    """Metrics from a single conversation turn, passed to the updater."""

    tool_used: str | None = None
    tool_success: bool = True
    llm_provider: str | None = None
    llm_latency_ms: float = 0.0
    llm_success: bool = True
    validation_score: float = 0.0
    detected_intent: str = "general"
    intent_correct: bool | None = None  # None = unknown
    conversation_id: str = ""


class SelfModelUpdater:
    """Updates the self-model after every conversation turn.

    Usage:
        updater = SelfModelUpdater()
        # Fire and forget — never await in hot path
        updater.schedule_update(metrics)
    """

    def __init__(self, state: SelfModelState | None = None) -> None:
        self._state = state or SelfModelState()

    def schedule_update(self, metrics: TurnMetrics) -> None:
        """Schedule a background update. Non-blocking.

        This creates an asyncio task that runs in the background.
        Should be called from async context but does NOT block.
        """
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._update(metrics))
        except RuntimeError:
            # No running loop — try to create a new one
            log.warning("No running event loop for self-model update, skipping")

    async def _update(self, metrics: TurnMetrics) -> None:
        """Perform the actual state updates. Runs in background."""
        try:
            # Update tool reliability
            if metrics.tool_used:
                self._state.update_tool_reliability(
                    tool_name=metrics.tool_used,
                    success=metrics.tool_success,
                    alpha=ALPHA,
                )

            # Update provider health
            if metrics.llm_provider:
                self._state.update_provider_health(
                    provider=metrics.llm_provider,
                    success=metrics.llm_success,
                    latency_ms=metrics.llm_latency_ms,
                    alpha=ALPHA,
                )

            # Update intent accuracy (if we have feedback)
            if metrics.intent_correct is not None:
                self._state.update_intent_accuracy(
                    intent=metrics.detected_intent,
                    correct=metrics.intent_correct,
                    alpha=ALPHA,
                )

            # Track failure patterns
            if metrics.validation_score < 0.5 and metrics.tool_used:
                hour = __import__("datetime").datetime.now().hour
                pattern = f"{metrics.tool_used}_low_score_at_{hour}h"
                self._state.add_failure_pattern(pattern)

            log.debug(
                "Self-model updated",
                tool=metrics.tool_used,
                provider=metrics.llm_provider,
                intent=metrics.detected_intent,
            )

        except Exception as e:
            log.error("Self-model update failed", error=str(e))

    def get_state(self) -> dict[str, Any]:
        """Get the current self-model state (synchronous, for coordinator)."""
        return self._state.get_state()

    def get_provider_health(self) -> dict[str, Any]:
        """Get provider health dict (for coordinator)."""
        return self._state.get_provider_health_dict()
