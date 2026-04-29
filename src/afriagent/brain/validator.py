"""9-Layer Response Validation Pipeline.

Each layer runs independently and returns a ValidationLayer result.
A response must pass ALL critical layers and achieve a minimum
composite score to be delivered.
"""

from __future__ import annotations

import re
import time
from typing import Any

from afriagent.config import settings
from afriagent.config.logging import get_logger
from afriagent.models import (
    AgentResponse,
    ConversationContext,
    Intent,
    ResponseCandidate,
    Sentiment,
    Urgency,
    ValidationLayer,
    ValidationResult,
)
from afriagent.brain.llm import BaseLLMProvider
from afriagent.observability import VALIDATION_PASS, VALIDATION_FAIL, get_tracer

log = get_logger(__name__)
tracer = get_tracer(__name__)


# ══════════════════════════════════════════════════════════════════
# Layer 1: Relevance Gate
# ══════════════════════════════════════════════════════════════════


async def relevance_gate(
    candidate: ResponseCandidate, context: ConversationContext
) -> ValidationLayer:
    """Check if the response actually addresses the customer's question."""
    start = time.time()
    issues: list[str] = []

    # Simple heuristic: response should contain keywords from the question
    question_words = set(context.current_message.content.lower().split())
    response_words = set(candidate.content.lower().split())

    # Remove common stop words
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "can", "shall",
        "i", "you", "he", "she", "it", "we", "they", "me", "him",
        "her", "us", "them", "my", "your", "his", "its", "our",
        "their", "this", "that", "these", "those", "and", "or",
        "but", "in", "on", "at", "to", "for", "of", "with", "by",
        "from", "as", "into", "through", "during", "before", "after",
        "above", "below", "between", "under", "again", "further",
        "then", "once", "not", "no", "nor", "so", "if", "about",
    }
    meaningful_q = question_words - stop_words
    meaningful_r = response_words - stop_words

    overlap = meaningful_q & meaningful_r
    if meaningful_q and len(overlap) / len(meaningful_q) < 0.1:
        # Very low overlap — might be off-topic
        issues.append("Response may not address the customer's question")

    score = min(1.0, len(overlap) / max(len(meaningful_q), 1) + 0.3)

    return ValidationLayer(
        layer_name="relevance_gate",
        passed=len(issues) == 0,
        score=score,
        issues=issues,
        processing_time_ms=(time.time() - start) * 1000,
    )


# ══════════════════════════════════════════════════════════════════
# Layer 2: Safety Filter
# ══════════════════════════════════════════════════════════════════

# Patterns that should NEVER appear in customer support responses
UNSAFE_PATTERNS = [
    r"(?i)\b(hack|crack|exploit|bypass)\b.*(?i)\b(system|server|password)\b",
    r"(?i)\b(suicide|kill\s*(your)?self|self[\s-]harm)\b",
    r"(?i)\b(hate|destroy|attack)\b.*(?i)\b(group|race|religion|ethnic)\b",
    r"(?i)\b(illegal|criminal|fraud)\b.*(?i)\b(how\s+to|instructions)\b",
]

# Sensitive data patterns that must be redacted
SENSITIVE_PATTERNS = [
    (r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b", "[CARD_REDACTED]"),  # Credit card
    (r"\b\d{3}-\d{2}-\d{4}\b", "[SSN_REDACTED]"),  # SSN
    (r"(?i)password\s*[:=]\s*\S+", "password=[REDACTED]"),
]


async def safety_filter(
    candidate: ResponseCandidate, context: ConversationContext
) -> ValidationLayer:
    """Ensure response contains no harmful content or leaked sensitive data."""
    start = time.time()
    issues: list[str] = []
    content = candidate.content

    # Check for unsafe patterns
    for pattern in UNSAFE_PATTERNS:
        if re.search(pattern, content):
            issues.append(f"Unsafe content detected: {pattern}")

    # Check for sensitive data leaks
    for pattern, _ in SENSITIVE_PATTERNS:
        if re.search(pattern, content):
            issues.append("Sensitive data pattern detected in response")

    return ValidationLayer(
        layer_name="safety_filter",
        passed=len(issues) == 0,
        score=1.0 if len(issues) == 0 else 0.0,
        issues=issues,
        processing_time_ms=(time.time() - start) * 1000,
    )


# ══════════════════════════════════════════════════════════════════
# Layer 3: Tone Checker
# ══════════════════════════════════════════════════════════════════

FORMAL_MARKERS = ["dear", "sir", "madam", "regards", "sincerely", "hereby"]
CASUAL_MARKERS = ["hey", "yo", "sup", "lol", "omg", "btw", "gonna", "wanna"]
AGGRESSIVE_MARKERS = ["must", "immediately", "failure to", "consequences", "warning"]


async def tone_checker(
    candidate: ResponseCandidate, context: ConversationContext
) -> ValidationLayer:
    """Verify tone matches the customer's emotional state and context."""
    start = time.time()
    issues: list[str] = []
    score = 1.0

    lower = candidate.content.lower()

    # Frustrated customers need empathetic tone
    if context.detected_sentiment in (Sentiment.FRUSTRATED, Sentiment.NEGATIVE):
        empathy_markers = [
            "understand", "sorry", "apolog", "frustrat", "hear you",
            "let me help", "resolve", "fix",
        ]
        has_empathy = any(m in lower for m in empathy_markers)
        if not has_empathy:
            issues.append("Response lacks empathy for frustrated customer")
            score -= 0.3

        # Aggressive tone with frustrated customer is bad
        if any(m in lower for m in AGGRESSIVE_MARKERS):
            issues.append("Aggressive tone used with frustrated customer")
            score -= 0.5

    # Critical urgency should have direct, action-oriented tone
    if context.detected_urgency == Urgency.CRITICAL:
        action_words = ["immediately", "right away", "now", "urgent", "priority"]
        if not any(w in lower for w in action_words):
            issues.append("Critical urgency but response lacks action-oriented language")
            score -= 0.2

    score = max(0.0, min(1.0, score))

    return ValidationLayer(
        layer_name="tone_checker",
        passed=score >= 0.5,
        score=score,
        issues=issues,
        processing_time_ms=(time.time() - start) * 1000,
    )


# ══════════════════════════════════════════════════════════════════
# Layer 4: Cultural Sensitivity Gate
# ══════════════════════════════════════════════════════════════════

# Culturally insensitive patterns for African markets
CULTURAL_RED_FLAGS = [
    (r"(?i)\b(third[\s-]world|underdeveloped|backward)\b", "Derogatory development terminology"),
    (r"(?i)\b(tribal|primitive|savage)\b", "Culturally insensitive language"),
    (r"(?i)\b(you\s+people|those\s+people|your\s+kind)\b", "Othering language"),
]


async def cultural_sensitivity_gate(
    candidate: ResponseCandidate, context: ConversationContext
) -> ValidationLayer:
    """Ensure response is culturally appropriate for African markets."""
    start = time.time()
    issues: list[str] = []

    for pattern, description in CULTURAL_RED_FLAGS:
        if re.search(pattern, candidate.content):
            issues.append(f"Cultural sensitivity issue: {description}")

    # Check for appropriate greetings based on context
    if context.detected_intent == Intent.GREETING:
        if context.detected_language == "sw":
            swahili_greetings = ["habari", "jambo", "karibu", "nzuri", "shikamoo"]
            if not any(g in candidate.content.lower() for g in swahili_greetings):
                issues.append("Missing appropriate Swahili greeting")

    return ValidationLayer(
        layer_name="cultural_sensitivity",
        passed=len(issues) == 0,
        score=1.0 if len(issues) == 0 else 0.3,
        issues=issues,
        processing_time_ms=(time.time() - start) * 1000,
    )


# ══════════════════════════════════════════════════════════════════
# Layer 5: Factual Consistency Gate
# ══════════════════════════════════════════════════════════════════


async def factual_consistency_gate(
    candidate: ResponseCandidate, context: ConversationContext,
    llm: BaseLLMProvider,
) -> ValidationLayer:
    """Use LLM to verify the response doesn't contradict known facts."""
    start = time.time()
    issues: list[str] = []

    # Build fact check prompt with known context
    known_facts = []
    if context.customer.active_services:
        for svc in context.customer.active_services:
            known_facts.append(f"Service: {svc.get('name', 'unknown')} - Status: {svc.get('status', 'unknown')}")
    if context.customer.open_tickets:
        for ticket in context.customer.open_tickets:
            known_facts.append(f"Ticket #{ticket.get('id')}: {ticket.get('subject', '')}")

    if known_facts:
        facts_text = "\n".join(known_facts)
        try:
            resp = await llm.generate([
                {
                    "role": "system",
                    "content": (
                        "You are a fact checker. Given these KNOWN FACTS about a customer:\n"
                        f"{facts_text}\n\n"
                        "Check if the AGENT RESPONSE contradicts any known facts. "
                        "Reply with ONLY: CONSISTENT or INCONSISTENT: <reason>"
                    ),
                },
                {"role": "user", "content": f"Agent response: {candidate.content}"},
            ])
            if "INCONSISTENT" in resp.content.upper():
                issues.append(f"Factual inconsistency: {resp.content}")
        except Exception as e:
            log.warning("Fact check failed", error=str(e))

    return ValidationLayer(
        layer_name="factual_consistency",
        passed=len(issues) == 0,
        score=1.0 if len(issues) == 0 else 0.2,
        issues=issues,
        processing_time_ms=(time.time() - start) * 1000,
    )


# ══════════════════════════════════════════════════════════════════
# Layer 6: Completeness Gate
# ══════════════════════════════════════════════════════════════════


async def completeness_gate(
    candidate: ResponseCandidate, context: ConversationContext
) -> ValidationLayer:
    """Ensure response fully addresses the customer's needs."""
    start = time.time()
    issues: list[str] = []
    score = 1.0

    content_lower = candidate.content.lower()

    # Billing questions should include amounts/next steps
    if context.detected_intent == Intent.BILLING:
        has_action = any(
            w in content_lower
            for w in ["pay", "invoice", "bill", "amount", "due", "link", "mpesa", "checkout"]
        )
        if not has_action:
            issues.append("Billing response lacks actionable next steps")
            score -= 0.3

    # Technical issues should include troubleshooting steps
    if context.detected_intent == Intent.TECHNICAL:
        has_steps = any(
            w in content_lower
            for w in ["try", "step", "first", "then", "next", "check", "go to", "click"]
        )
        if not has_steps and len(candidate.content) < 100:
            issues.append("Technical response may be too brief")
            score -= 0.2

    # Sales inquiries should include pricing/feature info
    if context.detected_intent == Intent.SALES:
        has_info = any(
            w in content_lower
            for w in ["price", "plan", "feature", "include", "offer", "ksh", "usd", "$"]
        )
        if not has_info:
            issues.append("Sales response lacks pricing/feature information")
            score -= 0.2

    # Escalation should include timeline/next steps
    if context.detected_intent == Intent.ESCALATION:
        has_escalation_info = any(
            w in content_lower
            for w in ["escalat", "manager", "supervisor", "team", "follow up", "contact"]
        )
        if not has_escalation_info:
            issues.append("Escalation response lacks clear next steps")
            score -= 0.3

    score = max(0.0, score)

    return ValidationLayer(
        layer_name="completeness_gate",
        passed=score >= 0.5,
        score=score,
        issues=issues,
        processing_time_ms=(time.time() - start) * 1000,
    )


# ══════════════════════════════════════════════════════════════════
# Layer 7: Length & Format Gate
# ══════════════════════════════════════════════════════════════════


async def length_format_gate(
    candidate: ResponseCandidate, context: ConversationContext
) -> ValidationLayer:
    """Ensure appropriate length and formatting for the channel."""
    start = time.time()
    issues: list[str] = []
    score = 1.0

    content = candidate.content
    word_count = len(content.split())

    # WhatsApp messages should be concise
    if context.current_message.channel.value == "whatsapp":
        if word_count > 200:
            issues.append(f"WhatsApp response too long ({word_count} words, max ~200)")
            score -= 0.2
        # WhatsApp doesn't render markdown well
        if "**" in content or "```" in content:
            issues.append("Markdown formatting in WhatsApp message")
            score -= 0.1

    # Check for empty or trivially short responses
    if word_count < 3:
        issues.append("Response is too short")
        score -= 0.5

    # Check for excessively long responses
    if word_count > 500:
        issues.append(f"Response very long ({word_count} words)")
        score -= 0.1

    # Check for broken formatting
    if content.count("**") % 2 != 0:
        issues.append("Unclosed bold markdown")
        score -= 0.1
    if content.count("`") % 2 != 0:
        issues.append("Unclosed code markdown")
        score -= 0.1

    score = max(0.0, min(1.0, score))

    return ValidationLayer(
        layer_name="length_format",
        passed=score >= 0.5,
        score=score,
        issues=issues,
        processing_time_ms=(time.time() - start) * 1000,
    )


# ══════════════════════════════════════════════════════════════════
# Layer 8: Emotional Alignment Gate
# ══════════════════════════════════════════════════════════════════


async def emotional_alignment_gate(
    candidate: ResponseCandidate, context: ConversationContext
) -> ValidationLayer:
    """Verify emotional alignment between customer state and response."""
    start = time.time()
    issues: list[str] = []
    score = 1.0

    sentiment = context.detected_sentiment
    content = candidate.content.lower()

    if sentiment == Sentiment.FRUSTRATED:
        # Response MUST acknowledge frustration
        acknowledgment = [
            "sorry", "apolog", "understand", "frustrat", "inconvenien",
            "hear you", "empathize", "regret",
        ]
        if not any(a in content for a in acknowledgment):
            issues.append("Frustrated customer not receiving empathetic acknowledgment")
            score -= 0.4

        # Should NOT be dismissive
        dismissive = ["just", "simply", "easy", "obvious", "clearly", "you should have"]
        if any(d in content for d in dismissive):
            issues.append("Dismissive language used with frustrated customer")
            score -= 0.3

    elif sentiment == Sentiment.POSITIVE:
        # Match positive energy
        positive_match = ["glad", "great", "wonderful", "happy", "pleased", "fantastic"]
        if not any(p in content for p in positive_match) and context.detected_intent != Intent.TECHNICAL:
            issues.append("Positive customer sentiment not matched in response")
            score -= 0.1  # Minor issue

    score = max(0.0, min(1.0, score))

    return ValidationLayer(
        layer_name="emotional_alignment",
        passed=score >= 0.5,
        score=score,
        issues=issues,
        processing_time_ms=(time.time() - start) * 1000,
    )


# ══════════════════════════════════════════════════════════════════
# Layer 9: Escalation Gate
# ══════════════════════════════════════════════════════════════════

ESCALATION_TRIGGERS = [
    "speak to a human",
    "talk to someone",
    "get me a manager",
    "this is ridiculous",
    "i want a refund",
    "i'm canceling",
    "legal action",
    "lawyer",
    "sue",
    "report you",
    "ombudsman",
]


async def escalation_gate(
    candidate: ResponseCandidate, context: ConversationContext
) -> ValidationLayer:
    """Determine if the conversation should be escalated to a human agent."""
    start = time.time()
    issues: list[str] = []
    suggestions: list[str] = []
    score = 1.0

    # Explicit escalation request
    msg_lower = context.current_message.content.lower()
    if any(trigger in msg_lower for trigger in ESCALATION_TRIGGERS):
        suggestions.append("Customer explicitly requesting escalation — route to human agent")
        score -= 0.3

    # Repeated frustration across messages
    frustrated_count = sum(
        1 for m in context.message_history[-5:]
        if any(f in m.content.lower() for f in ["angry", "frustrated", "terrible", "worst"])
    )
    if frustrated_count >= 2:
        suggestions.append("Repeated frustration detected — consider escalation")
        score -= 0.2

    # Critical urgency
    if context.detected_urgency == Urgency.CRITICAL:
        suggestions.append("Critical urgency — immediate human escalation recommended")
        score -= 0.2

    # Multiple failed resolution attempts
    if len(context.message_history) > 6:
        agent_msgs = [m for m in context.message_history if m.role.value == "agent"]
        if len(agent_msgs) >= 3:
            suggestions.append("Multiple agent responses without resolution — consider escalation")
            score -= 0.1

    score = max(0.0, min(1.0, score))

    return ValidationLayer(
        layer_name="escalation_gate",
        passed=score >= 0.3,  # More lenient — escalation is a signal, not a failure
        score=score,
        issues=issues,
        suggestions=suggestions,
        processing_time_ms=(time.time() - start) * 1000,
    )


# ══════════════════════════════════════════════════════════════════
# Validation Pipeline Orchestrator
# ══════════════════════════════════════════════════════════════════


class ResponseValidator:
    """Runs all 9 validation layers and produces a composite result."""

    def __init__(self, llm: BaseLLMProvider) -> None:
        self.llm = llm

    async def validate(
        self,
        candidate: ResponseCandidate,
        context: ConversationContext,
    ) -> ValidationResult:
        """Run the full 9-layer validation pipeline."""
        with tracer.start_as_current_span("validator.validate") as span:
            start = time.time()
            layers: list[ValidationLayer] = []
            all_issues: list[str] = []

            # Run layers (some can be parallelized, but sequential for clarity)
            layers.append(await relevance_gate(candidate, context))
            layers.append(await safety_filter(candidate, context))
            layers.append(await tone_checker(candidate, context))
            layers.append(await cultural_sensitivity_gate(candidate, context))
            layers.append(await factual_consistency_gate(candidate, context, self.llm))
            layers.append(await completeness_gate(candidate, context))
            layers.append(await length_format_gate(candidate, context))
            layers.append(await emotional_alignment_gate(candidate, context))
            layers.append(await escalation_gate(candidate, context))

            # Aggregate results
            for layer in layers:
                all_issues.extend(layer.issues)
                if layer.passed:
                    VALIDATION_PASS.labels(layer=layer.layer_name).inc()
                else:
                    VALIDATION_FAIL.labels(layer=layer.layer_name).inc()

            # Composite score: weighted average
            weights = {
                "relevance_gate": 0.15,
                "safety_filter": 0.20,      # Critical
                "tone_checker": 0.10,
                "cultural_sensitivity": 0.15,  # Critical
                "factual_consistency": 0.10,
                "completeness_gate": 0.10,
                "length_format": 0.05,
                "emotional_alignment": 0.10,
                "escalation_gate": 0.05,
            }

            total_score = sum(
                layer.score * weights.get(layer.layer_name, 0.1)
                for layer in layers
            )

            # Critical layers must pass
            critical_layers = {"safety_filter", "cultural_sensitivity"}
            critical_failed = any(
                not layer.passed for layer in layers if layer.layer_name in critical_layers
            )

            total_time = (time.time() - start) * 1000
            passed = not critical_failed and total_score >= settings.confidence_threshold

            result = ValidationResult(
                passed=passed,
                final_score=round(total_score, 3),
                layers=layers,
                issues=all_issues,
                processing_time_ms=round(total_time, 2),
            )

            log.info(
                "Validation complete",
                passed=passed,
                score=total_score,
                issues=len(all_issues),
                time_ms=round(total_time, 2),
            )

            return result
