---
status: DONE
---

# Test contract — run-20260416-190523-next-steps

## Mission

Lock the smallest honest proof contract for:
- O2 (F1): domain-aware weak-match allowlist in `src/rocky/learning/policies.py` L203.
- O1 (commit): confirmed via full-suite green gate.

Pass-bar from solution-decision.md Option A:
- `repo` policies whose only keyword overlap with the query is `command` now pass
  the L232 `strong_token_matches` gate and are returned by `retrieve()`.
- Non-repo family policies whose only overlap is `command` are still filtered
  (anti-overreach: the allowlist is domain-scoped, not global).
- Carry-field and route-intersection regressions stay green.
- Full baseline stays green (730+14+0 → 731+14+0 after +1 new test).

---

## Required scenario contracts

### SC-1 — repo policy with `command`-only overlap is retrieved

- objective: `O2 / F1 / A2a`
- requirement_kind: `F2P`
- priority: `P0`
- query_ids: `Q1 I1 A1`

**Minimum fixture**

```python
from pathlib import Path
from rocky.learning.policies import LearnedPolicy, LearnedPolicyRetriever

policy = LearnedPolicy(
    policy_id="repo-shell-guidance",
    scope="project",
    path=Path("/tmp/fake/POLICY.md"),
    body="Use git status before running commands.",
    metadata={
        "task_family": "repo",
        "retrieval": {"keywords": ["command", "shell"]},
        "promotion_state": "promoted",
    },
)
retriever = LearnedPolicyRetriever([policy])
# Query shares only "command" with keyword_tokens; no trigger, no task_signature match.
results = retriever.retrieve("run the command", "repo/shell_execution")
assert any(p.policy_id == "repo-shell-guidance" for p in results), (
    "repo policy with command-only overlap must be retrieved after domain allowlist fix"
)
```

**Why this bites without the fix**

Before Option A, `strong_token_matches = token_matches - WEAK_MATCH_TOKENS`.
`WEAK_MATCH_TOKENS` contains `"command"`, so `strong_token_matches == set()` for
this policy (no trigger, no task_signature match).  The L232 gate
`if not trigger_match and not task_signature_score and not strong_token_matches: continue`
filters the policy out.  `retrieve()` returns `[]`.  The assert fails.

After Option A:
```python
_DOMAIN_ALLOWED_WEAK_TOKENS = {"repo": frozenset({"command"})}
effective_weak = WEAK_MATCH_TOKENS - _DOMAIN_ALLOWED_WEAK_TOKENS.get(
    policy.metadata.get("task_family") or "", frozenset()
)
strong_token_matches = token_matches - effective_weak
```
`effective_weak` excludes `"command"` for `task_family="repo"`, so
`strong_token_matches == {"command"}`, the gate passes, score = 2 + 2 (project scope) = 4 ≥ 2,
and the policy is returned.

- fast_check: `pytest tests/test_policy_domain_allowlist.py::test_sc1_repo_command_policy_retrieved -q`
- ship_phase: `fast`
- ship_check: `pytest tests/test_policy_domain_allowlist.py -q`
- smoke_check: NONE
- regression_check: `pytest -q`
- manual_smoke: NONE

**Sensitivity witness (exact revert)**

Revert L203 in `src/rocky/learning/policies.py`:

```diff
-            effective_weak = WEAK_MATCH_TOKENS - _DOMAIN_ALLOWED_WEAK_TOKENS.get(
-                policy.metadata.get("task_family") or "", frozenset()
-            )
-            strong_token_matches = token_matches - effective_weak
+            strong_token_matches = token_matches - WEAK_MATCH_TOKENS
```

With the revert applied: `pytest tests/test_policy_domain_allowlist.py::test_sc1_repo_command_policy_retrieved -q` → **FAIL** (`AssertionError: repo policy with command-only overlap must be retrieved after domain allowlist fix`).
Restore the Option A lines → **PASS**.

- anti_monkey_probe: A patch that changes `WEAK_MATCH_TOKENS` to remove `"command"` globally (Option B) would also make SC-1 pass but would break SC-2.  SC-1 alone is not enough; SC-2 is mandatory.

---

### SC-2 — non-repo (`conversation`) policy with `command`-only overlap is NOT retrieved

- objective: `O2 / F1 / A2a (anti-overreach)`
- requirement_kind: `F2P`
- priority: `P0`
- query_ids: `Q1 I1 A1`

**Minimum fixture**

```python
from pathlib import Path
from rocky.learning.policies import LearnedPolicy, LearnedPolicyRetriever

policy = LearnedPolicy(
    policy_id="conversation-greeting-guidance",
    scope="project",
    path=Path("/tmp/fake/POLICY.md"),
    body="Greet the user warmly before issuing commands.",
    metadata={
        "task_family": "conversation",
        "retrieval": {"keywords": ["command", "greeting"]},
        "promotion_state": "promoted",
    },
)
retriever = LearnedPolicyRetriever([policy])
# Query shares only "command" with keyword_tokens; no trigger, no task_signature match.
results = retriever.retrieve("run the command", "conversation/general")
assert not any(p.policy_id == "conversation-greeting-guidance" for p in results), (
    "conversation policy with command-only overlap must NOT be retrieved (allowlist is repo-scoped)"
)
```

**Why this must stay filtered**

For `task_family="conversation"`, `_DOMAIN_ALLOWED_WEAK_TOKENS.get("conversation", frozenset())` returns
`frozenset()`, so `effective_weak == WEAK_MATCH_TOKENS` (unchanged).
`"command"` remains weak; `strong_token_matches == set()` → L232 gate fires → filtered.

A global removal of `"command"` from `WEAK_MATCH_TOKENS` (Option B) would return this policy
incorrectly.  SC-2 is the guard that kills Option B.

- fast_check: `pytest tests/test_policy_domain_allowlist.py::test_sc2_conversation_command_policy_not_retrieved -q`
- ship_phase: `fast`
- ship_check: `pytest tests/test_policy_domain_allowlist.py -q`
- smoke_check: NONE
- regression_check: `pytest -q`
- manual_smoke: NONE

**Sensitivity witness**

Apply the Option B anti-pattern: remove `"command"` from `WEAK_MATCH_TOKENS` globally instead of using the domain allowlist.  SC-2 fails (`AssertionError: conversation policy with command-only overlap must NOT be retrieved`).
Restore to Option A → **PASS**.

- anti_monkey_probe: If a patch adds `"command"` to the allowlist for ALL families (i.e., `_DOMAIN_ALLOWED_WEAK_TOKENS = {"repo": ..., "conversation": ..., ...}`), SC-2 would still pass — but only if the fixture uses `task_family="conversation"` and the allowlist is left empty for that key.  The fixture is safe: `frozenset()` for `"conversation"` means the token stays weak.

---

### SC-3 — lexically-diverse repo prompt still retrieves the policy (generalization)

- objective: `O2 / F1 / A2a (generalization)`
- requirement_kind: `F2P`
- priority: `P1`
- query_ids: `Q1 I1`

**Minimum fixture**

```python
# Same policy as SC-1 (task_family="repo", keyword="command").
# Use a lexically different prompt that still tokenizes to include "command".
results_alt = retriever.retrieve("execute the command in the shell", "repo/shell_execution")
assert any(p.policy_id == "repo-shell-guidance" for p in results_alt), (
    "generalization: alternate repo prompt with 'command' token must also retrieve the policy"
)
```

This prevents a shallow patch that hard-codes the exact prompt string from SC-1.

- fast_check: `pytest tests/test_policy_domain_allowlist.py::test_sc3_repo_command_policy_generalization -q`
- ship_phase: `fast`
- ship_check: `pytest tests/test_policy_domain_allowlist.py -q`
- smoke_check: NONE
- regression_check: NONE (SC-1 sensitivity already covers the code path)
- manual_smoke: NONE
- anti_monkey_probe: A patch that pattern-matches the exact prompt `"run the command"` would fail SC-3.

---

### SC-4 — carry-field and route-intersection regressions stay green

- objective: `O1 (release gate)`
- requirement_kind: `P2P`
- priority: `P0`
- query_ids: n/a

Existing test files must stay green after the Option A change.

- practical_steps:
  1. Apply the Option A fix to `src/rocky/learning/policies.py`.
  2. Run `pytest tests/test_route_upgrade_driving_policy.py -q` — expect all passing.
  3. Run `pytest tests/test_route_intersection.py -q` — expect all passing.
  4. Run `pytest -q` — expect full baseline green (731+14+0 with +1 from SC-1..SC-3).
- fast_check: `pytest tests/test_route_upgrade_driving_policy.py tests/test_route_intersection.py -q`
- ship_phase: `fast`
- ship_check: `pytest -q`
- smoke_check: NONE
- regression_check: `pytest -q`
- manual_smoke: NONE
- anti_monkey_probe: The Option A change is structurally isolated to the `LearnedPolicyRetriever.retrieve` scoring loop.  If a diff accidentally touches `_maybe_upgrade_route_from_project_context` or the carry-field inject site, SC-4 would catch it.
- notes: Baseline count (730+14+0) is from prior sibling context.  +1 new test file adds 3 test functions (SC-1, SC-2, SC-3); baseline becomes 733+14+0.

---

## Trivially-satisfiable-assert traps

1. **Asserting on score number instead of `retrieve()` return value** — a patch could inflate the score without fixing the L232 gate.  All SC scenarios assert `policy_id in [p.policy_id for p in results]` which is the real behavioral claim.
2. **Asserting on the presence of `_DOMAIN_ALLOWED_WEAK_TOKENS` attribute** — structural check, not behavioral.  Rejected.
3. **Using a prompt with multiple strong tokens** — would make SC-1 pass even without the fix (score gate cleared by other tokens).  Fixture is carefully limited to `command` as the only shared token.

## Proof obligation for proof-map.md

| Proof ID | Claim | Witness |
|---|---|---|
| PM-1 | repo policy with command-only overlap is retrieved after fix | SC-1 RED→GREEN across revert |
| PM-2 | fix does not overgeneralize to non-repo families | SC-2 GREEN throughout |
| PM-3 | carry-field invariant unaffected | SC-4 / `test_route_upgrade_driving_policy.py` |
| PM-4 | baseline suite unaffected | `pytest -q` full run |

## New test file required

`tests/test_policy_domain_allowlist.py` — must be created by the implementation lane.
Contains `test_sc1_repo_command_policy_retrieved`, `test_sc2_conversation_command_policy_not_retrieved`,
`test_sc3_repo_command_policy_generalization`.
Uses `LearnedPolicy` constructor directly (no synthesized POLICY.md files) for full determinism.
No mocking of LLM providers required — pure unit tests against the scoring path.
