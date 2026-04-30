# Changelog — Review Response + Engine Structure Fixes

## 2026-04-30 — Response to Senior Engineer Review

### Critical Bug Fixes

#### 1. `src/afriagent/models/__init__.py` — Duplicate definitions removed
- **Bug**: `DispatchStep` and `DispatchPlan` defined twice — Pydantic `BaseModel` + `@dataclass`
- **Impact**: `@dataclass` versions shadowed Pydantic, breaking `.model_dump()` calls everywhere
- **Fix**: Removed `@dataclass` duplicates. Single Pydantic definitions retained.

#### 2. `src/afriagent/brain/llm.py` — Import order fixed
- **Bug**: `from pydantic import BaseModel` appeared AFTER `class LLMResponse(BaseModel):`
- **Impact**: `NameError` at import time — entire module fails to load
- **Fix**: Moved import to top of file.

### Missing Files Created

#### 3. `src/afriagent/knowledge/__init__.py`
- Directory had `playbook_loader.py` but no `__init__.py`

#### 4. `src/afriagent/plugins/__init__.py` (new)
- Hermes-style plugin system with `PluginManager`, `PluginContext`
- Discovery from `~/.afriagent/plugins/` and project `plugins/` dir
- Context API for registering tools, hooks, and adapters

#### 5. `src/afriagent/tools/registry.py` — Missing tool added
- `check_invoice_status` was referenced in `brain/__init__.py` but missing from registry
- Added `get_tools_by_class()`, `register_tool()` helpers

#### 6. `src/afriagent/tools/__init__.py` — Updated exports
- Added new registry functions to `__all__`

### Documentation (Review Response)

#### 7. `docs/AGENT_CONTRACTS.md` — Rewritten to match actual code
- **Previous**: Referenced old paths (`agents/core/perceiver.py`) and non-existent classes (`Perception`, `RoutingTask`, `ReasoningPackage`)
- **Updated**: Uses actual paths (`perceiver/__init__.py → Perceiver.process()`) and actual classes (`ConversationContext`, `DispatchPlan`, `ResponseCandidate`)
- Added: exact inputs/outputs per stage, latency budgets, failure modes, invariants

#### 8. `docs/REQUEST_LIFECYCLE_EXAMPLE.md` — Rewritten to match actual pipeline
- **Previous**: Used old naming (Perceiver → Router → Reasoner → Drafter → Validator)
- **Updated**: Matches actual pipeline (Perceiver → Coordinator → Brain → Validator → Transmitter)
- Added: exact code paths, latency breakdown, cost breakdown, real validator output

#### 9. `docs/SUPPORTED_SCENARIOS.md` (new)
- Email issues, DNS/domain, website/hosting, billing/payments
- Multi-turn troubleshooting examples (vague input, confused users, escalation)

#### 10. `docs/EVALUATION.md` (new)
- Core metrics with Prometheus metric names
- Per-layer validation metrics
- LLM provider metrics, memory tier metrics, business metrics
- Evaluation suite commands, regression detection, cost tracking

#### 11. `docs/SELF_IMPROVEMENT_GUARDRAILS.md` (new)
- Learning flow diagram
- 5 guardrails: confidence threshold, escalation exclusion, validation requirement, coordinator logging, no auto-push
- Risk prevention table
- Future improvements roadmap

#### 12. `docs/ARCHITECTURE.md` (new)
- Full architecture reference with ASCII diagrams
- Directory structure, data flow, major subsystem descriptions
- Design principles table

#### 13. `AGENTS.md` (new)
- Developer context file for AI agents working on the codebase

#### 14. `knowledge/hosting_playbooks.yaml` (new)
- Domain-specific playbooks for African hosting support
- Email (sending, receiving, bounceback), DNS (propagation, SSL), Website (down, WordPress), Billing (M-Pesa, suspension)
- Each with: triggers, diagnostic steps, common causes, escalation criteria, empathy statements

#### 15. `README.md` — Rewritten
- Added: "What It Actually Solves" scenarios table
- Added: links to lifecycle example, agent contracts, evaluation, guardrails
- Added: Coordinator to architecture diagram
- Added: full project structure including coordinator/, knowledge/, plugins/, self_model/
- Clarified: 9-layer validator as scoring system (not just pass/fail)
- Clarified: self-improvement has guardrails (not uncontrolled)

### File Manifest

```
afriagent-fixes/
├── AGENTS.md                                    # Developer context
├── CHANGELOG.md                                 # This file
├── README.md                                    # Rewritten with all review responses
├── docs/
│   ├── AGENT_CONTRACTS.md                       # Rewritten — actual code paths
│   ├── ARCHITECTURE.md                          # New — full architecture reference
│   ├── EVALUATION.md                            # New — metrics + Prometheus
│   ├── REQUEST_LIFECYCLE_EXAMPLE.md             # Rewritten — actual pipeline
│   ├── SELF_IMPROVEMENT_GUARDRAILS.md           # New — learning controls
│   └── SUPPORTED_SCENARIOS.md                   # New — domain scenarios
├── knowledge/
│   └── hosting_playbooks.yaml                   # New — hosting-specific playbooks
└── src/afriagent/
    ├── brain/llm.py                             # Fixed — import order
    ├── knowledge/__init__.py                    # New — package init
    ├── models/__init__.py                       # Fixed — duplicate definitions
    ├── plugins/__init__.py                      # New — plugin system
    └── tools/
        ├── __init__.py                          # Updated — new exports
        └── registry.py                          # Fixed — missing tool entry
```
