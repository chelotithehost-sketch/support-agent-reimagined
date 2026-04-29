"""Dispatcher — converts a customer message into a DispatchPlan."""

from __future__ import annotations

import json
from typing import Any

from afriagent.config import settings
from afriagent.config.logging import get_logger
from afriagent.models import DispatchPlan, DispatchStep, ConversationContext
from afriagent.coordinator.model import generate_json
from afriagent.coordinator.prompts import build_system_prompt, get_few_shot_messages

log = get_logger(__name__)


# ── Fallback dispatch logic (when coordinator LLM is unavailable) ─

INTENT_KEYWORDS_FALLBACK: dict[str, list[str]] = {
    "billing": [
        "invoice", "payment", "pay", "bill", "mpesa", "charge",
        "subscription", "renew", "cost", "price", "fee", "amount",
        "malipo", "deni", "lipa",
    ],
    "outage": [
        "down", "not working", "error", "slow", "timeout", "dns",
        "domain", "ssl", "email", "hosting", "server", "site",
        "tatizo", "shida",
    ],
    "hostile": [
        "scam", "fraud", "refund", "cancel", "lawyer", "sue",
        "terrible", "worst", "angry", "furious", "unacceptable",
    ],
}


def _fallback_intent(text: str) -> str:
    """Simple keyword-based intent detection as fallback."""
    lower = text.lower()
    scores: dict[str, int] = {}
    for intent, keywords in INTENT_KEYWORDS_FALLBACK.items():
        score = sum(1 for kw in keywords if kw in lower)
        if score > 0:
            scores[intent] = score
    if not scores:
        return "unclear"
    return max(scores, key=scores.get)  # type: ignore[arg-type]


def _fallback_language(text: str) -> str:
    """Simple language detection fallback."""
    sheng_markers = ["sasa", "poa", "maze", "fiti", "niaje", "uko", "buda", "dame"]
    lower = text.lower()
    if any(m in lower for m in sheng_markers):
        return "sheng"
    sw_markers = ["habari", "nzuri", "sawa", "asante", "tafadhali", "karibu"]
    if any(m in lower for m in sw_markers):
        return "sw"
    return "en"


def _build_fallback_plan(context: ConversationContext) -> DispatchPlan:
    """Build a simple dispatch plan without the coordinator LLM."""
    text = context.current_message.content
    intent = _fallback_intent(text)
    language = _fallback_language(text)

    # Map intent to urgency
    urgency_map = {"billing": 3, "outage": 4, "hostile": 5, "general": 2, "unclear": 1}
    urgency = urgency_map.get(intent, 2)

    steps: list[DispatchStep] = []

    if intent == "hostile":
        steps.append(DispatchStep(
            tool="create_support_ticket",
            llm_provider=None,
            params={"priority": "High", "subject": f"Escalation: {text[:50]}"},
        ))

    # Add LLM response step
    provider_priority = settings.llm_providers  # list like ["ollama", "openai", "anthropic"]
    llm_provider = provider_priority[0] if provider_priority else "openai"

    steps.append(DispatchStep(
        tool=None,
        llm_provider=llm_provider,
        params={"task": "generate_response", "intent": intent, "language": language},
    ))

    confidence = 0.5 if intent == "unclear" else 0.7

    return DispatchPlan(
        intent=intent,
        urgency=urgency,
        language=language,
        steps=steps,
        confidence=confidence,
        reasoning=f"Fallback dispatch (coordinator LLM unavailable). Intent: {intent}",
    )


# ── Main dispatch function ───────────────────────────────────────


async def dispatch(
    context: ConversationContext,
    tool_registry: dict[str, Any],
    self_model_state: dict[str, Any],
    provider_health: dict[str, Any],
) -> DispatchPlan:
    """Analyze the incoming message and produce a DispatchPlan.

    Tries the coordinator LLM first; falls back to keyword-based dispatch.
    """
    # Check if coordinator is enabled
    if not settings.coordinator_enabled:
        log.debug("Coordinator disabled, using fallback dispatch")
        return _build_fallback_plan(context)

    # Build the prompt
    system_prompt = build_system_prompt(tool_registry, self_model_state, provider_health)
    few_shot = get_few_shot_messages()

    # Build user message
    user_msg = (
        f"Customer message: {context.current_message.content}\n"
        f"Detected language: {context.detected_language}\n"
        f"Detected sentiment: {context.detected_sentiment.value}\n"
        f"Detected urgency: {context.detected_urgency.value}\n"
        f"Channel: {context.current_message.channel.value}"
    )
    if context.customer.name:
        user_msg += f"\nCustomer: {context.customer.name}"
    if context.customer.open_tickets:
        user_msg += f"\nOpen tickets: {len(context.customer.open_tickets)}"

    full_prompt = "\n".join([m["content"] for m in few_shot if m["role"] == "user"][:4])
    full_prompt += "\n\n---\n\n" + user_msg

    # Try coordinator LLM
    result = generate_json(
        prompt=full_prompt,
        system_prompt=system_prompt,
        max_tokens=1024,
        temperature=0.1,
    )

    if result is None:
        log.info("Coordinator LLM unavailable, using fallback dispatch")
        return _build_fallback_plan(context)

    # Parse the result into DispatchPlan
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
            urgency=int(result.get("urgency", 2)),
            language=result.get("language", "en"),
            steps=steps,
            confidence=float(result.get("confidence", 0.5)),
            reasoning=result.get("reasoning", "No reasoning provided"),
        )

        log.info(
            "Dispatch plan created",
            intent=plan.intent,
            urgency=plan.urgency,
            confidence=plan.confidence,
            steps=len(plan.steps),
        )
        return plan

    except (KeyError, ValueError, TypeError) as e:
        log.warning("Failed to parse coordinator output", error=str(e), raw=result)
        return _build_fallback_plan(context)
