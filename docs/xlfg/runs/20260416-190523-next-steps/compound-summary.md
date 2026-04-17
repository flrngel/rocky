---
name: compound-summary
description: Durable-lesson promotion for run-20260416-190523-next-steps.
status: DONE
---

# Compound summary — next-steps (migration validated; F1 rejected)

## Run outcome

GREEN for migration, REJECTED-BY-LIVE-EVIDENCE for F1.

Deterministic suite (final): **730 passed + 14 skipped + 0 failed** after F1 revert.
(Interim state: 733+14 with F1 applied; revert removed the 3 F1 tests.)

Live-LLM A/B triple-live on gemma4:26b via `tests/agent/test_self_learn_live.py` + `/agent-testing` `run_eval.py` — detailed in `evidence/live/summary.md`:
- **With F1**: 47/48 across 4 runs — 2× flakes on `test_sl_undo_behavioral_correction_fully_gone` (the documented Phase-2 derived-autonomous leak).
- **Without F1** (pre-fix baseline): 36/36 clean.
- **Post-revert confirmation**: 36/36 clean (12+12+12 in 193s/207s/236s).

Causal hypothesis (not conclusively proven, but consistent with n=3+3+3 evidence): F1's more aggressive repo-family retrieval makes `capture_project_memory` more consistently write turn-lineage memories during the teach-reuse turn, which then surface post-undo as the Phase-2 leak. F1 amplifies an existing failure mode without delivering a live-LLM benefit, because its motivating scenario is already covered by the `AgentCore._route_upgrade_driving_policy` carry-field fix.

Commits landed on `feat/agent-testing`:
- `06c5066` — T1 rollup of prior run-20260416-161631's approved work (carry-field fix from run-205534 + F4/R2/F2 tests + run-dir). **Kept.**
- `05fa5ec` — T4 F1 fix (domain allowlist in `policies.py`) + 3 deterministic tests + run-dir artifacts. **Code REVERTED in forward-fix commit; run-dir docs preserved as historical record.**
- `034e13a` — review-fix comment + review artifacts. **Comment REVERTED; review artifacts preserved.**
- `767519d` — compound summary + lessons. **Kept (updated in follow-up commit with the L20 post-mortem lesson).**
- (forward-fix commit) — removes F1 code + test from source tree; preserves `evidence/live/summary.md` and this compound as historical record.

## Objectives

- **O1** — commit prior runs' uncommitted work. DONE via 06c5066.
- **O2** — F1 (`WEAK_MATCH_TOKENS` principled review). **CLOSED as REJECTED-BY-LIVE-EVIDENCE**. Mechanism understood; deterministic tests proved the scoring behavior; live A/B on gemma4:26b showed the change is neutral-to-regressive. Decision: keep the carry-field fix (which is already committed and proven at 36/36 triple-live) as the single layer of defense; do not layer F1 on top. Filed as rejected in current-state.md with the evidence pointer.

## Durable lessons to promote

### L17 — F1 theoretically covers a gap the carry-field doesn't (MECHANISM-ONLY)

This lesson describes the MECHANISM that motivated F1: `AgentCore._route_upgrade_driving_policy` (set `agent.py:538`, inject `agent.py:3188`) rescues ONLY the policy that DROVE a route upgrade. A per-task-family weak-token allowlist would rescue a broader class: policies whose `task_family` matches a domain where a weak token is actually discriminative. `policy_retriever.retrieve` is called at TWO sites (`agent.py:455` upgrade-drive, `context.py:318` context-build); the carry-field covers the former's drop for drivers, a family-allowlist would cover the latter's drop for same-family relevant policies.

**BUT** — see L20. The theoretical gap is not empirically load-bearing. `capture_project_memory`-based memory + brief + retrospective paths already preserve learning across turns independently of the retrieval-scoring allowlist. Shipping F1 amplified the documented Phase-2 derived-autonomous leak without producing a measurable behavioral win. Treat this lesson as a mechanism-understanding artifact, not as a recommendation to ship F1.

### L20 — Deterministic mechanism proof ≠ behavioral live proof; require live A/B before shipping retrieval-scoring changes

F1 had a clean deterministic proof (SC-1/SC-2/SC-3 + sensitivity witness) that the scoring mechanism worked. I shipped it based on that proof. A follow-up live-LLM A/B (3× with F1 vs 3× without F1 vs 3× post-revert, all on gemma4:26b) showed F1 was neutral-to-regressive: **47/48 with F1 vs 36/36 without F1 vs 36/36 post-revert**. The regression was NOT on the F1-targeted test surface — it was on `test_sl_undo_behavioral_correction_fully_gone`, the documented Phase-2 derived-autonomous leak. F1's more aggressive repo-family retrieval plausibly made turn-lineage memory capture more reliable during the teach-reuse turn, which then surfaces post-undo.

**Rule**: any change to `LearnedPolicyRetriever.retrieve` / `ContextBuilder._build_policies` / `AgentCore._maybe_upgrade_route_from_project_context` scoring paths MUST carry a live-LLM A/B (triple-live with and without the change) before shipping. Deterministic mechanism tests prove the code does what you wrote; they do not prove the behavior does what you want in interaction with the model's stochasticity + the downstream memory/brief/retrospective paths. This extends CF-feedback/claim-scope-honesty to the retrieval-scoring layer specifically.

**Artifact of this lesson**: `docs/xlfg/runs/20260416-190523-next-steps/evidence/live/summary.md` with the 7-run A/B table and per-run logs under `evidence/live/run{1,2,3}.log`, `baseline-no-f1-run{1,2,3}.log`, `postrevert-run{1,2,3}.log`.

### L18 — Intent-phase refiner must cross-reference working-tree + prior compound, not just named docs

The `xlfg-query-refiner` specialist treated `/tmp/205534-follow-ups.md` as a fresh todo list and proposed re-shipping F4/R2/F2/F3 — all of which had already been shipped by prior run-20260416-161631 and lived in the uncommitted working tree. The refiner read the named doc but did not reconcile with:
- Prior run's `compound-summary.md` (explicit DONE status for O1-O4)
- `git status --short` (showed the untracked artifacts)
- `docs/xlfg/knowledge/current-state.md` header (named the shipped objectives)

Generalization: when a doc lists tasks to do, the refiner MUST cross-reference what's already landed (in HEAD, in the working tree, or in the knowledge ledger) BEFORE proposing to ship those tasks. This is L11 (git-log-since-baseline) extended from recall-phase into intent-phase. Fix in the refiner specialist: include a "working-tree reconcile" check as part of the mandatory input surface.

### L19 — Run-dir is gitignored; review/verify artifacts need `git add -f`

`.gitignore` contains `docs/xlfg/runs/*` with only `.gitkeep` and `README.md` exceptions. Any run-dir artifact (spec.md, evidence/, reviews/, compound-summary.md, etc.) requires `git add -f` to stage. Worth codifying in the review and compound phase skills: when committing run artifacts, use `git add -f docs/xlfg/runs/<RUN_ID>/` explicitly. Otherwise the commit silently drops the proof evidence and the next run's recall can't find the artifacts.

## Reinforced prior lessons (no new file changes needed)

- **L10/L12** — route-upgrade ↔ retrieval consistency: today's F1 change preserves the carry-field invariant; `tests/test_route_upgrade_driving_policy.py` stays green.
- **L11** — git-recency guard: applied during recall phase; clean on F1 surface.
- **L13** — AgentCore single-caller-per-instance: not touched this run.
- **L14** — external-commit detection for cross-repo tasks: not applicable (no cross-repo work).
- **L15** — `__all__` on shared helpers: not applicable.
- **L16** — non-markdown PRIMARY_ARTIFACTs must skip YAML frontmatter: re-observed this run — verify-runner prepended `Status: DONE` to `results.json`; conductor stripped it post-facto. The xlfg-engineering v4.3.0 `ARTIFACT_KIND` mechanism should have prevented this. L16 stands as a durable lesson and the plugin issue remains.

## Non-promotions (explicit)

- The review's F1-P1-B `task_family` duplicate-read concern is hygiene-only; no durable lesson.
- The refiner's factual error (L18) is a process lesson, not a policy change to Rocky itself.

## Cross-references

- Verification: `verification.md` (verdict GREEN).
- Review: `review-summary.md` (APPROVE-WITH-NOTES-FIXED) + `reviews/architecture.md`.
- Evidence: `evidence/` (results.json, fast/ship/full logs, sensitivity witness).
- F1 diagnostic foundation: prior run's `docs/xlfg/runs/20260416-205534-agent-testing-pass/diagnosis.md` + `/tmp/205534-follow-ups.md` L78-106.
