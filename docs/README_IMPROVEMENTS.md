# docs/README_IMPROVEMENTS.md
# ───────────────────────────────────────────────
# Sections to add/replace in README.md
# ───────────────────────────────────────────────

## Suggested README Changes

### 1. Replace the architecture description section with the lifecycle example

The current README describes the pipeline conceptually. Replace (or append to) the pipeline description with the full walkthrough from `docs/REQUEST_LIFECYCLE_EXAMPLE.md`. This makes the architecture tangible.

### 2. Add "Supported Support Scenarios" section after the pipeline description

```markdown
### What It Actually Solves

The system handles these scenarios end-to-end today:

| Scenario | Intent | Typical Resolution |
|----------|--------|--------------------|
| Email not sending | EMAIL_SETUP | MX/SPF/DKIM check → DNS fix |
| Email not receiving | EMAIL_SETUP | MX verification → email routing |
| SSL certificate errors | SSL_ISSUE | Cert renewal → DNS verification |
| DNS not resolving | TECHNICAL_ISSUE | Nameserver check → propagation wait |
| Website down (500/503) | TECHNICAL_ISSUE | Error log → resource check → .htaccess |
| cPanel login failed | ACCOUNT_ACCESS | Password reset → IP unblock |
| WordPress white screen | TECHNICAL_ISSUE | Debug mode → plugin conflict → memory |
| Billing double charge | BILLING_DISPUTE | Refund processing → confirmation |
| Domain transfer | DOMAIN_MANAGEMENT | EPP code → registrar coordination |

Each scenario has a dedicated playbook in `knowledge/hosting_playbooks.yaml` with:
- Diagnostic steps (exact commands and cPanel paths)
- Common causes (ranked by frequency in African hosting market)
- Resolution templates
- Escalation criteria
```

### 3. Add "Agent Contracts" section

```markdown
### Agent Contracts

Every agent in the pipeline has a formal contract defining inputs, outputs, latency budgets, and failure modes. See [docs/AGENT_CONTRACTS.md](docs/AGENT_CONTRACTS.md) for the full specification.

Quick reference:
- **Perceiver**: Rule-based classification, < 50ms, fails to UNKNOWN
- **Router**: Multi-factor model scoring, < 10ms, fails to local Ollama
- **Reasoner**: Context assembly, < 20ms, fails to clarification
- **Drafter**: LLM generation, 1-30s, retries with fallback
- **Validator**: 9-layer check, < 100ms, fails to escalation
- **Transmitter**: Channel formatting, < 500ms, retries 3x then queues
```

### 4. Add "Evaluation Metrics" section

```markdown
### Evaluation

The system tracks these metrics nightly via the AutoEvaluator:

| Metric | Target | Description |
|--------|--------|-------------|
| Resolution rate | > 70% | % of conversations resolved without escalation |
| Avg CSAT | > 4.0/5 | Customer satisfaction score |
| Hallucination rate | < 5% | Claims not verifiable against KB |
| Escalation rate | < 15% | % escalated to human |
| Avg turns to resolution | < 4 | Efficiency of resolution |
| Cost per conversation | < $0.05 | LLM cost efficiency |
| P95 latency | < 5s | End-to-end response time |

Regression detection: if any metric degrades by >10% vs 7-day rolling average, the system flags it in the nightly report.
```

### 5. Add "Self-Improving" clarification

```markdown
### Self-Improving (Current Status)

The learning loop has three components:

| Component | Status | Description |
|-----------|--------|-------------|
| Feedback Collector | ✅ Implemented | 5 signal sources (explicit CSAT, implicit CSAT, human correction, resolution confirmation, LLM judge) |
| Auto Evaluator | ✅ Implemented | Nightly quality scoring with regression detection |
| Few-Shot Updater | 🔲 Planned | Selects high-CSAT examples for future prompt refinement |

**Guardrails currently in place:**
- Examples with CSAT < 3 are excluded from training
- Human corrections are weighted 2x vs automated signals
- Regression detection prevents quality degradation
- All updates require human review before deployment (no auto-push)
```
