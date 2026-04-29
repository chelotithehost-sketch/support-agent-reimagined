# PLACEMENT.md — Where Each File Goes

## File Placement Map

| File in this package | Destination in repo | Action |
|---------------------|---------------------|--------|
| `tests/conftest.py` | `tests/conftest.py` | **Merge** — add new fixtures to existing file |
| `tests/unit/test_proactive_engine.py` | `tests/unit/test_proactive_engine.py` | **Create new** |
| `tests/unit/test_learning_loop.py` | `tests/unit/test_learning_loop.py` | **Create new** |
| `tests/integration/test_full_pipeline.py` | `tests/integration/test_full_pipeline.py` | **Create new** |
| `knowledge/hosting_playbooks.yaml` | `knowledge/hosting_playbooks.yaml` | **Create new** |
| `knowledge/playbook_loader.py` | `knowledge/playbook_loader.py` | **Create new** |
| `config/llm_profiles.yaml` | `config/llm_profiles.yaml` | **Create new** (or merge with existing) |
| `docs/AGENT_CONTRACTS.md` | `docs/AGENT_CONTRACTS.md` | **Create new** |
| `docs/REQUEST_LIFECYCLE_EXAMPLE.md` | `docs/REQUEST_LIFECYCLE_EXAMPLE.md` | **Create new** — then copy content into README.md |
| `docs/README_IMPROVEMENTS.md` | *(reference only)* | Instructions for README edits |

## How to Integrate

### 1. Tests (copy files, run)
```bash
cp tests/conftest.py your-repo/tests/conftest.py  # merge with existing
cp tests/unit/test_proactive_engine.py your-repo/tests/unit/
cp tests/unit/test_learning_loop.py your-repo/tests/unit/
cp tests/integration/test_full_pipeline.py your-repo/tests/integration/
cd your-repo && make test-unit
```

### 2. Playbooks + Loader (new capability)
```bash
cp knowledge/hosting_playbooks.yaml your-repo/knowledge/
cp knowledge/playbook_loader.py your-repo/knowledge/
# Then wire into reasoner.py:
#   from knowledge.playbook_loader import PlaybookLoader
#   playbook = PlaybookLoader().lookup(perception.issue_category, perception.product_area.value)
```

### 3. LLM Profiles (config update)
```bash
cp config/llm_profiles.yaml your-repo/config/
# Compare with existing config, merge any new models
```

### 4. Docs (README update)
```bash
cp docs/AGENT_CONTRACTS.md your-repo/docs/
cp docs/REQUEST_LIFECYCLE_EXAMPLE.md your-repo/docs/
# Then follow docs/README_IMPROVEMENTS.md to update README.md
```

## What Each Improvement Addresses

| Improvement | Solves |
|-------------|--------|
| **conftest.py expansion** | Shared test fixtures reduce duplication across unit tests |
| **Integration test** | Proves the pipeline actually works end-to-end (the #1 gap) |
| **Hosting playbooks** | Domain-specific knowledge for African hosting (DirectAdmin, M-Pesa, .co.ke) |
| **Playbook loader** | Connects playbooks to the Reasoner for resolution path building |
| **Proactive engine tests** | Tests for signal monitor + trigger rules (currently untested) |
| **Learning loop tests** | Tests for collector + evaluator (currently untested) |
| **Agent contracts** | Formal spec for each agent's I/O, latency, failure mode |
| **Lifecycle example** | Makes the README's architecture claims tangible |
| **README improvements** | Supported scenarios, evaluation metrics, self-improving clarification |
| **LLM profiles** | Concrete model configs for the Router (vs abstract YAML template) |
