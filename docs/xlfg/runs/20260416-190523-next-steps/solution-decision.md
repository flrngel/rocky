---
name: solution-decision
description: Root-fix choice for F1 — per-task-family weak-token allowlist in policies.py.
status: DONE
---

# Solution decision (F1 — run-20260416-190523-next-steps O2)

## Options considered

### Option A — Per-task-family weak-token allowlist (CHOSEN)

How it works: Add a module-level dict `_DOMAIN_ALLOWED_WEAK_TOKENS: dict[str, frozenset[str]]`
in `policies.py`. In `LearnedPolicyRetriever.retrieve()`, before computing `strong_token_matches`,
read the policy's `task_family` from metadata and subtract any domain-allowed tokens from the
global `WEAK_MATCH_TOKENS` set before the subtraction at L203.

```python
# policies.py — after WEAK_MATCH_TOKENS definition (line 14)
_DOMAIN_ALLOWED_WEAK_TOKENS: dict[str, frozenset[str]] = {
    "repo": frozenset({"command"}),
}

# policies.py — replace L203
policy_task_family = str(policy.metadata.get("task_family") or "")
effective_weak = WEAK_MATCH_TOKENS - _DOMAIN_ALLOWED_WEAK_TOKENS.get(
    policy_task_family, frozenset()
)
strong_token_matches = token_matches - effective_weak
```

`token_overlap` (L204-210) is unaffected — `command` already contributes to the raw score
if it matches. Only the gate guard at L232 changes: `strong_token_matches` for a `repo`
policy can now be non-empty when `command` is the only overlap, so the policy is no longer
dropped by the three-way gate.

Pros:
- Minimal blast radius: one file (`policies.py`), two new lines + one changed line.
- Preserves all six hard constraints (C1-C6).
- `task_family` is always present in synthesized policy metadata (synthesis.py:1253).
- Does not affect retrieval scoring weight (`token_overlap` unchanged).
- Does not affect policies with no `task_family` (empty string → no allowlist applied).

Cons:
- Introduces a second mutable configuration surface alongside `WEAK_MATCH_TOKENS`;
  future maintainers must know both. Mitigated by co-locating them in the same file
  and adding an inline comment.
- Only rescues the `repo/command` case by default; other families (research, data, site)
  still suppress `find`, `help`, etc. uniformly. This is correct scope for this run.

---

### Option B — task-family match as a fourth gate clause

How it works: At L232, add `or (task_family and thread is not None and task_family == thread.task_family)`
as a fourth OR clause. A policy whose `task_family` matches the current thread family
passes the gate even with zero `strong_token_matches`.

Pros:
- No new configuration surface.
- Handles any family (repo, research, conversation) symmetrically.

Cons:
- Too broad: a `conversation` policy with zero meaningful token overlap would be admitted
  for any conversation-family query, degrading precision. The gate exists precisely to
  prevent noise injection — this option silently removes the gate for same-family policies.
- The carry-field fix (agent.py:537-538) already provides a task-family-based bypass for
  the upgrade-driver case. Adding a second task-family gate in the retriever creates
  overlapping but subtly different logic.
- Does not address the root: the token classification is wrong. The gate behavior is
  a downstream consequence.

---

### Option C — Drop `command` from `WEAK_MATCH_TOKENS` globally

How it works: Remove `"command"` from the `WEAK_MATCH_TOKENS` set at L14.

Pros:
- One-character change; trivially reviewable.

Cons:
- `command` is weak in non-repo contexts: a `conversation/general` policy whose only
  keyword is `command` (e.g. "when user asks about a command do X") would then match any
  query containing `command`, regardless of domain relevance.
- Diagnosis.md sibling explicitly rejected this: "would break cross-domain retrieval."
- Contradicts the intent of `WEAK_MATCH_TOKENS` — `command` IS high-frequency and low
  signal in general, just not in the `repo` family.

---

## Chosen solution

**Option A** — per-task-family weak-token allowlist, `repo` family only, key `"command"`.

The fix is 3 lines in `policies.py`. No other file is touched.

---

## Why this is the root solution

The actual failure is a misclassification at the token-classification layer: `command` is
domain-specific signal in the `repo` task family (shell commands are the primary affordance)
but generic noise elsewhere. `WEAK_MATCH_TOKENS` treats it as globally weak, which is correct
for conversation/research but wrong for repo. Option A fixes the classification at the
correct layer (token semantics conditioned on domain) rather than patching the gate or the
scoring weights.

The gate at L232 is correct in design: require at least one strong signal or an exact trigger
match. Option A restores `command` to "strong" status within the `repo` domain, which is the
semantically honest fix.

---

## Narrow question 1 — should `conversation` also get `command` allowlisted?

**No.**

Evidence from synthesis.py: `task_family` for conversation policies is `"conversation"`,
resolved at L795 as `task_signature.split("/", 1)[0]`. The triggers for a conversation-family
policy include `[task_signature, task_family, *path_hints[:4], *tool_names[:4], *prompt_keywords[:4]]`
(L943). There are no shell-execution tool names in a conversation episode by construction.

A `conversation` policy whose retrieval keywords include `command` is categorically different
from a `repo` policy with the same keyword: the conversation policy is describing a meta-rule
("when the user mentions a command, respond concisely") rather than a shell-execution technique.
Admitting it via the `command` token would import meta-instructional policies into repo queries,
which is exactly the cross-domain noise `WEAK_MATCH_TOKENS` guards against.

The pnpm scenario (query: "What command should I use to add axios?") targets a `repo`-family
policy. The `conversation` family is not involved. Restricting the allowlist to `repo` is the
minimum honest scope.

---

## Narrow question 2 — does the L234 score<2 cutoff create a second trap?

**No, it does not trap the pnpm case, but the reasoning matters.**

Trace for a `repo` policy where `command` is the only token overlap, post-fix:

- `strong_token_matches = {"command"}` (non-empty → L232 gate passes)
- `token_overlap`: L204-210 — `command` matches against keyword_tokens, contributing
  `len(query_words & keyword_tokens) * 2 = 1 * 2 = 2` to `score`. So `score >= 2` before
  any other contributions.
- L234 condition is `score < 2 and not trigger_match`. With `score == 2`, the condition
  is false → policy proceeds to `scored.append`.

Therefore the L234 guard does NOT create a second trap for the minimum viable case
(single `command` keyword match). The policy clears L234 with a score of exactly 2.

If the policy also has `task_family == thread.task_family` (score += 3, L227),
`promotion_state == "promoted"` (score += 3, L228), or a partial signature match, score
rises further. The L234 cutoff is only ever a threat when `token_overlap == 0` and there
is no trigger match — which cannot happen post-fix because `command` matching gives
`token_overlap >= 2`.

The L234 cutoff is a redundant second guard confirmed safe here; it does not need to change.

---

## Rejected shortcuts

- **Remove `command` from `WEAK_MATCH_TOKENS` globally**: fixes the symptom for repo but
  degrades precision for conversation and research families where `command` is noise.
- **Expand carry-field fix (agent.py) to non-driver policies**: carry-field rescue is
  already a targeted bypass for upgrade drivers only; expanding it would conflate routing
  with retrieval and would not fix the underlying token classification.
- **Add task-family as a fourth gate clause**: over-admits same-family policies with zero
  relevant overlap; removes the gate for the common case of same-family but unrelated policy.
- **Half-weight weak tokens (Option D from context.md)**: adds floating-point complexity to
  an integer scoring system; does not address the root misclassification; harder to reason
  about and test.
- **Defer with findings-doc only (Option E)**: the fix is 3 lines and the blast radius is
  fully bounded; deferral is not justified.

---

## Disconfirming evidence to watch for

- A `repo`-family policy retrieved ONLY because of `command` token overlap (no signature
  match, no trigger match, no other keyword overlap) that is factually wrong for the query.
  This would indicate `command` is still too weak as a repo signal. Monitor via retrieval
  trace logs (`selected_policies` in trace).
- A policy whose `task_family` metadata is absent or empty. The fix degrades gracefully
  (empty string → no allowlist applied → behavior unchanged vs today). Synthesis.py L795
  always resolves `task_family` before writing frontmatter at L1253, so absence requires
  a hand-authored policy. No action needed; the defensive `or ""` guard handles it.
- Any future expansion of `_DOMAIN_ALLOWED_WEAK_TOKENS` to `"research"` or `"data"` families.
  Before adding: verify that the candidate token (`find`, `help`) is genuinely domain-specific
  in that family and not cross-contaminating retrieval in adjacent families.

---

## Blast radius

**Files touched by the fix:**
- `src/rocky/learning/policies.py` — 3-line change (add constant, modify L203). No import
  changes. No interface changes.

**Files read but not touched:**
- `src/rocky/learning/synthesis.py` — confirms `task_family` is always written at L1253;
  confirms `conversation` family policies don't include shell tool names in keywords.
- `src/rocky/core/agent.py` — carry-field fix at L537-538 is orthogonal; not touched.

**Tests that could be affected:**
- `tests/test_route_intersection.py` — tests `_maybe_upgrade_route_from_project_context`,
  not `LearnedPolicyRetriever.retrieve()`. No retrieval scoring is exercised. **Not affected.**
- `tests/test_agent_runtime.py` — contains `test_learned_tool_refusal_policy_can_upgrade_...`.
  This test uses a policy that was retrieved by trigger match, not by `strong_token_matches`.
  Trigger-match path is unchanged. **Not affected.**
- `tests/agent/` (new test files listed in git status) — unknown; should be verified post-fix
  with `pytest tests/agent/ -v`.
- No existing test directly exercises `WEAK_MATCH_TOKENS` or `strong_token_matches`
  (confirmed via Grep across `tests/`). The fix adds no new failure mode for existing tests.

**Downstream consumers:**
- `LearnedPolicyRetriever.retrieve()` is called from `ContextBuilder` (assembles retrieval
  into `ContextPackage`). The interface signature and return type are unchanged; only which
  policies survive the gate changes.
- `LedgerRetriever` is a separate retriever; not touched (satisfies C3).
- `MemoryRetriever` / `StudentStore` — not touched (satisfies C4).

---

## Rollback plan

The minimal revert is:

```python
# Revert: remove _DOMAIN_ALLOWED_WEAK_TOKENS constant (2 lines)
# Revert: restore L203 to:
strong_token_matches = token_matches - WEAK_MATCH_TOKENS
```

That is a 3-line revert of a single file. No migration, no data change, no config change.
Policies on disk are unaffected; the change is purely in retrieval scoring logic.

Git revert command: `git revert <commit-sha>` — or manual 3-line edit if the commit includes
other changes.

---

## Constraint compliance (C1-C6)

| Constraint | Status | Evidence |
|---|---|---|
| C1 carry-field intact | PASS | `agent.py` not touched |
| C2 no-task_family policies bit-identical | PASS | empty `task_family` → `_DOMAIN_ALLOWED_WEAK_TOKENS.get("", frozenset())` → no change |
| C3 no LedgerRetriever drift | PASS | `LedgerRetriever` not touched |
| C4 MemoryRetriever/StudentStore untouched | PASS | neither file in blast radius |
| C5 commit ≤72 chars | PASS | subject: `fix: allowlist command token for repo-family policy retrieval` = 55 chars |
| C6 no hook-skipping | PASS | no `--no-verify` or similar in rollout plan |

---

## Task decomposition hints

1. **Edit `policies.py`** — add `_DOMAIN_ALLOWED_WEAK_TOKENS` after L14; replace L203 with
   3-line block (read `policy_task_family`, compute `effective_weak`, compute
   `strong_token_matches`). No other edits.
2. **Add a unit test** — create a `LearnedPolicy` fixture with `task_family="repo"` and
   `retrieval_keywords=["command"]`. Assert that `retrieve(prompt="what command to use")` returns
   the policy. Assert that a `task_family="conversation"` policy with same keywords is NOT
   returned by the same prompt (gate still applies).
3. **Run `pytest tests/ -v`** before commit. Confirm `test_route_intersection.py` and
   `test_agent_runtime.py` still pass.
4. **Commit** with message: `fix: allowlist command token for repo-family policy retrieval`.
