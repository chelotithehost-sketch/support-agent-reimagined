# AGENTS.md — AfriAgent Developer Context

This file provides context for AI agents working on the AfriAgent codebase.

## Architecture Overview

AfriAgent is a production-grade AI customer support agent for African businesses.
It follows a **Perceiver → Brain → Transmitter** pipeline with coordinator-driven dispatch.

```
InboundMessage → Perceiver → Brain → Transmitter → Customer
                  (enrich)   (think)   (deliver)
```

## Key Directories

```
src/afriagent/
├── adapters/          # Webhook handlers (WhatsApp, Telegram, M-Pesa)
├── api/               # FastAPI routes (chat, health, admin)
├── brain/             # Response generation + 9-layer validator
│   ├── __init__.py    # Brain orchestrator (main generate_response loop)
│   ├── llm.py         # LLM providers (OpenAI, Anthropic, Ollama) + circuit breaker
│   └── validator.py   # 9-layer validation pipeline
├── coordinator/       # Dispatch and replanning engine
│   ├── __init__.py    # CoordinatorBrain class
│   ├── dispatcher.py  # Message → DispatchPlan conversion
│   ├── model.py       # Coordinator LLM (llama-cpp-python, <100ms)
│   ├── prompts.py     # System prompt + few-shot examples
│   └── replanner.py   # Low-confidence retry logic
├── config/            # Configuration (pydantic-settings, AFRI_ prefix)
├── knowledge/         # Playbook loading (hosting_playbooks.yaml)
├── learning/          # Self-improvement engine (few-shot from validated interactions)
├── memory/            # Three-tier memory (Redis → Postgres → Qdrant)
├── models/            # Pydantic data models (shared vocabulary)
├── observability/     # OpenTelemetry + Prometheus metrics
├── perceiver/         # Multi-channel intake + enrichment
├── plugins/           # Plugin system (extensible tool/adapter registration)
├── self_model/        # Agent self-awareness (tool reliability, provider health)
├── tools/             # External integrations (WHMCS, M-Pesa, DNS)
└── transmitter/       # Multi-channel delivery (WhatsApp, Telegram, Webchat)
```

## Data Flow

### Message Processing
1. **Adapters** receive webhooks → create `InboundMessage`
2. **Perceiver** enriches: language detection, intent classification, sentiment, urgency
3. **Brain** asks **Coordinator** for a `DispatchPlan`
4. **Coordinator** returns steps (tool calls + LLM calls)
5. **Brain** executes steps in a loop with replanning on low confidence
6. **Brain** runs 9-layer validation pipeline
7. **Transmitter** delivers response through channel adapter
8. **Learning** captures high-confidence interactions for few-shot improvement

### Coordinator Dispatch Loop
```
CoordinatorBrain.dispatch(context) → DispatchPlan
  for step in plan.steps:
      execute(step) → StepResult
      if low_confidence → replan() → new DispatchPlan
      if max_replans → escalate
```

## Key Patterns

- **Pydantic models**: All shared types in `models/__init__.py` (single source of truth)
- **Circuit breaker**: LLM providers protected by circuit breaker (closed → open → half-open)
- **Three-tier memory**: Redis (hot session) → Postgres (episodic) → Qdrant (semantic vectors)
- **Self-model**: SQLite-backed EMA tracking of tool reliability and provider health
- **Plugin system**: Plugins register tools/hooks via `PluginManager` context API

## Testing

```bash
make test           # Unit tests (no external deps)
make test-cov       # Unit tests with coverage
make test-integration  # Integration tests (needs docker-compose up)
make test-all       # All tests
```

## Configuration

All config via `AFRI_` prefix env vars. See `.env.example` for full list.
Key: `AFRI_LLM_PROVIDER`, `AFRI_OPENAI_API_KEY`, `AFRI_REDIS_URL`, `AFRI_DATABASE_URL`.

## Common Pitfalls

- `DispatchStep` and `DispatchPlan` must be Pydantic BaseModel (not dataclass)
- `LLMResponse` requires `from pydantic import BaseModel` import before its class definition
- The coordinator LLM is optional (falls back to keyword-based dispatch)
- Language detection handles Sheng (Kenyan slang) separately from Swahili
