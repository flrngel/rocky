# Rocky Hyperlearning v2 — deferred backlog

Provenance
- Authored during run-20260412-000228
- PRD: `/Users/flrngel/Downloads/rocky_hyperlearning_v2_prd.md`
- Date: 2026-04-12
- Shipped in that run: Phase 0 safety patch (candidate-never-hard, `/policies` removed, `/learn` hidden, `/learned review`, `slow_learner_enabled=False`) + self-learn verification scenarios in `tests/test_self_learn_scenarios.py`.

This document captures every PRD obligation that remains after Phase 0. Future `/xlfg` runs can resume each phase independently; the phases are loosely ordered but not strictly sequential once the ledger exists.

---

## What's next — recommended run order (as of 2026-04-13)

Current state: **Phase 1 shipped, Phase 2 mostly shipped (T1/T2/T4/T5/T6/T7/T8/T9/T10), Phase 3/4 + North Star queued.**

### STATUS 2026-04-13 (run-20260413-015018): 1 of 2 live behavioral targets flipped

Run result on gemma4:26b:
- ✅ `test_sl_undo_behavioral_correction_fully_gone` — FLIPPED to PASS via Phase 2.4 migration-dedup + candidate-correction-visibility fixes.
- ❌ `test_sl_retrospect_phase_B_behavioral_style_carries_over` — STILL FAILS. T1 receives the shell-style retrospective cue ("verified via Python one-liner"), but T2 on a lexically-different prompt chooses `if __name__ == "__main__"` + `print()` self-verification — gemma's native preference for self-contained `__main__` blocks over shell command invocation, not overridden by the imperative style directive.

Phase 2 is **behaviorally 1/2 closed**. Phase 2.5 is the open work to flip the remaining retrospective test.

### Phase 2.5 — Retrospective style influence on gemma-class models
Fix candidate hypotheses (escalated from run-20260413-015018):
1. Post-generation harness hook: if a retrospective tagged `shell` is in context AND the answer text lacks a shell-command literal regex, re-invoke the model with an appended instruction "your verification must use a shell command literal like `python3 -c` or `python <file>.py` — replace the `__main__` self-test". Gated to the automation/general flow loop's verify burst (decision C divide-conquer).
2. Add `required_textual_pattern` field on retrospective records; flow loop's verify step enforces pattern match before advancing to finalize.
3. Model-capability guard: keep the test as regular but mark with `@pytest.mark.skipif(os.environ.get("ROCKY_LIVE_MODEL", "") not in RESPONSIVE_MODELS)`. Demotes the claim from "all models flip" to "models in an allowlist flip." Least satisfying but most honest.
4. Model upgrade — try qwen3.5:27b or nemotron-cascade-2 (available on the same host) for comparison.

Phase 2.5 acceptance: `test_sl_retrospect_phase_B_behavioral_style_carries_over` PASSES on the chosen target model with honest sensitivity check (revert fix → test fails → restore → passes).

### NEXT-1 (blocking for "Phase 2 fully verified") — Operator live-harness verification
Before starting any new phase, **the operator must run the live suite** to confirm the two decorator-removed tests actually flip on gemma4:26b:
```bash
ROCKY_LLM_SMOKE=1 .venv/bin/pytest tests/test_self_learn_live.py -v
```
- **Both regular → PASS**: Phase 2 is behaviorally closed. T3 can be picked up as cleanup; skip to NEXT-3.
- **Either FAILS**: open **Phase 2.4** — investigate whether style block needs stronger placement (higher in the pack, bigger font), whether retrospective cue needs a more imperative phrasing, or whether the target model simply can't carry the signal (in which case the test should carry an explicit model-capability guard, not an xfail).

### NEXT-2 (only if NEXT-1 flags a model-capability gap) — Phase 2.4 packer-influence hardening
Scope narrowed by the specific live failure:
- If **retrospect** test fails: elevate `## Verification / Style conventions` above `## Project instructions`; try imperative rewording of the style cue ("Use `python3 -c` for verification" instead of "style: shell"); consider giving retrospectives a `## Operator preference` promotion when their failure_class matches the current task.
- If **undo** test fails: contamination-scan fallback per CF-10 option 2 — on `/undo`, also scan memory evidence_excerpt fields for the rolled-back feedback text, move matches.
Acceptance: original live test flips to PASS.

### NEXT-3 (cleanup, unblocks Phase 3) — T3 adapter collapse
Wire `LearnedPolicyRetriever / MemoryRetriever / StudentStore.retrieve` to internally delegate to `LedgerRetriever`, preserving their public signatures. Low-urgency; code-cleanup only. Do it alongside Phase 3 work once `LedgerRetriever.retrieve` is known-good from operator usage.

### NEXT-4 (next phase) — Phase 3 bounded meta-learning archive
See Phase 3 section below. Blocks on: stable ledger reads (Phase 2 done), canary harness (can reuse `tests/test_context_budget_benchmark.py` pattern + deterministic replay). Depends on no live xfails outstanding.

### NEXT-5 (after Phase 3) — Phase 4 transfer evaluation
See Phase 4 section below.

### NEXT-6 (post-learning-stack) — North Star productization (NS-1..NS-8)
The learning substrate is instrumental, not the goal. Once Phase 3+4 stabilize, begin North Star. Priorities within NS:
- **NS-5 typed `/teach` response**: most operator-visible; would retire the Phase 2.1 guard by narrowing task_signatures at write time.
- **NS-1 `/learning` command family**: makes the ledger operator-navigable.
- **NS-2 metrics dashboard**: every future change ships with a before/after metric.
- **NS-3 safety governance**: freeze mode + adversarial `/teach` rejection.
- **NS-4/6/7/8**: legacy cleanup, reliability hardening, cross-model robustness, workspace portability.

### Parking lot / deprioritized
- PRD §17.1 `/memory` redesign — touches user-facing command surface; wait for Phase 2 live verification + NS-1 before reopening.
- PRD §18 legacy removal (`cmd_student`, `slow_learner`) — migration window still open; defer until NS-4.

---


## Phase 1 — Canonical Learning Ledger

**STATUS: SHIPPED (run-20260412-142114)** — `src/rocky/learning/ledger.py` + migration + lineage-aware /undo + self-reflect rollback gate + 5 deterministic ledger tests + 1 live regular-PASS + 1 live behavioral XFAIL(strict) scoping the derived-autonomous leak to Phase 2. SL-UNDO structural test proves 4 teach-fanout artifacts move on /undo (vs the pre-Phase-1 single-store behavior).

PRD references: §8.2 "Canonical data model", §16.1 FR-1, §20.2 "Phase 1", §25.2 migration mapping.

Goal: every `/teach` event creates one canonical record lineage instead of parallel notebook/pattern/policy artifacts. Legacy stores remain readable but no longer receive new writes.

Shipped in run-20260412-142114:
- **Multi-store `/undo` leak fix (concrete, live-verified in run-20260412-023455):** `runtime.undo()` → `learning_manager.rollback_latest()` (`src/rocky/learning/manager.py:424`) currently only `shutil.move`s the `.rocky/policies/learned/<policy_id>/` directory into `.rocky/artifacts/rollback/`. The following durable stores created by the same `/teach` event are NOT touched and continue to inject the correction into post-undo system prompts: `.rocky/student/notebook.jsonl`, `.rocky/student/patterns/*.md`, `.rocky/student/retrospectives/*.md`, `.rocky/memories/candidates/*.json`, `.rocky/memories/auto/*.json` (note auto-promoted memories), `.rocky/memories/project_brief.md`. Evidence: `tests/test_self_learn_live.py::test_sc_undo_phase_F_behavioral_correction_gone` is `xfail(strict=True)` with full evidence in `docs/xlfg/runs/run-20260412-023455/evidence/live/sc_undo/`. **Second-order issue**: `AgentCore`'s self-retrospection (`self_learning.persisted=True` in post-undo traces) actively WRITES NEW retrospective + pattern artifacts during each post-undo reuse turn, re-widening the leak on every interaction. Phase 1 fix must BOTH (a) route /teach writes through the canonical ledger instead of fanning out to parallel stores AND (b) gate `self_learning` promotion logic on rollback state so post-undo turns do not re-record. XPASS on the xfail test is the alert that Phase 1 is complete.
- Define the `LearningRecord` dataclass per PRD §8.2 (fields: `id`, `kind`, `scope`, `authority`, `promotion_state`, `activation_mode`, `task_signature`, `task_family`, `failure_class`, `triggers`, `required_behavior`, `prohibited_behavior`, `evidence`, `lineage`, `created_at`, `updated_at`, `origin`, `reuse_stats`).
- Implement `LearningLedgerStore` with JSONL append + meta sidecar layout under `.rocky/ledger/`.
- Implement write adapters that route `runtime.teach()` and `runtime.learn()` into the ledger in addition to (later: instead of) `StudentStore` + `LearnedPolicyLoader`.
- Implement lookup adapters so existing `LearnedPolicyRetriever`, `MemoryRetriever`, and `StudentStore.retrieve()` queries read from the ledger by kind filter.
- Implement migration mapping per PRD §25.2 (`project auto memory goal/constraint/preference/decision/path/fact → kind=<same>`; `student pattern → kind=procedure`; `legacy learned skill → kind=procedure, origin=migration_legacy_skill`; etc.).
- Cover with a focused test file exercising a round-trip: teach → ledger record → retrieve by lookup adapter → matches expected canonical id.
- Acceptance: each new teach event has one canonical lineage id (PRD §20.2 success criterion). No new parallel notebook+pattern+policy durable artifacts are created.

### Residual Phase-1 items (inherited by Phase 2)
- **A4 retriever-reads-ledger-first**: `LearnedPolicyRetriever`, `MemoryRetriever`, and `StudentStore.retrieve()` still read from legacy filesystem walks. Phase 1 covers write-registration only. Phase 2 must swap retriever internals to query `ledger.filter_by_kind(...)` before the legacy walk, then eventually retire the legacy walk entirely.
- **Derived-autonomous leak** (live-verified in run-20260412-142114): when `/teach`'s correction is reused before `/undo`, `capture_project_memory` autonomously writes `.rocky/memories/candidates/*.json` + `.rocky/memories/auto/*.json` + `.rocky/memories/project_brief.md` under a turn-lineage (`ln-<uuid>`), NOT the teach-lineage (`teach-<uuid>`). Teach-lineage rollback doesn't move them. Evidence: `tests/test_self_learn_live.py::test_sl_undo_behavioral_correction_fully_gone` is `xfail(strict=True)`. Fix options: (1) link turn-lineage derivatives to the teach-lineage active at capture time, (2) add a contamination scan to `/undo` that moves memory entries whose `evidence_excerpt` matches the rolled-back feedback, or (3) Phase-2's unified retriever filters out memories captured while a rolled-back policy was active. XPASS is the acceptance signal.

## Phase 2 — Runtime retrieval + context packing rewrite

PRD references: §12 "Runtime retrieval and context assembly redesign", §16.5 FR-5 (dedupe at runtime), §20.3 "Phase 2", §11 promotion/authority model, §12.4 deduplication rules.

Goal: replace "retrieve everything relevant" with "retrieve one packed operating brief". Reduce learning-related prompt chars by 30%+ without regressing replay performance (PRD §20.3 success criteria).

**Teach over-tagging fix — SHIPPED in run-20260413-124455**: Guard in `AgentCore._maybe_upgrade_route_from_project_context` at `src/rocky/core/agent.py:237-253`. When a POLICY (not skill) declares multiple task_signatures including the current route, the guard prefers the current route (skips cross-family upgrade). Evidence: 5 parametrized tests in `tests/test_route_intersection.py` using the captured real `/teach` policy from run-20260412-013706 evidence tree. Sensitivity check: fix reverted → all 5 tests fail → restore → 5 pass. Full suite 313 passed (from 308 baseline + 5 new). Legitimate cross-family upgrades protected: `test_learned_tool_refusal_policy_can_upgrade_conversation_route_to_research` (single-declared policy passes), 3 product-catalog skill tests (skills exempt from guard).

Deferred work items:
- **Teach over-tagging fix (historical — SHIPPED run-20260413-124455):** The current symbol is `AgentCore._maybe_upgrade_route_from_project_context` (`src/rocky/core/agent.py:215–305`, renamed from `_refine_route_with_project_guidance`) with helper `_infer_route_signatures_from_guidance` at 307–336. It re-infers route signatures by running the lexical router over a concatenation of the current prompt + policy description + feedback_excerpt + required_behavior + prohibited_behavior + evidence_requirements. Because `/teach` auto-generates policies with broad descriptions, a learned policy for a greeting correction can hijack subsequent greeting prompts into `repo/shell_execution` or `site/understanding/general` with `source=project_context, confidence=0.93`.
  - **Run-20260413-115313 finding**: The naive "intersection allowlist" fix (skip inference-extension when guidance declared any task_signatures) is **too aggressive**. It regresses `tests/test_agent_runtime.py::test_learned_tool_refusal_policy_can_upgrade_conversation_route_to_research` — a test that explicitly proves a policy declaring `task_signatures: [conversation/general]` MUST upgrade to `research/live_compare/general` via description-driven inference when the prompt semantically aligns. This is intentional product behavior.
  - **Revised fix directions for Phase 2.1** (pick one or combine):
    1. Raise the scoring threshold or add a misalignment penalty in the inference-extension scoring path (`_maybe_upgrade_route_from_project_context:281`), not the candidate-enumeration path. The bug vs feature distinction is prompt-policy semantic alignment — a continuous signal, not a binary declared/undeclared flag.
    2. Add a per-policy `allow_inference_extension` metadata flag (default True for legacy, False for future auto-generated `/teach` output). Requires `/teach` to stop over-tagging at write time — pair this with a write-side narrowing pass that scopes task_signatures to lexically-aligned classes only.
    3. Score inferred-only (not declared) signatures differently from declared-plus-inferred signatures, so declared alignment weighs higher than pure inference.
  - **Both regression tests must stay green simultaneously**:
    - `test_learned_tool_refusal_policy_can_upgrade_conversation_route_to_research` (exists; defends the legitimate cross-family upgrade case).
    - `test_greeting_policy_does_not_reroute_greeting_to_shell` (does NOT exist; must be added alongside any future O1 fix).

**T7 at-capture teach-lineage linking — SHIPPED in run-20260413-161313**: `src/rocky/learning/ledger.py::find_teach_lineage_for_policy` + `src/rocky/app.py::_active_teach_lineages` + call in `run_prompt` that registers derived-autonomous memory artifacts under any reused-teach-lineage IN ADDITION to the turn-lineage. Closes the "derived-autonomous leak" at the write-registration layer. 5 deterministic tests in `tests/test_at_capture_lineage.py`. CF-4 (autonomous pathway preservation) maintained — only fires when trace.selected_policies has teach-origin records.

**T4 retriever-side rollback filter — SHIPPED in run-20260413-161313**: `src/rocky/learning/ledger.py::is_path_in_rolled_back_lineage` + `src/rocky/core/context.py::ContextBuilder._is_artifact_rolled_back` + filter application in memories/skills/policies/student-notes collection. Belt-and-suspenders: artifacts whose lineage is rolled back are dropped from context even if the file is still on disk. 3 deterministic tests in `tests/test_rollback_filter.py`.

**Phase 2.3 shipped (run-20260413-162250)**:
- **T2 LedgerRetriever + 10-factor ranking** — new module `src/rocky/learning/ledger_retriever.py` exposing `LedgerRetriever.retrieve(prompt, task_signature, *, thread=None, limit=8, kind_filter=None) -> list[RankedRecord]`. Each record carries a `rank_breakdown` dict with the PRD §12.3 10 factors (authority, promotion_state, task_signature, task_family, thread_relevance, prompt_relevance, trigger_literal, failure_class, evidence_quality, recency, conflict_status, prior_success). Additive — existing retrievers untouched. 7 deterministic tests in `tests/test_ledger_retriever.py`.
- **T5 6-block context packer** — `src/rocky/core/system_prompt.py` reorganized into `_append_framing_blocks` + `_append_learning_pack_blocks`. Canonical 6-block layout per PRD §12.1: Hard constraints, Workspace brief, Verification/Style conventions, Procedural brief, Curated skills, Retrieved memory+student notebook. CF-14 two-site gate preserved (filter at `system_prompt.py:76` + judge at `core/agent.py::_learned_constraint_records`).
- **T6 retrospective style extraction** — new helper `_style_cue_from_retrospective(note)` detects shell/format/tool-use style families from retrospective title + text; surfaces a compact cue in the Verification block. Retrospective bodies retained in compact form (400-char limit, down from 4000).
- **T8 context-budget benchmark** — `tests/test_context_budget_benchmark.py` with 3-fixture corpus (policy_heavy, retrospective_heavy, mixed). Policy-dominated workloads achieve ≥20% reduction floor on the compact fixtures; ad-hoc measurement on realistic 4-policy fixture (~1.6 KB per body) shows **55.2% reduction** (12909 → 5781 chars), well above PRD §20.3 30% target. Retrospective-heavy honestly does not regress.
- **T9 xfail decorator removal** — `test_sl_retrospect_phase_B_behavioral_style_carries_over` and `test_sl_undo_behavioral_correction_fully_gone` now regular tests. Will pass or fail honestly under `ROCKY_LLM_SMOKE=1` — no more `xfail(strict=True)` gating. Operator verification via live harness is the integration-level sensitivity check for T5+T6+T7.
- **T10 sensitivity-check documentation** — `docs/xlfg/runs/run-20260413-162250/verification.md` enumerates per-task revert-to-bite checks; live T9 flips deferred to operator auth.

**Still queued (T3 + operator verification)**:
- **T3 adapter collapse** — internally wire `LearnedPolicyRetriever`, `MemoryRetriever`, `StudentStore.retrieve` to delegate to `LedgerRetriever` while preserving public signatures. Deferred from Phase 2.3 because the legacy retrievers already work and the refactor risks regressions without matching behavioral improvement. A future Phase 2.4 run can do this with full-suite regression coverage.
- **Operator live verification** — run `ROCKY_LLM_SMOKE=1 pytest tests/test_self_learn_live.py` to confirm T6 and T7 actually flip behaviorally on gemma4:26b. Either outcome is honest; a failure would name Phase 2.4 model-capability work.
- Unify into one ledger retriever with kind filters + one curated-skill retriever. Delete or collapse `MemoryRetriever`, `StudentStore.retrieve()`, and the second-layer dedup path in `ContextBuilder`.
- Implement ranking engine per PRD §12.3 factors: authority, promotion state (`candidate<validated<promoted`), task-signature match, task-family match, thread relevance, failure-class match, evidence-support quality, recency, conflict status, prior-success attribution.
- Implement context packer per PRD §12.1 blocks: (1) hard-constraints summary (deduped, authority-aware), (2) workspace brief, (3) procedural brief, (4) ≤2 examples, (5) curated skills only when stronger than procedure briefs, (6) thread handoff + evidence + answer contract. Retire the current `## Learned policies` verbose injection.
- **Retrospective influence gap (live-verified, run-20260412-032319):** Autonomous retrospectives (`.rocky/artifacts/self_reflections/retro_*.json` + `.rocky/student/retrospectives/*.md`) persist and DO cross the process boundary into the next turn's `trace.context.student_notes`, but for gemma4:26b the retrospective's style-specific guidance is NOT measurably followed in generation. Live probe: retrospective titled "Python functional verification via shell one-liners" loaded into T2's context; T2 answer for a similar task emits "Observed output:" code block instead of a `python3 -c` shell one-liner. Evidence: `tests/test_self_learn_live.py::test_sl_retrospect_phase_B_behavioral_style_carries_over` is `xfail(strict=True)`. Phase-2 context-packer must either (a) strengthen retrospective influence via explicit style-guidance extraction into a top-level brief block rather than raw note injection, (b) rank retrospective notes higher in the pack ordering, or (c) mark certain retrospectives as procedural (hard) vs reflective (soft). XPASS on the xfail is the acceptance signal.
- Enforce hard/soft/shadow activation at pack time (PRD §11.3 authority tiers). Notebook becomes audit-only; brief is canonical runtime content.
- Implement deduplication rules from PRD §12.4 (same rule as note+procedure → keep procedure; same guidance in skill+procedure → prefer skill for playbook, procedure for corrective rule; compact retrospectives into meta-summary).
- Add a context-budget benchmark per PRD §15.2 table (replay canary + project-local benchmark + context-budget benchmark).
- Acceptance: measurable ≥30% reduction in learning-related prompt chars on comparable tasks; equal or better replay performance; fewer conflicting-guidance collisions.

## Phase 3 — Bounded meta-learning archive

PRD references: §14 "Hyperagent-inspired archive and branching", §16.6 FR-6, §20.4 "Phase 3", §11 authority model, §21 safety rails.

Goal: let Rocky improve parts of the learning procedure itself (retrieval config, promotion thresholds, packing budgets, evaluation thresholds). Meta-variants are versioned, archived, and comparable under replay canaries before any promotion.

Deferred work items:
- Define `MetaVariant` schema: `variant_id`, `parent_variant_id`, `edits` (config deltas), `archive_role` (baseline/branch/promoted), `canary_results`, `created_at`, `promoted_at`, `rolled_back_at`.
- Implement archive storage at `.rocky/meta/variants/` — versioned directory per variant, append-only.
- Implement replay/canary engine: given a variant and a set of replay tasks, execute them against the variant config, capture outcomes, compare against baseline. Must be offline-capable (replay uses stored traces, not live provider calls).
- Implement promotion/rollback rules per PRD §11: variant may not activate as `hard` without passing configured gates; every promotion has lineage; rollback returns prior active meta config cleanly.
- Enforce that no meta-variant can weaken security boundaries (PRD §21.1 rule 2). Implement allow-list of editable config keys; reject edits that touch permissions, freeze behavior, or tool allow/deny logic.
- Surface variants via `/learning experiments` (PRD §17.3) once the command family lands.
- Acceptance: Rocky can compare at least two retrieval/promotion variants; a promoted meta-variant yields statistically meaningful replay improvement without safety regressions.

## Phase 4 — Transfer evaluation

PRD references: §15.3 `improve@N`, §16.7 FR-7, §20.5 "Phase 4", §23.3 meta-learning metrics.

Goal: measure whether a learned procedure from one task family helps another task family. This is the `Hyperagents` signature check that Rocky's current self-learning lacks (PRD §3.3).

Deferred work items:
- Define `improve@N`: given baseline score B and N iterations of meta-learning yielding scores S_1..S_N, report `max(S_i - B)` and `mean(S_i) - B` for both same-family and held-out-family replay sets.
- Build a transfer-benchmark bundle per PRD §15.4: replay canary + project-local benchmark + task-family transfer benchmark + context-budget benchmark + safety benchmark. At least one task-family-transfer benchmark must be automated.
- Extend experiment reports to include direct-task AND transfer-task deltas, not just same-family gains.
- Acceptance: at least one meta-variant shows positive transfer improvement over baseline; reports surface both direct and transfer deltas.

## Cross-cutting obligations (not a phase)

These must be picked up alongside Phases 1–3 rather than as a dedicated run.

- PRD §17 command-by-command product requirements. In particular §17.2 expands `/teach` into a typed response shape (`classification`, `scope`, `authority`, `activation_mode`, `promotion_state`, `record_id`, `why`, `derived_actions`). Not delivered in Phase 0.
- PRD §17.3 introduces `/learning` as the single operator inspection surface (`status`, `list`, `show`, `review`, `approve`, `reject`, `rollback`, `trace`, `experiments`). Phase 0 shipped only `/learned review` as a compatibility stub.
- PRD §17.1 `/memory` redesign: keep user-facing semantics but route into the ledger with `/memory list|add|set|show|remove`.
- PRD §21 safety and governance: hard rules on candidate/self-generated records, freeze-mode behavior, human oversight model.
- PRD §23 success metrics: product / learning-quality / meta-learning metric dashboards. Need a local reporter command (likely `/learning status --metrics`).
- PRD §18 removal list: after migration windows close, delete `cmd_student`, legacy `skills/learned` write path, and the heuristic `slow_learner` entirely. Phase 0 only disabled `slow_learner`.
- PRD §19 keep list: curated skills, rollback, audit trail of raw teacher feedback, project brief, self-retrospective (compacted and downgraded) — each has specific contracts to preserve during the rewrites above.
- PRD §22 risks: migration confusion, over/under-consolidation, meta-learning instability, operator distrust, context regression, safety drift. Future runs should reference each risk mitigation when touching the relevant surface.

## Suggested ordering for future runs

1. Phase 1 (ledger) unblocks everything else — it defines the canonical write target.
2. Phase 2 (retrieval) depends on Phase 1 but is the highest operator-visible payoff (context-budget wins, fewer conflicts).
3. Phase 3 (meta archive) depends on Phases 1+2 because it needs a stable read surface + a knob surface to vary.
4. Phase 4 (transfer eval) depends on Phase 3 because it needs variants to compare and an archive to sample from.
5. `/learning` command family and `/teach` typed response can ship incrementally during Phases 1+2.

## North Star — Production-grade, trusted CLI general agent

Source: `MANIFESTO.md` ("One sentence": production-grade, CLI-first general agent that people trust with real work). The learning subsystem is instrumental to this, not the goal.

The learning roadmap (Phases 1–4) is a **necessary-but-not-sufficient** substrate. After Phases 1–4 ship, the North Star still requires a productization slice that turns the learning substrate into a trustworthy operator surface.

North Star work items (each is its own future `/xlfg` run; all depend on Phases 1–4 being stable):

- **NS-1 — Operator trust surface.** The `/learning` command family per PRD §17.3 (`status`, `list`, `show`, `review`, `approve`, `reject`, `rollback`, `trace`, `experiments`). An operator must be able to inspect any learned record, see its lineage and reuse stats, and roll it back without reading JSON files. Acceptance: a new operator can answer "why did Rocky do that?" for any turn using only `/learning trace <turn-id>`.
- **NS-2 — Observability & metrics.** PRD §23 dashboards surfaced via `/learning status --metrics`. Local reporter: retrieval hit-rate, promotion rate, rollback rate, context-budget share, replay canary delta, transfer delta. Acceptance: every learning-system change must ship with a measurable before/after on these metrics in its PR.
- **NS-3 — Safety governance.** PRD §21 — freeze mode (halts all promotion + new writes), hard-rule allow-list on candidates, explicit human-oversight gates for meta-variants touching tool/permission configs. Acceptance: an adversarial `/teach` that would weaken a security boundary is rejected with a named safety violation, not silently absorbed.
- **NS-4 — Legacy cleanup per PRD §18.** After migration windows close, delete `cmd_student`, legacy `skills/learned` write path, and the heuristic `slow_learner` entirely. Acceptance: code search shows no writes to legacy paths; existing read adapters are the only legacy touchpoint and only during migration.
- **NS-5 — Typed `/teach` response.** PRD §17.2 — structured `classification`, `scope`, `authority`, `activation_mode`, `promotion_state`, `record_id`, `why`, `derived_actions`. Acceptance: teach traces contain a machine-parseable response object; operator UI can show "this teach created record X with authority Y, activation Z."
- **NS-6 — Reliability hardening.** Failure-mode inventory: provider timeouts, partial tool failures, corrupted ledger lines, half-finished migrations, clock skew, concurrent runtime instances on same workspace. Each gets a named failure test + recovery path. Acceptance: ledger corruption on one record does not block startup; `rocky /learning status` surfaces the corruption and offers quarantine.
- **NS-7 — Cross-model robustness.** Phase 2's retrospective-influence fix lands on gemma4:26b, not just frontier models. Acceptance: every live xfail that passes on `claude-opus` also passes on the configured local Ollama model, OR is explicitly scoped as "frontier-only" in the test marker with a cited model-capability reason.
- **NS-8 — Workspace portability.** A `.rocky/` directory is transferable across machines without breaking. Absolute paths in records become relative on load; ledger is git-mergeable (deterministic ordering, stable IDs). Acceptance: two engineers can share the same `.rocky/` via git without conflict on happy-path teach events.

**Non-goals for the North Star slice:**
- Cloud-hosted Rocky, multi-user memory sharing across accounts — remains local-first per MANIFESTO.md.
- Replacing the file-first legibility contract with opaque binary stores — `.rocky/` must stay `cat`/`grep`/`git diff`-able.
- Automated teach-generation from user mistakes without an explicit `/teach` event — violates "candidates never hard" unless the captured record enters as candidate and earns promotion via verified reuse.

**North Star acceptance (composite):** a new operator can hand Rocky a non-trivial repo task, observe learning happen autonomously, inspect what was learned via `/learning` commands, trust the safety rails to reject adversarial teaches, and see quantified learning quality metrics — all without reading a single file under `.rocky/` unless they choose to. Until that story is end-to-end clean, the North Star is not met.
