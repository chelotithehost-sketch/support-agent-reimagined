"""Brain — Response generation + validation orchestrator.

The Brain takes an enriched ConversationContext from the Perceiver,
generates a candidate response via LLM, runs it through the 9-layer
validation pipeline, and returns a validated AgentResponse.
"""

from __future__ import annotations

from typing import Any

from afriagent.config import settings
from afriagent.config.logging import get_logger
from afriagent.models import (
    AgentResponse,
    ConversationContext,
    Intent,
    MessageRole,
    ResponseCandidate,
    Sentiment,
    Urgency,
)
from afriagent.brain.llm import BaseLLMProvider, LLMResponse
from afriagent.brain.validator import ResponseValidator
from afriagent.memory import MemoryManager
from afriagent.observability import get_tracer

log = get_logger(__name__)
tracer = get_tracer(__name__)


# ── System Prompts ────────────────────────────────────────────────

SYSTEM_PROMPT_BASE = """You are a professional, empathetic customer support agent for an African technology company.

CORE PRINCIPLES:
- Be warm, respectful, and solution-oriented
- Use clear, simple language (avoid jargon)
- Acknowledge the customer's feelings before solving the problem
- Provide concrete next steps, not vague promises
- If you don't know something, say so honestly
- For M-Pesa payments, always provide step-by-step instructions

RESPONSE FORMAT:
- Keep responses concise (under 150 words for WhatsApp)
- Use numbered steps for troubleshooting
- End with a clear call-to-action or confirmation question
- Do NOT use markdown formatting (no **bold**, no ```code blocks```)
"""

LANGUAGE_INSTRUCTIONS = {
    "sw": "Respond in Swahili. Use polite, respectful language (e.g., 'Karibu', 'Asante').",
    "fr": "Respond in French. Use 'vous' form for formality.",
    "en": "Respond in English. Use clear, simple sentences.",
    "ha": "Respond in Hausa. Use respectful greetings.",
    "yo": "Respond in Yoruba. Use appropriate honorifics.",
}

INTENT_PROMPTS = {
    Intent.BILLING: """BILLING CONTEXT:
- Always check if M-Pesa is available as a payment method
- Provide exact amounts in KSH when possible
- Include payment deadlines and consequences of late payment
- Offer payment plan options if the customer seems unable to pay""",
    Intent.TECHNICAL: """TECHNICAL CONTEXT:
- Ask for specific error messages or screenshots
- Provide numbered troubleshooting steps
- Offer to escalate to technical team if issue persists
- Set expectations for resolution timeline""",
    Intent.SALES: """SALES CONTEXT:
- Highlight value propositions relevant to African businesses
- Mention local support and understanding of market needs
- Provide pricing in local currency when possible
- Offer a free trial or demo if available""",
    Intent.ESCALATION: """ESCALATION CONTEXT:
- Acknowledge the customer's frustration immediately
- Explain the escalation process clearly
- Provide a timeline for when they'll hear back
- Give them a reference number for tracking""",
    Intent.GREETING: """GREETING CONTEXT:
- Be warm but professional
- Ask how you can help today
- If returning customer, acknowledge their history""",
    Intent.COMPLAINT: """COMPLAINT CONTEXT:
- Lead with empathy and acknowledgment
- Do NOT be defensive or make excuses
- Focus on resolution, not explanation
- Offer compensation if appropriate""",
    Intent.GENERAL: """GENERAL CONTEXT:
- Be helpful and informative
- Ask clarifying questions if the request is unclear
- Provide relevant links or documentation""",
}


class Brain:
    """Response generation and validation orchestrator."""

    def __init__(
        self,
        llm: BaseLLMProvider,
        memory: MemoryManager,
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.validator = ResponseValidator(llm)

    async def generate_response(self, context: ConversationContext) -> AgentResponse:
        """Full Brain pipeline: context → response generation → validation → delivery."""
        with tracer.start_as_current_span("brain.generate_response") as span:
            span.set_attribute("intent", context.detected_intent.value)
            span.set_attribute("conversation_id", context.conversation_id)

            # 1. Generate candidate response
            candidate = await self._generate_candidate(context)

            # 2. Run through 9-layer validation
            validation = await self.validator.validate(candidate, context)

            # 3. If validation fails, attempt regeneration
            if not validation.passed:
                log.warning(
                    "Response failed validation",
                    score=validation.final_score,
                    issues=validation.issues,
                )
                # Try once more with feedback
                candidate = await self._generate_candidate(
                    context, feedback=validation.issues
                )
                validation = await self.validator.validate(candidate, context)

            # 4. Determine if escalation is needed
            escalated = False
            escalate_reason = ""
            for layer in validation.layers:
                if layer.layer_name == "escalation_gate" and layer.suggestions:
                    escalated = True
                    escalate_reason = "; ".join(layer.suggestions)
                    break

            # 5. Translate response if customer wrote in non-English
            final_content = candidate.content
            if context.detected_language != "en":
                final_content = await self._translate_response(
                    candidate.content, context.detected_language
                )

            # 6. Build final response
            response = AgentResponse(
                conversation_id=context.conversation_id,
                content=final_content,
                channel=context.current_message.channel,
                confidence=validation.final_score,
                validation=validation,
                intent_handled=context.detected_intent,
                escalated=escalated,
                escalate_reason=escalate_reason,
            )

            # 7. Persist to memory
            await self._persist_response(response, context)

            log.info(
                "Brain complete",
                conversation_id=context.conversation_id,
                confidence=response.confidence,
                escalated=escalated,
            )

            return response

    async def _generate_candidate(
        self,
        context: ConversationContext,
        feedback: list[str] | None = None,
    ) -> ResponseCandidate:
        """Generate a response candidate using LLM."""
        with tracer.start_as_current_span("brain.generate_candidate"):
            # Build messages for LLM
            messages: list[dict[str, str]] = []

            # System prompt
            system = SYSTEM_PROMPT_BASE
            system += "\n" + LANGUAGE_INSTRUCTIONS.get(
                context.detected_language, LANGUAGE_INSTRUCTIONS["en"]
            )
            system += "\n" + INTENT_PROMPTS.get(context.detected_intent, INTENT_PROMPTS[Intent.GENERAL])

            # Add business context if available
            if context.business_context:
                system += f"\n\nBUSINESS CONTEXT:\n{context.business_context}"

            # Add similar patterns as few-shot examples
            if context.similar_patterns:
                system += "\n\nSIMILAR RESOLUTIONS (for reference, not copy):"
                for i, pattern in enumerate(context.similar_patterns[:3], 1):
                    system += f"\n{i}. Q: {pattern.get('question', 'N/A')}"
                    system += f"\n   A: {pattern.get('answer', 'N/A')}"

            # Add validation feedback if regenerating
            if feedback:
                system += "\n\nIMPORTANT - Previous response had issues. Fix these:"
                for issue in feedback:
                    system += f"\n- {issue}"

            messages.append({"role": "system", "content": system})

            # Add conversation history
            for msg in context.message_history[-10:]:  # Last 10 messages
                role = "assistant" if msg.role == MessageRole.AGENT else "user"
                messages.append({"role": role, "content": msg.content})

            # Current message (use translation if available)
            current = context.current_message.translated_content or context.current_message.content
            messages.append({"role": "user", "content": current})

            # Generate
            response = await self.llm.generate(messages)

            return ResponseCandidate(
                content=response.content,
                confidence=0.8,  # Will be overridden by validation
                model_used=response.model,
                tokens_used=response.tokens_input + response.tokens_output,
                reasoning=f"Generated by {response.provider}/{response.model}",
            )

    async def _translate_response(self, content: str, target_lang: str) -> str:
        """Translate the response to the customer's language."""
        try:
            resp = await self.llm.generate([
                {
                    "role": "system",
                    "content": (
                        f"Translate the following customer support response to {target_lang}. "
                        "Keep the professional but warm tone. Return ONLY the translation."
                    ),
                },
                {"role": "user", "content": content},
            ])
            return resp.content
        except Exception as e:
            log.warning("Translation failed, using English", error=str(e))
            return content

    async def _persist_response(
        self, response: AgentResponse, context: ConversationContext
    ) -> None:
        """Save response to all memory tiers."""
        try:
            # Postgres — message record
            await self.memory.episodic.save_message({
                "id": f"resp-{response.conversation_id}",
                "conversation_id": response.conversation_id,
                "role": "agent",
                "content": response.content,
                "channel": response.channel.value,
                "created_at": response.metadata.get("timestamp"),
            })

            # Redis — update session
            session = await self.memory.session.get_session(response.conversation_id)
            if session:
                session["last_response"] = response.content
                session["confidence"] = response.confidence
                session["escalated"] = response.escalated
                await self.memory.session.set_session(
                    response.conversation_id, session
                )

            # Qdrant — store resolution pattern if high confidence
            if response.confidence >= 0.7 and not response.escalated:
                try:
                    vector = await self.llm.embed(
                        context.current_message.translated_content
                        or context.current_message.content
                    )
                    await self.memory.semantic.store_pattern(
                        pattern_id=response.conversation_id,
                        vector=vector,
                        payload={
                            "question": context.current_message.content,
                            "answer": response.content,
                            "intent": context.detected_intent.value,
                            "confidence": response.confidence,
                        },
                    )
                except Exception:
                    pass  # Non-critical

        except Exception as e:
            log.error("Failed to persist response", error=str(e))
