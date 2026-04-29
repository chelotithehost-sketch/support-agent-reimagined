# docs/AGENT_CONTRACTS.md
# ───────────────────────────────────────────────
# Formal contracts for each agent in the pipeline.
# Copy into: docs/AGENT_CONTRACTS.md

# Agent Contracts

Each agent in the pipeline has a strict contract: defined inputs, outputs, responsibilities, and failure modes. If an agent violates its contract, the pipeline must detect it and handle the failure.

---

## 1. Perceiver (`agents/core/perceiver.py`)

**Responsibility:** Extract structured signals from raw customer messages.

| Field | Type | Description |
|-------|------|-------------|
| **Input** | `str` (raw message) + `WorkingMemory` | Customer's message and conversation history |
| **Output** | `Perception` | Intent, product area, urgency, emotional state, entities |
| **Latency budget** | < 50ms | Rule-based, no LLM call |
| **Failure mode** | Returns `Intent.UNKNOWN` with `confidence=0.0` | Downstream Reasoner handles via clarification |

**Invariants:**
- `confidence` must be in `[0.0, 1.0]`
- `emotional_intensity` must be in `[0.0, 1.0]`
- If `contains_threat=True`, then `emotional_state` must be `ANGRY` or `FRUSTRATED`
- If `is_escalation_request=True`, urgency must be at least `HIGH`

**Fast-path rules (no LLM):**
- Billing keywords → `Intent.BILLING_QUERY`
- Human/supervisor request → `is_escalation_request=True`
- Legal language → `contains_legal_language=True`
- Threat language → `contains_threat=True`

---

## 2. Router (`agents/core/router.py`)

**Responsibility:** Select the cheapest capable LLM for the task.

| Field | Type | Description |
|-------|------|-------------|
| **Input** | `RoutingTask` | Required capabilities, urgency, emotional state, estimated tokens |
| **Output** | `ModelSelection` | Provider, model, score, reason, estimated cost/latency |
| **Latency budget** | < 10ms | Pure scoring, no network calls |
| **Failure mode** | Falls back to local Ollama model | Always returns a selection |

**Scoring formula:**
```
score = (capability_match × 0.40)
      + (cost_efficiency × 0.25)
      + (latency_score × 0.15)
      + (historical_csat × 0.20)
```

**Invariants:**
- If all cloud providers are circuit-broken, MUST return local Ollama
- `estimated_cost_usd` must be non-negative
- For `Urgency.CRITICAL`: skip cost optimization, prioritize latency
- For `EmotionalState.ANGRY` + `contains_legal_language`: prefer models with higher CSAT

---

## 3. Reasoner (`agents/core/reasoner.py`)

**Responsibility:** Construct the complete context package for response generation.

| Field | Type | Description |
|-------|------|-------------|
| **Input** | `Perception` + `WorkingMemory` + `CustomerContext` + `SemanticResults` | All available context |
| **Output** | `ReasoningPackage` | Resolution path, tone, format, constraints, KB chunks |
| **Latency budget** | < 20ms | Assembly only, no LLM call |
| **Failure mode** | Returns `needs_clarification=True` with a question | Never returns empty package |

**Decision logic:**
```
if confidence < 0.60 AND clarifications_asked < max_clarifications:
    → needs_clarification = True
elif confidence < 0.60 AND clarifications_asked >= max_clarifications:
    → needs_clarification = False (attempt resolution anyway)
else:
    → build resolution path
```

**Invariants:**
- `tone_instruction` must be non-empty
- `must_avoid` must include at least `["I cannot help you"]`
- If `perception.contains_threat=True`: `must_include` must contain `"empathy statement"`
- If `perception.is_escalation_request=True`: `escalation_recommended=True`
- `clarification_question` must end with `?` when set

---

## 4. Drafter (`agents/core/drafter.py`)

**Responsibility:** Generate a candidate response using the selected LLM.

| Field | Type | Description |
|-------|------|-------------|
| **Input** | customer message + `ReasoningPackage` + `Perception` + `ModelSelection` | Everything needed to write a response |
| **Output** | `DraftResult` | Response text, confidence, metadata, cost |
| **Latency budget** | 1-30s (LLM-dependent) | This is the expensive step |
| **Failure mode** | Raises `LLMTimeoutError` or returns low-confidence draft | Pipeline retries with fallback model |

**Invariants:**
- `response_text` must be non-empty
- `cost_usd` must match actual API usage
- `tokens_used` must be > 0
- System prompt must include all `must_include` items as instructions
- System prompt must include all `must_avoid` items as negative constraints

---

## 5. Validator (`agents/core/validator.py`)

**Responsibility:** Ensure response quality across 9 dimensions before delivery.

| Field | Type | Description |
|-------|------|-------------|
| **Input** | draft text + `ReasoningPackage` + `Perception` + optional `WorkingMemory` | Draft and all context |
| **Output** | `ValidationResult` | Pass/fail per layer, revision suggestions, escalation flags |
| **Latency budget** | < 100ms | Pattern matching + heuristics, no LLM |
| **Failure mode** | Returns `passed=False` with `escalation_required=True` | Pipeline escalates to human |

**Layer order and blocking:**
| # | Layer | Blocking | Failure action |
|---|-------|----------|----------------|
| 1 | Safety | ✅ | Immediate escalation, no retry |
| 2 | Accuracy | ✅ | Revise with missing info |
| 3 | Completeness | ✅ | Revise with missing elements |
| 4 | Emotional Alignment | ✅ | Revise with tone correction |
| 5 | Clarity | ❌ | Log, continue |
| 6 | Brand | ❌ | Log, continue |
| 7 | Legal | ✅ if legal-flagged | Escalate to human |
| 8 | Escalation | ✅ | Route to human queue |
| 9 | Cost | ❌ | Trim if possible |

**Invariants:**
- If Safety fails → `escalation_required=True`, no revision attempt
- If any blocking layer fails → `passed=False`
- `revision_count` tracks how many times the draft was revised (max 2 before escalation)
- `final_draft` is set only when `passed=True`

---

## 6. Transmitter (`agents/core/transmitter.py`)

**Responsibility:** Format and deliver the validated response to the customer's channel.

| Field | Type | Description |
|-------|------|-------------|
| **Input** | validated text + channel + `Perception` | Final text and delivery context |
| **Output** | `OutboundMessage` | Formatted text, metadata, delivery status |
| **Latency budget** | < 500ms (network) | WhatsApp/Telegram API call |
| **Failure mode** | Retries 3x, then queues for manual delivery | Never silently drops a message |

**Channel constraints:**
| Channel | Max length | Formatting | Special |
|---------|-----------|------------|---------|
| WhatsApp | 4096 chars | Bold, italic, lists | Template messages for proactive |
| Telegram | 4096 chars | Full markdown | Inline keyboards for options |
| Email | No limit | Full HTML | Subject line required |
| Web | No limit | Markdown | Supports streaming |

**Invariants:**
- `outbound.text` must be non-empty
- Text must not exceed channel max length (trim or split if needed)
- If `perception.is_escalation_request=True`: must include human handoff indicator
- Typing indicator must be sent within 500ms of message receipt (WhatsApp requirement)

---

## Pipeline Invariants

These hold across the entire pipeline:

1. **A message is never silently dropped.** Every customer message gets a response, an escalation, or a queued retry.
2. **Cost is tracked per-turn.** Every LLM call reports `cost_usd`, and the daily budget is enforced.
3. **Escalation is one-way.** Once escalated to human, the AI does not resume without human approval.
4. **Revision limit = 2.** If the Validator fails the same draft twice, escalate rather than loop forever.
5. **Latency budget = 35s total.** From message receipt to response delivery. If exceeded, send a holding message and continue processing.
