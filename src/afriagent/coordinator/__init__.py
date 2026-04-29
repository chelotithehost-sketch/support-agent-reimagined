"""Coordinator — the core brain that dispatches and replans.

The CoordinatorBrain is the decision-making layer that:
1. Analyzes incoming messages and produces a DispatchPlan
2. Handles replanning when steps fail or return low confidence
3. Manages provider failover and escalation logic

The coordinator LLM runs in-process via llama-cpp-python for <100ms latency.
When unavailable, falls back to keyword-based dispatch.
"""

from __future__ import annotations

from typing import Any

from afriagent.config.logging import get_logger
from afriagent.models import ConversationContext, DispatchPlan
from afriagent.coordinator.dispatcher import dispatch as _dispatch
from afriagent.coordinator.replanner import (
    replan as _replan,
    StepResult,
    should_replan,
    should_escalate,
)

log = get_logger(__name__)

__all__ = ["CoordinatorBrain", "StepResult"]


class CoordinatorBrain:
    """The central dispatcher and replanner for the agent.

    Usage:
        coordinator = CoordinatorBrain(
            tool_registry=TOOL_REGISTRY,
            get_self_model_state=self_model.get_state,
            get_provider_health=llm.get_provider_health,
        )
        plan = await coordinator.dispatch(context)
        # ... execute steps ...
        new_plan = await coordinator.replan(context, previous_result)
    """

    def __init__(
        self,
        tool_registry: dict[str, Any] | None = None,
        get_self_model_state: Any = None,
        get_provider_health: Any = None,
    ) -> None:
        """Initialize the CoordinatorBrain.

        Args:
            tool_registry: Dict of tool_name → tool metadata.
                If None, will attempt to import from tools.registry.
            get_self_model_state: Callable returning self-model state dict.
                If None, returns empty dict.
            get_provider_health: Callable returning provider health dict.
                If None, returns empty dict.
        """
        self._tool_registry = tool_registry
        self._get_self_model_state = get_self_model_state
        self._get_provider_health = get_provider_health

    def _get_tool_registry(self) -> dict[str, Any]:
        """Get tool registry, lazily importing if needed."""
        if self._tool_registry is not None:
            return self._tool_registry
        try:
            from afriagent.tools.registry import TOOL_REGISTRY
            self._tool_registry = TOOL_REGISTRY
            return TOOL_REGISTRY
        except ImportError:
            log.warning("Tool registry not available")
            return {}

    def _get_self_state(self) -> dict[str, Any]:
        """Get self-model state."""
        if self._get_self_model_state is not None:
            try:
                return self._get_self_model_state()
            except Exception:
                return {}
        return {}

    def _get_provider_health(self) -> dict[str, Any]:
        """Get provider health status."""
        if self._get_provider_health is not None:
            try:
                return self._get_provider_health()
            except Exception:
                return {}
        return {}

    async def dispatch(self, context: ConversationContext) -> DispatchPlan:
        """Analyze the incoming message and produce a DispatchPlan.

        Args:
            context: The enriched conversation context from the Perceiver.

        Returns:
            DispatchPlan with intent, urgency, language, steps, and confidence.
        """
        tool_registry = self._get_tool_registry()
        self_state = self._get_self_state()
        provider_health = self._get_provider_health()

        plan = await _dispatch(
            context=context,
            tool_registry=tool_registry,
            self_model_state=self_state,
            provider_health=provider_health,
        )

        log.info(
            "Dispatch complete",
            intent=plan.intent,
            urgency=plan.urgency,
            confidence=plan.confidence,
            steps=len(plan.steps),
        )
        return plan

    async def replan(
        self,
        context: ConversationContext,
        previous_result: StepResult,
        replan_count: int = 0,
    ) -> DispatchPlan:
        """Replan after a low-confidence or failed step.

        Args:
            context: The original conversation context.
            previous_result: The result that triggered replanning.
            replan_count: How many replan cycles have occurred.

        Returns:
            A new DispatchPlan. If escalate is True, the caller should escalate.
        """
        tool_registry = self._get_tool_registry()
        self_state = self._get_self_state()
        provider_health = self._get_provider_health()

        plan = await _replan(
            context=context,
            previous_result=previous_result,
            replan_count=replan_count,
            tool_registry=tool_registry,
            self_model_state=self_state,
            provider_health=provider_health,
        )

        log.info(
            "Replan complete",
            cycle=replan_count + 1,
            intent=plan.intent,
            confidence=plan.confidence,
            escalate=getattr(plan, 'escalate', False),
        )
        return plan
