# Rocky v0.3.0 Upgrade Report

## Purpose

This upgrade moves Rocky from a prompt-routed tool assistant toward a stronger runtime-grounded, local-model-first general agent. The goal was not a cosmetic refactor. The goal was to improve:

- multi-turn continuation
- evidence discipline
- answer quality and directness
- memory safety
- workflow learning quality
- retrieval quality
- debuggability
- maintainability for future tuning

## Executive summary

Rocky v0.3.0 introduces a new runtime state layer centered on **active task threads**, **evidence graphs**, and **answer contracts**. That runtime state is now used to steer routing, context assembly, verification, memory promotion, and learning.

The most important change is architectural: Rocky now treats tool observations, user assertions, inferred claims, and learned behaviors as different classes of state instead of collapsing them into generic prompt/answer text.

## What changed at a high level

### 1. Thread-aware runtime

A new `runtime_state.py` module introduces:

- `ActiveTaskThread`
- `EvidenceGraph`
- `Claim`
- `AnswerContract`
- `AnswerContractBuilder`
- `EvidenceAccumulator`
- `ThreadRegistry`
- continuation scoring helpers

This gives Rocky a structured live unit of work instead of relying on unstructured recent text.

### 2. Continuation-aware routing

Routing now runs in two stages:

1. continuation resolution against active/recent threads
2. lexical route fallback

This reduces the classic failure mode where a tool-backed task starts correctly and then degrades into `conversation/general` on the next short follow-up.

### 3. Evidence-first context

Context assembly now includes:

- workspace focus
- active thread summary
- evidence summary
- contradiction summary
- answer target / answer contract
- learned behaviors
- durable memory
- handoffs

This gives the provider a much more structured state surface while keeping the raw prompt size manageable for local models.

### 4. Stronger answer discipline

Before finalizing an answer, Rocky now builds an answer contract that identifies:

- the current question
- allowed claims
- forbidden claims
- missing evidence
- uncertainty requirements
- brevity / delta-answering constraints

Verification now checks unsupported claims and answer drift rather than only tool-family shape.

### 5. Safer memory

Project memory capture no longer depends primarily on answer prose. Candidate memory now prefers:

- supported claims
- explicit user assertions/corrections
- verified paths from tool activity

Durable memory promotion is gated by provenance, contradiction state, and stability. Project briefs are rebuilt only from active promoted notes.

### 6. Better runtime learning

`/learn` now prefers the current thread snapshot rather than blindly binding to the last route surface. Learned skills are synthesized as workflow corrections with:

- failure class
- required behavior
- prohibited behavior
- evidence requirements
- candidate/promotion state
- reuse and verified-success counters

### 7. Better retrieval

Memory retrieval now considers:

- provenance strength
- contradiction state
- task signature match
- thread overlap
- stability/reusability

Learned-skill retrieval now considers:

- task signatures
- task family
- failure class
- trigger features
- thread overlap
- promotion state
- verified success count

### 8. Harness hardening

The repo already had seed-based generator/oracle harness assets. In v0.3.0 I kept that direction and hardened it further for local repeatability by ensuring deterministic runtime shim binaries are always materialized into the harness workspace, even when host binaries exist.

## Research inputs translated into code

The upgrade plan was shaped by several families of agent-system patterns:

- **LangGraph / graph-state orchestration** -> use explicit runtime state instead of implicit prompt-only state.
- **OpenHands action-observation loops** -> preserve observed tool events as first-class runtime evidence.
- **Aider’s context discipline** -> keep runtime context compact, task-focused, and delta-oriented.
- **Letta / hierarchical memory ideas** -> separate working state, candidate memory, and durable memory.
- **ReAct-style loops** -> keep tool execution intertwined with reasoning instead of one-shot narration.
- **Reflexion-style self-correction** -> use structured verifier feedback and repair loops, but avoid heavyweight self-reflection trees.
- **PydanticAI / eval-centric agent development** -> keep outputs structured and make evaluation/test surfaces explicit.
- **Anthropic eval guidance** -> prefer path-independent grading and generated tasks rather than memorized scenario literals.
- **Local-model-first tool/structured output patterns (e.g. Ollama ecosystems)** -> keep instructions explicit and compact; rely on runtime structure more than giant prompts.

Several tempting patterns were intentionally rejected:

- no full graph database
- no opaque planner stack with deep recursive subagents
- no multi-model cascade by default
- no prompt-only “fixes” for continuation or memory poisoning
- no heavyweight framework abstraction that would make later tuning harder

## Detailed file-level changes

### New module

#### `src/rocky/core/runtime_state.py`

Introduced a structured runtime state layer:

- claim model with provenance and contradiction refs
- evidence graph with artifacts, entities, questions, decisions, corrections
- active task thread model
- answer contract builder
- evidence accumulator from prompt and tool events
- thread registry persisted in session metadata
- continuation scoring helper

### Core runtime

#### `src/rocky/core/router.py`

Added:

- `ContinuationResolver`
- `ContinuationDecision`
- thread-aware route merging
- `Router.resolve(...)` returning both route and continuation decisions
- route metadata: confidence, source, continued thread id, continuation decision

#### `src/rocky/core/context.py`

Context is now built from structured runtime state, not only retrieved text. Added:

- `thread_summary`
- `evidence_summary`
- `contradictions`
- `answer_target`

#### `src/rocky/core/system_prompt.py`

Reduced monolithic behavior load and moved more behavior into runtime state. Added prompt blocks for:

- active thread
- evidence
- contradictions
- answer contract
- learned behaviors
- handoffs

Retained compatibility phrases required by the existing test suite.

#### `src/rocky/core/verifiers.py`

Extended verification output with:

- `failure_class`
- `unsupported_claim_ids`
- `missing_evidence_ids`
- `answer_drift_score`
- `memory_promotion_allowed`
- `learning_promotion_allowed`
- structured detail payloads

Added verification stages for:

- continuation/route validity
- claim support
- answer-discipline / repetition drift

#### `src/rocky/core/agent.py`

The main orchestration loop now:

- looks up session thread state
- resolves continuation before route finalization
- ensures/updates active task threads
- ingests prompt/tool evidence into the graph
- builds a pre-answer contract and structured context
- verifies answers with claim-level feedback
- persists thread/evidence/contract snapshots into traces

### Memory

#### `src/rocky/memory/store.py`

Added candidate-first memory support with:

- provenance-bearing `MemoryNote`
- `CandidateMemory`
- contradiction state
- promotion state
- supported-claim-based extraction
- durable-memory rebuild policy for project brief

#### `src/rocky/memory/retriever.py`

Retrieval now considers provenance, contradiction state, task relevance, thread overlap, stability, and reusability.

### Learning

#### `src/rocky/learning/synthesis.py`

Learned skills now encode workflow corrections instead of answer snippets. Added sections for:

- failure class
- operational guidance
- required behavior
- prohibited behavior
- evidence requirements
- workspace hints

#### `src/rocky/learning/manager.py`

Added candidate/promotion semantics and richer metadata:

- thread binding
- task family
- failure class
- promotion state
- reuse count
- verified success count

Verified successful reuse can promote candidate skills automatically.

### Retrieval / continuity support

#### `src/rocky/skills/retriever.py`

Learned-skill retrieval now weights:

- task signatures
- learned failure class
- task family
- promotion state
- verified success count
- thread overlap

#### `src/rocky/session/store.py`

Sessions now store both turn summaries and thread summaries. Handoff retrieval became more thread-aware.

### App/runtime integration

#### `src/rocky/app.py`

Integrated new state surfaces into the app layer:

- memory capture now uses supported claims and thread ids from traces
- memory capture is gated by verifier output
- `/learn` now binds to thread snapshots when available instead of only the last lexical route

### Harness

#### `src/rocky/harness/scenarios.py`

Hardened generator/oracle workspaces by always materializing runtime shim binaries into the harness bin directory so generated local-runtime oracles stay deterministic across machines.

## Tests added or updated

### Added

- `tests/test_runtime_state.py`
  - continuation inheritance for short follow-ups
  - answer contract support/uncertainty behavior
  - memory capture from supported claims rather than answer rhetoric
- `tests/test_runtime_learning_binding.py`
  - `/learn` binds to thread snapshots over the last lexical route surface

### Updated

- `tests/test_live_agentic_provider.py`
  - live tests now skip cleanly when the configured provider is unreachable in the current environment
- `tests/test_cli.py`
  - version assertion updated to `0.3.0`
- `tests/test_harness.py`
  - version assertion updated to `0.3.0`

## Verification performed

### Automated

Executed:

```bash
pytest -q
```

Result in this environment:

- **171 tests passed**
- **112 tests skipped** (live/provider-dependent tests auto-skipped because the configured local provider endpoint was unreachable)
- **0 failed**

### Additional direct checks

- `python -m compileall src tests`
- targeted runs of new runtime-state and learning-binding tests during development
- targeted reruns for system prompt, learning, and harness compatibility after patching

## What remains unverified here

The following were not directly validated end-to-end against a real local LLM in this environment:

- actual live local model quality across multiple model families
- latency/quality tuning under your machine’s CPU/GPU/VRAM constraints
- long-horizon multi-session behavior with real operator feedback over time
- real browser/web tooling behavior against your machine’s installed Playwright/browser stack
- learned-skill promotion quality across many real reuse episodes

The code and docs were built to make those next steps easier, not to pretend they were already proven here.

## Tradeoffs made intentionally

### Chosen

- explicit runtime state over hidden prompt conventions
- shallow-but-solid thread/evidence abstractions over grand agent frameworks
- candidate-first promotion over immediate durability
- structured verifier outputs over purely free-text repair messages
- compatibility with the existing tool/provider architecture rather than a total rewrite

### Deferred

- richer planner/decomposer model
- long-context summarization compaction policies per thread
- stronger contradiction resolution between many durable memories
- richer oracle grading for learning promotion
- more specialized local-model adapters

## Recommended next tuning priorities

1. Tune provider/model defaults for your hardware.
2. Run real local evals across continuation, automation, extraction, and repo tasks.
3. Inspect traces for unsupported claims and answer drift.
4. Tune retrieval thresholds for project memory and learned skills.
5. Add more eval generators around contradiction handling and long-horizon continuation.

## Deliverables included in this release

- upgraded codebase
- version bump to `0.3.0`
- updated tests
- updated README
- `RELEASE_v0.3.0.md`
- `UPGRADE_REPORT_v0.3.0.md`
- `ROCKY_TUNING_KNOWLEDGE.md`

