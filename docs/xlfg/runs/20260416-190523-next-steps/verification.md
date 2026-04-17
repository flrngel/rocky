---
name: verification
description: Layered proof for run-20260416-190523-next-steps (F1 domain allowlist + prior-run commit rollup).
status: DONE
verdict: GREEN
---

# Verification — run-20260416-190523-next-steps

## Verify run

- Timestamp: 20260416-190523
- Commits under test: 06c5066 (O1 pytest green post-commit), 05fa5ec (O2 F1 allowlist + deterministic tests + sensitivity witness)
- Result: GREEN

## Environment doctor

- Working tree confirmed clean after sensitivity restore: `git diff src/rocky/learning/policies.py` returned empty.
- Cosmetic note: `evidence/sensitivity-witness.txt` contains a log echo typo (`political/rocky` vs `personal/rocky`) in one shell echo line; the actual pytest invocations used the correct path and all passed. Non-blocking.
- `evidence/results.json` had a YAML-like `Status: DONE` preamble prepended by the runner; conductor stripped it before JSON parse. No effect on verdict.

## Commands and results

- [fast] `pytest tests/test_policy_domain_allowlist.py -q` — exit 0 — **3 passed**
  - Evidence: `evidence/fast-check.log`
- [smoke] `pytest tests/test_route_upgrade_driving_policy.py tests/test_route_intersection.py tests/test_policy_domain_allowlist.py -q` — exit 0 — **10 passed**
  - Evidence: `evidence/ship-check.log`
- [full] `pytest -q` — exit 0 — **733 passed, 14 skipped in 15.41s** (0 failed)
  - Evidence: `evidence/full-suite.log`
- [sensitivity] Revert Option A lines `policies.py:206-208` → SC-1 fails (AssertionError: repo policy with command-only overlap must be retrieved after domain allowlist fix; 1 failed in 0.20s). Restore → SC-1 passes (1 passed in 0.26s).
  - Evidence: `evidence/sensitivity-witness.txt` L12-46

## Scenario coverage

| ID   | Name                                          | Status |
|------|-----------------------------------------------|--------|
| SC-1 | repo policy with command-only overlap retrieved | pass   |
| SC-2 | conversation policy with command-only overlap NOT retrieved (anti-overreach) | pass   |
| SC-3 | lexically-diverse repo prompt retrieves policy (generalization) | pass   |
| SC-4 | full suite green (regression gate) — 733 passed, 14 skipped, 0 failed | pass   |

## Sensitivity witness

- Bit: true
- Revert path: `src/rocky/learning/policies.py:206-208` (Option A domain allowlist lines)
- Revert result: SC-1 fails — confirms the test bites the real code path
- Restore result: SC-1 passes — confirms the fix is load-bearing
- Working tree clean after restore: confirmed

## First actionable failure

None. All phases GREEN. No required proof gaps remain.
