"""Self-Improvement Engine — Few-shot learning from validated interactions.

Extended to also log coordinator dispatch decisions for self-improvement
and prepare data for future fine-tuning.
"""

from __future__ import annotations

from typing import Any

from afriagent.config import settings
from afriagent.config.logging import get_logger
from afriagent.models import (
    AgentResponse,
    ConversationContext,
    DispatchPlan,
    LearningExample,
)
from afriagent.memory import MemoryManager

log = get_logger(__name__)


class LearningEngine:
    """Captures high-quality interactions and uses them for few-shot learning.

    The flow:
    1. After each validated response, evaluate if it's good enough to learn from
    2. Store positive examples in Postgres (episodic memory)
    3. Embed and store in Qdrant (semantic memory) for retrieval
    4. Retrieve similar examples as few-shot prompts for future responses

    Extended:
    5. Log coordinator dispatch plans + outcomes to coordinator_decisions table
    6. Provide data export for fine-tuning prep
    """

    def __init__(self, memory: MemoryManager) -> None:
        self.memory = memory

    async def capture_interaction(
        self,
        context: ConversationContext,
        response: AgentResponse,
    ) -> bool:
        """Capture a validated interaction for learning.

        Returns True if the example was stored.
        """
        if not settings.learning_enabled:
            return False

        # Only learn from high-confidence, non-escalated interactions
        if response.confidence < settings.min_confidence_for_learning:
            log.debug("Skipping learning: low confidence", confidence=response.confidence)
            return False

        if response.escalated:
            log.debug("Skipping learning: escalated")
            return False

        # Check if customer was satisfied (if we have feedback)
        satisfaction = response.metadata.get("satisfaction_score")

        example = LearningExample(
            conversation_id=response.conversation_id,
            customer_message=context.current_message.translated_content
            or context.current_message.content,
            agent_response=response.content,
            intent=context.detected_intent,
            sentiment=context.detected_sentiment,
            confidence=response.confidence,
            satisfaction_score=satisfaction,
        )

        try:
            # Store in Postgres
            await self.memory.episodic.save_learning_example(
                example.model_dump()
            )

            # Store in Qdrant for semantic retrieval
            try:
                await self.memory.semantic.store_pattern(
                    pattern_id=example.id,
                    vector=[],  # Will be populated by the Brain's persist step
                    payload={
                        "question": example.customer_message,
                        "answer": example.agent_response,
                        "intent": example.intent.value,
                        "confidence": example.confidence,
                        "type": "learning_example",
                    },
                )
            except Exception:
                pass  # Non-critical

            log.info(
                "Learning example captured",
                example_id=example.id,
                intent=example.intent.value,
                confidence=example.confidence,
            )
            return True

        except Exception as e:
            log.error("Failed to capture learning example", error=str(e))
            return False

    async def log_coordinator_decision(
        self,
        plan: DispatchPlan,
        outcome_confidence: float,
        replan_count: int,
        escalated: bool,
        conversation_id: str = "",
    ) -> bool:
        """Log a coordinator dispatch decision for analysis and self-improvement.

        This data is used by:
        - The weekly clustering job to find low-confidence patterns
        - The finetune_prep script to export training data
        - The self-model to track coordinator accuracy
        """
        try:
            import json
            from datetime import datetime, timezone

            record = {
                "conversation_id": conversation_id,
                "intent": plan.intent,
                "plan_json": json.dumps(plan.model_dump(), default=str),
                "outcome_confidence": outcome_confidence,
                "replan_count": replan_count,
                "escalated": escalated,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

            # Store in Postgres via episodic memory
            # Uses a dedicated table or the existing learning_examples table
            # with a different type marker
            await self.memory.episodic.save_learning_example({
                "id": f"coord-{conversation_id}-{datetime.now(timezone.utc).timestamp()}",
                "conversation_id": conversation_id,
                "customer_message": f"[COORDINATOR_DECISION] intent={plan.intent}",
                "agent_response": json.dumps(plan.model_dump(), default=str),
                "intent": plan.intent,
                "sentiment": "neutral",
                "confidence": outcome_confidence,
                "satisfaction_score": None,
            })

            log.info(
                "Coordinator decision logged",
                intent=plan.intent,
                confidence=outcome_confidence,
                replans=replan_count,
                escalated=escalated,
            )
            return True

        except Exception as e:
            log.error("Failed to log coordinator decision", error=str(e))
            return False

    async def get_few_shot_examples(
        self, intent: str, limit: int | None = None
    ) -> list[dict[str, str]]:
        """Retrieve high-quality examples for few-shot prompting.

        Returns list of {"customer_message": ..., "agent_response": ...}
        """
        limit = limit or settings.few_shot_examples_limit

        try:
            examples = await self.memory.episodic.get_learning_examples(
                intent, limit=limit
            )
            if examples:
                log.debug(
                    "Retrieved few-shot examples",
                    intent=intent,
                    count=len(examples),
                )
            return examples
        except Exception as e:
            log.warning("Failed to retrieve few-shot examples", error=str(e))
            return []

    async def get_stats(self) -> dict[str, Any]:
        """Get learning statistics."""
        return {
            "enabled": settings.learning_enabled,
            "min_confidence": settings.min_confidence_for_learning,
            "max_examples_per_intent": settings.few_shot_examples_limit,
        }
