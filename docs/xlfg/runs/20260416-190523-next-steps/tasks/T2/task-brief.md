---
status: IN_PROGRESS
task_id: T2
PRIMARY_ARTIFACT: /Users/flrngel/project/personal/rocky/src/rocky/learning/policies.py
FILE_SCOPE: |
  EDIT: src/rocky/learning/policies.py (two targeted edits only — see Mission)
  READ-ONLY:
    docs/xlfg/runs/20260416-190523-next-steps/solution-decision.md
    docs/xlfg/runs/20260416-190523-next-steps/test-contract.md
DONE_CHECK: python -c "from rocky.learning.policies import _DOMAIN_ALLOWED_WEAK_TOKENS; assert 'repo' in _DOMAIN_ALLOWED_WEAK_TOKENS, 'constant missing'"
RETURN_CONTRACT: DONE|BLOCKED|FAILED /Users/flrngel/project/personal/rocky/src/rocky/learning/policies.py
CONTEXT_DIGEST: |
  Option A from solution-decision.md:
  1. After line 14 of policies.py, add at module top:
       _DOMAIN_ALLOWED_WEAK_TOKENS = {"repo": frozenset({"command"})}
  2. Replace line 203 (`strong_token_matches = token_matches - WEAK_MATCH_TOKENS`) with:
       policy_task_family = str(policy.metadata.get("task_family") or "")
       effective_weak = WEAK_MATCH_TOKENS - _DOMAIN_ALLOWED_WEAK_TOKENS.get(policy_task_family, frozenset())
       strong_token_matches = token_matches - effective_weak
  The gate at ~L232: `if not trigger_match and not task_signature_score and not strong_token_matches: continue`
  For repo policies with keyword "command": effective_weak excludes "command" → strong_token_matches = {"command"} → gate passes → policy retrieved.
  For non-repo families: effective_weak unchanged → "command" remains weak → gate still fires → filtered (anti-overreach preserved).
  Do NOT edit any other lines. Do NOT touch _maybe_upgrade_route_from_project_context or carry-field logic.
  Python 3.13; no YAML frontmatter in policies.py.
PRIOR_SIBLINGS: |
  T1: committed prior-run work (O1). Its commit SHA is in evidence/T1-commit-sha.txt.
  This task's change will be committed separately in T4.
---

# Task brief — T2

## Identity

- task_id: `T2`
- objectives: `O2 / F1`
- scenarios: `SC-1, SC-2, SC-3 (via T3 tests), SC-4`
- owner: `xlfg-task-implementer`

## Scope

- allowed files / dirs:
  - `src/rocky/learning/policies.py` — two targeted edits (constant addition + L203 replacement)

- out-of-scope files / dirs:
  - All test files — written in T3
  - `src/rocky/core/agent.py` — already committed in T1; no further changes
  - Any other file in `src/rocky/` — this is a minimal tightening only

## Mission

- exact change to make:
  1. Read `src/rocky/learning/policies.py` around line 14 (module-level constants) and around line 203 (inside `retrieve()` scoring loop).
  2. After line 14 (after `WEAK_MATCH_TOKENS` definition or its vicinity), add:
     ```python
     _DOMAIN_ALLOWED_WEAK_TOKENS: dict[str, frozenset[str]] = {
         "repo": frozenset({"command"}),
     }
     ```
  3. Locate the existing line `strong_token_matches = token_matches - WEAK_MATCH_TOKENS` (approx L203) and replace it with:
     ```python
     policy_task_family = str(policy.metadata.get("task_family") or "")
     effective_weak = WEAK_MATCH_TOKENS - _DOMAIN_ALLOWED_WEAK_TOKENS.get(policy_task_family, frozenset())
     strong_token_matches = token_matches - effective_weak
     ```
  4. Verify the edit with a quick import check: `python -c "from rocky.learning.policies import _DOMAIN_ALLOWED_WEAK_TOKENS; print(_DOMAIN_ALLOWED_WEAK_TOKENS)"`
  5. Run `pytest tests/test_route_upgrade_driving_policy.py tests/test_route_intersection.py -q` to confirm no regressions from the edit.

- false success to avoid:
  - Editing lines other than the two targeted locations.
  - Adding `_DOMAIN_ALLOWED_WEAK_TOKENS` globally for all families (Option B anti-pattern — SC-2 would fail).
  - Touching the L232 gate condition itself; the fix works by changing what feeds `strong_token_matches`, not by weakening the gate.
  - Not verifying the import after the edit.

## Handoff

- required artifact: `src/rocky/learning/policies.py` (edited in-place; no separate report file)
  - Evidence the implementer must confirm: `python -c "from rocky.learning.policies import _DOMAIN_ALLOWED_WEAK_TOKENS; assert 'repo' in _DOMAIN_ALLOWED_WEAK_TOKENS"` exits 0.
- done check: `python -c "from rocky.learning.policies import _DOMAIN_ALLOWED_WEAK_TOKENS; assert 'repo' in _DOMAIN_ALLOWED_WEAK_TOKENS, 'constant missing'"` — exits 0.
- dependencies: T1 (ensures the working tree baseline is the committed O1 state before F1 diff begins)
