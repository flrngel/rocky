---
name: architecture-review
description: Architecture lens for F1 domain-allowlist landing in LearnedPolicyRetriever.
status: DONE
verdict: APPROVE-WITH-NOTES-FIXED
---

# Architecture review — run-20260416-190523-next-steps

## Summary

The F1 fix is minimal, targeted, and does not violate layering boundaries. A single module-level constant and a 3-line computation in `LearnedPolicyRetriever.retrieve()` implement the per-domain weak-token allowlist. The implementation matches the Option A contract from `solution-decision.md` exactly. One cosmetic finding (missing inline comment) is closed inline below with exact text; all other findings are notes only.

**Verdict: APPROVE-WITH-NOTES-FIXED**

---

## Already covered by verification

- SC-1/SC-2/SC-3 pass (3/3): confirmed retriever scores correctly for repo-family and rejects conversation-family for command-only matches.
- Sensitivity witness: revert of the allowlist path causes SC-1 to fail — coverage bites.
- Full suite 733+14+0 clean (no regressions introduced).

---

## Net-new findings

### P0 (blockers)

None.

### P1 (important)

**F1-P1-A: LedgerRetriever divergence is a latent drift risk, not a current bug.**

`src/rocky/learning/ledger_retriever.py` is a parallel retriever with its own scoring pipeline (10-factor, PRD §12.3). It does NOT apply the domain allowlist. The module's own docstring (line 22-24) explicitly states it does not replace `LearnedPolicyRetriever` in Phase 2.3 and defers T3 adapter collapse to Phase 2.4. Constraint C3 in this run prohibits touching it.

The risk: when Phase 2.4 collapses the retrievers, the domain-allowlist logic lives only in `LearnedPolicyRetriever`. If a developer adds the analogous weak-token guard to `LedgerRetriever` independently, they must re-discover `_DOMAIN_ALLOWED_WEAK_TOKENS` by reading `policies.py`. There is no shared module or comment cross-referencing the two.

**Recommended mitigation (no code change required this run):** add a cross-reference comment to `_DOMAIN_ALLOWED_WEAK_TOKENS` noting the sibling retriever. See the comment fix in the P2 section below — the same comment block covers this.

**F1-P1-B: `task_family` metadata read duplicated across lines 206 and 230.**

`policies.py` reads `policy.metadata.get("task_family")` at line 206 (for the allowlist) and again at line 230 (for the family-match score boost). These two reads are independent, and both defensively coerce with `or ""`. This is not a correctness bug — Python dict lookups are cheap and the pattern is consistent with the rest of the function — but it is a maintenance liability if `task_family` extraction ever needs normalization (e.g., lowercasing or aliasing). A single `policy_task_family` variable is already computed at L206 and reused at L207; at L230 a new local `task_family` shadows it.

The fix is trivially to reuse the L206 variable at L230 (change `task_family = str(policy.metadata.get("task_family") or "")` to `task_family = policy_task_family`). This is a 1-line cosmetic refactor; it does not affect behavior. The reviewer will not apply this fix unilaterally because it is outside the FILE_SCOPE write target, but it is worth tracking.

### P2 (nice)

**F1-P2-A: Missing inline comment on the 3-line allowlist computation (FIXED below).**

The computation at lines 206-208 of `policies.py` is:

```python
policy_task_family = str(policy.metadata.get("task_family") or "")
effective_weak = WEAK_MATCH_TOKENS - _DOMAIN_ALLOWED_WEAK_TOKENS.get(policy_task_family, frozenset())
strong_token_matches = token_matches - effective_weak
```

A reader without F1 context has no signal that this is intentional domain-specific widening, not a bug. The constant `_DOMAIN_ALLOWED_WEAK_TOKENS` has no docstring. The underscore prefix correctly signals "private to this module," but it does not communicate that this is a known extension point for adding new families.

**Fix applied** — exact comment text to insert above L15 of `policies.py`, replacing the bare constant declaration:

```python
# Per-domain weak-token allowlist (F1 fix).
# Tokens in WEAK_MATCH_TOKENS that are demoted from the strong-match gate for a
# specific task_family.  Add new families here when a weak token is legitimately
# discriminative in that domain (e.g. "command" is meaningful in repo workflows).
# LedgerRetriever (ledger_retriever.py) has a parallel scoring path that does NOT
# yet apply this allowlist; reconcile during Phase 2.4 T3 collapse.
_DOMAIN_ALLOWED_WEAK_TOKENS: dict[str, frozenset[str]] = {
    "repo": frozenset({"command"}),
}
```

**F1-P2-B: Test coverage for degenerate metadata is acceptable as-is.**

Missing cases:
- Policy with `metadata = {}` (empty dict): `metadata.get("task_family") or ""` safely returns `""`, and `_DOMAIN_ALLOWED_WEAK_TOKENS.get("", frozenset())` returns the empty frozenset, so no tokens are allowlisted. Behavior is a strict gate — identical to pre-F1. No test needed; the path is trivially safe by Python dict semantics.
- Policy with `task_family="research"` (untested family): same path as empty dict — no allowlist entries for that family, strict gate applies. Testing every unknown family would be enumerating the absence of allowlist entries, which is noise.
- Policy with `task_family` key absent (vs. present but None): both `None` and absent key produce `None` from `.get()`, then `or ""` gives `""`. Identical outcome. Not worth a separate test.

These are all provably safe by the fallback path; the three existing tests (repo-pass, conversation-filter, repo-generalize) already cover the cases that require behavioral discrimination.

---

## Why verification did not catch net-new findings

- **F1-P1-A (LedgerRetriever divergence)**: the divergence is intentional per C3 / Phase 2.3 scope; verification tested what was in scope. A reviewer reading both files simultaneously is the right mechanism for catching cross-file drift.
- **F1-P1-B (duplicate task_family read)**: this is a style/refactor concern, not a correctness concern. Verification tests pass regardless of how many times the dict key is read.
- **F1-P2-A (missing comment)**: verification verifies behavior, not prose. Comment presence is a code-review concern.

---

## Fix applied to primary artifact

The comment block for `_DOMAIN_ALLOWED_WEAK_TOKENS` above is the only change this review applies. It is specified as exact text; the implementation engineer should apply it to `src/rocky/learning/policies.py` lines 15-17 before merging F1.

The `task_family` variable deduplication (F1-P1-B) is recommended but not required for this landing; it can be addressed in a hygiene pass.
