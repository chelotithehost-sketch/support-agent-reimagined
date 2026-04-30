# Architecture

This page maps the AfriAgent internals. Use it to orient yourself in the codebase.

## System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Entry Points                                 │
│                                                                     │
│  FastAPI Server (api/)    CLI (main.py)    Webhooks (adapters/)     │
└──────────┬──────────────────┬──────────────────┬────────────────────┘
           │                  │                  │
           ▼                  ▼                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     AfriAgent (main.py)                             │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │  Perceiver   │  │    Brain     │  │ Transmitter  │              │
│  │  (perceiver/)│  │  (brain/)    │  │(transmitter/)│              │
│  │              │  │              │  │              │              │
│  │ Language     │  │ Coordinator  │  │ WhatsApp     │              │
│  │ Intent       │  │ (coordinator)│  │ Telegram     │              │
│  │ Sentiment    │  │ Validator    │  │ Webchat      │              │
│  │ Urgency      │  │ (validator)  │  │              │              │
│  └──────┬───────┘  └──────┬───────┘  └──────────────┘              │
│         │                 │                                         │
│  ┌──────┴───────┐  ┌──────┴───────┐                                │
│  │   Memory     │  │    Tools     │                                │
│  │  (memory/)   │  │  (tools/)    │                                │
│  │              │  │              │                                │
│  │ Redis (hot)  │  │ WHMCS        │                                │
│  │ Postgres     │  │ M-Pesa       │                                │
│  │ Qdrant       │  │ DNS Check    │                                │
│  └──────────────┘  └──────────────┘                                │
└─────────────────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────┐  ┌──────────────────────┐
│    Self-Model        │  │     Learning         │
│  (self_model/)       │  │   (learning/)        │
│                      │  │                      │
│ Tool reliability     │  │ Few-shot capture     │
│ Provider health      │  │ Coordinator logging  │
│ Intent accuracy      │  │ Fine-tune export     │
│ Failure patterns     │  │                      │
└──────────────────────┘  └──────────────────────┘
```

## Directory Structure

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
│   ├── knowledge/         # Playbook loading
│   ├── learning/          # Self-improvement engine
│   ├── memory/            # Three-tier memory system
│   ├── models/            # Pydantic data models
│   ├── observability/     # OpenTelemetry + Prometheus
│   ├── perceiver/         # Multi-channel intake + enrichment
│   ├── plugins/           # Plugin system
│   ├── self_model/        # Agent self-awareness (SQLite)
│   ├── tools/             # External integrations
│   │   ├── registry.py    # Central tool registry (single source of truth)
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

## Data Flow

### CLI / API Session

```
User input → AfriAgent.handle_message()
  → Perceiver.process(inbound)
    → detect_language()
    → classify_intent()
    → detect_sentiment()
    → detect_urgency()
    → load customer profile
    → load conversation history
    → vector search for similar patterns
    → return ConversationContext
  → Brain.generate_response(context)
    → CoordinatorBrain.dispatch(context) → DispatchPlan
    → for step in plan.steps:
        → execute_step(step) → StepResult
        → if low_confidence → replan()
    → ResponseValidator.validate(candidate) → ValidationResult
    → if not validated → regenerate with feedback
    → return AgentResponse
  → Transmitter.deliver(response, recipient)
  → Learning.capture_interaction(context, response)
```

### Gateway Message (future)

```
Platform event → Adapter.on_message() → InboundMessage
  → AfriAgent.handle_message()
  → deliver response back through adapter
```

## Major Subsystems

### Coordinator (`coordinator/`)

The decision-making layer. Runs a small local LLM (llama-cpp-python, <100ms) to analyze incoming messages and produce a `DispatchPlan` — an ordered list of tool calls and LLM calls. Falls back to keyword-based dispatch when the coordinator LLM is unavailable.

### Brain (`brain/`)

The agentic response generator. Executes the coordinator's dispatch plan in a loop with replanning. Runs every response through a 9-layer validation pipeline before delivery.

### Memory (`memory/`)

Three-tier memory system:
- **Tier 1 (Redis)**: Active session state, <1ms latency, TTL-based
- **Tier 2 (Postgres)**: Conversation history, episodic memory, ~5ms
- **Tier 3 (Qdrant)**: Cross-customer semantic patterns, ~20ms

### Self-Model (`self_model/`)

SQLite-backed self-awareness layer. Tracks tool reliability (EMA), provider health (error streaks, latency), intent accuracy, and learned failure patterns. Updated asynchronously after every turn — never blocks the hot path.

### Learning (`learning/`)

Captures high-confidence interactions (>0.8) as few-shot examples. Future responses retrieve similar examples for improved quality. Also logs coordinator dispatch decisions for fine-tuning data export.

### Tools (`tools/`)

Central registry (`registry.py`) with metadata for all tools. Each tool entry describes what it does, what it requires, latency profile, and failure mode. The coordinator reads this for dispatch decisions.

### Plugins (`plugins/`)

Extensible plugin system. Plugins register tools, hooks, and adapters through a context API. Discovered from `~/.afriagent/plugins/` and project-level `plugins/` directory.

## Design Principles

| Principle | What it means in practice |
|-----------|--------------------------|
| **Coordinator-driven dispatch** | Small LLM decides what to do; big LLM does it |
| **Replan on failure** | Low-confidence results trigger automatic replanning |
| **Validate everything** | 9-layer pipeline catches bad responses before delivery |
| **Self-improving** | High-confidence interactions become few-shot examples |
| **Observable** | OpenTelemetry traces + Prometheus metrics on every path |
| **Graceful degradation** | Circuit breakers, fallback dispatch, non-critical failures swallowed |
