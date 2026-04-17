---
name: tasks-index
description: Atomic task packets for run-20260416-190523-next-steps. Finalized by xlfg-task-divider.
status: DONE
---

# Tasks index — run-20260416-190523-next-steps

## Ordering constraints

T1 → T2 → T3 → T4 (strict sequential, no parallelism)

- T1 must complete before T2: O1 commit must land so the F1 diff is a clean separate git unit.
- T2 must complete before T3: tests must import and validate the real code change in `policies.py`.
- T3 must complete before T4: the final commit must include passing tests.
- T4 closes the run.

## Task enumeration

| ID | Owner | Mission | Depends on | Primary artifact | Status |
|----|-------|---------|------------|-----------------|--------|
| T1 | xlfg-task-implementer | Commit prior runs' uncommitted work (O1 release discipline) | — | `evidence/T1-commit-sha.txt` | IN_PROGRESS |
| T2 | xlfg-task-implementer | Apply Option A domain-allowlist fix to `policies.py` (O2/F1 code change) | T1 | `src/rocky/learning/policies.py` (edit in-place) | IN_PROGRESS |
| T3 | xlfg-task-implementer | Create `tests/test_policy_domain_allowlist.py` with SC-1/SC-2/SC-3 (O2/F1 tests) | T2 | `tests/test_policy_domain_allowlist.py` (new file) | IN_PROGRESS |
| T4 | xlfg-task-implementer | Commit F1 change + tests + run-dir artifacts (O2 release discipline) | T3 | `evidence/T4-commit-sha.txt` | IN_PROGRESS |

## Scope notes

- T1 and T4 are commit tasks. The implementer writes the commit SHA to a small evidence file; no YAML frontmatter required in the committed source files.
- T2 is a source-file edit; the edited `policies.py` carries no YAML frontmatter.
- T3 is a test-file creation; `tests/test_policy_domain_allowlist.py` carries no YAML frontmatter.
- Excluded from both commits: `.agent-testing/`, `.rocky/`, `docs/pi-autoresearch-*`.
- Parallelism: none — all four tasks are sequentially dependent.
