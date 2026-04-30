# Agent Contracts

Every component in the pipeline has a strict contract: defined inputs, outputs, latency budgets, and failure modes. If a component violates its contract, the pipeline detects it and handles the failure.

---

## 1. Perceiver (`perceiver/__init__.py` → `Perceiver.process()`)

**Responsibility:** Enrich raw inbound messages with language, intent, sentiment, urgency, customer context, and conversation history.

| Field | Type | Description |
|-------|------|-------------|
| **Input** | `InboundMessage` | Raw message from channel adapter (channel, sender_id, content, metadata) |
| **Output** | `ConversationContext` | Enriched context with customer profile, history, NLU signals, similar patterns |
| **Latency budget** | < 50ms (classification) + vector search time | Rule-based classification is instant; vector search adds ~20ms |
| **Failure mode** | Raises `ValueError("Duplicate message")` for dedup; returns `Intent.GENERAL` with `confidence=0.0` on classification failure | Brain handles via coordinator fallback |

**Processing steps:**
1. Dedup check (Redis lock, TTL 10s)
2. Language detection (`language.py` — Sheng-aware, marker-based + langdetect fallback)
3. Translate to English if non-English (LLM call)
4. Intent classification (keyword matching)
5. Sentiment detection (marker counting)
6. Urgency detection (keyword + sentiment correlation)
7. Load customer profile (Redis cache → WHMCS API)
8. Load conversation history (Redis → Postgres)
9. Vector search for similar patterns (Qdrant)
10. Persist message to Postgres
11. Update session state in Redis

**Invariants:**
- `detected_language` is one of: `"en"`, `"sw"`, `"sheng"`, `"fr"`, `"ha"`, `"yo"`, `"other"`
- `detected_intent` is one of: `BILLING`, `TECHNICAL`, `SALES`, `GENERAL`, `ESCALATION`, `GREETING`, `COMPLAINT`
- If content contains escalation keywords (`"speak to human"`, `"manager"`, etc.), `detected_urgency` must be at least `HIGH`
- Duplicate messages within 10s window are rejected (Redis lock)

**Fast-path rules (no LLM):**
- `"invoice"`, `"payment"`, `"mpesa"` → `Intent.BILLING`
- `"down"`, `"error"`, `"not working"` → `Intent.TECHNICAL`
- `"manager"`, `"supervisor"`, `"human"` → `Intent.ESCALATION`
- `"hello"`, `"habari"`, `"jambo"` → `Intent.GREETING`

---

## 2. Coordinator (`coordinator/__init__.py` → `CoordinatorBrain.dispatch()`)

**Responsibility:** Analyze the enriched context and produce a `DispatchPlan` — an ordered list of tool calls and LLM calls.

| Field | Type | Description |
|-------|------|-------------|
| **Input** | `ConversationContext` | Enriched context from Perceiver |
| **Output** | `DispatchPlan` | intent, urgency, language, steps[], confidence, reasoning, escalate |
| **Latency budget** | < 100ms (local LLM) or < 10ms (fallback) | llama-cpp-python for intelligent dispatch; keyword fallback when unavailable |
| **Failure mode** | Falls back to keyword-based `_build_fallback_plan()` | Always returns a valid plan |

**Dispatch decision flow:**
```
if coordinator_enabled AND llama-cpp-python available:
    → generate DispatchPlan via local LLM (JSON output)
    → parse and validate JSON
    → if parse fails → fallback
else:
    → keyword-based intent detection
    → provider priority selection
    → build simple plan
```

**Invariants:**
- `steps` must be non-empty (at minimum one LLM response step)
- `confidence` must be in `[0.0, 1.0]`
- `intent` must be one of: `"billing"`, `"outage"`, `"general"`, `"hostile"`, `"unclear"`
- `urgency` must be in `[1, 5]`
- Max 4 steps per plan
- If `intent == "hostile"`, first step must be `create_support_ticket`
- If `confidence < 0.5`, must include a clarification LLM step

**Tool routing rules:**
- M-Pesa queries → `check_invoice` before `mpesa_push`
- DNS/hosting issues → `check_domain_dns` as first step
- Billing queries → `check_invoice` + `check_invoice_status`
- Hostile customers → `create_support_ticket` (High priority) + de-escalation LLM step

---

## 3. Brain (`brain/__init__.py` → `Brain.generate_response()`)

**Responsibility:** Execute the coordinator's dispatch plan, validate the result, and produce the final `AgentResponse`.

| Field | Type | Description |
|-------|------|-------------|
| **Input** | `ConversationContext` | Enriched context from Perceiver |
| **Output** | `AgentResponse` | Validated response content, confidence, validation result, escalation status |
| **Latency budget** | 1-30s (LLM-dependent) | Dominated by LLM call latency |
| **Failure mode** | Retries with replanning (max 3 cycles); escalates if all retries fail | Never returns an unvalidated response |

**Execution loop:**
```
plan = coordinator.dispatch(context)
for step in plan.steps:
    result = execute_step(step, context)
    if should_replan(result, replan_count):
        plan = coordinator.replan(context, result, replan_count)
        replan_count += 1
        continue  # re-execute with new plan
    if should_escalate(result, replan_count):
        break  # escalate to human
candidate = build_response_candidate(result)
validation = validator.validate(candidate, context)
if not validation.passed:
    candidate = regenerate_with_feedback(context, validation.issues)
    validation = validator.validate(candidate, context)
```

**Step execution:**
- Tool steps → route to appropriate tool client (WHMCS, M-Pesa, DNS)
- LLM steps → build messages with system prompt + intent context + conversation history → call LLM

**Invariants:**
- Every response passes through the 9-layer validation pipeline
- If validation fails twice, the response is escalated (never loops forever)
- Non-English responses are translated before delivery
- Responses are persisted to all memory tiers (Redis, Postgres, Qdrant)
- Self-model is updated in background (never blocks the hot path)

---

## 4. Validator (`brain/validator.py` → `ResponseValidator.validate()`)

**Responsibility:** Run the 9-layer validation pipeline and produce a composite `ValidationResult`.

| Field | Type | Description |
|-------|------|-------------|
| **Input** | `ResponseCandidate` + `ConversationContext` | Draft response and full context |
| **Output** | `ValidationResult` | passed (bool), final_score, layers[], issues[], processing_time_ms |
| **Latency budget** | < 100ms | Pattern matching + heuristics; factual_consistency uses LLM (~50ms) |
| **Failure mode** | Returns `passed=False` with issues list | Brain regenerates with feedback |

**Layer order, weights, and blocking:**

| # | Layer | Weight | Blocking | Failure action |
|---|-------|--------|----------|----------------|
| 1 | Relevance Gate | 0.15 | ❌ | Log, deduct score |
| 2 | Safety Filter | 0.20 | ✅ | Immediate fail, no retry |
| 3 | Tone Checker | 0.10 | ❌ | Revise with tone correction |
| 4 | Cultural Sensitivity | 0.15 | ✅ | Immediate fail for derogatory language |
| 5 | Factual Consistency | 0.10 | ❌ | Revise with missing facts |
| 6 | Completeness Gate | 0.10 | ❌ | Revise with missing elements |
| 7 | Length & Format | 0.05 | ❌ | Trim or reformat |
| 8 | Emotional Alignment | 0.10 | ❌ | Revise with empathy |
| 9 | Escalation Gate | 0.05 | ❌ | Signal for human routing |

**Composite score calculation:**
```
total_score = Σ(layer.score × weight)
critical_failed = safety_filter.failed OR cultural_sensitivity.failed
passed = NOT critical_failed AND total_score >= confidence_threshold (0.5)
```

**Invariants:**
- If Safety Filter fails → `passed=False`, no revision attempt
- If Cultural Sensitivity fails → `passed=False`, no revision attempt
- `final_score` is a weighted average, not a simple pass/fail count
- `revision_count` tracks how many times the draft was revised (max 2)
- Escalation Gate can set `suggestions` even when `passed=True` (signals, doesn't block)

---

## 5. Transmitter (`transmitter/__init__.py` → `Transmitter.deliver()`)

**Responsibility:** Deliver the validated response through the appropriate channel adapter.

| Field | Type | Description |
|-------|------|-------------|
| **Input** | `AgentResponse` + `recipient` | Validated response and recipient identifier |
| **Output** | `bool` | True on success, False on failure |
| **Latency budget** | < 500ms (network) | WhatsApp/Telegram API call |
| **Failure mode** | Returns False; logs error | Never silently drops a message |

**Channel constraints:**

| Channel | Max length | Formatting | Special |
|---------|-----------|------------|---------|
| WhatsApp | 4096 chars | No markdown (plain text, numbered lists) | Typing indicator within 500ms |
| Telegram | 4096 chars | Full markdown | Parse mode support |
| Webchat | No limit | Markdown | Responses stored in-memory for API retrieval |

**Invariants:**
- Response text must be non-empty
- Webchat responses are stored for API retrieval (not sent externally)
- Messaging channel failures are logged but don't crash the pipeline
- All adapters implement `send()` and `send_media()` abstract methods

---

## Pipeline Invariants

These hold across the entire pipeline:

1. **A message is never silently dropped.** Every customer message gets a response, an escalation, or an error message.
2. **Escalation is one-way.** Once escalated to human, the AI does not resume without human approval.
3. **Revision limit = 2.** If the Validator fails the same draft twice, escalate rather than loop forever.
4. **Latency budget = 35s total.** From message receipt to response delivery. If exceeded, send error message.
5. **Cost is tracked per-turn.** Every LLM call reports latency; Prometheus metrics track token usage.
6. **Self-model updates are async.** Background tasks only, never block the response path.
7. **Memory persistence is best-effort.** Failures in Qdrant/Redis don't prevent response delivery.
