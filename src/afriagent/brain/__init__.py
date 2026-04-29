"""Brain — Agentic response generation with coordinator-driven dispatch loop.

The Brain now operates as a loop:
1. Coordinator dispatches a plan (small LLM, <100ms)
2. Steps are executed sequentially (tool calls or big LLM calls)
3. Low-confidence results trigger replanning
4. Self-model is updated in background after each turn

This replaces the old single-shot orchestration pattern.
"""

from __future__ import annotations

from typing import Any

from afriagent.config import settings
from afriagent.config.logging import get_logger
from afriagent.models import (
    AgentResponse,
    ConversationContext,
    DispatchPlan,
    DispatchStep,
    Intent,
    MessageRole,
    ResponseCandidate,
    Sentiment,
    Urgency,
    ValidationLayer,
    ValidationResult,
)
from afriagent.brain.llm import BaseLLMProvider, LLMResponse
from afriagent.brain.validator import ResponseValidator
from afriagent.coordinator import CoordinatorBrain
from afriagent.coordinator.replanner import StepResult, should_replan, should_escalate
from afriagent.self_model import SelfModelState, SelfModelUpdater, TurnMetrics
from afriagent.memory import MemoryManager
from afriagent.observability import get_tracer

log = get_logger(__name__)
tracer = get_tracer(__name__)

CONFIDENCE_THRESHOLD = 0.6


# ── System Prompts (for big LLM response generation) ─────────────

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
    "sheng": "Respond in Sheng (Kenyan urban slang). Be casual and friendly. Mix Swahili and English naturally.",
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
    """Agentic response generation and validation orchestrator.

    Now uses the coordinator for dispatch decisions and operates
    as a loop with replanning instead of single-shot generation.
    """

    def __init__(
        self,
        llm: BaseLLMProvider,
        memory: MemoryManager,
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.validator = ResponseValidator(llm)

        # Self-model (SQLite-backed)
        self._self_model_state = SelfModelState()
        self._self_model_updater = SelfModelUpdater(self._self_model_state)

        # Coordinator (small embedded LLM for dispatch)
        self._coordinator = CoordinatorBrain(
            tool_registry=None,  # Lazy-loaded from tools.registry
            get_self_model_state=self._self_model_state.get_state,
            get_provider_health=self._self_model_state.get_provider_health_dict,
        )

    async def generate_response(self, context: ConversationContext) -> AgentResponse:
        """Agentic Brain pipeline: coordinator dispatch → execute loop → validate → respond.

        This is the main entry point. It:
        1. Asks the coordinator for a DispatchPlan
        2. Executes each step in the plan (tool call or big LLM call)
        3. If a step returns low confidence, replans via coordinator
        4. Validates the final result through the 9-layer pipeline
        5. Updates the self-model in background
        """
        with tracer.start_as_current_span("brain.generate_response") as span:
            span.set_attribute("intent", context.detected_intent.value)
            span.set_attribute("conversation_id", context.conversation_id)

            # 1. Coordinator dispatch
            plan = await self._coordinator.dispatch(context)

            # 2. Execute steps in a loop with replanning
            result: StepResult | None = None
            replan_count = 0

            for step in plan.steps:
                result = await self._execute_step(step, context)

                # Check if we need to replan
                if should_replan(result, replan_count):
                    log.info(
                        "Step returned low confidence, replanning",
                        confidence=result.confidence,
                        step_tool=step.tool,
                        cycle=replan_count + 1,
                    )
                    plan = await self._coordinator.replan(
                        context, result, replan_count
                    )
                    replan_count += 1

                    # If replanner says escalate, break
                    if getattr(plan, "escalate", False):
                        break

                    # Re-execute with new plan (restart loop)
                    for new_step in plan.steps:
                        result = await self._execute_step(new_step, context)
                        if not should_replan(result, replan_count):
                            break
                    break

                # Check for forced escalation
                if should_escalate(result, replan_count):
                    log.info("Forced escalation", cycle=replan_count)
                    break

            # 3. Build response candidate from step results
            if result is None:
                # No steps executed — generate a fallback response
                candidate = await self._generate_candidate(context)
            else:
                candidate = ResponseCandidate(
                    content=result.content,
                    confidence=result.confidence,
                    model_used=result.provider_used or "tool",
                    tokens_used=0,
                    reasoning=f"Dispatch step: tool={result.step.tool}, provider={result.step.llm_provider}",
                )

            # 4. Run through 9-layer validation
            validation = await self.validator.validate(candidate, context)

            # 5. If validation fails, attempt regeneration with feedback
            if not validation.passed:
                log.warning(
                    "Response failed validation",
                    score=validation.final_score,
                    issues=validation.issues,
                )
                candidate = await self._generate_candidate(
                    context, feedback=validation.issues
                )
                validation = await self.validator.validate(candidate, context)

            # 6. Determine if escalation is needed
            escalated = getattr(plan, "escalate", False)
            escalate_reason = ""
            for layer in validation.layers:
                if layer.layer_name == "escalation_gate" and layer.suggestions:
                    escalated = True
                    escalate_reason = "; ".join(layer.suggestions)
                    break

            # 7. Translate response if customer wrote in non-English
            final_content = candidate.content
            if context.detected_language != "en":
                final_content = await self._translate_response(
                    candidate.content, context.detected_language
                )

            # 8. Build final response
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

            # 9. Persist to memory
            await self._persist_response(response, context)

            # 10. Update self-model in background (never block)
            self._self_model_updater.schedule_update(TurnMetrics(
                tool_used=result.step.tool if result else None,
                tool_success=result.success if result else True,
                llm_provider=result.provider_used if result else None,
                llm_latency_ms=result.latency_ms if result else 0.0,
                llm_success=result.success if result else True,
                validation_score=validation.final_score,
                detected_intent=context.detected_intent.value,
                conversation_id=context.conversation_id,
            ))

            log.info(
                "Brain complete",
                conversation_id=context.conversation_id,
                confidence=response.confidence,
                escalated=escalated,
                replan_cycles=replan_count,
            )

            return response

    async def _execute_step(
        self, step: DispatchStep, context: ConversationContext
    ) -> StepResult:
        """Execute a single dispatch step — either a tool call or an LLM call."""
        with tracer.start_as_current_span("brain.execute_step") as span:
            span.set_attribute("tool", step.tool or "llm")
            span.set_attribute("provider", step.llm_provider or "tool")

            if step.tool:
                return await self._execute_tool_step(step, context)
            else:
                return await self._execute_llm_step(step, context)

    async def _execute_tool_step(
        self, step: DispatchStep, context: ConversationContext
    ) -> StepResult:
        """Execute a tool call step."""
        import time
        start = time.time()

        try:
            # Import tool registry lazily
            from afriagent.tools.registry import TOOL_REGISTRY

            tool_meta = TOOL_REGISTRY.get(step.tool)
            if not tool_meta:
                return StepResult(
                    step=step,
                    content=f"Unknown tool: {step.tool}",
                    confidence=0.0,
                    success=False,
                    error=f"Tool '{step.tool}' not found in registry",
                )

            # Route to the appropriate tool client
            result_content = ""
            if step.tool in ("check_invoice", "create_support_ticket", "lookup_customer", "check_invoice_status"):
                # WHMCS tools — would call self.tools.whmcs in production
                result_content = f"[Tool {step.tool} executed — result would come from WHMCS API]"
            elif step.tool in ("mpesa_push", "mpesa_query_status"):
                result_content = f"[Tool {step.tool} executed — result would come from M-Pesa API]"
            elif step.tool == "check_domain_dns":
                from afriagent.tools.dns_check import get_dns_checker
                domain = step.params.get("domain", context.current_message.content)
                dns_result = await get_dns_checker().check_domain(domain)
                result_content = str(dns_result)
            else:
                result_content = f"[Tool {step.tool} — no handler configured]"

            latency = (time.time() - start) * 1000

            return StepResult(
                step=step,
                content=result_content,
                confidence=0.8,  # Tools are generally reliable
                success=True,
                latency_ms=latency,
            )

        except Exception as e:
            latency = (time.time() - start) * 1000
            log.error("Tool step failed", tool=step.tool, error=str(e))
            return StepResult(
                step=step,
                content="",
                confidence=0.0,
                success=False,
                error=str(e),
                latency_ms=latency,
            )

    async def _execute_llm_step(
        self, step: DispatchStep, context: ConversationContext
    ) -> StepResult:
        """Execute an LLM call step using the big LLM."""
        import time
        start = time.time()

        try:
            # Build the prompt for this step
            task = step.params.get("task", "generate_response")
            language = step.params.get("language", context.detected_language)

            messages = self._build_llm_messages(context, task, language)
            response = await self.llm.generate(messages)

            latency = (time.time() - start) * 1000

            return StepResult(
                step=step,
                content=response.content,
                confidence=0.8,  # Will be refined by validation
                success=True,
                provider_used=response.provider,
                latency_ms=latency,
            )

        except Exception as e:
            latency = (time.time() - start) * 1000
            log.error("LLM step failed", provider=step.llm_provider, error=str(e))
            return StepResult(
                step=step,
                content="",
                confidence=0.0,
                success=False,
                error=str(e),
                provider_used=step.llm_provider,
                latency_ms=latency,
            )

    def _build_llm_messages(
        self, context: ConversationContext, task: str, language: str
    ) -> list[dict[str, str]]:
        """Build messages for the big LLM based on the dispatch step task."""
        messages: list[dict[str, str]] = []

        # System prompt
        system = SYSTEM_PROMPT_BASE
        system += "\n" + LANGUAGE_INSTRUCTIONS.get(language, LANGUAGE_INSTRUCTIONS["en"])
        system += "\n" + INTENT_PROMPTS.get(context.detected_intent, INTENT_PROMPTS[Intent.GENERAL])

        # Add task-specific instruction
        task_instructions = {
            "generate_response": "",
            "ask_clarification": "The customer's message is unclear. Ask a specific, friendly clarifying question.",
            "de_escalate_and_acknowledge": "The customer is angry. Lead with empathy, acknowledge their frustration, and offer a concrete solution.",
            "greet_in_sheng": "Respond to this casual Sheng greeting warmly and naturally in Sheng.",
            "email_troubleshooting_swahili": "Provide email troubleshooting steps in Swahili. Be patient and clear.",
            "explain_dns_results_and_remediate": "Explain DNS check results and provide actionable remediation steps.",
            "explain_propagation_status": "Explain DNS propagation status and timeline in simple terms.",
            "sales_pitch_hosting_plans": "Describe hosting plans with pricing. Be enthusiastic but honest.",
            "confirm_payment_and_warn_about_suspension": "Confirm the payment and explain the suspension timeline.",
            "investigate_payment_mismatch": "Investigate why an M-Pesa payment isn't reflecting in the account.",
            "apologetic_escalation_message": "Apologize for the issue and explain that you're escalating to a human agent.",
        }
        task_instruction = task_instructions.get(task, "")
        if task_instruction:
            system += f"\n\nSPECIFIC TASK: {task_instruction}"

        # Add business context
        if context.business_context:
            system += f"\n\nBUSINESS CONTEXT:\n{context.business_context}"

        # Add similar patterns
        if context.similar_patterns:
            system += "\n\nSIMILAR RESOLUTIONS (for reference, not copy):"
            for i, pattern in enumerate(context.similar_patterns[:3], 1):
                system += f"\n{i}. Q: {pattern.get('question', 'N/A')}"
                system += f"\n   A: {pattern.get('answer', 'N/A')}"

        messages.append({"role": "system", "content": system})

        # Conversation history
        for msg in context.message_history[-10:]:
            role = "assistant" if msg.role == MessageRole.AGENT else "user"
            messages.append({"role": role, "content": msg.content})

        # Current message
        current = context.current_message.translated_content or context.current_message.content
        messages.append({"role": "user", "content": current})

        return messages

    async def _generate_candidate(
        self,
        context: ConversationContext,
        feedback: list[str] | None = None,
    ) -> ResponseCandidate:
        """Generate a response candidate using LLM (fallback when no step result)."""
        with tracer.start_as_current_span("brain.generate_candidate"):
            messages = self._build_llm_messages(context, "generate_response", context.detected_language)

            if feedback:
                # Inject feedback into system prompt
                system = messages[0]["content"]
                system += "\n\nIMPORTANT - Previous response had issues. Fix these:"
                for issue in feedback:
                    system += f"\n- {issue}"
                messages[0]["content"] = system

            response = await self.llm.generate(messages)

            return ResponseCandidate(
                content=response.content,
                confidence=0.8,
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
