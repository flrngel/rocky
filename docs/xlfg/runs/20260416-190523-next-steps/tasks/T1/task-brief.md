---
status: IN_PROGRESS
task_id: T1
PRIMARY_ARTIFACT: /Users/flrngel/project/personal/rocky/docs/xlfg/runs/20260416-190523-next-steps/evidence/T1-commit-sha.txt
FILE_SCOPE: |
  WRITE: docs/xlfg/runs/20260416-190523-next-steps/evidence/T1-commit-sha.txt
  COMMIT (stage + commit only the exact in-scope list below):
    src/rocky/core/agent.py
    tests/test_route_upgrade_driving_policy.py
    tests/agent/__init__.py
    tests/agent/_helpers.py
    tests/agent/test_self_learn_live.py
    tests/test_self_learn_live.py  (deletion)
    tests/test_agent_testing_specs.py
    docs/xlfg/knowledge/current-state.md
    docs/xlfg/knowledge/ledger.jsonl
    docs/xlfg/runs/20260416-161631-follow-ups-cleanup/ (whole directory)
  EXCLUDE (must NOT appear in this commit):
    .agent-testing/
    .rocky/
    docs/pi-autoresearch-rocky-comparison.md
    docs/pi-autoresearch-rocky-learning-integration-analysis.md
DONE_CHECK: git show --stat HEAD | head -40  (verify in-scope files appear; excluded files absent); pytest -q (730+14+0 or 730+14+0 baseline green)
RETURN_CONTRACT: DONE|BLOCKED|FAILED /Users/flrngel/project/personal/rocky/docs/xlfg/runs/20260416-190523-next-steps/evidence/T1-commit-sha.txt
CONTEXT_DIGEST: |
  Prior run-20260416-161631 ended GREEN + APPROVE-WITH-NOTES-FIXED but left no commit.
  All in-scope files are working-tree changes (modified, new untracked, or deleted) on branch feat/agent-testing.
  Commit message style: Conventional Commits.  Example heading:
    feat(agent): commit prior-run approved work (F4/R2/F2 + carry-field fix)
  Deterministic baseline before commit: 730+14+0 (pytest -q, Python 3.13, Ollama not required for suite).
  After this commit the branch history shows O1 as a clean unit; T2's F1 diff will land as a separate commit.
PRIOR_SIBLINGS: none
---

# Task brief — T1

## Identity

- task_id: `T1`
- objectives: `O1`
- scenarios: `SC-4 (release gate: full suite green)`
- owner: `xlfg-task-implementer`

## Scope

- allowed files / dirs:
  - `src/rocky/core/agent.py` (stage as-is)
  - `tests/test_route_upgrade_driving_policy.py` (stage as-is)
  - `tests/agent/__init__.py` (stage as-is)
  - `tests/agent/_helpers.py` (stage as-is)
  - `tests/agent/test_self_learn_live.py` (stage as-is)
  - `tests/test_self_learn_live.py` (stage deletion)
  - `tests/test_agent_testing_specs.py` (stage as-is)
  - `docs/xlfg/knowledge/current-state.md` (stage as-is)
  - `docs/xlfg/knowledge/ledger.jsonl` (stage as-is)
  - `docs/xlfg/runs/20260416-161631-follow-ups-cleanup/` (stage whole directory)
  - `docs/xlfg/runs/20260416-190523-next-steps/evidence/T1-commit-sha.txt` (create after commit; do NOT include in the commit itself)

- out-of-scope files / dirs:
  - `.agent-testing/` — local state, gitignored; must not appear in commit
  - `.rocky/` — local state, gitignored; must not appear in commit
  - `docs/pi-autoresearch-rocky-comparison.md` — excluded per NG4
  - `docs/pi-autoresearch-rocky-learning-integration-analysis.md` — excluded per NG4
  - any F1 code changes — those belong in T2's commit

## Mission

- exact change to make:
  1. Run `pytest -q` and confirm the baseline reads 730+14+0 (or the known pre-commit count). Record output.
  2. Stage exactly the in-scope files using explicit `git add` paths (do NOT use `git add .`). Stage the deletion of `tests/test_self_learn_live.py`.
  3. Verify staging with `git diff --staged --stat`; confirm excluded files are absent.
  4. Commit with a Conventional Commits message, e.g.: `feat(agent): commit prior-run approved work (carry-field fix, F4/R2/F2 tests, run-dir)`
  5. Write the resulting commit SHA (from `git rev-parse HEAD`) to `docs/xlfg/runs/20260416-190523-next-steps/evidence/T1-commit-sha.txt`.

- false success to avoid:
  - Running `git add .` or `git add -A` — this would sweep in `.agent-testing/`, `.rocky/`, and `docs/pi-autoresearch-*`. Use explicit paths only.
  - Committing without verifying `pytest -q` is green first.
  - Writing a non-Conventional Commits message.
  - Recording a stale or fabricated SHA in the evidence file.

## Handoff

- required artifact: `docs/xlfg/runs/20260416-190523-next-steps/evidence/T1-commit-sha.txt`
  - content: the full 40-char commit SHA on line 1; optionally the one-line commit subject on line 2.
- done check: `git show --stat HEAD | head -40` — in-scope files appear; excluded files absent. `pytest -q` — full suite green.
- dependencies: none
