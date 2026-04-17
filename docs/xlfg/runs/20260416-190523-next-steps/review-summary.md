---
name: review-summary
description: Proportional review synthesis for run-20260416-190523-next-steps.
status: DONE
verdict: APPROVE-WITH-NOTES-FIXED
---

# Review summary — run-20260416-190523-next-steps

## Verdict: APPROVE-WITH-NOTES-FIXED

Single architecture lens (low-risk change: 3 lines in `policies.py` + 3 new deterministic tests). One P2 finding surfaced with explicit fix-in-place text; fix applied inline and deterministic proof subset re-run green. No P0/P1 blockers. Does NOT consume a loopback.

## Findings resolution

| ID | Severity | Finding | Resolution |
|----|----------|---------|------------|
| F1-P2-A | P2 | `_DOMAIN_ALLOWED_WEAK_TOKENS` constant had no inline comment; readers without F1 context have no signal this is intentional domain-specific widening | Added 6-line comment above the constant at `src/rocky/learning/policies.py:15` (cross-refs `LedgerRetriever` parallel path per F1-P1-A). Deterministic subset (10 tests) + full suite (733+14+0) re-green after fix. |
| F1-P1-A | P1 obs | `LedgerRetriever` (`src/rocky/learning/ledger_retriever.py`) does NOT apply the domain allowlist — intentional per constraint C3 / Phase 2.3 scope | No action this run. Covered by the new inline comment which flags the Phase 2.4 T3 collapse as the reconciliation point. |
| F1-P1-B | P1 obs | `task_family` metadata is read twice (policies.py:206 and :230) — style/refactor concern, not correctness | No action this run. Acceptable hygiene follow-up. Not blocking. |
| F1-P2-B | P2 obs | Test coverage for degenerate metadata (empty dict, missing key, untested family) — reviewer explicitly judged as safe-by-fallback, no added test needed | No action. |

## Fixed in-run

- `src/rocky/learning/policies.py` — added 6-line comment above `_DOMAIN_ALLOWED_WEAK_TOKENS` documenting intent + extension point + cross-ref to `LedgerRetriever` divergence.

Before:
```python
WEAK_MATCH_TOKENS = {...}
_DOMAIN_ALLOWED_WEAK_TOKENS: dict[str, frozenset[str]] = {
    "repo": frozenset({"command"}),
}
```

After:
```python
WEAK_MATCH_TOKENS = {...}
# Per-domain weak-token allowlist (F1 fix).
# Tokens in WEAK_MATCH_TOKENS that are demoted from the strong-match gate for a
# specific task_family. Add new families here when a weak token is legitimately
# discriminative in that domain (e.g. "command" is meaningful in repo workflows).
# LedgerRetriever (ledger_retriever.py) has a parallel scoring path that does NOT
# yet apply this allowlist; reconcile during Phase 2.4 T3 collapse.
_DOMAIN_ALLOWED_WEAK_TOKENS: dict[str, frozenset[str]] = {
    "repo": frozenset({"command"}),
}
```

## Re-verification after inline fix

- `pytest tests/test_policy_domain_allowlist.py tests/test_route_upgrade_driving_policy.py tests/test_route_intersection.py -q` → 10 passed in 0.97s
- `pytest -q` (full) → 733 passed, 14 skipped in 12.01s (unchanged from pre-fix verification)

## Residual risks (accepted, not blocking)

1. **LedgerRetriever divergence** (F1-P1-A): the parallel retriever at `src/rocky/learning/ledger_retriever.py` does not apply `_DOMAIN_ALLOWED_WEAK_TOKENS`. Scheduled for Phase 2.4 T3 collapse per the module's own docstring. The new inline comment makes this cross-reference discoverable at the source site.
2. **`task_family` duplicate read** (F1-P1-B): minor hygiene. Deferred to a future hygiene pass.

## Scope limits

- Only `src/rocky/learning/policies.py` and `tests/test_policy_domain_allowlist.py` landed this run. The T1 commit (06c5066) was already reviewed by run-20260416-161631 and not re-audited here.
- No security / performance / UX lens — F1 touches retrieval scoring (a hot path in theory, but the per-policy cost is a single dict.get() and set-difference, bounded by policy count). No auth, secrets, user-facing surfaces.
