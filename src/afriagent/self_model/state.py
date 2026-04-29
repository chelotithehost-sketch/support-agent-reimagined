"""Self-model state — read/write the self-model document in SQLite.

The self-model tracks:
- Tool reliability scores (EMA)
- Intent accuracy by domain
- LLM provider health (status, error streak, avg latency)
- Learned failure patterns
- Peak hours
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from afriagent.config import settings
from afriagent.config.logging import get_logger

log = get_logger(__name__)

# ── Default state ─────────────────────────────────────────────────

DEFAULT_STATE: dict[str, Any] = {
    "tool_reliability": {},
    "intent_accuracy_by_domain": {
        "billing": 0.85,
        "outage": 0.80,
        "general": 0.80,
        "hostile": 0.75,
        "unclear": 0.50,
    },
    "provider_health": {},
    "learned_failure_patterns": [],
    "peak_hours": [8, 9, 17, 18, 19, 20],
    "last_updated": datetime.now(timezone.utc).isoformat(),
}


class SelfModelState:
    """SQLite-backed self-model document store.

    Thread-safe via a lock. The entire state is stored as a single JSON
    document in a key-value table for simplicity.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or settings.self_model_db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the SQLite database and create the table if needed."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS self_model (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # Insert default state if not exists
            row = conn.execute(
                "SELECT value FROM self_model WHERE key = 'state'"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO self_model (key, value, updated_at) VALUES (?, ?, ?)",
                    ("state", json.dumps(DEFAULT_STATE), datetime.now(timezone.utc).isoformat()),
                )
            conn.commit()
        finally:
            conn.close()

    def get_state(self) -> dict[str, Any]:
        """Read the current self-model state."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                row = conn.execute(
                    "SELECT value FROM self_model WHERE key = 'state'"
                ).fetchone()
                if row:
                    return json.loads(row[0])
                return dict(DEFAULT_STATE)
            except Exception as e:
                log.error("Failed to read self-model state", error=str(e))
                return dict(DEFAULT_STATE)
            finally:
                conn.close()

    def write_state(self, state: dict[str, Any]) -> None:
        """Write the full self-model state."""
        state["last_updated"] = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO self_model (key, value, updated_at) VALUES (?, ?, ?)",
                    ("state", json.dumps(state, default=str), state["last_updated"]),
                )
                conn.commit()
            except Exception as e:
                log.error("Failed to write self-model state", error=str(e))
            finally:
                conn.close()

    def update_tool_reliability(self, tool_name: str, success: bool, alpha: float = 0.1) -> None:
        """Update tool reliability using exponential moving average."""
        state = self.get_state()
        current = state.get("tool_reliability", {}).get(tool_name, 1.0)
        new_score = alpha * (1.0 if success else 0.0) + (1 - alpha) * current
        state.setdefault("tool_reliability", {})[tool_name] = round(new_score, 4)
        self.write_state(state)

    def update_provider_health(
        self,
        provider: str,
        success: bool,
        latency_ms: float,
        alpha: float = 0.1,
    ) -> None:
        """Update provider health metrics."""
        state = self.get_state()
        health = state.setdefault("provider_health", {})
        prov = health.setdefault(provider, {
            "status": "healthy",
            "error_streak": 0,
            "avg_latency_ms": 0.0,
        })

        if success:
            prov["error_streak"] = 0
            prov["status"] = "healthy"
        else:
            prov["error_streak"] = prov.get("error_streak", 0) + 1
            if prov["error_streak"] >= 3:
                prov["status"] = "degraded"
            if prov["error_streak"] >= 5:
                prov["status"] = "circuit_open"

        # EMA for latency
        old_latency = prov.get("avg_latency_ms", latency_ms)
        prov["avg_latency_ms"] = round(alpha * latency_ms + (1 - alpha) * old_latency, 1)

        health[provider] = prov
        self.write_state(state)

    def update_intent_accuracy(self, intent: str, correct: bool, alpha: float = 0.1) -> None:
        """Update intent classification accuracy using EMA."""
        state = self.get_state()
        current = state.get("intent_accuracy_by_domain", {}).get(intent, 0.8)
        new_score = alpha * (1.0 if correct else 0.0) + (1 - alpha) * current
        state.setdefault("intent_accuracy_by_domain", {})[intent] = round(new_score, 4)
        self.write_state(state)

    def add_failure_pattern(self, pattern: str) -> None:
        """Add a learned failure pattern."""
        state = self.get_state()
        patterns = state.setdefault("learned_failure_patterns", [])
        if pattern not in patterns:
            patterns.append(pattern)
            # Keep max 50 patterns
            if len(patterns) > 50:
                state["learned_failure_patterns"] = patterns[-50:]
            self.write_state(state)

    def get_provider_health_dict(self) -> dict[str, Any]:
        """Get provider health in the format expected by coordinator."""
        state = self.get_state()
        return state.get("provider_health", {})

    def get_tool_reliability_dict(self) -> dict[str, Any]:
        """Get tool reliability scores."""
        state = self.get_state()
        return state.get("tool_reliability", {})
