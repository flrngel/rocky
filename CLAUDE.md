# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Rocky

Rocky is a CLI-first, continuously learning general agent. It routes user tasks through different lanes (DIRECT, STANDARD, DEEP, META), selects appropriate tools, calls an LLM provider, verifies results, and learns from feedback. Default LLM backend is Ollama (litellm_local).

## Commands

```bash
# Install (dev mode with uv)
uv pip install -e ".[dev]"

# Run the agent
rocky "your task here"
rocky --provider <name> --model <model> "task"

# Deterministic suite (baseline: 730 passed + 14 skipped)
pytest -q
pytest tests/test_cli.py::test_name   # single test
pytest --cov                          # with coverage

# Live-LLM suite (real Ollama — see AGENTS.md for the L20 trigger rule)
ROCKY_LLM_SMOKE=1 ROCKY_BIN=./.venv/bin/rocky pytest tests/agent/test_self_learn_live.py -v
```

No separate lint or build step — setuptools builds from `src/` layout.

## Architecture

### Runtime bootstrap

`cli.py:main()` → parses args → `RockyRuntime.create()` (in `app.py`) wires all subsystems → either one-shot execution or interactive REPL (`ui/repl.py`).

### Request flow

1. **Router** (`core/router.py`) — classifies task into a `TaskClass` (REPO, RESEARCH, DATA, SITE, etc.) and picks a `Lane` (DIRECT/STANDARD/DEEP/META). Uses lexical heuristics + continuation detection for multi-turn threads.
2. **ContextBuilder** (`core/context.py`) — assembles a `ContextPackage` with system prompt, memories, skills, policies, evidence graph, and answer contract.
3. **AgentCore** (`core/agent.py`) — orchestrates the turn loop: sends messages to the LLM provider, dispatches tool calls via `ToolRegistry`, accumulates evidence, and runs verification.
4. **Verification** (`core/verifiers.py`) — `VerifierRegistry` runs checks (evidence support, list requirements, answer format, tool success) producing `VerificationResult`.

### Provider system

`providers/registry.py` — `ProviderRegistry` instantiates the active provider from config. Three styles: `LITELLM_CHAT` (default — Ollama), `OPENAI_CHAT`, `OPENAI_RESPONSES`. Provider is selected per config, overridable via CLI `--provider`/`--model`/`--base-url`.

### Tool system

`tools/base.py` defines `Tool` (with JSON schema) and `ToolResult`. `tools/registry.py` manages the registry. Tool families: `shell`, `filesystem`, `web`, `browser`, `spreadsheet`, `git`, `python_exec`. Tools receive a `ToolContext` with workspace paths and permission info. Read-only vs write tools are distinguished for permission gating.

### Learning system

`learning/manager.py` — `LearningManager` records episodes, synthesizes policies via `PolicySynthesizer`. The `/learn` command publishes policies to `.rocky/policies/learned/`. On subsequent runs, `LearnedPolicyRetriever` loads matching policies into context. This is distinct from authored skills.

### Memory and Skills

- **Memory** (`memory/store.py`, `memory/retriever.py`) — persists memories to disk with auto-categorization and provenance tracking. Retrieved by keyword match.
- **Skills** (`skills/loader.py`, `skills/retriever.py`) — loads `SKILL.md` files from skill roots. Bundled skills live in `data/bundled_skills/`. Skills have metadata (triggers, keywords, task_signatures).

### State tracking

`core/runtime_state.py` — `ThreadRegistry` manages `ActiveTaskThread` instances. `EvidenceGraph` tracks claims with provenance. `AnswerContract` specifies what the answer must contain.

## Testing

See `AGENTS.md` for the full testing discipline: deterministic baseline, live-LLM triggers (**L20**), triple-live rule, sensitivity witnesses, and the decision-rule table for when live-LLM A/B is required before shipping.

## Conventions

- Python 3.11+ with full type annotations.
- Heavy use of `@dataclass(slots=True)`.
- Source lives in `src/rocky/`; tests in `tests/`.
- Config stored at `~/.config/rocky/config.yaml`; workspace state in `.rocky/`.
- Permission modes: `plan`, `supervised`, `accept-edits`, `auto`, `bypass`.
