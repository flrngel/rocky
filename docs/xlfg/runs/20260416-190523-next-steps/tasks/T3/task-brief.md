---
status: IN_PROGRESS
task_id: T3
PRIMARY_ARTIFACT: /Users/flrngel/project/personal/rocky/tests/test_policy_domain_allowlist.py
FILE_SCOPE: |
  CREATE: tests/test_policy_domain_allowlist.py (new file; no YAML frontmatter)
  READ-ONLY:
    docs/xlfg/runs/20260416-190523-next-steps/test-contract.md (SC-1/SC-2/SC-3 fixture bodies)
    src/rocky/learning/policies.py (confirm LearnedPolicy/LearnedPolicyRetriever import paths)
DONE_CHECK: pytest tests/test_policy_domain_allowlist.py -q  (expect 3 passed)
RETURN_CONTRACT: DONE|BLOCKED|FAILED /Users/flrngel/project/personal/rocky/tests/test_policy_domain_allowlist.py
CONTEXT_DIGEST: |
  Three test functions required (verbatim from test-contract.md):
    test_sc1_repo_command_policy_retrieved
    test_sc2_conversation_command_policy_not_retrieved
    test_sc3_repo_command_policy_generalization
  All use LearnedPolicy constructor directly — no POLICY.md file I/O, no LLM calls, no mocking.
  Import path: from rocky.learning.policies import LearnedPolicy, LearnedPolicyRetriever
  SC-1: policy task_family="repo", keywords=["command","shell"]; query="run the command"; task_context="repo/shell_execution"; assert policy IS in results.
  SC-2: policy task_family="conversation", keywords=["command","greeting"]; query="run the command"; task_context="conversation/general"; assert policy is NOT in results.
  SC-3: reuse SC-1 policy; query="execute the command in the shell"; task_context="repo/shell_execution"; assert policy IS in results.
  Full suite baseline after adding 3 tests: 733+14+0 (was 730+14+0).
  No YAML frontmatter in the test file itself (it is a plain Python test file).
  The sensitivity witness for SC-1 is documented in test-contract.md but is a verify-phase task, not a test function.
PRIOR_SIBLINGS: |
  T1: committed prior-run work (O1).
  T2: applied Option A domain-allowlist fix to policies.py. The _DOMAIN_ALLOWED_WEAK_TOKENS constant and effective_weak logic are now live.
  SC-1 should be GREEN after T2; if it is RED, T2 did not apply correctly — report BLOCKED, do not paper over.
---

# Task brief — T3

## Identity

- task_id: `T3`
- objectives: `O2 / F1`
- scenarios: `SC-1, SC-2, SC-3`
- owner: `xlfg-task-implementer`

## Scope

- allowed files / dirs:
  - `tests/test_policy_domain_allowlist.py` — create new file

- out-of-scope files / dirs:
  - `src/rocky/learning/policies.py` — already edited in T2; read-only here
  - Any existing test file — must not be modified
  - Any conftest or fixture file — not needed (tests are self-contained)

## Mission

- exact change to make:
  1. Confirm `LearnedPolicy` and `LearnedPolicyRetriever` are importable from `rocky.learning.policies` (quick import check).
  2. Create `tests/test_policy_domain_allowlist.py` with exactly three test functions matching the SC-1/SC-2/SC-3 fixture bodies from test-contract.md. The file must:
     - Have a module docstring identifying its purpose.
     - Import `Path` from `pathlib` and `LearnedPolicy`, `LearnedPolicyRetriever` from `rocky.learning.policies`.
     - `test_sc1_repo_command_policy_retrieved`: builds a `LearnedPolicy` with `task_family="repo"`, `keywords=["command", "shell"]`; calls `retriever.retrieve("run the command", "repo/shell_execution")`; asserts the policy is in results.
     - `test_sc2_conversation_command_policy_not_retrieved`: builds a `LearnedPolicy` with `task_family="conversation"`, `keywords=["command", "greeting"]`; calls `retriever.retrieve("run the command", "conversation/general")`; asserts the policy is NOT in results.
     - `test_sc3_repo_command_policy_generalization`: reuses the SC-1 policy; calls `retriever.retrieve("execute the command in the shell", "repo/shell_execution")`; asserts the policy IS in results.
  3. Run `pytest tests/test_policy_domain_allowlist.py -q` and confirm 3 passed, 0 failed.
  4. Run `pytest -q` and confirm full suite is green (expected 733+14+0).

- false success to avoid:
  - Using `@pytest.mark.skip` or `@pytest.mark.xfail` on any of the three tests — they must pass unconditionally after T2's fix.
  - Asserting on score values or intermediate attributes (e.g., `strong_token_matches`) rather than the actual `retrieve()` return list.
  - Using a prompt with multiple strong tokens (would pass SC-1 even without T2's fix — defeats the test's bite).
  - Mocking `LearnedPolicyRetriever` internals — the test must call the real `retrieve()` against real policy objects.
  - Adding YAML frontmatter or any non-Python header to `tests/test_policy_domain_allowlist.py`.

## Handoff

- required artifact: `tests/test_policy_domain_allowlist.py` (new file; plain Python; no frontmatter)
  - Must contain exactly: `test_sc1_repo_command_policy_retrieved`, `test_sc2_conversation_command_policy_not_retrieved`, `test_sc3_repo_command_policy_generalization`.
- done check: `pytest tests/test_policy_domain_allowlist.py -q` — 3 passed, 0 failed, 0 errors.
- dependencies: T2 (the Option A fix in `policies.py` must be in place or all SC-1/SC-3 asserts will fail)
