---
status: DONE
task_id: T4
commit_sha: 05fa5ec2b31079cc4926820c63e149076262323d
PRIMARY_ARTIFACT: /Users/flrngel/project/personal/rocky/docs/xlfg/runs/20260416-190523-next-steps/evidence/T4-commit-sha.txt
FILE_SCOPE: |
  WRITE: docs/xlfg/runs/20260416-190523-next-steps/evidence/T4-commit-sha.txt
  COMMIT (stage + commit only the exact in-scope list below):
    src/rocky/learning/policies.py  (Option A domain-allowlist edit from T2)
    tests/test_policy_domain_allowlist.py  (new file from T3)
    docs/xlfg/runs/20260416-190523-next-steps/  (entire run-dir: spec, tasks, evidence, context, etc.)
  EXCLUDE (must NOT appear in this commit):
    .agent-testing/
    .rocky/
    docs/pi-autoresearch-rocky-comparison.md
    docs/pi-autoresearch-rocky-learning-integration-analysis.md
    src/rocky/core/agent.py (already committed in T1)
    tests/test_route_upgrade_driving_policy.py (already committed in T1)
    tests/agent/ (already committed in T1)
    tests/test_agent_testing_specs.py (already committed in T1)
DONE_CHECK: git show --stat HEAD | head -40  (verify policies.py, test_policy_domain_allowlist.py, and run-dir appear; T1-committed files absent); pytest -q (733+14+0 green)
RETURN_CONTRACT: DONE|BLOCKED|FAILED /Users/flrngel/project/personal/rocky/docs/xlfg/runs/20260416-190523-next-steps/evidence/T4-commit-sha.txt
CONTEXT_DIGEST: |
  This is the O2 commit. It closes F1 in git history as a single clean unit.
  T1 committed O1 work. T2 edited policies.py. T3 created tests/test_policy_domain_allowlist.py.
  T4 bundles: the code fix (policies.py) + the regression tests (test_policy_domain_allowlist.py) + the run-dir artifacts for this run (spec.md, tasks/, evidence/, context.md, solution-decision.md, test-contract.md, diagnosis.md, etc.).
  Full suite baseline after T3: 733+14+0. Confirm this before committing.
  Commit message style: Conventional Commits. Example heading:
    fix(learning): add per-domain weak-token allowlist for repo/command retrieval (F1)
  The commit subject may optionally mention the test file.
  Do NOT use `git add .` — use explicit paths to avoid sweeping in local state.
  After committing, write the SHA to evidence/T4-commit-sha.txt (do NOT include the evidence file itself in the commit; it is written post-commit).
PRIOR_SIBLINGS: |
  T1: O1 commit done. SHA in evidence/T1-commit-sha.txt.
  T2: policies.py edited with _DOMAIN_ALLOWED_WEAK_TOKENS and effective_weak logic.
  T3: tests/test_policy_domain_allowlist.py created with 3 passing tests (SC-1/SC-2/SC-3).
---

# Task brief — T4

## Identity

- task_id: `T4`
- objectives: `O2 / F1 (release discipline)`
- scenarios: `SC-4 (full suite green gate)`
- owner: `xlfg-task-implementer`

## Scope

- allowed files / dirs:
  - `src/rocky/learning/policies.py` (stage as-is from T2)
  - `tests/test_policy_domain_allowlist.py` (stage as-is from T3)
  - `docs/xlfg/runs/20260416-190523-next-steps/` (stage entire run-dir)
  - `docs/xlfg/runs/20260416-190523-next-steps/evidence/T4-commit-sha.txt` (write after commit; do NOT include in commit)

- out-of-scope files / dirs:
  - `.agent-testing/` — must not appear in commit
  - `.rocky/` — must not appear in commit
  - `docs/pi-autoresearch-*.md` — excluded per NG4
  - Files already committed in T1 — already in history; do not re-stage

## Mission

- exact change to make:
  1. Run `pytest -q` and confirm 733+14+0 (full suite green after T2+T3 work). Record output.
  2. Stage exactly: `src/rocky/learning/policies.py`, `tests/test_policy_domain_allowlist.py`, and `docs/xlfg/runs/20260416-190523-next-steps/` using explicit `git add` paths.
  3. Verify staging with `git diff --staged --stat` — confirm only the F1 code change, new test file, and run-dir appear; confirm T1-committed files and excluded files are absent.
  4. Commit with a Conventional Commits message, e.g.: `fix(learning): add per-domain weak-token allowlist for repo/command retrieval (F1)`
  5. Write the resulting commit SHA to `docs/xlfg/runs/20260416-190523-next-steps/evidence/T4-commit-sha.txt`.

- false success to avoid:
  - Running `git add .` or `git add -A` — sweeps in local state.
  - Committing before confirming `pytest -q` is green.
  - Including T1-committed files in the staged diff (would create duplicate history).
  - Including the evidence file itself inside the commit (write it after `git commit`).
  - Writing a fabricated or stale SHA.

## Handoff

- required artifact: `docs/xlfg/runs/20260416-190523-next-steps/evidence/T4-commit-sha.txt`
  - content: full 40-char commit SHA on line 1; optionally one-line commit subject on line 2.
- done check: `git show --stat HEAD | head -40` — `policies.py`, `test_policy_domain_allowlist.py`, and run-dir files appear; T1-scoped files and excluded files absent. `pytest -q` — 733+14+0 green.
- dependencies: T3 (test file must exist and pass before committing)
