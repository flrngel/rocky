---
name: spec
description: Intent contract + chosen solution + task map + proof status + release notes for run-20260416-190523-next-steps.
status: DONE
intent_finalized_by: conductor (refiner's factual premise was wrong — F4/R2/F2/F3 were already shipped by prior run-20260416-161631; conductor repaired in-place)
---

# Spec — run-20260416-190523-next-steps

## Invocation

- Command: `/xlfg-engineering:xlfg`
- Args: `work on next steps`
- Date: 2026-04-16

## Memory recall (summary)

Recall complete — see `memory-recall.md`. Working hypothesis grounded in prior-run compound summary + working-tree evidence: F4/R2/F2/F3 already shipped (uncommitted); F1 genuinely OPEN. Git-recency clean on F1 surface. Six carry-forward rules adopted.

## Factual ground truth (confirmed 2026-04-16 19:05)

Prior run-20260416-161631 (compound-summary.md + current-state.md header + working-tree ls) shipped 4 of 5 follow-ups from `/tmp/205534-follow-ups.md`:

| Follow-up | Priority | Status | Evidence in working tree |
|-----------|----------|--------|--------------------------|
| F4/R3 — driving-policy carry deterministic test | 1 | SHIPPED | `tests/test_route_upgrade_driving_policy.py` (new, untracked) |
| R2 — shared test helpers to `tests/agent/_helpers.py` | 2 | SHIPPED | `tests/agent/_helpers.py` (new); `tests/test_self_learn_live.py` (deleted) |
| F2 — `.agent-testing/specs` drift guard | 3 | SHIPPED | `tests/test_agent_testing_specs.py` (new, untracked) |
| F3 — recall-phase SKILL.md guardrail | 4 | SHIPPED-EXTERNAL | sibling-repo commit `c4e4f38` (xlfg v4.3.0) |
| F1 — `WEAK_MATCH_TOKENS` principled review | 5 | **OPEN** | n/a |

Plus the `src/rocky/core/agent.py` carry-field fix from run-20260416-205534 (uncommitted; covered by the F4/R3 regression tests above).

**The query-refiner's earlier output had its premise wrong** — it read `/tmp/205534-follow-ups.md` as a fresh todo list without checking the working tree or the prior compound summary. The refiner's list of O2-O5 was already done; only O1 (commit) and F1 remain.

## Intent contract

### Direct asks (user's literal words)

- **Q1** — "work on next steps". User wants the repo advanced to its natural next unit of engineering work after the prior completed-but-uncommitted run.

### Implied asks (grounded from repo state)

- **I1** — commit the prior runs' uncommitted work (standing instruction in global `CLAUDE.md`: "Always git commit your work is done. (Conventional Commits style)"). Prior run-20260416-161631 and the underlying run-20260416-205534 ended GREEN + APPROVE-WITH-NOTES-FIXED but left no commit. That IS the biggest release-discipline gap.
- **I2** — resolve F1 (`WEAK_MATCH_TOKENS` principled review) — the single genuinely OPEN engineering follow-up. Options: (a) ship a minimal scoring-path change + regression test + sensitivity witness, or (b) publish an analysis doc that closes F1 with a documented finding (either "no change justified" or "live-corpus data required — scoped to a dedicated future run").

### Non-goals

- **NG1** — NOT re-running the triple-live gemma4:26b campaign. If F1 truly needs live-LLM runs, scope that to a dedicated future run; do not bundle it here.
- **NG2** — NOT touching the `AgentCore._route_upgrade_driving_policy` carry-field logic. That fix is covered by the new regression tests; preserve the invariant.
- **NG3** — NOT committing `.agent-testing/` or `.rocky/` (local state; already in `.gitignore`-like posture — confirm before commit).
- **NG4** — NOT committing `docs/pi-autoresearch-rocky-comparison.md` or `docs/pi-autoresearch-rocky-learning-integration-analysis.md`. These are untracked research docs whose relationship to this engineering lineage is unconfirmed. Default: exclude from the commit absent explicit user confirmation.
- **NG5** — NOT wholesale redesigning the retrieval scoring pipeline. If F1 lands a code change, it is the minimal tightening (e.g., per-task-family weak-token allowlist), not a scoring rewrite.
- **NG6** — NOT re-doing the prior run's verification or review. That work is already in its run-dir; the commit just records it.

### Acceptance criteria

- **A1** — prior-runs' uncommitted work committed in a single Conventional Commits-style commit. Commit covers the in-scope file set (enumerated in O1's completion note). Deterministic baseline (730+14+0 per prior compound summary) reconfirmed immediately before commit via a single `pytest` run.
- **A2** — F1 closed by one of:
  - **A2a (code path)**: minimal scoring-path change in `src/rocky/learning/policies.py` with deterministic regression test(s) covering the intended new behavior AND a sensitivity witness (revert → fail → restore → pass). Full suite stays green.
  - **A2b (analysis-doc path)**: `docs/xlfg/runs/20260416-190523-next-steps/f1-weak-match-tokens-analysis.md` documents: (i) concrete offline analysis of `policies.py:L194-234` on the actual policies under `.rocky/policies/learned/`; (ii) a specific empirical decision (change / no-change / live-corpus-required); (iii) a concrete follow-up action (e.g., "defer to live-corpus run" with acceptance criteria for that future run).
- **A3** — full deterministic suite stays green. `tests/test_route_upgrade_driving_policy.py` + `tests/test_route_intersection.py` stay green.
- **A4** — if A2a, sensitivity witness captured under `evidence/`.

### Blocking ambiguities

None. The state is well-grounded. The only decision is whether A2a or A2b is honest for F1, which the plan phase will resolve based on what offline analysis can actually conclude.

### Assumptions (low-risk, explicit, reversible)

- **AS1** — commit prior runs' work AS-IS. Prior run delivered its own verification + review + compound; don't re-do.
- **AS2** — F1's analysis path (A2b) is preferred if offline analysis cannot produce a confident empirical decision on the weak-token allowlist in <1d of budget. Honest documentation > speculative code change.
- **AS3** — untracked `docs/pi-autoresearch-*` files are excluded from the commit absent explicit user input. If the user mentions them later, revisit.
- **AS4** — the two-path acceptance criterion (A2a OR A2b) is honest for F1 because F1's core question IS empirical: "is `command` genuinely too noisy in practice?" requires data, not code reasoning. A no-change decision with cited analysis is a legitimate close.
- **AS5** — the commit (O1) can and should run FIRST, before any F1 work, so that any F1 code change is clearly separable in git history.

### Carry-forward anchor

- CF-L10: preserve driving-policy carry-field invariant; re-run `tests/test_route_upgrade_driving_policy.py` on any retrieval-scoring change.
- CF-L11: git-recency applied this recall; clean on F1 surface.
- CF-feedback/generalization: if A2a, test must witness on lexically-diverse P2 prompts.
- CF-feedback/assert-real-output: A2a behavioral claims assert on `trace.selected_policies` or `response["text"]`.
- CF-feedback/production-paths: any new test uses `_build_runtime_with_policy` and `RockyRuntime.load_from(workspace)` patterns.
- CF-sensitivity: every code change carries a sensitivity witness under `evidence/`.

### Resolution

`proceed-with-assumptions`. Two objectives; scope honestly bounded.

## Objective groups

### O1 — Commit prior runs' uncommitted work (release discipline)

- **Covers**: I1, A1.
- **Depends on**: none.
- **Completion note**: single Conventional Commits-style commit. In-scope files:
  - `src/rocky/core/agent.py` (carry-field fix from run-20260416-205534)
  - `tests/test_route_upgrade_driving_policy.py` (new, covers F4/R3)
  - `tests/agent/_helpers.py` + `tests/agent/__init__.py` (R2 shared helpers)
  - `tests/agent/test_self_learn_live.py` (R2 refactor — imports from `_helpers`)
  - `tests/test_self_learn_live.py` (R2 — deleted)
  - `tests/test_agent_testing_specs.py` (F2 drift guard)
  - `docs/xlfg/knowledge/current-state.md` + `docs/xlfg/knowledge/ledger.jsonl`
  - `docs/xlfg/runs/20260416-161631-follow-ups-cleanup/` (whole directory)
  
  Explicitly excluded: `.agent-testing/`, `.rocky/`, `docs/pi-autoresearch-*`. Verify deterministic suite green (730+14+0) before commit.

### O2 — F1: `WEAK_MATCH_TOKENS` principled review (close the last OPEN follow-up)

- **Covers**: I2, A2, A3, A4.
- **Depends on**: O1 (commit baseline first so F1's diff is clearly separable).
- **Completion note**: context-phase investigates; plan-phase decides A2a vs A2b based on what offline analysis can honestly conclude. Default preference: A2b (findings doc) unless offline data is decisively conclusive. If A2a, use `_build_runtime_with_policy` pattern and include sensitivity witness. Either way, F1 is closed at end-of-run.

## Context (summary)

See `context.md`. For O2, minimal viable fix is Option A (per-task-family weak-token allowlist in `src/rocky/learning/policies.py`, ~4-line addition + 2 deterministic tests with sensitivity). Mechanism fully grounded; not redundant with carry-field (carry-field only rescues policies that drove an upgrade; F1 fix also rescues same-family policies that didn't). No live-LLM required. Research not required.

## Chosen solution

**Option A — per-task-family weak-token allowlist** in `src/rocky/learning/policies.py`. Add module-level `_DOMAIN_ALLOWED_WEAK_TOKENS: dict[str, frozenset[str]] = {"repo": frozenset({"command"})}` after L14. Replace L203 `strong_token_matches = token_matches - WEAK_MATCH_TOKENS` with a task-family-aware effective_weak computation. Plus `tests/test_policy_domain_allowlist.py` with SC-1/SC-2/SC-3. Full detail in `solution-decision.md`; test contract in `test-contract.md`; readiness verdict READY in `test-readiness.md`.

Rejected shortcuts:
- Option C (global removal of `command` from `WEAK_MATCH_TOKENS`): killed by SC-2.
- Option B (task-family match as fourth gate clause): too broad; degrades precision.
- Option D (half-weight weak tokens): no empirical data justifies the complexity.
- Option E (defer F1 with findings-only): unnecessary — mechanism is clear and fix is 3 lines.

## Task map

| Task | Objective | Scenarios | Scope | Primary artifact | Done check |
|------|-----------|-----------|-------|------------------|------------|
| T1 | O1 | SC-4 (full-suite gate) | Commit prior uncommitted work (enumerated file set); explicit `git add` paths only | `evidence/T1-commit-sha.txt` | `git show --stat HEAD` shows in-scope files; excluded absent; `pytest -q` green |
| T2 | O2/F1 | SC-1, SC-2 (via T3) | 2 targeted edits in `src/rocky/learning/policies.py` (constant + L203 replacement) | `src/rocky/learning/policies.py` | `python -c "from rocky.learning.policies import _DOMAIN_ALLOWED_WEAK_TOKENS; assert 'repo' in _DOMAIN_ALLOWED_WEAK_TOKENS"` exits 0 |
| T3 | O2/F1 | SC-1, SC-2, SC-3 | Create `tests/test_policy_domain_allowlist.py` (3 deterministic tests) | `tests/test_policy_domain_allowlist.py` | `pytest tests/test_policy_domain_allowlist.py -q` reports 3 passed |
| T4 | O2 | SC-4 | Commit F1 change (policies.py + test + run-dir) | `evidence/T4-commit-sha.txt` | `git show --stat HEAD` includes policies.py + test + run-dir; `pytest -q` green |

Ordering: T1 → T2 → T3 → T4 (strict sequential per `tasks-index.md`).

## Proof status

- Fast checks (per test-contract.md):
  - `pytest tests/test_policy_domain_allowlist.py -q` — 3 passed
  - Sensitivity: revert `effective_weak` lines → SC-1 fails; restore → SC-1 passes
- Ship checks:
  - `pytest tests/test_route_upgrade_driving_policy.py tests/test_route_intersection.py -q` — green
  - `pytest -q` — full baseline green (730+14+0 pre-T3 → 733+14+0 post-T3)
- Readiness gate: **READY** (see `test-readiness.md`)

## Release notes

- PM: closes the last OPEN follow-up from `/tmp/205534-follow-ups.md` (F1 — `WEAK_MATCH_TOKENS` review). Prior run-20260416-161631's shipped-but-uncommitted work is now committed. No feature-level user-visible change; this is retrieval-scoring hardening.
- UX: none (backend-only).
- Engineering: minimal 3-line change to `policies.py` plus 3 new deterministic tests. Defense-in-depth for the retrieval drop that prior run's carry-field fix covered at the route-upgrade site; F1 extends coverage to the non-driver case.
- QA: sensitivity witness required; no live-LLM needed for this run.
- Release: two commits (O1 prior-run rollup; O2 F1 change). Conventional Commits style. No version bump (scoring-path fix is neither feature nor breaking).
