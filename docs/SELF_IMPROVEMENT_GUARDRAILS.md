# Self-Improvement Guardrails

The learning loop has strict controls to prevent quality degradation.

## Learning Flow

```
Validated Response
    ↓
Confidence Check (>= 0.8) → skip if low confidence
    ↓
Escalation Check → skip if escalated
    ↓
Store in Postgres (learning_examples table)
    ↓
Embed and store in Qdrant (semantic memory)
    ↓
Future responses retrieve as few-shot examples
```

## Guardrails Currently In Place

### 1. Confidence Threshold
- Only interactions with `confidence >= AFRI_MIN_CONFIDENCE_FOR_LEARNING` (default: 0.8) are captured
- Low-confidence responses are logged but NOT used for learning
- This prevents the system from learning from uncertain or potentially wrong answers

### 2. Escalation Exclusion
- Escalated conversations are NEVER used for learning
- If the system couldn't resolve it, it shouldn't learn from the attempt
- Escalation reasons are logged for analysis but not for training

### 3. Validation Score Requirement
- Only responses that pass the 9-layer validation pipeline are eligible
- If any critical layer (safety, cultural sensitivity) fails, the response is excluded
- This ensures the system doesn't learn unsafe or culturally inappropriate patterns

### 4. Coordinator Decision Logging
- Every dispatch plan + outcome is logged separately
- Used for:
  - Weekly clustering to find low-confidence patterns
  - Fine-tuning data export (`scripts/finetune_prep.py`)
  - Self-model accuracy tracking
- NOT used for automatic self-modification

### 5. No Auto-Push
- Learning examples are stored but NOT automatically applied
- Few-shot retrieval selects from validated examples only
- Model fine-tuning requires human review and manual deployment
- The system improves its prompts, not its weights

## What Could Go Wrong (and How We Prevent It)

| Risk | Prevention | Detection |
|------|------------|-----------|
| Learning from wrong answers | Confidence threshold + validation requirement | Nightly eval suite regression check |
| Cultural drift | Cultural sensitivity layer is CRITICAL (blocks learning) | Weekly review of flagged responses |
| Safety degradation | Safety filter is CRITICAL (blocks learning immediately) | Prometheus alerting on safety failures |
| Reward hacking | Confidence is computed by validation, not self-reported | Cross-validation with CSAT when available |
| Distribution shift | Few-shot examples are intent-filtered (only similar examples) | Monitor intent accuracy over time |
| Stale examples | Examples include timestamps; older examples deprioritized | Periodic review of learning_examples table |

## Future Improvements

### Planned
- [ ] **CSAT integration**: Weight examples by customer satisfaction score when available
- [ ] **Human correction tracking**: When a human agent overrides the AI, log the correction and exclude the AI response from learning
- [ ] **A/B testing**: Compare prompt versions with different few-shot example sets
- [ ] **Automatic regression rollback**: If nightly eval scores drop >10%, revert to previous few-shot set

### Under Consideration
- [ ] **LLM-as-judge**: Use a separate LLM to evaluate response quality before learning
- [ ] **Peer review**: Cross-validate learning examples across similar businesses
- [ ] **Temporal weighting**: Recent examples weighted higher than older ones
