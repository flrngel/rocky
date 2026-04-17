---
status: DONE
---

# Diagnosis (F1 — run-20260416-190523-next-steps O2)

## Problem summary

`LearnedPolicyRetriever.retrieve` silently drops a teach-sourced policy for the
query "What command should I use to add axios?" because `command` is the only
overlapping token and it sits in `WEAK_MATCH_TOKENS`. The result is
`selected_policies=[]` on the T3 reuse turn. The F1 follow-up asks whether the
gate at `policies.py:232` is the correct drop site, whether `task_family` is
reliably present on real synthesized policies, whether the WEAK_MATCH_TOKENS fix
is additive to the carry-field fix or redundant with it, and whether other drop
sites compound the problem.

---

## Current behavior / baseline

For the documented scenario:

- Teach is run on a pnpm install exchange.  The synthesizer generates retrieval
  triggers such as "install dependency", "add package", "pnpm" and keywords
  ["dependency", "install", "package", "pnpm", ...].  None of these contain
  "command", so the only token that overlaps with the reuse query "What command
  should I use to add axios?" is the literal word "command" — which appears in
  the policy's description or trigger text but not in the keyword list.
- `tokenize_keywords` strips stopwords and lowercases; "command" is left as a
  content token but is explicitly listed in `WEAK_MATCH_TOKENS` at
  `src/rocky/learning/policies.py:14`.
- After subtraction at `policies.py:203`, `strong_token_matches` is empty.
- No trigger substring-matches the reuse query, and the reuse query's upgraded
  signature does not match any of the policy's declared `task_signatures`, so
  `trigger_match=False` and `task_signature_score=0`.
- The three-way gate at `policies.py:232-233` therefore fires: `continue`.
- The policy is never appended to `scored` and is absent from `context.learned_policies`.

---

## Causal chain

1. **Synthesizer keyword generation** (stochastic path): gemma4 synthesizes
   triggers and keywords at `/teach` time focused on the domain ("pnpm",
   "dependency", "install").  These do not include generic imperative words like
   "command" or "use".
2. **Reuse query mismatch**: "What command should I use to add axios?" does not
   share those domain tokens strongly — the dominant word "command" is exactly
   the word the synthesizer skipped.
3. **WEAK_MATCH_TOKENS blanket drop** (`policies.py:14, 203`): "command" is in
   the set, so it cannot rescue the policy from the gate.
4. **Three-way gate fires** (`policies.py:232-233`): `trigger_match=False`,
   `task_signature_score=0`, `strong_token_matches=set()` → `continue`.
5. **Policy not in `scored`**: it is never appended and therefore never returned
   from `retrieve()`.
6. **context.learned_policies is empty** (`context.py:318`): `ContextBuilder`
   calls `self.policy_retriever.retrieve(...)` with no override path for
   non-upgrade-driving policies.
7. **Carry-field rescue does not apply**: `AgentCore._route_upgrade_driving_policy`
   (`agent.py:386`) holds at most one policy — the one that caused the route
   upgrade via `_maybe_upgrade_route_from_project_context` (`agent.py:537-538`).
   If the pnpm policy did NOT drive the upgrade (e.g., the route was already
   `repo/shell_execution`, or a different policy drove it), it receives no rescue.

---

## Root cause / missing capability

**Root cause**: `WEAK_MATCH_TOKENS` is a blanket set applied identically
regardless of domain context.  The word "command" is genuinely noisy for
cross-domain retrieval (a policy about "run a command" should not be recalled
for every conversational prompt that contains "command"), but it is also the
primary lexical carrier for shell-execution queries.  For policies whose
`task_family` is "repo" or "conversation" (within a repo thread), "command"
matches the user's actual intent — but the current implementation neutralizes it
the same way regardless of policy family.

**Missing capability**: no domain-aware token weighting path exists.  All tokens
in `WEAK_MATCH_TOKENS` are dropped uniformly; there is no partial-credit or
family-qualified rescue.  The gate fires before `task_family` scoring at
`policies.py:225-227` (which is a score bonus for `thread.task_family ==
policy.task_family`) so even if the bonus would push score above 0, it does not
help because the gate already ejected the policy.

---

## Evidence

- `src/rocky/learning/policies.py:14` — `WEAK_MATCH_TOKENS = {"command", "find",
  "help", "information", "task", "user"}`.  "command" is a blanket-excluded token
  with no domain qualifier.

- `src/rocky/learning/policies.py:203` — `strong_token_matches = token_matches -
  WEAK_MATCH_TOKENS`.  Subtraction is unconditional.

- `src/rocky/learning/policies.py:225-227` — `task_family` bonus is computed
  AFTER the subtraction, but only as a score bonus; it does not affect
  `strong_token_matches` or the gate at L232.

- `src/rocky/learning/policies.py:232-233` — gate fires when all three signals
  are absent.  Score accumulated up to this point (including `token_overlap` from
  L211) is irrelevant: a policy with score 1 due to weak-token-only overlap is
  ejected before reaching L234.

- `src/rocky/learning/policies.py:234-235` — **second drop site**: `if score < 2
  and not trigger_match: continue`.  A policy that somehow escaped L232 (e.g.,
  via an unlikely trigger substring match) but scored 1 would be dropped here.
  In the documented scenario L232 is the primary drop site; L234 is a redundant
  guard that would also fire if L232 were relaxed but no other score accrued.

- `src/rocky/learning/synthesis.py:1247-1287` — `build_draft` writes
  `"task_family": analysis.task_family` into the POLICY.md YAML frontmatter at
  line 1253.  `LearnedPolicyLoader._scan` (`policies.py:106`) reads that
  frontmatter into `LearnedPolicy.metadata`.  Therefore **`task_family` is
  reliably present** in `policy.metadata` for all policies synthesized via
  `build_draft`, including `/teach`-sourced policies.  `analysis.task_family` is
  set to `task_signature.split("/", 1)[0]` at `synthesis.py:1188` if not
  provided, so it cannot be empty.

- `src/rocky/core/agent.py:537-538` — `_route_upgrade_driving_policy` is assigned
  only when `best_candidate[3] == "policy"` and the candidate drove the upgrade.
  It is a single-slot field.  Policies that matched the session but did not drive
  the upgrade receive no injection.  This confirms the carry-field fix is strictly
  narrower than a general WEAK_MATCH_TOKENS fix.

- `src/rocky/learning/policies.py:237-238` — sort-and-truncate at `scored[:limit]`
  is a THIRD potential drop site but only applies after a policy passes both gates.
  It does not affect the documented scenario (the policy never reaches `scored`).

- `tests/` — no existing test references `WEAK_MATCH_TOKENS`, `weak_match`, or
  `strong_token_matches`.  The gate is untested.  Confirmed by grep across all
  `tests/*.py`.

---

## Tempting shortcuts to reject

- **Expand `WEAK_MATCH_TOKENS` removal for "command"**: removing "command"
  entirely from the set would fix the pnpm case but regress policies that happen
  to mention "command" in an unrelated domain (e.g., a research policy about CLI
  man-page formatting).  The word IS genuinely noisy when not domain-qualified.

- **Rely on the carry-field fix**: the carry-field fix (`agent.py:386, 537-538`)
  rescues exactly one policy per turn — the one that drove the route upgrade.  If
  that policy and the pnpm policy are different objects (they often are: route
  upgrade may be driven by a shell-execution scaffold policy, while the pnpm
  teaching is a separate candidate policy), the carry-field fix leaves the pnpm
  policy stranded.  The two fixes address different failure modes and are additive,
  not redundant.

- **Raise the score from `task_family` bonus**: the `task_family == thread.task_family`
  bonus at `policies.py:226-227` adds 3 to `score`.  That would push score from 0
  to 3, which passes L234.  But it runs AFTER L232, which already ejected the
  policy unconditionally.  Reordering the gate and the bonus would allow
  domain-qualified rescue without touching `WEAK_MATCH_TOKENS` itself — this is a
  viable fix path, but it is not what the current code does.

- **Assume stochastic metadata explains all failures**: the prior run's diagnosis
  (`20260416-205534`) correctly identifies LLM keyword stochasticity as a
  compounding factor when the model generates non-"command" keywords.  However, if
  "command" IS the synthesized keyword overlap, the gate is deterministically
  wrong regardless of stochasticity.  The two failure modes are independent and
  both real.

---

## Unknowns

- Whether `tokenize_keywords("What command should I use to add axios?")` actually
  produces "command" as an output token, or whether it strips it as a stopword.
  If `tokenize_keywords` strips "command", the problem is upstream of
  `WEAK_MATCH_TOKENS` entirely.  This should be verified by inspecting
  `src/rocky/util/text.py`.

- Whether a real teach-sourced policy's keyword list ever includes "command" from
  the model output (which would produce a `keyword_tokens` match).  If gemma4
  never emits "command" as a keyword but the query does contain it, the mismatch
  is in keyword generation, not in the gate.

- Whether the reuse query would produce a trigger-substring match if the synthesizer
  emitted "What command" or "command to add" as a trigger phrase.  This would
  bypass L232 via `trigger_match=True`.

---

## Quick validation probes

1. **Tokenizer check**: run `from rocky.util.text import tokenize_keywords;
   print(tokenize_keywords("What command should I use to add axios?"))` — if
   "command" does not appear, the root cause is upstream of `WEAK_MATCH_TOKENS`.

2. **Gate isolation test**: write a unit test that constructs a `LearnedPolicy`
   with `retrieval_keywords=["command"]` and calls
   `LearnedPolicyRetriever.retrieve("What command should I use to add axios?",
   "repo/shell_execution")`.  Assert the policy IS returned.  This test should
   currently fail, confirming the gate is the drop site.

3. **Reorder gate proof**: move `task_family` scoring before L232 and repeat probe
   2 with a `task_family="repo"` policy and an active repo thread.  The test
   should pass, confirming that domain-qualified rescue via reordering is a viable
   fix without touching `WEAK_MATCH_TOKENS`.

4. **Carry-field independence check**: run the scenario where two separate policies
   exist — one that drives the route upgrade and one that matches only "command" —
   and assert both appear in `context.learned_policies`.  Currently only one will.

5. **Score path check**: add a `print(score, trigger_match, task_signature_score,
   strong_token_matches)` trace inside `retrieve` for the pnpm reuse scenario and
   confirm `score=0, trigger_match=False, task_signature_score=0,
   strong_token_matches=set()` before L232.
