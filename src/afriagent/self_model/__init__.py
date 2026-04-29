"""Self-model — the agent's self-awareness layer.

Tracks tool reliability, provider health, intent accuracy,
and learned failure patterns using SQLite-backed state with
exponential moving average updates.

All updates happen asynchronously in background tasks.
"""

from __future__ import annotations

from afriagent.self_model.state import SelfModelState
from afriagent.self_model.updater import SelfModelUpdater, TurnMetrics

__all__ = ["SelfModelState", "SelfModelUpdater", "TurnMetrics"]
