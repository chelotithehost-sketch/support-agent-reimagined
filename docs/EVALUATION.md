# Evaluation Metrics

The system tracks these metrics via Prometheus (exposed at `/metrics`) and can run nightly evaluation suites.

## Core Metrics

| Metric | Target | Prometheus Metric | Description |
|--------|--------|-------------------|-------------|
| Resolution rate | > 70% | `afriagent_escalations_total` / `afriagent_requests_total` | % of conversations resolved without escalation |
| Avg confidence | > 0.75 | `afriagent_validation_passed_total` | Average validation score across all responses |
| Escalation rate | < 15% | `afriagent_escalations_total` / `afriagent_requests_total` | % escalated to human agent |
| Avg turns to resolution | < 4 | (calculated from conversation length) | Efficiency of resolution |
| P95 latency | < 5s | `afriagent_request_duration_seconds` | End-to-end response time |
| LLM success rate | > 95% | `afriagent_llm_calls_total{status="success"}` | LLM API call success rate |
| Validation pass rate | > 85% | `afriagent_validation_passed_total` / total | % of responses passing validation on first attempt |

## Per-Layer Validation Metrics

Each validation layer tracks pass/fail independently:

```
afriagent_validation_passed_total{layer="relevance_gate"}
afriagent_validation_passed_total{layer="safety_filter"}
afriagent_validation_passed_total{layer="tone_checker"}
afriagent_validation_passed_total{layer="cultural_sensitivity"}
afriagent_validation_passed_total{layer="factual_consistency"}
afriagent_validation_passed_total{layer="completeness_gate"}
afriagent_validation_passed_total{layer="length_format"}
afriagent_validation_passed_total{layer="emotional_alignment"}
afriagent_validation_passed_total{layer="escalation_gate"}
```

## LLM Provider Metrics

```
afriagent_llm_calls_total{provider="openai", model="gpt-4o", status="success"}
afriagent_llm_calls_total{provider="openai", model="gpt-4o", status="error"}
afriagent_llm_latency_seconds{provider="openai"}
afriagent_llm_tokens_total{provider="openai", direction="input"}
afriagent_llm_tokens_total{provider="openai", direction="output"}
afriagent_circuit_breaker_state{provider="openai"}  # 0=closed, 1=open, 2=half-open
```

## Memory Tier Metrics

```
afriagent_memory_operations_total{tier="redis", operation="get"}
afriagent_memory_operations_total{tier="postgres", operation="save_message"}
afriagent_memory_operations_total{tier="qdrant", operation="search"}
afriagent_memory_latency_seconds{tier="redis"}
afriagent_memory_latency_seconds{tier="postgres"}
afriagent_memory_latency_seconds{tier="qdrant"}
```

## Business Metrics

```
afriagent_requests_total{channel="whatsapp", intent="billing"}
afriagent_requests_total{channel="telegram", intent="technical"}
afriagent_active_conversations{channel="whatsapp"}
afriagent_satisfaction_score  # Histogram of CSAT scores
```

## Evaluation Suite

Run the evaluation suite to measure quality against known scenarios:

```bash
# Run default evaluation suite
afriagent eval --suite default

# Run with specific provider
AFRI_LLM_PROVIDER=openai afriagent eval --suite default
```

The evaluation suite tests:
1. **Intent classification accuracy** — known messages → expected intents
2. **Language detection accuracy** — Sheng, Swahili, English, French samples
3. **Validation pipeline correctness** — known good/bad responses → expected pass/fail
4. **Coordinator dispatch quality** — known scenarios → expected dispatch plans
5. **End-to-end resolution** — full pipeline with known customer messages

## Regression Detection

If any metric degrades by >10% vs 7-day rolling average, the system flags it:

- Prometheus alerting rules (in `scripts/prometheus.yml`)
- Grafana dashboard annotations
- Nightly report summary

## Cost Tracking

Per-conversation cost is tracked via LLM token metrics:

```
cost_per_conversation = Σ(llm_tokens_input × input_price + llm_tokens_output × output_price)
```

Budget enforcement:
- Daily budget configurable via `AFRI_DAILY_BUDGET_USD` (default: $15)
- When budget exceeded, coordinator forces local Ollama model
- Critical urgency bypasses budget (latency priority)
