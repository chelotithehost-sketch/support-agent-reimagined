# docs/REQUEST_LIFECYCLE_EXAMPLE.md
# ───────────────────────────────────────────────
# A complete request lifecycle walkthrough for the README.
# Copy the content below into README.md (replace or append to existing).
# ───────────────────────────────────────────────

## Request Lifecycle: Full Walkthrough

Here's exactly what happens when a customer sends **"My email isn't sending from example.co.ke"** on WhatsApp:

```
┌──────────────────────────────────────────────────────────────────────────┐
│  1. WhatsApp Webhook                                                     │
│  ─────────────────                                                       │
│  Incoming: "My email isn't sending from example.co.ke"                   │
│  From: +254712345678                                                     │
│  Channel: WhatsApp                                                       │
│                                                                          │
│  Adapter: WhatsAppAdapter.verify_signature() → HMAC-SHA256 ✓            │
│  Adapter: WhatsAppAdapter.parse_webhook() → WhatsAppMessage             │
│  Adapter: WhatsAppAdapter.send_typing_indicator() → ✓ (< 500ms)        │
└──────────────────────────┬───────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  2. Perceiver                                                            │
│  ──────────                                                              │
│  Input: "My email isn't sending from example.co.ke"                     │
│                                                                          │
│  Fast-path regex classification (no LLM):                               │
│    ✓ "email" → Intent.EMAIL_SETUP, ProductArea.EMAIL                    │
│    ✓ "example.co.ke" → domain_names: ["example.co.ke"]                  │
│    ✓ "isn't sending" → issue_category: "email_not_sending"              │
│    ✓ EmotionalState.CALM (no frustration markers)                       │
│    ✓ Urgency.MEDIUM (no urgency markers)                                │
│    ✓ confidence: 0.85                                                   │
│                                                                          │
│  Output: Perception(intent=EMAIL_SETUP, confidence=0.85, ...)           │
│  Latency: ~2ms                                                          │
└──────────────────────────┬───────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  3. Router                                                               │
│  ────────                                                                │
│  Input: RoutingTask(required_capabilities=["email"], urgency=MEDIUM)     │
│                                                                          │
│  Scoring all eligible models:                                            │
│    ollama/qwen2.5:7b  → 0.72 (free, local, good enough for email)       │
│    openai/gpt-4o-mini → 0.68 (cheap, fast, but costs $)                 │
│    openai/gpt-4o      → 0.55 (overkill for this task)                   │
│                                                                          │
│  Circuit breaker status: all healthy ✓                                  │
│  Daily budget remaining: $12.40 / $15.00 ✓                              │
│                                                                          │
│  Output: ModelSelection(provider="ollama", model="qwen2.5:7b")          │
│  Latency: ~3ms                                                          │
└──────────────────────────┬───────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  4. Reasoner                                                             │
│  ──────────                                                              │
│  Assembles ReasoningPackage from 4 sources:                             │
│                                                                          │
│  Working Memory:                                                         │
│    • 1 prior turn (customer's message)                                   │
│    • clarifications_asked: 0                                            │
│                                                                          │
│  Episodic Memory (Postgres):                                            │
│    • Customer: Standard tier, 120 days, CSAT [4,5,4]                    │
│    • No prior email issues                                               │
│                                                                          │
│  Semantic Memory (Qdrant):                                               │
│    • Similar case: "MX record pointing to old provider" (CSAT 5)        │
│    • Pattern: ".co.ke domains — nameserver propagation delays up to 48h"│
│                                                                          │
│  Knowledge Base:                                                         │
│    • Playbook: email_not_sending (from hosting_playbooks.yaml)           │
│    • KB chunk: "MX records direct email to correct mail server..."       │
│                                                                          │
│  Decision: confidence=0.85 → no clarification needed                    │
│                                                                          │
│  Output: ReasoningPackage(                                              │
│    resolution_path=[Check MX → Update NS → Wait propagation → Test],    │
│    tone_instruction="Professional, efficient, friendly",                 │
│    must_include=["MX record check step", "propagation time"],            │
│    must_avoid=["I cannot help you"],                                     │
│    escalation_recommended=False,                                         │
│  )                                                                       │
│  Latency: ~5ms                                                          │
└──────────────────────────┬───────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  5. Drafter                                                              │
│  ────────                                                                │
│  System prompt assembled from ReasoningPackage:                         │
│    Role: "Professional, empathetic customer support agent..."            │
│    Customer: "Standard customer, 120 days, no prior email issues"       │
│    KB: "MX records direct email to correct mail server..."              │
│    Past case: "MX record pointing to old provider" (CSAT 5)             │
│    Tone: "Professional, efficient, friendly"                             │
│    Format: max 300 words, use numbered steps                             │
│    Required: MX record check, propagation time                          │
│    Forbidden: "I cannot help you"                                        │
│                                                                          │
│  LLM call: ollama/qwen2.5:7b                                            │
│  Tokens: 300 in / 80 out                                                │
│  Cost: $0.00 (local model)                                              │
│                                                                          │
│  Output: DraftResult(                                                   │
│    response_text="I see you're having trouble sending email from        │
│    example.co.ke. Here's what to check:                                 │
│    1. Verify your MX records point to our mail servers                   │
│    2. Check that your SPF record includes our servers                    │
│    3. Wait up to 30 minutes for DNS propagation                         │
│    Would you like me to walk you through checking your MX records?",    │
│    confidence=0.82,                                                      │
│    cost_usd=0.00,                                                        │
│    latency_ms=800,                                                       │
│  )                                                                       │
└──────────────────────────┬───────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  6. Validator (9 layers)                                                 │
│  ──────────────────────                                                  │
│                                                                          │
│  Layer 1 — Safety Gate (blocking)                                       │
│    ✓ No dangerous commands, no credentials shared                       │
│                                                                          │
│  Layer 2 — Accuracy Gate (blocking)                                     │
│    ✓ MX record step present ✓ SPF mention present                      │
│                                                                          │
│  Layer 3 — Completeness Gate (blocking)                                 │
│    ✓ "MX record check step" present ✓ "propagation time" present        │
│                                                                          │
│  Layer 4 — Emotional Alignment Gate (blocking)                          │
│    ✓ Customer is calm → professional tone is appropriate                │
│                                                                          │
│  Layer 5 — Clarity Gate (non-blocking)                                  │
│    ✓ Flesch-Kincaid: grade 8 ✓ No unexplained jargon                   │
│                                                                          │
│  Layer 6 — Brand Gate (non-blocking)                                    │
│    ✓ No competitor mentions ✓ No dismissive language                    │
│                                                                          │
│  Layer 7 — Legal Gate (skipped — not legal-flagged)                     │
│    ○ N/A                                                                │
│                                                                          │
│  Layer 8 — Escalation Gate (blocking)                                   │
│    ✓ Customer did not request human agent                               │
│                                                                          │
│  Layer 9 — Cost Gate (non-blocking)                                     │
│    ✓ Response is 67 words — under 300 word limit                       │
│                                                                          │
│  Result: ValidationResult(passed=True, revision_count=0)                │
│  Latency: ~15ms                                                         │
└──────────────────────────┬───────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  7. Transmitter                                                          │
│  ────────────                                                            │
│  Format for WhatsApp:                                                    │
│    • Text within 4096 char limit ✓ (67 words ≈ 400 chars)              │
│    • No unsupported markdown                                             │
│                                                                          │
│  Delivery:                                                               │
│    → WhatsAppAdapter.send_text("+254712345678", response)               │
│    → API response: {"id": "wamid.HBgLMjU0NzEy..."}                     │
│                                                                          │
│  Post-delivery:                                                          │
│    • Log turn to Episodic Memory (Postgres)                             │
│    • Update Working Memory (Redis, 4h TTL)                              │
│    • Record cost ($0.00) to daily budget tracker                        │
│    • Record latency (820ms total) to metrics                            │
│                                                                          │
│  Total pipeline latency: ~825ms                                         │
└──────────────────────────────────────────────────────────────────────────┘
```

### What the customer sees (WhatsApp):

```
I see you're having trouble sending email from example.co.ke.
Here's what to check:

1. Verify your MX records point to our mail servers
2. Check that your SPF record includes our servers
3. Wait up to 30 minutes for DNS propagation

Would you like me to walk you through checking your MX records?
```

### Cost breakdown for this conversation:

| Component | Cost |
|-----------|------|
| Perceiver | $0.00 (regex) |
| Router | $0.00 (scoring) |
| Reasoner | $0.00 (assembly) |
| Drafter | $0.00 (Ollama local) |
| Validator | $0.00 (pattern matching) |
| Transmitter | $0.00 (API call only) |
| **Total** | **$0.00** |

When the local model can't handle a complex task (e.g., billing dispute with legal language), the Router selects a cloud model and cost is tracked per-turn against `DAILY_BUDGET_USD`.
