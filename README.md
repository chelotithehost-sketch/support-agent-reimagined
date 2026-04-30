# AfriAgent

**Production-grade AI customer support agent for African businesses.**

AfriAgent is an intelligent customer support system designed for the African market. It integrates with WhatsApp, Telegram, and webchat to provide automated, culturally-aware customer support with built-in M-Pesa payment handling and WHMCS integration.

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌──────────────┐
│  WhatsApp    │     │  Telegram    │     │   Webchat    │
│  (Twilio)    │     │  (Bot API)   │     │   (FastAPI)  │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │
       └────────────────────┼────────────────────┘
                            │
                    ┌───────▼───────┐
                    │   Perceiver   │  Language detection, intent
                    │               │  classification, sentiment,
                    │               │  urgency detection, context
                    │               │  enrichment
                    └───────┬───────┘
                            │
                    ┌───────▼───────┐
                    │  Coordinator  │  Dispatch planning — small
                    │               │  local LLM (<100ms) or
                    │               │  keyword fallback
                    └───────┬───────┘
                            │
                    ┌───────▼───────┐
                    │     Brain     │  Step execution loop with
                    │               │  replanning, 9-layer
                    │               │  validation pipeline
                    └───────┬───────┘
                            │
                    ┌───────▼───────┐
                    │  Transmitter  │  Multi-channel delivery
                    │               │  with channel-specific
                    │               │  formatting
                    └───────────────┘
```

### What It Actually Solves

| Scenario | Intent | Resolution Path |
|----------|--------|-----------------|
| Email not sending | TECHNICAL | MX/SPF/DKIM check → DNS fix |
| Email not receiving | TECHNICAL | MX verification → email routing |
| DNS not resolving | TECHNICAL | Nameserver check → propagation wait |
| Website down (500/503) | TECHNICAL | Error log → resource check → .htaccess |
| WordPress white screen | TECHNICAL | Debug mode → plugin conflict → memory |
| SSL certificate errors | TECHNICAL | Cert renewal → DNS verification |
| Invoice payment (M-Pesa) | BILLING | Check invoice → STK Push → confirm |
| Payment not reflecting | BILLING | Query M-Pesa → verify receipt |
| Account suspension | BILLING | Check overdue → payment → unsuspend |
| Domain transfer | TECHNICAL | EPP code → registrar coordination |
| Plan upgrade/downgrade | SALES | Show plans → compare → process change |

Each scenario has a dedicated playbook in `knowledge/hosting_playbooks.yaml`.

### Request Lifecycle

See [docs/REQUEST_LIFECYCLE_EXAMPLE.md](docs/REQUEST_LIFECYCLE_EXAMPLE.md) for a complete walkthrough of what happens when a customer sends "My email isn't sending from example.co.ke" on WhatsApp — including exact code paths, latency breakdown, and cost.

### Agent Contracts

Every component has a formal contract defining inputs, outputs, latency budgets, and failure modes. See [docs/AGENT_CONTRACTS.md](docs/AGENT_CONTRACTS.md).

Quick reference:
- **Perceiver**: Rule-based enrichment, < 50ms classification, fails to `Intent.GENERAL`
- **Coordinator**: Small LLM dispatch, < 100ms, falls back to keyword matching
- **Brain**: Step execution + replanning, 1-30s (LLM-dependent), escalates after 3 retries
- **Validator**: 9-layer pipeline, < 100ms, critical layers block immediately
- **Transmitter**: Channel delivery, < 500ms, logs failure (never silently drops)

### Three-Tier Memory

| Tier | Technology | Purpose | Latency |
|------|-----------|---------|---------|
| **Tier 1** | Redis | Active session state | <1ms |
| **Tier 2** | PostgreSQL | Conversation history, episodic memory | ~5ms |
| **Tier 3** | Qdrant | Cross-customer semantic patterns | ~20ms |

### 9-Layer Response Validation

Every response passes through 9 validation layers before delivery:

| # | Layer | Type | What it checks |
|---|-------|------|----------------|
| 1 | Relevance Gate | Scoring | Does the response address the customer's question? |
| 2 | Safety Filter | **Critical** | No harmful content or sensitive data leaks |
| 3 | Tone Checker | Scoring | Matches customer's emotional state |
| 4 | Cultural Sensitivity | **Critical** | Appropriate for African markets |
| 5 | Factual Consistency | Scoring | Doesn't contradict known facts (WHMCS data) |
| 6 | Completeness Gate | Scoring | Fully addresses the customer's needs |
| 7 | Length & Format | Scoring | Appropriate for the channel (WhatsApp ≠ email) |
| 8 | Emotional Alignment | Scoring | Empathy matching for frustrated customers |
| 9 | Escalation Gate | Signal | Detects when human intervention is needed |

Critical layers (Safety, Cultural Sensitivity) block immediately on failure. Other layers contribute to a weighted composite score.

### Circuit Breaker

LLM providers are protected by a circuit breaker pattern:
- **Closed**: Normal operation
- **Open**: Provider failing, requests rejected
- **Half-Open**: Testing recovery

### Self-Improvement

The learning loop captures high-confidence interactions (>0.8) as few-shot examples for future responses.

| Component | Status | Description |
|-----------|--------|-------------|
| Interaction capture | ✅ | High-confidence responses stored as learning examples |
| Coordinator logging | ✅ | Dispatch decisions + outcomes logged for analysis |
| Few-shot retrieval | ✅ | Similar examples retrieved for future prompts |
| Fine-tune export | ✅ | `scripts/finetune_prep.py` exports training data |

**Guardrails:** Confidence threshold, escalation exclusion, validation requirement, no auto-push. See [docs/SELF_IMPROVEMENT_GUARDRAILS.md](docs/SELF_IMPROVEMENT_GUARDRAILS.md).

### Evaluation

| Metric | Target | Description |
|--------|--------|-------------|
| Resolution rate | > 70% | % resolved without escalation |
| Escalation rate | < 15% | % escalated to human |
| P95 latency | < 5s | End-to-end response time |
| Validation pass rate | > 85% | % passing validation on first attempt |
| LLM success rate | > 95% | API call success rate |

See [docs/EVALUATION.md](docs/EVALUATION.md) for full metrics and Prometheus configuration.

## Quick Start

### Prerequisites

- Python 3.11+
- Redis, PostgreSQL, Qdrant (or use Docker Compose)

### Using Docker Compose (Recommended)

```bash
# Clone and configure
git clone <repo-url> && cd afriagent
cp .env.example .env
# Edit .env with your API keys

# Start everything
make docker-up

# Run migrations
docker compose exec afriagent afriagent migrate

# Access the API
open http://localhost:8000/docs
```

### Local Development

```bash
# Install
make dev

# Configure
cp .env.example .env
# Edit .env

# Start services (Redis, Postgres, Qdrant)
docker compose up -d redis postgres qdrant

# Run migrations
make migrate

# Start the server
make run
```

## API Endpoints

### Chat
```bash
# Send a message (webchat)
curl -X POST http://localhost:8000/api/v1/chat/ \
  -H "Content-Type: application/json" \
  -d '{"message": "I need help paying my invoice", "customer_id": "user-123"}'

# Get conversation history
curl http://localhost:8000/api/v1/chat/history/{conversation_id}
```

### Webhooks
```
POST /webhooks/whatsapp     — Twilio WhatsApp incoming
POST /webhooks/telegram     — Telegram Bot updates
POST /webhooks/mpesa/callback — M-Pesa payment callbacks
```

### Health
```
GET /health                 — Basic health check
GET /health/detailed        — Component health (Redis, Postgres, Qdrant)
```

### Admin
```
GET  /api/v1/admin/stats    — Agent statistics
POST /api/v1/admin/conversations/{id}/escalate — Manual escalation
```

## Configuration

All configuration is via environment variables with `AFRI_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `AFRI_ENV` | `dev` | Environment (dev/staging/prod) |
| `AFRI_LLM_PROVIDER` | `openai` | LLM provider (openai/anthropic/ollama) |
| `AFRI_OPENAI_API_KEY` | — | OpenAI API key |
| `AFRI_REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `AFRI_DATABASE_URL` | `postgresql+asyncpg://...` | PostgreSQL connection URL |
| `AFRI_QDRANT_URL` | `http://localhost:6333` | Qdrant connection URL |
| `AFRI_TWILIO_ACCOUNT_SID` | — | Twilio Account SID |
| `AFRI_TWILIO_AUTH_TOKEN` | — | Twilio Auth Token |
| `AFRI_TELEGRAM_BOT_TOKEN` | — | Telegram Bot Token |
| `AFRI_WHMCS_URL` | — | WHMCS installation URL |
| `AFRI_MPESA_CONSUMER_KEY` | — | M-Pesa API key |
| `AFRI_LEARNING_ENABLED` | `true` | Enable self-improvement |

See `.env.example` for the full list.

## Testing

```bash
# Unit tests
make test

# With coverage
make test-cov

# Integration tests (requires running services)
make test-integration

# All tests
make test-all
```

## Project Structure

```
afriagent/
├── src/afriagent/
│   ├── adapters/          # Webhook handlers (WhatsApp, Telegram, M-Pesa)
│   ├── api/               # FastAPI routes (chat, health, admin)
│   ├── brain/             # Response generation + 9-layer validator
│   │   ├── __init__.py    # Brain orchestrator
│   │   ├── llm.py         # LLM providers + circuit breaker
│   │   └── validator.py   # 9-layer validation pipeline
│   ├── config/            # Configuration (pydantic-settings)
│   ├── coordinator/       # Dispatch and replanning engine
│   │   ├── __init__.py    # CoordinatorBrain class
│   │   ├── dispatcher.py  # Message → DispatchPlan
│   │   ├── model.py       # Coordinator LLM (llama-cpp-python)
│   │   ├── prompts.py     # System prompt + few-shot examples
│   │   └── replanner.py   # Low-confidence retry logic
│   ├── knowledge/         # Playbook loading (hosting_playbooks.yaml)
│   ├── learning/          # Self-improvement engine
│   ├── memory/            # Three-tier memory system
│   ├── models/            # Pydantic data models
│   ├── observability/     # OpenTelemetry + Prometheus metrics
│   ├── perceiver/         # Multi-channel intake + enrichment
│   ├── plugins/           # Plugin system (extensible)
│   ├── self_model/        # Agent self-awareness (SQLite)
│   ├── tools/             # External integrations
│   │   ├── registry.py    # Central tool registry
│   │   ├── whmcs.py       # WHMCS billing/ticketing
│   │   ├── mpesa.py       # M-Pesa STK Push
│   │   └── dns_check.py   # DNS propagation checker
│   ├── transmitter/       # Multi-channel delivery
│   └── main.py            # App entry point + CLI
├── tests/
│   ├── unit/              # Unit tests (no external deps)
│   └── integration/       # Integration tests (needs services)
├── migrations/            # Alembic database migrations
├── docker-compose.yml     # Full development stack
├── Dockerfile             # Production container
├── Makefile               # Developer commands
└── pyproject.toml         # Project metadata + dependencies
```

## Observability

### Prometheus Metrics

Available at `http://localhost:9090/metrics`:

- `afriagent_requests_total` — Inbound messages by channel/intent
- `afriagent_llm_calls_total` — LLM API calls by provider/status
- `afriagent_validation_passed_total` — Validation pass rates by layer
- `afriagent_memory_operations_total` — Memory tier operations
- `afriagent_active_conversations` — Currently active conversations
- `afriagent_escalations_total` — Escalations by channel/reason
- `afriagent_circuit_breaker_state` — LLM circuit breaker status

### Grafana

Access Grafana at `http://localhost:3000` (admin/admin) for dashboards.

## License

MIT
