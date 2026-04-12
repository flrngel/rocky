# Rocky Hyperlearning v2 — deferred backlog

Provenance
- Authored during run-20260412-000228
- PRD: `/Users/flrngel/Downloads/rocky_hyperlearning_v2_prd.md`
- Date: 2026-04-12
- Shipped in that run: Phase 0 safety patch (candidate-never-hard, `/policies` removed, `/learn` hidden, `/learned review`, `slow_learner_enabled=False`) + self-learn verification scenarios in `tests/test_self_learn_scenarios.py`.

This document captures every PRD obligation that remains after Phase 0. Future `/xlfg` runs can resume each phase independently; the phases are loosely ordered but not strictly sequential once the ledger exists.

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

Deferred work items:
- **Teach over-tagging fix (concrete item, discovered in run-20260412-013706):** `AgentCore._refine_route_with_project_guidance` (`src/rocky/core/agent.py:307-336`) re-infers route signatures by running the lexical router over a concatenation of the current prompt + policy description + feedback_excerpt + required_behavior + prohibited_behavior + evidence_requirements. Because `/teach` auto-generates policies with broad descriptions, a learned policy for a greeting correction can hijack subsequent greeting prompts into `repo/shell_execution` or `site/understanding/general` with `source=project_context, confidence=0.93`. Fix in Phase 2: make the reinference honor declared `task_signatures` as an upper-bound allowlist instead of augmenting it, OR restrict reinference to policies whose declared `task_family` matches the current prompt's lexical class.
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
