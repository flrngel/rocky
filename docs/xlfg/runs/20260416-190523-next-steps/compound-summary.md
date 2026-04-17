---
name: compound-summary
description: Durable-lesson promotion for run-20260416-190523-next-steps.
status: DONE
---

# Compound summary — next-steps (F1 close + prior-run rollup)

## Run outcome

GREEN. APPROVE-WITH-NOTES-FIXED. All objectives shipped; last OPEN follow-up (F1) closed.

Deterministic suite: **733 passed + 14 skipped + 0 failed** (baseline was also 733+14; F1's 3 new tests were committed in 05fa5ec by an earlier session so they were already included in baseline).

Three commits landed on `feat/agent-testing`:
- `06c5066` — T1 rollup of prior run-20260416-161631's approved work (carry-field fix from run-205534 + F4/R2/F2 tests + run-dir)
- `05fa5ec` — T4 F1 fix (domain allowlist in `policies.py`) + 3 deterministic tests + run-dir artifacts
- `034e13a` — review-fix: added 6-line inline comment on `_DOMAIN_ALLOWED_WEAK_TOKENS` per APPROVE-WITH-NOTES-FIXED; captured review-summary + architecture review + verification evidence

## Objectives

- **O1** — commit prior runs' uncommitted work. DONE via 06c5066.
- **O2** — F1 (`WEAK_MATCH_TOKENS` principled review). DONE via 05fa5ec + inline doc-fix in 034e13a. Mechanism: `_DOMAIN_ALLOWED_WEAK_TOKENS = {"repo": frozenset({"command"})}`; in `LearnedPolicyRetriever.retrieve()`, `effective_weak = WEAK_MATCH_TOKENS - _DOMAIN_ALLOWED_WEAK_TOKENS.get(policy_task_family, frozenset())`. Defense-in-depth with the `AgentCore._route_upgrade_driving_policy` carry-field fix: carry-field rescues only upgrade-driving policies; F1 fix rescues the broader class of same-family relevant policies whose only query overlap is a domain-specific weak token.

## Durable lessons to promote

### L17 — F1 closes a defense-in-depth gap, not a duplicate fix

The `AgentCore._route_upgrade_driving_policy` carry-field (set at `agent.py:538`, injected at `agent.py:3188`) rescues only the policy that DROVE a route upgrade. The F1 allowlist rescues a broader class: any policy whose `task_family` matches a domain where a weak token is actually discriminative (e.g., `command` for repo-family shell-execution policies). The two fixes are complementary, not redundant. `policy_retriever.retrieve` is called at TWO sites (`agent.py:455` upgrade-drive, `context.py:318` context-build); the carry-field covers the former's drop when the policy does drive an upgrade, F1 covers the latter's drop for same-family relevant policies. Document this separation when adding new retrieval-scoring fixes — otherwise the next person adds a third redundant layer.

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
