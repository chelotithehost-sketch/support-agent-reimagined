"""Replanner — handles low-confidence results, retry/escalate logic."""

from __future__ import annotations

import json
from typing import Any

from afriagent.config import settings
from afriagent.config.logging import get_logger
from afriagent.models import DispatchPlan, DispatchStep, ConversationContext
from afriagent.coordinator.model import generate_json
from afriagent.coordinator.prompts import build_system_prompt

log = get_logger(__name__)

MAX_REPLAN_CYCLES = 3
CONFIDENCE_THRESHOLD = 0.6


class StepResult:
    """Result from executing a single dispatch step."""

    def __init__(
        self,
        step: DispatchStep,
        content: str = "",
        confidence: float = 0.0,
        success: bool = True,
        error: str | None = None,
        provider_used: str | None = None,
        latency_ms: float = 0.0,
    ) -> None:
        self.step = step
        self.content = content
        self.confidence = confidence
        self.success = success
        self.error = error
        self.provider_used = provider_used
        self.latency_ms = latency_ms


def should_replan(result: StepResult, replan_count: int) -> bool:
    """Determine if we should replan based on step result."""
    if replan_count >= MAX_REPLAN_CYCLES:
        return False
    if result.confidence < CONFIDENCE_THRESHOLD:
        return True
    if not result.success:
        return True
    return False


def should_escalate(result: StepResult, replan_count: int) -> bool:
    """Determine if we should escalate to human agent."""
    if replan_count >= MAX_REPLAN_CYCLES:
        return True
    if result.step.tool == "create_support_ticket" and not result.success:
        return True
    return False


def get_next_provider(
    current_provider: str | None,
    provider_health: dict[str, Any],
) -> str | None:
    """Get the next healthy provider, skipping circuit-broken ones."""
    provider_priority = settings.llm_providers  # e.g. ["ollama", "openai", "anthropic"]

    if not current_provider:
        # Pick the first healthy one
        for p in provider_priority:
            health = provider_health.get(p, {})
            if health.get("status") != "circuit_open":
                return p
        return provider_priority[0] if provider_priority else None

    # Skip to next provider after current
    try:
        idx = provider_priority.index(current_provider)
    except ValueError:
        return provider_priority[0] if provider_priority else None

    for p in provider_priority[idx + 1:]:
        health = provider_health.get(p, {})
        if health.get("status") != "circuit_open":
            return p

    # All remaining are circuit-broken — try first healthy one
    for p in provider_priority:
        health = provider_health.get(p, {})
        if health.get("status") != "circuit_open":
            return p

    return None  # All circuit-broken


async def replan(
    context: ConversationContext,
    previous_result: StepResult,
    replan_count: int,
    tool_registry: dict[str, Any],
    self_model_state: dict[str, Any],
    provider_health: dict[str, Any],
) -> DispatchPlan:
    """Replan after a low-confidence or failed step result.

    Args:
        context: The original conversation context.
        previous_result: The result that triggered replanning.
        replan_count: How many times we've already replanned.
        tool_registry: Available tools.
        self_model_state: Self-model state for context.
        provider_health: Current LLM provider health.

    Returns:
        A new DispatchPlan, possibly with escalation flag set.

    Raises:
        ValueError: If max replan cycles exceeded (caller should escalate).
    """
    if replan_count >= MAX_REPLAN_CYCLES:
        log.warning(
            "Max replan cycles reached, forcing escalation",
            replan_count=replan_count,
        )
        return DispatchPlan(
            intent="unclear",
            urgency=5,
            language=context.detected_language,
            steps=[],
            confidence=0.0,
            reasoning=f"Max replan cycles ({MAX_REPLAN_CYCLES}) exceeded. Escalating.",
            escalate=True,
        )

    # If provider is circuit-broken, skip to next
    if not previous_result.success and previous_result.error:
        next_provider = get_next_provider(
            previous_result.provider_used, provider_health
        )
        if next_provider and next_provider != previous_result.provider_used:
            log.info(
                "Skipping circuit-broken provider",
                failed=previous_result.provider_used,
                switching_to=next_provider,
            )
            return DispatchPlan(
                intent=context.detected_intent.value if hasattr(context.detected_intent, 'value') else str(context.detected_intent),
                urgency=4,
                language=context.detected_language,
                steps=[
                    DispatchStep(
                        tool=previous_result.step.tool,
                        llm_provider=next_provider if not previous_result.step.tool else None,
                        params=previous_result.step.params,
                    )
                ],
                confidence=0.7,
                reasoning=f"Switched from {previous_result.provider_used} to {next_provider} due to failure",
            )

    # Try coordinator LLM for intelligent replanning
    if settings.coordinator_enabled:
        system_prompt = build_system_prompt(tool_registry, self_model_state, provider_health)

        replan_prompt = (
            f"REPLAN REQUEST (cycle {replan_count + 1}/{MAX_REPLAN_CYCLES})\n\n"
            f"Original customer message: {context.current_message.content}\n"
            f"Previous step: tool={previous_result.step.tool}, provider={previous_result.step.llm_provider}\n"
            f"Previous result confidence: {previous_result.confidence}\n"
            f"Previous result success: {previous_result.success}\n"
            f"Previous result error: {previous_result.error or 'none'}\n"
            f"Previous result content (truncated): {previous_result.content[:200]}\n\n"
            f"Produce a NEW dispatch plan that addresses the failure. "
            f"Consider using a different tool or provider. "
            f"If this is replan cycle {MAX_REPLAN_CYCLES}, set urgency to 5."
        )

        result = generate_json(
            prompt=replan_prompt,
            system_prompt=system_prompt,
            max_tokens=512,
            temperature=0.2,
        )

        if result is not None:
            try:
                steps_raw = result.get("steps", [])
                steps = [
                    DispatchStep(
                        tool=s.get("tool"),
                        llm_provider=s.get("llm_provider"),
                        params=s.get("params", {}),
                    )
                    for s in steps_raw
                ]

                plan = DispatchPlan(
                    intent=result.get("intent", "unclear"),
                    urgency=int(result.get("urgency", 4)),
                    language=result.get("language", context.detected_language),
                    steps=steps,
                    confidence=float(result.get("confidence", 0.5)),
                    reasoning=result.get("reasoning", "Replanned by coordinator"),
                )
                log.info("Replan successful", cycle=replan_count + 1, intent=plan.intent)
                return plan
            except (KeyError, ValueError, TypeError) as e:
                log.warning("Failed to parse replan output", error=str(e))

    # Fallback replan: escalate with a support ticket
    log.info("Using fallback replan with escalation")
    return DispatchPlan(
        intent="unclear",
        urgency=4,
        language=context.detected_language,
        steps=[
            DispatchStep(
                tool="create_support_ticket",
                llm_provider=None,
                params={
                    "priority": "High",
                    "subject": f"Auto-escalation after {replan_count + 1} failed attempts",
                },
            ),
            DispatchStep(
                tool=None,
                llm_provider=get_next_provider(None, provider_health) or "openai",
                params={"task": "apologetic_escalation_message"},
            ),
        ],
        confidence=0.6,
        reasoning=f"Fallback replan after {replan_count + 1} failed attempts. Creating ticket and sending apologetic message.",
    )
