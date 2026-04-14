# Rocky — Current State

Last updated: 2026-04-14 (run-20260414-203004 — **T3 limit-overlay reach + LIVE LLM evidence**; backlog reconciled with per-risk resolution table)

## 2026-04-14 run (run-20260414-203004) — T3 limit-overlay reach + LIVE LLM evidence
User flagged the prior Phase 3 closeout shipped without live-LLM proof. This run resolved that gap and additionally shipped T3 limit-overlay so meta-variants reach LIVE retrieval. Three legacy retriever constructors (`LearnedPolicyRetriever`, `MemoryRetriever`, `StudentStore`) gained an optional `config: RetrievalConfig | None = None` kwarg sourcing top-K from `config.top_k_limit` when caller's `limit` is None. `RockyRuntime.load_from` + `refresh_knowledge` thread `active_overlay.retrieval` into each retriever — but only when an actual variant is active (CF-4 baseline-parity guard via `meta_registry.is_baseline_active()`). 8 new deterministic tests in `tests/test_meta_variant_live_reach.py`; sensitivity bites (revert→4!=2→restore→2==2). Full deterministic suite **428 passed + 12 skipped** (was 420+12, +8 net). LIVE LLM evidence: pre-T3 baseline 11/12 in 165.88s, post-T3 9/12 in 141.46s on gemma4:26b — different stochastic failures each run (pre: `sl_undo_behavioral`; post: `sl_promote_A/B/C` cascading from gemma's `memory_kind=lesson` reflection). Phase 3 changed nothing in `runtime.teach()` / reflection / `/undo` paths; both failure modes are gemma4:26b stochasticity, not regressions. Backlog updated with per-risk resolution table (29 rows) + honest reframing of prior compound's "12/12 PASS" claim. Live LLM stochasticity now an OPEN named risk owned by NS-7. Evidence: `docs/xlfg/runs/run-20260414-203004/evidence/live/{baseline,postT3}_full_run.txt`.

## 2026-04-14 run (run-20260414-194516) — Phase 3 SHIPPED; bounded meta-learning archive
PRD Phase 3 lands as additive surface. New `src/rocky/meta/` package (7 modules — `safety.py` + `variants.py` + `overlay.py` + `canary.py` + `ledger.py` + `registry.py` + `__init__.py`) + new `RetrievalConfig` + `PackingConfig` dataclasses in `src/rocky/config/models.py` + `cmd_meta` subcommand at `src/rocky/commands/registry.py`. `LedgerRetriever.__init__` and `build_system_prompt` gain optional `config` / `packing` kwargs that default to baseline; baseline behavior bit-identical when no variant active (CF-4 preserved). `RockyRuntime.load_from` constructs `MetaVariantRegistry` and threads the active overlay into `AgentCore`. Safety allow-list (`ALLOWED_KEYS` + `BLOCKED_KEY_PREFIXES`) checked at three sites — create / activate / overlay — with weight-subtree bounds added during review (F1). State machine: candidate → validated (canary-passed) → promoted (active) → rolled_back. Append-only meta-ledger at `.rocky/meta/meta_ledger.jsonl` records every transition. Offline deterministic canary at `tests/test_meta_variant_canary.py` proves a `top_k_limit=2` variant produces a measurable `-8` records delta on the default 6-record corpus; zero-edit variant produces zero delta (A9 sensitivity bites). 70 new deterministic tests across 7 files; full suite **420 passed + 12 skipped** (was 350 + 12). Zero regressions. Phase 3 entirely done and ready for Phase 4.

## 2026-04-13 run (run-20260413-032250) — Phase 2.5 SHIPPED; Phase 2 behaviorally closed
Both formerly-strict xfails flipped to regular PASS on gemma4:26b: `test_sl_retrospect_phase_B_behavioral_style_carries_over` and `test_sl_undo_behavioral_correction_fully_gone`. Plus all 10 structural SL-* tests. Live: 12/12 passed in 196s. Deterministic 350 passed, 12 skipped (340 + 10 new). Three coordinated fixes: O1 structured workflow extraction in packer (parse `## Repeat next time` / `## Avoid next time` from retro md), O2 post-gen style-gap repair in agent (`_RETRO_SHELL_CMD_RE` + `_repair_retrospective_style_gap` — re-invoke with instruction quoting actual observed shell commands; reject cosmetic paraphrase), T7-extension content-overlap fallback in `_active_teach_lineages` (CF-4-safe student_note substring-match + token overlap — closes the regression where gemma kept teach as lesson rather than policy). Sensitivity check confirmed (revert → import error → restore → 350 green). Commit 7d586e9.

## 2026-04-13 run (run-20260413-015018) — Phase 2.4 live-behavior fixes; 1/2 xfails flipped
Committed 4 per-phase squashed commits on main (2.1/2.2/2.3/docs). Then ran ROCKY_LLM_SMOKE=1 on gemma4:26b via remote Ollama. Two iterations (loopback 1 + 2). Final state: 11 of 12 live tests pass (was 10 pre-fix). Behavioral results: test_sl_undo_behavioral_correction_fully_gone FLIPPED to PASS (was xfail) via migration-dedup fix + candidate-correction-visibility restoration. test_sl_retrospect_phase_B_behavioral_style_carries_over STILL FAILS — gemma chose if __name__ self-test over python command invocation despite imperative style directive. Loopback cap reached; remaining work queued as Phase 2.5. Deterministic suite 340 passed, 12 skipped (unchanged). Commits: 6301555 (2.1), 9acccf9 (2.2), f622661 (2.3), 1c8418b (docs), c98fe41 (2.4).

## 2026-04-13 run (run-20260413-162250) — Phase 2.3 T2/T5/T6/T8/T9/T10 SHIPPED
6 of 7 remaining Phase 2 tasks shipped: T2 `LedgerRetriever` with 10-factor PRD §12.3 ranking (new `src/rocky/learning/ledger_retriever.py`, 7 tests), T5+T6 canonical 6-block packer + retrospective style extraction (`src/rocky/core/system_prompt.py` reorganized with `_append_framing_blocks` + `_append_learning_pack_blocks`, 6 tests), T8 deterministic context-budget benchmark (`tests/test_context_budget_benchmark.py`, 6 tests), T9 xfail decorator removals on both live behavioral tests (`test_sl_retrospect_phase_B_behavioral_style_carries_over` + `test_sl_undo_behavioral_correction_fully_gone`), T10 sensitivity-check documentation. Only T3 adapter collapse deferred — legacy retrievers kept alongside `LedgerRetriever` (additive). Full suite 340 passed, 12 skipped (from 321 baseline + 19 net). Realistic policy-heavy workload measured at **55.2% char reduction** (PRD §20.3 target 30%, exceeded). Operator next action: `ROCKY_LLM_SMOKE=1 pytest tests/test_self_learn_live.py` to verify behavioral xfails flip on gemma4:26b.

## 2026-04-13 run (run-20260413-161313) — Phase 2.2 /undo leak two-layer defense SHIPPED
T7 at-capture teach-lineage linking + T4 retriever-side rollback filter shipped. 8 new deterministic tests, zero regressions. Full suite 321 passed, 12 skipped. T7 links derived-autonomous memories to reused teach-lineages at capture time (via `ledger.find_teach_lineage_for_policy` + `_active_teach_lineages`); T4 filters rolled-back artifacts at read time (via `ledger.is_path_in_rolled_back_lineage` + `ContextBuilder._is_artifact_rolled_back`). Together they close the derived-autonomous leak at the structural layer. T9 live xfail flip (test_sl_undo_behavioral_correction_fully_gone) deferred to Phase 2.3 — will serve as integration-level sensitivity check once T5/T6 retriever+packer rewrite lands.

## 2026-04-13 run (run-20260413-124455) — Phase 2.1 teach-overtag guard SHIPPED
Refined O1 shipped. `AgentCore._maybe_upgrade_route_from_project_context` at `src/rocky/core/agent.py:237-253` now gates on "policy declares multiple task_signatures including current route". 5 parametrized regression tests in `tests/test_route_intersection.py` use the captured real `/teach` policy from run-20260412-013706 — not a synthetic fixture. Sensitivity check bites. Full suite 313 passed, 12 skipped. Legitimate cross-family upgrades preserved (tool-use-refusal single-declared policy + 3 product-catalog shell skills untouched).

## 2026-04-13 run (run-20260413-115313) — Phase 2 planning + honest-RED on O1
Planning artifacts produced for full Phase 2 (spec.md + context.md + solution-decision.md + test-contract.md + test-readiness.md). Scope was honestly narrowed mid-implement to T1 only given the user's "don't cheat, battle tested" directive and the ~1,000 LOC scope of T2..T10. T1 (teach over-tagging intersection allowlist) was implemented, passed isolated tests, then regressed `test_learned_tool_refusal_policy_can_upgrade_conversation_route_to_research` in the full suite — proving CF-8 as stated is architecturally insufficient. Fix reverted; 308-test baseline restored. T2..T10 + a refined O1 re-queued to Phase 2.1. North Star NS-1..NS-8 appended to backlog.

## Phase 3 bounded meta-learning archive (SHIPPED run-20260414-194516)
- **`src/rocky/meta/variants.py`**: `MetaVariant` dataclass with PRD §14 fields (variant_id, parent_variant_id, edits, archive_role, canary_results, created_at, promoted_at, rolled_back_at + promotion_state + notes). `MetaVariantStore` with append-only per-variant directory layout under `.rocky/meta/variants/<variant_id>/{variant.json, canary.jsonl}`; `rewrite_state` enforces immutable fields (variant_id, parent_variant_id, edits, created_at).
- **`src/rocky/meta/safety.py`**: `ALLOWED_KEYS` whitelist scoped to `retrieval.*` + `packing.*`; `BLOCKED_KEY_PREFIXES` (permissions, providers, tools.shell_timeout_s, tools.python_timeout_s, learning.enabled/slow_learner_enabled/auto_publish/auto_self_reflection, freeze, bypass, active_provider); `_BOUNDS` for top-level scalars + `_WEIGHT_SUBTREE_BOUNDS` for `authority_weight.*` / `promotion_weight.*` (added in F1 review). `validate_edits` raises `SafetyViolation` at three sites (create, activate, overlay).
- **`src/rocky/meta/overlay.py`**: `apply_variant_edits(retrieval, packing, edits) -> (RetrievalConfig, PackingConfig)` is a pure function — deep-copies inputs, never mutates baseline. Defense-in-depth `validate_edits` re-check at the overlay site.
- **`src/rocky/meta/canary.py`**: `CanaryRunner` runs a fixed `default_corpus()` (12 seed records across 2 tasks) against a temp ledger; offline deterministic; metrics include `top_k_record_count`, `top_1_record_id`, `top_score`, `packer_char_count`, plus aggregate `top1_stability_hash` for change-detection.
- **`src/rocky/meta/ledger.py`**: `MetaLedger` is an append-only JSONL log at `.rocky/meta/meta_ledger.jsonl` with event types (`created`, `canary_run`, `validated`, `promoted`, `activated`, `rolled_back`, `deactivated`, `rejected`).
- **`src/rocky/meta/registry.py`**: `MetaVariantRegistry` is the operator-visible coordinator. Owns `.rocky/meta/active.json` pointer with history. State machine: canary flips candidate → validated only when `differs_from_baseline=True`. Activate refused on candidate state. Rollback flips pointer to most recent prior `promoted` variant in history (else baseline) and stamps `rolled_back_at`. `apply_active_overlay` self-heals stale pointers (missing/corrupt/rolled-back active variant → falls back to baseline + records `deactivated` event).
- **`src/rocky/config/models.py`**: new `RetrievalConfig` + `PackingConfig` dataclasses with defaults equal to the prior hard-coded constants in `LedgerRetriever` and `system_prompt.py`. Both consumers gained an optional config kwarg; baseline behavior bit-identical without an active variant.
- **`src/rocky/commands/registry.py`**: `cmd_meta` subcommand (list / show / create / canary / activate / rollback / active). `create` accepts JSON edits payload via shlex-quoted argument; safety violations surface as `ok: False` with `violation_key` field.
- **`src/rocky/app.py`**: `RockyRuntime.load_from` constructs `MetaVariantRegistry`, calls `apply_active_overlay()`, threads `packing` (live in `build_system_prompt`) and `retrieval` (canary-only today; live wiring is the Phase 2 T3 deferral) into `AgentCore`.
- **70 new deterministic tests** across `tests/test_meta_variant_{schema,safety,overlay,canary,promotion,ledger,command}.py` (+1 weight-bounds test from review). All pass with `bare pytest -q` in <1s for the meta suite.

## Phase 3 acceptance signal for Phase 4
The `MetaVariant.canary_results` field is rich enough that Phase 4 (`improve@N` / transfer evaluation) can read it without schema changes. `differs_from_baseline` + per-task `top_1_record_id` + `top_k_record_count` + `packer_char_count` give a Phase-4 transfer engine the substrate to compute deltas across task families. The only Phase 3 → Phase 4 work is: (1) define a "task family" tag on `CanaryTask` (currently single-family corpus), (2) add `improve@N` calculator that consumes `MetaVariant.canary_results`, (3) extend the corpus with held-out-family tasks. No Phase 3 schema or mechanism needs to change.

## Test suite
- **420 deterministic tests** + 12 skipped, ~9 s, zero LLM dependency (bare `pytest -q`). +70 from new `tests/test_meta_variant_*.py` + `tests/test_meta_command.py`.
- **308 deterministic tests** historical baseline (before Phase 1), ~10s. +5 from new `tests/test_learning_ledger.py`.
- 8 in-process self-learn structural scenarios in `tests/test_self_learn_scenarios.py`
- **5 canonical ledger tests** in `tests/test_learning_ledger.py` (run-20260412-142114): round-trip, migration idempotency, lineage rollback with anti-monkey guard (unrelated records untouched), lineage-scoped self-reflect gate with boundary test (same thread_id + fresh lineage = NOT suppressed), one-canonical-record-per-teach.
- **Autonomous self-learn live catalog** in `tests/test_self_learn_live.py`, env-gated by `ROCKY_LLM_SMOKE=1`, subprocess-driven via `.venv/bin/rocky`. ~220s runtime, 10 passed + 2 xfailed. Five scenarios — no `/teach` except as setup for SL-PROMOTE and SL-UNDO:
  - **SL-MEMORY** — `MemoryStore.capture_project_memory` auto-classifies & auto-promotes a preference statement from a normal `run_prompt` turn; fresh subprocess's answer references the captured preference via the loaded memory. Live: T1 "Our team prefers using uv…" → `.rocky/memories/auto/constraint-…json` + T2 answer "You should use `uv`…".
  - **SL-RETROSPECT** — `_auto_self_reflect` (app.py:232) persists `.rocky/artifacts/self_reflections/retro_*.json` + `.rocky/student/retrospectives/*.md` on substantive tasks. Structural phase B: retrospective LOADS into T2's `trace.context.student_notes` across process boundary. Behavioral phase B: XFAIL(strict=True) — for gemma4:26b the retrospective loads but does NOT measurably shape verification-style generation (expected `python3 -c` per the retrospective title "functional verification via shell one-liners", got "Observed output:" block instead). Phase-2 context-packer / stronger-model target.
  - **SL-PROMOTE** — `/teach` seeds candidate; autonomous `record_query`→`_promote_policy_meta` flips POLICY.meta.json candidate→promoted on first `verification.status=pass` reuse. Live: meta before `top=candidate, vsc=0`; after `top=promoted, vsc=1`. No operator action.
  - **SL-BRIEF** — `rebuild_project_brief` auto-synthesises `.rocky/memories/project_brief.md`; fresh subprocess's `trace.context.memories` includes the brief entry. No /teach.
  - **SL-UNDO (Phase 1)** — ledger-aware `/undo` via `LearningLedgerStore.rollback_lineage()`. Structural phase PASSES: `data.rolled_back=True`, `moved` list has 4 artifacts (student notebook + student patterns + policy dir + learning reflection). Behavioral phase XFAIL(strict=True): post-undo model still prefers pnpm because `.rocky/memories/auto/*.json` + `project_brief.md` were captured under turn-lineage (derived-autonomous leak — Phase-2 scope).

## Phase 1 canonical ledger (SHIPPED run-20260412-142114)
- **`src/rocky/learning/ledger.py`**: `LearningRecord` dataclass (17 PRD §8.2 fields + `ledger_version` + `rolled_back` bookkeeping); `LearningLedgerStore` with append-only `.rocky/ledger/records.jsonl` + `.rocky/ledger/lineage_index.json`; `migrate_legacy_workspace()` for idempotent legacy→ledger migration; `new_lineage_id()` helper.
- **`runtime.learn()`** emits exactly one canonical `LearningRecord` per teach event with a `teach-<uuid>` lineage_id, and registers every produced artifact path (student notebook, student pattern, policy dir, reflection JSON) with the ledger's lineage index.
- **`run_prompt()`** generates a `turn-<uuid>` lineage_id per turn and registers artifacts written by `capture_project_memory` + `_auto_self_reflect` under that lineage.
- **`learning_manager.rollback_latest()`** now calls `ledger.rollback_lineage()` on the latest teach lineage, moving ALL registered artifacts into `.rocky/artifacts/rollback/<lineage_id>__<ts>/` atomically. Legacy single-store fallback preserved for tests that instantiate `LearningManager` without wiring a ledger.
- **`_auto_self_reflect()`** is gated on the current turn's lineage being rolled back (`ledger.is_lineage_rolled_back(lineage_id)`) — prevents PRD §8 Issue 1's second-order re-persistence bug where post-undo turns actively re-seed the correction.

## Phase-1 scope limits (honest Phase-2 targets)
- Retriever-reads-ledger-first is write-registration-only this run. Phase 2 unifies `LearnedPolicyRetriever`/`MemoryRetriever`/`StudentStore.retrieve()` onto the ledger read path.
- Derived-autonomous leak: `capture_project_memory` runs autonomously during `/teach`'s correction-reuse and writes memories under `turn-<uuid>` lineage, not the teach lineage. Teach rollback doesn't find them. XPASS on `test_sl_undo_behavioral_correction_fully_gone` is the acceptance signal.
- Retrospective style influence (from run 032319) unchanged — still Phase-2 packer work.
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
