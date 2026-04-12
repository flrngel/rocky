# Rocky — Current State

Last updated: 2026-04-12 (run-20260412-032319)

## Test suite
- 303 deterministic tests, ~9s, zero LLM dependency (bare `pytest -q`)
- 8 in-process self-learn structural scenarios in `tests/test_self_learn_scenarios.py`
- **Autonomous self-learn live catalog** in `tests/test_self_learn_live.py`, env-gated by `ROCKY_LLM_SMOKE=1`, subprocess-driven via `.venv/bin/rocky` (editable source; prior runs' stale-pipx gap closed). ~109s runtime, 9 passed + 1 xfailed. Four AUTONOMOUS self-learn pathways — no `/teach` except as setup for SL-PROMOTE:
  - **SL-MEMORY** — `MemoryStore.capture_project_memory` auto-classifies & auto-promotes a preference statement from a normal `run_prompt` turn; fresh subprocess's answer references the captured preference via the loaded memory. Live: T1 "Our team prefers using uv…" → `.rocky/memories/auto/constraint-…json` + T2 answer "You should use `uv`…".
  - **SL-RETROSPECT** — `_auto_self_reflect` (app.py:232) persists `.rocky/artifacts/self_reflections/retro_*.json` + `.rocky/student/retrospectives/*.md` on substantive tasks. Structural phase B: retrospective LOADS into T2's `trace.context.student_notes` across process boundary. Behavioral phase B: XFAIL(strict=True) — for gemma4:26b the retrospective loads but does NOT measurably shape verification-style generation (expected `python3 -c` per the retrospective title "functional verification via shell one-liners", got "Observed output:" block instead). Phase-2 context-packer / stronger-model target.
  - **SL-PROMOTE** — `/teach` seeds candidate; autonomous `record_query`→`_promote_policy_meta` flips POLICY.meta.json candidate→promoted on first `verification.status=pass` reuse. Live: meta before `top=candidate, vsc=0`; after `top=promoted, vsc=1`. No operator action.
  - **SL-BRIEF** — `rebuild_project_brief` auto-synthesises `.rocky/memories/project_brief.md`; fresh subprocess's `trace.context.memories` includes the brief entry. No /teach.
  - Research anchors (7 cited): Hyperagents (arXiv:2603.19461), Voyager (NeurIPS 2023, arXiv:2305.16291), RAGAs (EACL 2024, arXiv:2309.15217), RAG Eval Survey (arXiv:2405.07437), BenchPreS (arXiv:2603.16557), Catastrophic Forgetting (arXiv:2308.08747), OpenAI Memory docs.
  - **Replaced cheats**: run-013706 marker-injection (trivial instruction-following), run-023455 /teach-centric scenarios + irrelevant UNDO. This catalog tests SELF-learning — what Rocky writes autonomously during normal `run_prompt` turns.
  - **`ROCKY_BIN` default**: `.venv/bin/rocky` (editable install from src/) when present. Previously silently hit stale pipx binary.
- RunFlowManager multi-burst loop covered by 8 dedicated tests in test_run_flow.py
- Integration tests in test_agent_runtime.py use exact `==` call counts
- Web tool tests: 25 tests in test_web_tools.py
- Tool events tests: 6 tests in test_tool_events.py

## REPL toolbar
- Bottom toolbar at `ui/repl.py:452-467` shows keybindings, freeze/verbose state, token usage, context usage, session ID, provider label, thread ID
- Token usage label: `Tok P{prompt} C{completion} T{total}/{context_window}({pct}%)`
- Built-in defaults: litellm_local=32768, ollama=131072, openai=128000

## Learning subsystem — Hyperlearning v2 Phase 0 shipped (2026-04-12)
- **Candidate-never-hard invariant now enforced at two sites:**
  - `core/system_prompt.py` `## Learned constraints` block filters policies to `promotion_state == "promoted"` (default-on-missing). Candidate policies are still listed under `## Learned policies` for visibility but do NOT emit `Do not:` / `Do:` hard-constraint lines.
  - `core/agent.py::_learned_constraint_records` applies the identical filter so candidate rules never reach `_judge_learned_constraints` or `_repair_learned_constraint_output`. Judge prompt treats its input as hard rules — both sites must stay aligned.
- **Commands surface:**
  - `/policies` removed entirely (cmd_policies deleted; `"policies"` absent from names list).
  - `/learn <feedback>` hidden from `_help_text()` but the `learn ` prefix alias in `CommandRegistry.handle()` still dispatches to `runtime.learn()` (one-cycle transition alias per PRD §9.1).
  - `/learned review` filters to `promotion_state == "candidate"`, checking both top-level and `metadata.promotion_state` on the meta-JSON payload.
- **Config defaults:** `LearningConfig.slow_learner_enabled` defaults to `False` (both `config/models.py` and `config/loader.py` DEFAULT_CONFIG). `run_slow_learner()` short-circuits on the flag.
- **`_promote_policy_meta` consistency:** updates both `metadata.promotion_state` and top-level `promotion_state` to `"promoted"`; POLICY.md frontmatter is also rewritten.
- **Deferred work:** `docs/xlfg/knowledge/hyperlearning-backlog.md` captures PRD Phases 1 (ledger), 2 (retrieval rewrite), 3 (meta archive), 4 (transfer eval) + cross-cutting §17 (commands), §21 (safety), §23 (metrics).

## Scenario rule for self-learn tests
- Scenarios must exercise real `RockyRuntime`, `LearnedPolicyLoader`, `LearnedPolicyRetriever` — no mocks of the learning subsystem.
- Assertions must be on disk state / policy_id identity / system-prompt structure, never on substring matches of request text.
- Anti-tamper gate: every self-learn scenario file should contain at least one test that blanks the on-disk policy store and asserts the reuse observation flips to negative — this is the "never hard-code to pass the scenario" contract.
- Sensitivity checks (revert the fix → confirm test fails) are the honest proof that a code change is load-bearing.

## Agent loop
- Two execution paths in AgentCore.run():
  - Flow-controlled loop (_run_flow_controlled_loop): ALL tasks with tools (except conversation/)
  - Simple provider call: conversation tasks and tasks without tools
- _should_use_flow_loop() gate at agent.py:410 — returns True when route has tool_families AND task_signature is not conversation/
- Non-finalize early return works for ALL task types with full verification

## Flow loop task kinds by task type
- research/site → discover/gather/finalize (max_bursts=8)
- repo/shell_execution, automation/general → build/verify/finalize (max_bursts=4)
- extract/data → inspect/produce/finalize (max_bursts=4)
- fallback → inspect/finalize (max_bursts=4)

## Web tool system
- `search_web`: DuckDuckGo (3 endpoints) + Brave with algorithmic query broadening on zero results; emits `steps` list in metadata.
- `fetch_url`: readability-style BS4 extraction; strips nav/header/footer/aside; returns `link_items` with scored links.
- `agent_browser`: separate tool family with independent permissions.
- Bot detection: hard markers always trigger; soft markers require ≥2 matches OR 1 match + challenge HTTP status.
- `browser_fallback_hint: True` on bot challenge; `tool_events.py` emits "Hint: retry with agent_browser" fact.
