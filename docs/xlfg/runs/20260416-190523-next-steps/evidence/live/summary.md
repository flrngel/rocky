---
name: live-llm-validation-summary
description: Honest live-LLM A/B comparison of F1 (with vs without) + /agent-testing migration validation.
status: DONE
---

# Live-LLM validation — run-20260416-190523-next-steps

Environment: Ollama @ ainbr-research-fast:11434, model gemma4:26b (per `~/.config/rocky/config.yaml`). Test suite: `tests/agent/test_self_learn_live.py` (agent-testing layout).

## A/B: with F1 vs without F1

| Run | Code state | Outcome | Duration | Failing test |
|-----|-----------|---------|----------|--------------|
| 1 | F1 applied | **12/12 PASS** | 316s (5:16) | — |
| 2 | F1 applied | **11/12** | 249s (4:08) | `test_sl_undo_behavioral_correction_fully_gone` |
| 3 | F1 applied | **12/12 PASS** | 181s (3:00) | — |
| 4 (run_eval.py path) | F1 applied | **11/12** | 215s (3:34) | `test_sl_undo_behavioral_correction_fully_gone` |
| 5 (baseline) | F1 reverted | **12/12 PASS** | 188s (3:08) | — |
| 6 (baseline) | F1 reverted | **12/12 PASS** | 188s (3:08) | — |
| 7 (baseline) | F1 reverted | **12/12 PASS** | 238s (3:57) | — |

**With F1: 47/48** across 4 runs (one failing test: `sl_undo_behavioral`, 2× failures).
**Without F1: 36/36** across 3 runs.

## /agent-testing migration validation

- **pytest path**: `ROCKY_LLM_SMOKE=1 ./.venv/bin/pytest tests/agent/test_self_learn_live.py -v` — works correctly; run_eval-free invocation.
- **run_eval.py path**: `python3 .agents/skills/agent-testing/scripts/run_eval.py --repo . --spec .agent-testing/specs/sl-all.json` — works correctly; produces a structured run manifest at `.agent-testing/runs/<stamp>-sl-all-*.json`.
- Both paths execute the same test code and produce consistent outcomes.
- `.agent-testing/repo-profile.json` + `.agent-testing/specs/sl-*.json` are honored by `run_eval.py`.
- Evidence artifacts captured under `.agent-testing/evidence/<scenario>/` per the migration's convention (test file L3-6 comment).

## Analysis of the failing test

`test_sl_undo_behavioral_correction_fully_gone`: after `/teach` teaches pnpm + `run_prompt` triggers autonomous promotion + `/undo` rolls back the teach lineage, assert that a post-undo prompt's answer does NOT recommend pnpm.

Failure mode observed twice with F1, zero times without F1 (n=3 vs n=3). The test's own error message states: "Derived-autonomous leak expected here pre-Phase-2." This is the documented PRD §8 Issue where `capture_project_memory` writes memories under TURN-lineage (not teach-lineage), so `/undo` doesn't roll them back.

## Is F1 causing the increased flake rate?

F1 changes `LearnedPolicyRetriever.retrieve` scoring to allow `command` as strong for `repo`-family policies. This makes the pnpm policy MORE consistently retrieved during the teach-reuse turn. Consistently-retrieved policies mean `capture_project_memory` (called during the reuse turn) is MORE likely to autonomously capture pnpm-related memory under turn-lineage. Post-undo, those turn-lineage memories are the "derived-autonomous leak" and they surface pnpm in the answer.

**Causal hypothesis** (plausible, not conclusively proven by n=3+3): F1 amplifies an existing Phase-2 leak. Without F1, retrieval is noisier/less-consistent so turn-lineage memory capture is less reliable, so the leak fires less often.

## Historical variance

| Prior run | Triple-live | Notes |
|-----------|-------------|-------|
| run-20260414-205412 | 34/36 | flakes: sl_promote_B/C |
| run-20260414-215348 | 36/36 | retry-on-hedge added |
| run-20260416-205534 final | 36/36 | after carry-field fix |
| This run (F1 applied) | 35/36 | flake: sl_undo_behavioral |
| This run (F1 reverted) | 36/36 | baseline |

## Migration verdict: `/agent-testing` is good enough

Both invocation paths produce correct results. The run manifest format is useful. The 14-symbol shared helpers surface (`tests/agent/_helpers.py`) is clean. Spec drift guard (`tests/test_agent_testing_specs.py`) is in place. **Ship the migration.**

## F1 verdict: REVERT recommended

Reasons:
1. F1's benefit is theoretical; the live-LLM evidence shows F1 is at best neutral and at worst amplifies a known Phase-2 leak (2/4 failure on `sl_undo_behavioral`).
2. The deterministic mechanism test (SC-1/SC-2/SC-3) proves the CODE works but does not prove the BEHAVIORAL benefit.
3. F1's original motivating scenario (run-20260416-205534 pnpm reuse drop) is ALREADY covered by the `AgentCore._route_upgrade_driving_policy` carry-field fix.
4. Without F1, triple-live is 36/36; with F1, it's 35/36 + a 4th run also flaked.
5. Per CF-feedback/claim-scope-honesty: behavioral claims need live-LLM evidence, not deterministic substitutes. F1 fails this test.

Recommended next action: revert commits `05fa5ec` (F1 fix) and `034e13a` (F1 doc comment) via `git revert`. Keep commits `06c5066` (T1 prior-run rollup) and `767519d` (compound-summary + lessons) — those are independent of F1. Re-run triple-live after revert to confirm 36/36 baseline is restored (expected based on this evidence). Document F1 as a REJECTED change in current-state.md and update L17 to reflect the empirical finding.
