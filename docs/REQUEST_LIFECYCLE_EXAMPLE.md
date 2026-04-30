# Request Lifecycle: Full Walkthrough

Here's exactly what happens when a customer sends **"My email isn't sending from example.co.ke"** on WhatsApp.

This is the actual code path — every function and class exists in the codebase.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  1. WhatsApp Webhook (adapters/__init__.py → whatsapp_webhook())        │
│  ─────────────────────────────────────────────────────────────────────  │
│  Incoming: "My email isn't sending from example.co.ke"                  │
│  From: whatsapp:+254712345678                                           │
│  Channel: WhatsApp                                                      │
│                                                                         │
│  Form data parsed from Twilio webhook:                                  │
│    From = "whatsapp:+254712345678"                                      │
│    Body = "My email isn't sending from example.co.ke"                   │
│    NumMedia = "0"                                                       │
│                                                                         │
│  Creates: InboundMessage(                                               │
│    channel=Channel.WHATSAPP,                                            │
│    sender_id="+254712345678",                                           │
│    content="My email isn't sending from example.co.ke",                 │
│  )                                                                      │
│                                                                         │
│  Calls: agent.handle_message(inbound)                                   │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  2. Perceiver (perceiver/__init__.py → Perceiver.process())             │
│  ─────────────────────────────────────────────────────────────────────  │
│  Input: InboundMessage                                                  │
│                                                                         │
│  Step 1 — Dedup check (Redis lock, TTL 10s):                           │
│    lock_key = "dedup:whatsapp:+254712345678:<hash>"                     │
│    Redis SET NX → acquired ✓                                            │
│                                                                         │
│  Step 2 — Language detection (perceiver/language.py):                   │
│    Sheng markers: 0 matches                                             │
│    Swahili markers: 0 matches                                           │
│    langdetect → "en"                                                    │
│    Result: detected_language = "en"                                     │
│                                                                         │
│  Step 3 — Intent classification (keyword matching):                     │
│    "email" → TECHNICAL +1                                               │
│    "sending" → (no match)                                               │
│    "example.co.ke" → (no match)                                         │
│    Result: detected_intent = Intent.TECHNICAL                           │
│                                                                         │
│  Step 4 — Sentiment detection:                                          │
│    Negative markers: 0                                                  │
│    Positive markers: 0                                                  │
│    Frustrated markers: 0                                                │
│    Result: detected_sentiment = Sentiment.NEUTRAL                       │
│                                                                         │
│  Step 5 — Urgency detection:                                            │
│    "urgent"/"emergency"/"asap": not found                               │
│    sentiment = NEUTRAL → no urgency boost                               │
│    Result: detected_urgency = Urgency.LOW                               │
│                                                                         │
│  Step 6 — Load customer profile:                                        │
│    Redis cache check → miss                                            │
│    Create: CustomerProfile(id="+254712345678", phone="+254712345678")   │
│    Cache in Redis (TTL 1h)                                              │
│                                                                         │
│  Step 7 — Load conversation history:                                    │
│    Postgres query → 0 previous messages (new conversation)              │
│                                                                         │
│  Step 8 — Vector search (Qdrant):                                       │
│    Embed message via LLM → [0.12, -0.34, ...] (1536 dims)              │
│    Qdrant search → 2 similar patterns found:                            │
│      1. "MX record pointing to old provider" (score: 0.87)              │
│      2. "Email bounceback after NS change" (score: 0.81)                │
│                                                                         │
│  Step 9 — Persist message to Postgres                                   │
│  Step 10 — Update session in Redis                                      │
│                                                                         │
│  Output: ConversationContext(                                           │
│    conversation_id="whatsapp:+254712345678",                            │
│    customer=CustomerProfile(...),                                       │
│    current_message=Message(content="My email isn't sending..."),        │
│    message_history=[],                                                  │
│    detected_intent=TECHNICAL,                                           │
│    detected_sentiment=NEUTRAL,                                          │
│    detected_urgency=LOW,                                                │
│    detected_language="en",                                              │
│    similar_patterns=[...],                                              │
│  )                                                                      │
│  Latency: ~25ms (classification) + ~200ms (vector search + embed)      │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  3. Coordinator (coordinator/__init__.py → CoordinatorBrain.dispatch()) │
│  ─────────────────────────────────────────────────────────────────────  │
│  Input: ConversationContext                                             │
│                                                                         │
│  If coordinator LLM available (llama-cpp-python):                       │
│    System prompt includes tool registry + self-model state              │
│    User message: "Customer message: My email isn't sending..."          │
│    LLM generates JSON:                                                  │
│    {                                                                    │
│      "intent": "outage",                                                │
│      "urgency": 3,                                                      │
│      "language": "en",                                                  │
│      "steps": [                                                         │
│        {"tool": "check_domain_dns", "params": {"domain": "example.co.ke"}}, │
│        {"tool": "lookup_customer", "params": {}},                       │
│        {"llm_provider": "openai", "params": {"task": "email_troubleshooting"}} │
│      ],                                                                 │
│      "confidence": 0.88,                                                │
│      "reasoning": "Email issue on specific domain — check DNS first"    │
│    }                                                                    │
│                                                                         │
│  If coordinator LLM unavailable (fallback):                             │
│    Keyword match: "email" → outage intent                               │
│    Provider priority: ["ollama", "openai", "anthropic"]                 │
│    Result: DispatchPlan with single LLM step                            │
│                                                                         │
│  Output: DispatchPlan(                                                  │
│    intent="outage",                                                     │
│    urgency=3,                                                           │
│    language="en",                                                       │
│    steps=[                                                              │
│      DispatchStep(tool="check_domain_dns", params={domain: "example.co.ke"}), │
│      DispatchStep(tool="lookup_customer", params={}),                   │
│      DispatchStep(llm_provider="openai", params={task: "email_troubleshoot"}), │
│    ],                                                                   │
│    confidence=0.88,                                                     │
│  )                                                                      │
│  Latency: <100ms (local LLM) or <10ms (keyword fallback)               │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  4. Brain Execution Loop (brain/__init__.py → Brain.generate_response())│
│  ─────────────────────────────────────────────────────────────────────  │
│                                                                         │
│  Step 4a — Execute "check_domain_dns" tool:                             │
│    → tools/dns_check.py → DNSChecker.check_domain("example.co.ke")      │
│    → Resolves A, AAAA, MX, NS, CNAME, TXT records                      │
│    → Result: {                                                          │
│        A: ["192.168.1.100"],                                            │
│        MX: ["mx1.old-provider.com"],  ← problem found!                 │
│        NS: ["ns1.afrihost.co.ke"],                                      │
│        issues: ["MX records pointing to old provider"],                 │
│      }                                                                  │
│    → StepResult(confidence=0.8, success=True, latency=150ms)            │
│                                                                         │
│  Step 4b — Execute "lookup_customer" tool:                              │
│    → tools/whmcs.py → WHMCSClient.get_customer_context(client_id)       │
│    → Result: {services: [...], open_tickets: [...], unpaid_invoices: []}│
│    → StepResult(confidence=0.8, success=True, latency=200ms)            │
│                                                                         │
│  Step 4c — Execute LLM step (email troubleshooting):                    │
│    → brain/llm.py → OpenAIProvider.generate(messages)                   │
│    → System prompt: "You are a professional support agent..."           │
│    → Intent context: TECHNICAL                                          │
│    → Task: "explain_dns_results_and_remediate"                          │
│    → Includes DNS results + customer context + similar patterns         │
│    → LLM generates: "I can see your MX records are pointing to..."     │
│    → StepResult(confidence=0.85, success=True, latency=1200ms)          │
│                                                                         │
│  No replanning needed (all steps > 0.6 confidence)                      │
│                                                                         │
│  Candidate: ResponseCandidate(                                          │
│    content="I can see your MX records for example.co.ke are still...",  │
│    confidence=0.85,                                                     │
│    model_used="gpt-4o",                                                 │
│  )                                                                      │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  5. Validator (brain/validator.py → ResponseValidator.validate())       │
│  ─────────────────────────────────────────────────────────────────────  │
│                                                                         │
│  Layer 1 — Relevance Gate (weight: 0.15)                                │
│    Keywords overlap: "email", "MX", "records" → high relevance          │
│    Score: 0.92  ✓                                                       │
│                                                                         │
│  Layer 2 — Safety Filter (weight: 0.20) [CRITICAL]                      │
│    No unsafe patterns detected                                          │
│    No sensitive data leaks                                              │
│    Score: 1.0  ✓                                                        │
│                                                                         │
│  Layer 3 — Tone Checker (weight: 0.10)                                  │
│    Customer: NEUTRAL → professional tone appropriate                    │
│    No aggressive markers                                                │
│    Score: 1.0  ✓                                                        │
│                                                                         │
│  Layer 4 — Cultural Sensitivity (weight: 0.15) [CRITICAL]               │
│    No derogatory language                                               │
│    No "third world" / "primitive" markers                               │
│    Score: 1.0  ✓                                                        │
│                                                                         │
│  Layer 5 — Factual Consistency (weight: 0.10)                           │
│    LLM cross-check: response mentions MX records pointing to old        │
│    provider — matches DNS check results                                 │
│    Score: 1.0  ✓                                                        │
│                                                                         │
│  Layer 6 — Completeness Gate (weight: 0.10)                             │
│    TECHNICAL intent → needs troubleshooting steps                       │
│    Response includes: "update MX records", "wait for propagation"       │
│    Score: 0.9  ✓                                                        │
│                                                                         │
│  Layer 7 — Length & Format (weight: 0.05)                               │
│    Word count: 89 words → under 200 limit for WhatsApp                  │
│    No markdown formatting                                               │
│    Score: 1.0  ✓                                                        │
│                                                                         │
│  Layer 8 — Emotional Alignment (weight: 0.10)                           │
│    Customer: NEUTRAL → no empathy markers needed                        │
│    Score: 1.0  ✓                                                        │
│                                                                         │
│  Layer 9 — Escalation Gate (weight: 0.05)                               │
│    No escalation triggers in customer message                           │
│    No repeated frustration                                              │
│    Score: 1.0  ✓                                                        │
│                                                                         │
│  Composite: (0.92×0.15)+(1.0×0.20)+(1.0×0.10)+(1.0×0.15)+              │
│             (1.0×0.10)+(0.9×0.10)+(1.0×0.05)+(1.0×0.10)+(1.0×0.05)    │
│           = 0.138+0.20+0.10+0.15+0.10+0.09+0.05+0.10+0.05             │
│           = 0.978                                                       │
│                                                                         │
│  Result: ValidationResult(passed=True, final_score=0.978)               │
│  Latency: ~65ms                                                         │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  6. Post-Processing (brain/__init__.py)                                  │
│  ─────────────────────────────────────────────────────────────────────  │
│                                                                         │
│  Translation: not needed (detected_language == "en")                    │
│                                                                         │
│  Build AgentResponse:                                                   │
│    conversation_id = "whatsapp:+254712345678"                           │
│    content = "I can see your MX records for example.co.ke are still..." │
│    channel = Channel.WHATSAPP                                           │
│    confidence = 0.978                                                   │
│    validation = ValidationResult(passed=True, ...)                      │
│    intent_handled = Intent.TECHNICAL                                    │
│    escalated = False                                                    │
│                                                                         │
│  Persist to memory:                                                     │
│    Postgres → save message record                                       │
│    Redis → update session (last_response, confidence)                   │
│    Qdrant → store resolution pattern (if confidence >= 0.7)             │
│                                                                         │
│  Update self-model (background task):                                   │
│    tool_reliability: check_domain_dns → EMA update (success)            │
│    tool_reliability: lookup_customer → EMA update (success)             │
│    provider_health: openai → EMA latency update                         │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  7. Transmitter (transmitter/__init__.py → Transmitter.deliver())       │
│  ─────────────────────────────────────────────────────────────────────  │
│                                                                         │
│  Channel: WhatsApp → WhatsAppAdapter.send()                             │
│    → Twilio API POST to /Messages.json                                  │
│    → From: whatsapp:+14155238886                                        │
│    → To: whatsapp:+254712345678                                         │
│    → Body: "I can see your MX records for example.co.ke are still..."   │
│    → Response: {"id": "wamid.HBgLMjU0NzEy..."}                         │
│                                                                         │
│  Delivery: ✓                                                            │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  8. Learning (learning/__init__.py → LearningEngine.capture_interaction)│
│  ─────────────────────────────────────────────────────────────────────  │
│                                                                         │
│  Check: confidence 0.978 >= min_confidence 0.8 → learn from this ✓      │
│  Check: escalated = False → learn from this ✓                           │
│                                                                         │
│  Store learning example:                                                │
│    Postgres → learning_examples table                                   │
│    customer_message = "My email isn't sending from example.co.ke"       │
│    agent_response = "I can see your MX records..."                      │
│    intent = "technical"                                                 │
│    confidence = 0.978                                                   │
│                                                                         │
│  Log coordinator decision:                                              │
│    intent = "outage"                                                    │
│    outcome_confidence = 0.978                                           │
│    replan_count = 0                                                     │
│    escalated = False                                                    │
└──────────────────────────────────────────────────────────────────────────┘
```

### What the customer sees (WhatsApp):

```
I can see your MX records for example.co.ke are still pointing
to your old provider's mail servers. Here's what to do:

1. Log in to your DNS control panel
2. Update your MX records to:
   - Priority 10: mail.afrihost.co.ke
   - Priority 20: mail2.afrihost.co.ke
3. Wait 30-60 minutes for DNS propagation
4. Test by sending a test email

Your SPF record also needs updating. Would you like me to
walk you through that as well?
```

### Latency breakdown:

| Component | Latency |
|-----------|---------|
| Webhook parsing | <1ms |
| Perceiver (classification) | ~25ms |
| Perceiver (vector search) | ~200ms |
| Coordinator (dispatch) | ~80ms |
| Brain (DNS check tool) | ~150ms |
| Brain (WHMCS lookup) | ~200ms |
| Brain (LLM generation) | ~1200ms |
| Validator (9 layers) | ~65ms |
| Transmitter (WhatsApp API) | ~100ms |
| Learning (persist) | ~50ms |
| **Total** | **~2070ms** |

### Cost breakdown:

| Component | Cost |
|-----------|------|
| Perceiver | $0.00 (regex + Redis) |
| Coordinator | $0.00 (local llama-cpp-python) |
| Brain — DNS check | $0.00 (system resolver) |
| Brain — WHMCS lookup | $0.00 (API call) |
| Brain — LLM generation | ~$0.003 (GPT-4o, ~400 tokens) |
| Validator | $0.00 (pattern matching) + ~$0.001 (factual consistency LLM call) |
| Transmitter | $0.00 (Twilio API) |
| **Total** | **~$0.004** |

When the local model handles simpler tasks (greetings, clarifications), cost drops to $0.00.
