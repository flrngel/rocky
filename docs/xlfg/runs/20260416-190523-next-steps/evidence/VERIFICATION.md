---
status: DONE
---

# Verification Results

All test harnesses executed successfully.

## Results JSON

See `results.json` for complete details.

## Summary

- **Verdict**: GREEN
- **Fast Check**: 3 passed (SC-1, SC-2, SC-3)
- **Ship Check**: 10 passed
- **Full Suite**: 733 passed + 14 skipped (matches baseline)
- **Sensitivity Witness**: PASS (revert-fail-restore-pass cycle confirmed)

## Evidence Artifacts

- `results.json` - Complete verification results with all metrics
- `fast-check.log` - test_policy_domain_allowlist.py output
- `ship-check.log` - route upgrade and intersection tests output
- `full-suite.log` - Full pytest run output (733p + 14s)
- `sensitivity-witness.txt` - Revert-fail-restore-pass cycle documentation

## Working Tree State

Clean after restore: `git diff src/rocky/learning/policies.py` shows no changes.
