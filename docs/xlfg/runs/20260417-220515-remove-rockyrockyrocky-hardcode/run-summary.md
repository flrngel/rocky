# Run summary — 20260417-220515-remove-rockyrockyrocky-hardcode

## Ask
User flagged `/tmp/rockyrockyrocky` references in the codebase as "worse than hard-coded — calibrated against an external private project." Find them, elaborate, resolve.

## What changed
- `tests/agent/test_self_learn_live.py`: scrubbed 3 literal `/tmp/rockyrockyrocky` paths + 5 sibling private-trace metric citations ("56 fetches", "14× fragment-variant duplication", "earphones trace") + 2 dead-pointer `Spec: .agent-testing/specs/sl-breadth.json` lines. 8 comment/docstring blocks reframed to justify the `>=2 hosts` / `<=50 fetches` thresholds by mechanism (URL `#fragment` re-duplication multiplies counts by integer factors) with cross-reference to `tests/test_agent_dedup.py` as the authoritative deterministic proof. Stale module header rewritten to match what the predicate actually asserts (was claiming `>=8 URLs` / `>=5 queries` / `candidate_pool present` thresholds the test never enforced).

## Proof
- fast_check: `Grep "rockyrockyrocky" tests/ src/` — PASSED (zero hits; remaining hits in `docs/xlfg/knowledge/{current-state,ledger}.{md,jsonl}` are durable history, out of scope per intent).
- smoke_check: `pytest` — PASSED twice (761 passed + 15 skipped, 11.57s pre-review and 12.94s post-review-fix). Matches the 761+15 baseline noted in `docs/xlfg/knowledge/current-state.md`.
- ship_check: not required — comment-only change, threshold values and assertion bodies byte-identical, no live-LLM A/B needed (L20 doesn't apply).

## Residual risk
- **Untracked-specs trap (out of scope, real):** `tests/test_agent_testing_specs.py:26` asserts `assert spec_files`, but all 7 `.agent-testing/specs/*.json` are git-untracked. Fresh clone fails this test. Same class of "calibrated against private workspace state" problem as this run resolved — separate cleanup recommended.
- Historical run docs under `docs/xlfg/runs/20260417-222717-rocky-learning-hardening/{spec,context,compound-summary}.md` still contain `/tmp/rockyrockyrocky` references. Left as immutable archive per xlfg discipline; user may want a separate scrub pass on history.

## Durable lesson
Live-LLM smoke tests calibrated against a private workspace trace are functionally a hard-coded scenario — the test contract becomes unreproducible by anyone who doesn't have the trace. The durable artifact is the threshold's **reasoning** (mechanism-grounded + cross-reference to a deterministic test), not the threshold's **number**. Numbers from private runs are only honest if the deterministic mechanism test is the authoritative bite-point.
