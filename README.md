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
                    │     Brain     │  LLM-powered response
                    │               │  generation with 9-layer
                    │               │  validation pipeline
                    └───────┬───────┘
                            │
                    ┌───────▼───────┐
                    │  Transmitter  │  Multi-channel delivery
                    │               │  with channel-specific
                    │               │  formatting
                    └───────────────┘
```

### Three-Tier Memory

| Tier | Technology | Purpose | Latency |
|------|-----------|---------|---------|
| **Tier 1** | Redis | Active session state | <1ms |
| **Tier 2** | PostgreSQL | Conversation history, episodic memory | ~5ms |
| **Tier 3** | Qdrant | Cross-customer semantic patterns | ~20ms |

### 9-Layer Response Validation

Every response passes through 9 validation layers before delivery:

1. **Relevance Gate** — Does the response address the customer's question?
2. **Safety Filter** — No harmful content or sensitive data leaks
3. **Tone Checker** — Matches customer's emotional state
4. **Cultural Sensitivity** — Appropriate for African markets
5. **Factual Consistency** — Doesn't contradict known facts (WHMCS data)
6. **Completeness Gate** — Fully addresses the customer's needs
7. **Length & Format** — Appropriate for the channel (WhatsApp ≠ email)
8. **Emotional Alignment** — Empathy matching for frustrated customers
9. **Escalation Gate** — Detects when human intervention is needed

### Circuit Breaker

LLM providers are protected by a circuit breaker pattern:
- **Closed**: Normal operation
- **Open**: Provider failing, requests rejected
- **Half-Open**: Testing recovery

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
│   ├── learning/          # Self-improvement engine
│   ├── memory/            # Three-tier memory system
│   │   └── __init__.py    # Redis + Postgres + Qdrant
│   ├── models/            # Pydantic data models
│   ├── observability/     # OpenTelemetry + Prometheus metrics
│   ├── perceiver/         # Multi-channel intake + enrichment
│   ├── tools/             # External integrations
│   │   ├── whmcs.py       # WHMCS billing/ticketing
│   │   └── mpesa.py       # M-Pesa STK Push
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

## Self-Improvement

AfriAgent learns from validated interactions:

1. High-confidence responses (>0.8) are captured as learning examples
2. Examples are stored in Postgres + embedded in Qdrant
3. Future responses retrieve similar examples as few-shot prompts
4. The system improves over time without manual intervention

Configure with `AFRI_LEARNING_ENABLED=true` and `AFRI_MIN_CONFIDENCE_FOR_LEARNING`.

## License

MIT
