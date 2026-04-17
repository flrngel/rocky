---
name: context
description: Repo + product truth required for honest plan on O1 (commit) + O2 (F1 WEAK_MATCH_TOKENS review).
status: DONE
---

# Context — run-20260416-190523-next-steps

Skipped `xlfg-repo-mapper`: `memory-recall.md` carries grep-cited file:line anchors for F1's diagnosed surface (`policies.py:14, 203, 232`); O1 is a release-discipline commit with file list already enumerated in `spec.md` O1 completion note. No objective has an unfamiliar / no-hit surface.

## O1 — commit prior uncommitted work

### Working-tree state (confirmed via `git status --short`)

Modified (tracked):
- `docs/xlfg/knowledge/current-state.md` — prior run updated the header + prepended a run-entry
- `docs/xlfg/knowledge/ledger.jsonl` — appended 2 ledger rows for prior run
- `src/rocky/core/agent.py` — the `_route_upgrade_driving_policy` carry-field fix from run-20260416-205534 (NEVER committed; prior run-161631 relied on it as a given and added regression tests)

Deleted (tracked):
- `tests/test_self_learn_live.py` — legacy duplicate; prior run R2 removed it

Untracked (new):
- `tests/agent/__init__.py`, `tests/agent/_helpers.py`, `tests/agent/test_self_learn_live.py`
- `tests/test_agent_testing_specs.py`
- `tests/test_route_upgrade_driving_policy.py`
- `docs/xlfg/runs/20260416-161631-follow-ups-cleanup/` (entire directory)
- `.agent-testing/`, `.rocky/` — **LOCAL STATE, EXCLUDE**
- `docs/pi-autoresearch-rocky-comparison.md`, `docs/pi-autoresearch-rocky-learning-integration-analysis.md` — **UNCONFIRMED PROVENANCE, EXCLUDE**

### Commit message strategy

Last five commits for style grounding:
```
6151892 update .gitignore
c50629e chore: tier A hygiene pass + stats/migrate-retros CLI repair
0083040 feat: resolve all 17 in-scope items from follow-ups-detail (v1.2.0)
1832336 bump: v1.0.5 -> v1.1.0
06cc767 feat: resolve all 16 issues from rocky-issues-report (17 objectives)
```

Style: Conventional Commits with specific-but-compact subject and a structured body when multiple objectives ship. The prior run-20260416-161631's compound summary already enumerates "R3/F4 regression tests, R2 shared helpers, F2 drift guard, F3 recall guardrail" — that's the natural subject line substance.

Draft subject: `test+chore: driving-policy carry tests + shared helpers + drift guard`

Or more complete: `feat: driving-policy carry fix + R3/F4+R2+F2+F3 follow-ups cleanup`

Body: 2-3 bullet points citing the shipped work; end with the standard Co-Authored-By line per user's global CLAUDE.md instructions.

### Pre-commit check

Run `pytest -q` on deterministic suite. Expected: 730+14+0 per prior compound. If anything has drifted (e.g., from the .rocky/ workspace state), the commit must reproduce the 730+14+0 baseline — any deviation triggers investigation before committing.

## O2 — F1 `WEAK_MATCH_TOKENS` principled review

### The mechanism (verified by reading policies.py:L154-238)

`LearnedPolicyRetriever.retrieve`:
1. Tokenize prompt + thread context (`tokenize_keywords`, L10, L186-187). `tokenize_keywords` (util/text.py:L116-127) lowercases, splits on `[a-zA-Z0-9_:+./-]+`, drops tokens `len < 4` or in `STOP_WORDS`, and adds bare-stem variants.
2. For each policy, compute `token_matches` = union of `query ∩ {name, description, triggers, keywords}` plus `thread ∩ keywords` (L196-202).
3. **`strong_token_matches = token_matches - WEAK_MATCH_TOKENS`** (L203). This is F1's diagnosed site.
4. **Gate** (L232-233): `if not trigger_match and not task_signature_score and not strong_token_matches: continue`.
5. Secondary cutoff (L234-235): `if score < 2 and not trigger_match: continue`.

### The F1 scenario from run-20260416-205534

From `/tmp/205534-follow-ups.md` L87-95:
> The reuse prompt "What command should I use to add axios?" had exactly `command` as the only token overlap with the teach policy's keyword list — and that overlap was neutralized by `WEAK_MATCH_TOKENS`. Combined with the route upgrade and a cross-family `task_signature`, this caused the silent retrieval drop this run fixed around.

The gate trip happens at `ContextBuilder._build_policies` (context.py:318) which calls `policy_retriever.retrieve(prompt, task_signature)` against the UPGRADED `task_signature` = `repo/shell_execution`. The pnpm teach policy's declared `task_signatures` list did NOT include `repo/shell_execution` — only `conversation/general` and/or generic repo. Therefore `task_signature_score = 0`. `trigger_match` was False (the policy's triggers didn't substring-match the prompt). Only `{command}` overlapped. `strong_token_matches = {command} - WEAK_MATCH_TOKENS = {}`. Gate trips. Policy silently dropped from `context.learned_policies`.

The prior run fixed this via the carry-field at `AgentCore._route_upgrade_driving_policy`: when the upgrade-site retrieval (agent.py:454-455, using the ORIGINAL signature where the policy DID pass the gate) picks a driving policy, the post-build inject at agent.py:3188 restores it into `context.learned_policies`.

### Why F1 is NOT redundant with the carry-field fix

The carry-field only rescues policies that DROVE a route upgrade. The `policy_retriever.retrieve` call at `context.py:318` is also used for policies that are RELEVANT to the current (possibly upgraded) task but did not drive the upgrade. Those policies are still subject to the WEAK_MATCH_TOKENS gate without any rescue. Concretely: if the user asks "What command should I use to add axios?" and gets routed to `repo/shell_execution` via some mechanism (or was already on `repo/general`), a legitimate shell-family policy whose only overlap with the prompt is `command` would still be dropped at `context.py:318`.

So F1 is defense-in-depth for a broader class of cases than the carry-field covers. Not purely redundant.

### Minimal viable fix (Option A — per-task-family weak-token allowlist)

```python
# src/rocky/learning/policies.py — addition at module top
_DOMAIN_ALLOWED_WEAK_TOKENS: dict[str, frozenset[str]] = {
    "repo": frozenset({"command"}),
}

# Inside retrieve(), replace L203 with:
policy_task_family = str(policy.metadata.get("task_family") or "")
effective_weak = WEAK_MATCH_TOKENS - _DOMAIN_ALLOWED_WEAK_TOKENS.get(policy_task_family, frozenset())
strong_token_matches = token_matches - effective_weak
```

Rationale: `task_family` is already a real field on synthesized policies (set from `task_signature.split("/", 1)[0]` at `synthesis.py:795`). For a `repo` policy, the token `command` is genuinely load-bearing; for other families it can stay weak. This is the smallest principled change and it's directly motivated by the F1 open question.

### Rejected alternatives

- **Option B — add task_family match as a third gate clause** (`if not trigger_match and not task_signature_score and not strong_token_matches and not task_family_match`): broader than necessary, would match any policy whose family matches the current thread family regardless of token overlap, potentially over-matching.
- **Option C — drop WEAK_MATCH_TOKENS entirely**: regresses the original intent (preventing generic conversational tokens from spuriously matching), would likely break `tests/test_route_intersection.py` legitimate-case coverage.
- **Option D — half-weight weak tokens in domain context**: introduces a new scoring category (weak→half-strong→strong); increases code complexity beyond the minimum; no empirical data justifies the extra precision.
- **Option E — defer F1 with findings doc only**: viable fallback if A is rejected on offline-data-insufficiency grounds. Retained as A2b.

### Deterministic test contract for Option A

Two tests in a new `tests/test_learned_policy_weak_match_domain_allowlist.py`:

**Test 1 — `test_command_counts_for_repo_family_policy_in_gate`**:
- Synthesize a `LearnedPolicy` with `metadata = {"task_family": "repo", "retrieval": {"keywords": ["command", "install", "pnpm"]}}`, empty triggers, `task_signatures = []` (so task_signature_score = 0 for any signature).
- Call `retriever.retrieve("What command should I use to add axios?", task_signature="repo/shell_execution")`.
- **Assert**: policy IS in the returned list.
- **Sensitivity witness**: revert the allowlist change → policy is NOT returned → fail.

**Test 2 — `test_command_stays_weak_for_conversation_family_policy`**:
- Same scaffolding but `metadata.task_family = "conversation"`.
- Call with `task_signature="conversation/general"`.
- **Assert**: policy is NOT returned (gate trips because `command` is still weak for conversation family).
- **Sensitivity witness**: if the fix over-reaches (e.g., added `command` to the conversation allowlist by mistake), this test fails.

Test 2 is the anti-over-reach guard — it bites if the allowlist is wrong in the opposite direction, satisfying CF-sensitivity. Together T1 + T2 pin the allowlist to exactly `{"repo": {"command"}}`.

### Load-bearing regressions to re-run

- `tests/test_route_intersection.py` (5 tests) — CF-8 teach over-tagging guard. F1 doesn't touch agent.py, but retrieval scoring changes could affect upstream test expectations.
- `tests/test_route_upgrade_driving_policy.py` (2 tests, prior run) — driving-policy carry invariant. F1 doesn't touch the carry-field but let's verify the invariant holds under the new scoring.
- `tests/test_runtime_learning_binding.py` (if exists) — general retrieval wiring.
- Full `pytest -q` deterministic suite (730+14+0 baseline).

### Live-LLM (gated, NOT in this run's scope)

The follow-up doc L102-104 suggests "gather ~10 recent live-LLM runs, classify retrieval outcomes by whether `WEAK_MATCH_TOKENS` tipped the gate, and decide empirically." For this run we ship the deterministic mechanism fix with a deterministic regression test (A2a path). The live-LLM empirical campaign is orthogonal: it would measure the PREVALENCE of the failure mode, not the mechanism. With the carry-field + this Option A fix, the mechanism is double-covered; prevalence measurement is a future backlog item if it ever becomes interesting.

## Hard constraints

- **C1** — `AgentCore._route_upgrade_driving_policy` carry-field must stay intact; its regression tests (`tests/test_route_upgrade_driving_policy.py`) stay green.
- **C2** — Legacy retrievers' behavior for policies WITHOUT a `task_family` metadata field must be bit-identical to today (effective_weak == WEAK_MATCH_TOKENS). The `.get(policy_task_family, frozenset())` default guarantees this.
- **C3** — `LedgerRetriever` (`learning/ledger_retriever.py`) has its own scoring (different line numbers). F1 only touches `LearnedPolicyRetriever`; don't drift `LedgerRetriever`'s scoring unless a parallel change is explicitly justified. Canary tests in `tests/test_meta_variant_canary.py` must stay green.
- **C4** — `MemoryRetriever` and `StudentStore.retrieve` have their own scoring; F1 does NOT touch them. Out of scope for this run.
- **C5** — Conventional Commits subject for the commit (O1) must be ≤72 chars ideally. Multi-line body acceptable.
- **C6** — Do NOT skip pre-commit hooks (standing instruction).

## Known unknowns

- **U1** — Does the prior run's commit-ready deterministic baseline (730+14+0) still hold? Answer: run `pytest -q` once at the very start of O1, before any edits.
- **U2** — Do the `docs/pi-autoresearch-*` files belong to this repo? Default: no, exclude. If the user surfaces them later, revisit.
- **U3** — Could `task_family` be missing on real policies on disk? The workspace's `.rocky/policies/learned/` is empty (only `generation.json`), so this run has no real-world data to measure. The default-to-blanket fallback handles missing metadata gracefully.

## Harness / environment facts

- Python 3.13; pytest configured `quiet` per `pyproject.toml`.
- Deterministic suite runs in <30s typically; targeted module runs <1s.
- No live-LLM required for either O1 or O2.
- The `xlfg-harness-profiler` specialist is skipped: for A2a the cheapest honest harness is `pytest tests/test_learned_policy_weak_match_domain_allowlist.py tests/test_route_upgrade_driving_policy.py tests/test_route_intersection.py -q` (fast subset, <3s) plus a full `pytest -q` at ship time. For O1 the check is just `pytest -q` pre-commit. Both harnesses are well-understood from prior runs; no profiling specialist needed.

## Research

Not required. F1's scoring mechanism is fully readable in `policies.py` L154-238. No freshness-dependent facts.
