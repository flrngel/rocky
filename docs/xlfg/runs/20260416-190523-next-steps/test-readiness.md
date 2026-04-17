---
status: DONE
---

# Test readiness — run-20260416-190523-next-steps

## Verdict
- `READY`

---

## Required scenario coverage

**Covered by scenario contracts:**
- SC-1: repo policy with `command`-only overlap is retrieved (P0, bites without fix)
- SC-2: non-repo (`conversation`) policy with `command`-only overlap is NOT retrieved (P0, kills Option C/global-removal monkey fix)
- SC-3: lexically-diverse repo prompt still retrieves the policy (P1, kills exact-string-match monkey fix)
- SC-4: carry-field and route-intersection regressions stay green (P0, full suite gate)

**Vague or missing:** none.

---

## Trace verification — six checks from mission

### Check 1 — SC-1 actually fails without the fix

Verified against `src/rocky/learning/policies.py` (read in full, 244 lines).

`tokenize_keywords("run the command")` (text.py L116-127): min length 4, not stopword.
- "run" -> 3 chars -> dropped
- "the" -> stopword -> dropped
- "command" -> 7 chars, not in STOP_WORDS -> kept
Result: `query_words = {"command"}`

`keyword_tokens` for the fixture policy (`retrieval.keywords = ["command", "shell"]`):
- "command" -> kept, "shell" -> kept
Result: `keyword_tokens = {"command", "shell"}`

`token_matches = {"command"}` (keyword_tokens path fires; no other path adds new tokens from query_words).

Before fix (original L203): `strong_token_matches = {"command"} - WEAK_MATCH_TOKENS = set()`.
No trigger match (policy.triggers = []). task_signature_score = 0 (policy.task_signatures = []).
Gate at L237 (`if not trigger_match and not task_signature_score and not strong_token_matches: continue`) fires. Policy dropped. `retrieve()` returns `[]`. Assert `any(...)` fails. Trace holds.

After fix (actual code, L206-208):
```
policy_task_family = "repo"
effective_weak = WEAK_MATCH_TOKENS - frozenset({"command"}) = {"find","help","information","task","user"}
strong_token_matches = {"command"} - effective_weak = {"command"}
```
Gate does not fire. `token_overlap = 1 * 2 = 2`. `score = 2 + 2 (project scope) + 3 (promoted) = 7`. L239 guard (`score < 2`) false. Policy returned. Assert passes.

**Conclusion: trace holds. SC-1 assertion bites correctly without the fix.**

### Check 2 — SC-2 bites against Option C (global removal)

Fixture: `task_family="conversation"`, `retrieval.keywords=["command", "greeting"]`.
Query: `"run the command"` -> `query_words = {"command"}`.
`keyword_tokens = {"command", "greeting"}`.
`token_matches = {"command"}`.

Under Option A (correct): `_DOMAIN_ALLOWED_WEAK_TOKENS.get("conversation", frozenset()) = frozenset()`. `effective_weak = WEAK_MATCH_TOKENS`. `strong_token_matches = set()`. Gate fires -> filtered. SC-2 assert `not any(...)` passes.

Under Option C (global removal of "command" from WEAK_MATCH_TOKENS): `strong_token_matches = {"command"}` (non-empty). Gate does not fire. `score = 7`. Policy returned. SC-2 assert **fails**. Option C is killed correctly.

False-negative risk via task_signature_score: fixture metadata has no `"task_signatures"` key. `policy.task_signatures` property returns `[]` (L50-51: `metadata.get("task_signatures") or []`). `task_signature_score = 0`. No bypass via this path.

False-negative risk via thread task_family boost: retrieve called without `thread` argument (default None). L231 check (`if thread is not None and ...`) skips. No score boost can resurrect the policy past the gate.

**Conclusion: SC-2 bites cleanly. No false-negative risk from task_signature_score or thread paths.**

### Check 3 — SC-3 lexical diversity

Query: `"execute the command in the shell"`.
`tokenize_keywords`: "execute" (7 chars, not stopword -> kept), "the" (stopword -> dropped), "command" (kept), "in" (2 chars -> dropped), "shell" (5 chars, not stopword -> kept).
`query_words = {"execute", "command", "shell"}`.

`keyword_tokens = {"command", "shell"}` (same policy as SC-1).
`token_matches = {"command", "shell"}`.
`strong_token_matches` includes "shell" (not in WEAK_MATCH_TOKENS regardless of domain) -> gate does not fire even pre-fix.

Wait: this means SC-3 would pass even without the fix, because "shell" is a strong token. SC-3 is NOT a sensitivity witness for the fix itself — it is purely a generalization guard against a shallow prompt-string patch. The test-contract correctly marks SC-3 as P1 with no sensitivity witness of its own and defers sensitivity to SC-1. SC-3's anti-monkey probe is: "A patch that pattern-matches the exact prompt 'run the command' would fail SC-3." This is valid because "execute the command in the shell" is lexically distinct from "run the command".

The SC-3 fixture reuses the SC-1 retriever instance (same policy, task_family="repo"), so it passes post-fix. Pre-fix, it would also pass (because "shell" is strong). SC-3 is not meant to bite on the fix itself — only on the prompt-matching shortcut. This is correct and the test-contract is explicit about it. No issue.

**Conclusion: SC-3 is lexically distinct AND produces "command" (and "shell") in tokens. Generalization guard is sound for its stated purpose.**

### Check 4 — T3 fixtures: task_signatures and metadata.get behavior

`policy.task_signatures` property (L50-51):
```python
return [str(item) for item in (self.metadata.get("task_signatures") or [])]
```
Fixture metadata does not include `"task_signatures"` key. `.get("task_signatures")` returns `None`. `None or []` = `[]`. `task_signature_score = 0`. No exception, no bypass.

`policy.metadata.get("task_family")`: fixture always supplies `"task_family"` in metadata dict. `.get()` returns the string. The `or ""` guard in `str(policy.metadata.get("task_family") or "")` handles absent keys gracefully — no raise risk.

**Conclusion: empty task_signatures produces task_signature_score=0 safely. No raise risk on metadata access.**

### Check 5 — T1 exclusion list vs .gitignore

`.gitignore` (read in full) does NOT contain entries for `.agent-testing/` or `.rocky/`.

These directories appear in `git status` as untracked (`?? .agent-testing/`, `?? .rocky/`). Since they are untracked and not gitignored, `git add .` or `git add -A` WOULD sweep them in. T1's brief correctly requires explicit path staging and lists `.agent-testing/` and `.rocky/` as explicit exclusions (task-brief.md L60-61).

The plan's guard is the explicit-path discipline, not gitignore. The T1 done-check (`git diff --staged --stat`) catches any accidental staging before commit. This is sufficient.

**Non-blocking note:** adding `.agent-testing/` and `.rocky/` to `.gitignore` would be cleaner hygiene, but it is not required for T1's proof to hold. The brief's explicit-path discipline is the correct procedural guard.

**Conclusion: T1 exclusion is practical and traceable. Not REVISE-worthy since the brief already calls this out explicitly.**

### Check 6 — T4 commit scope completeness

T4 commits: `src/rocky/learning/policies.py` + `tests/test_policy_domain_allowlist.py` + `docs/xlfg/runs/20260416-190523-next-steps/` (entire run-dir including this file).

The fix touches only `policies.py` (blast radius confirmed from solution-decision.md, verified in source: constant at L15-17, effective_weak logic at L206-208, no import changes, no interface changes, no other files touched).

The run-dir staging (`docs/xlfg/runs/20260416-190523-next-steps/`) captures spec.md, context.md, diagnosis.md, solution-decision.md, test-contract.md, test-readiness.md (this file), tasks/, evidence/ — all run artifacts. The run-dir is covered by `docs/xlfg/runs/*` in `.gitignore`, so it requires explicit staging; the T4 brief accounts for this with explicit path staging.

T1-committed files (`agent.py`, `test_route_upgrade_driving_policy.py`, `tests/agent/`, `test_agent_testing_specs.py`) are already in history and must not be re-staged. T4 brief explicitly excludes them.

**Conclusion: T4 scope is complete and correctly bounded. Nothing is silently dropped.**

---

## Practicality check

- All checks are pure unit tests with no LLM provider dependency. `pytest tests/test_policy_domain_allowlist.py -q` runs in milliseconds.
- Fast proof (SC-1, SC-2, SC-3) is a single pytest invocation. No e2e stack required.
- Ship proof is `pytest -q` (full suite, 733+14+0). Baseline count is explicit in T4's done-check.
- The plan is not "run everything later" — specific test file and specific test function names are declared.
- The plan does not over-test. Three unit tests plus a full-suite regression run is the minimum honest proof for a scoring-path change.

---

## Under-testing risks

None. SC-1+SC-2 cover both the fix path and the anti-monkey direction. SC-3 adds lexical diversity. SC-4 provides regression cover via full suite.

---

## Over-testing risks

None. SC-4 full `pytest -q` is the minimum honest ship proof. No giant e2e stack is demanded beyond what already exists.

---

## Missing commands / manual proof gaps

None. All checks are automated pytest commands. No manual smoke required. No core verification is delegated to the user.

---

## Required fixes before implementation

None. All six trace checks pass. The plan is complete and implementation can proceed.

---

## Source grounding

- `/Users/flrngel/project/personal/rocky/src/rocky/learning/policies.py` (read in full, 244 lines): fix already applied at L15-17 and L206-208; gate at L237; score cutoff at L239.
- `/Users/flrngel/project/personal/rocky/src/rocky/util/text.py` (read L116-127): `tokenize_keywords` regex `[a-zA-Z0-9_:+./-]+`, min length 4, STOP_WORDS filter; "command", "shell", "execute" are not stopwords.
- `/Users/flrngel/project/personal/rocky/.gitignore` (read in full): `.agent-testing/` and `.rocky/` absent.
- `/Users/flrngel/project/personal/rocky/docs/xlfg/runs/20260416-190523-next-steps/test-contract.md`: SC-1..SC-4 fixtures and sensitivity witnesses reviewed.
- `/Users/flrngel/project/personal/rocky/docs/xlfg/runs/20260416-190523-next-steps/solution-decision.md`: Option A blast radius confirmed.
- `/Users/flrngel/project/personal/rocky/docs/xlfg/runs/20260416-190523-next-steps/tasks/T1/task-brief.md` and `T4/task-brief.md`: commit scope and exclusion lists verified.
