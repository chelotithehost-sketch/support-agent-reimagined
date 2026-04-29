#!/usr/bin/env python3
"""Export coordinator decision logs to JSONL for QLoRA fine-tuning.

Usage:
    python scripts/finetune_prep.py [--output coordinator_finetune.jsonl] [--min-confidence 0.3] [--max-confidence 0.8]

This script:
1. Queries coordinator_decisions from Postgres
2. Filters by confidence range (default: low-confidence decisions that improved)
3. Formats as JSONL for QLoRA training
4. Outputs to a file ready for fine-tuning
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export coordinator decisions for fine-tuning"
    )
    parser.add_argument(
        "--output", "-o",
        default="coordinator_finetune.jsonl",
        help="Output JSONL file path",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.3,
        help="Minimum outcome confidence to include",
    )
    parser.add_argument(
        "--max-confidence",
        type=float,
        default=0.8,
        help="Maximum outcome confidence to include",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10000,
        help="Maximum number of examples to export",
    )
    parser.add_argument(
        "--include-escalated",
        action="store_true",
        help="Include escalated conversations",
    )
    return parser.parse_args()


async def export_decisions(args: argparse.Namespace) -> int:
    """Export coordinator decisions to JSONL format."""
    from afriagent.config import settings
    from afriagent.memory import MemoryManager

    memory = MemoryManager()

    try:
        # Connect to Postgres only
        await memory.episodic.init_tables()

        # Query learning examples that are coordinator decisions
        from sqlalchemy import select, text
        from afriagent.memory import LearningRecord

        async with memory.episodic.session as session:
            query = (
                select(LearningRecord)
                .where(LearningRecord.customer_message.like("[COORDINATOR_DECISION]%"))
                .where(LearningRecord.confidence >= args.min_confidence)
                .where(LearningRecord.confidence <= args.max_confidence)
                .order_by(LearningRecord.created_at.desc())
                .limit(args.limit)
            )

            result = await session.execute(query)
            records = result.scalars().all()

        count = 0
        with open(args.output, "w", encoding="utf-8") as f:
            for record in records:
                try:
                    # Parse the coordinator decision
                    plan_data = json.loads(record.agent_response)

                    # Build the training example
                    # Input: customer context + coordinator system prompt
                    # Output: the dispatch plan JSON
                    training_example = {
                        "messages": [
                            {
                                "role": "system",
                                "content": "You are a coordinator for an African web hosting support agent. Analyze the customer message and produce a dispatch plan in JSON.",
                            },
                            {
                                "role": "user",
                                "content": f"Customer message intent: {record.intent}\nOutcome confidence: {record.confidence}",
                            },
                            {
                                "role": "assistant",
                                "content": json.dumps(plan_data, indent=2),
                            },
                        ],
                        "metadata": {
                            "intent": record.intent,
                            "confidence": record.confidence,
                            "created_at": record.created_at.isoformat() if record.created_at else None,
                            "conversation_id": record.conversation_id,
                        },
                    }

                    f.write(json.dumps(training_example, default=str) + "\n")
                    count += 1

                except (json.JSONDecodeError, TypeError) as e:
                    print(f"Skipping malformed record: {e}", file=sys.stderr)
                    continue

        return count

    finally:
        await memory.episodic.close()


def main() -> None:
    args = parse_args()
    print(f"Exporting coordinator decisions to {args.output}...")
    print(f"Confidence range: [{args.min_confidence}, {args.max_confidence}]")

    import asyncio
    count = asyncio.run(export_decisions(args))

    print(f"Exported {count} examples to {args.output}")
    if count > 0:
        print(f"\nNext steps:")
        print(f"  1. Review the exported data: head -5 {args.output}")
        print(f"  2. Use with a QLoRA trainer (e.g., axolotl, unsloth)")
        print(f"  3. Fine-tune the coordinator model on these examples")
    else:
        print("No examples found. Run some conversations first to generate data.")


if __name__ == "__main__":
    main()
