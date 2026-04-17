---
name: memory-recall
description: Deterministic recall for run-20260416-190523-next-steps before broad repo fan-out.
status: DONE
---

# Memory recall — run-20260416-190523-next-steps

Invocation: `/xlfg-engineering:xlfg work on next steps`.

## Sources checked

- `docs/xlfg/knowledge/current-state.md` (full read)
- `docs/xlfg/knowledge/ledger.jsonl` (tail ~20)
- `/tmp/205534-follow-ups.md` (prior run's residuals doc)
- `/Users/flrngel/.claude/projects/-Users-flrngel-project-personal-rocky/memory/MEMORY.md` (session-persistent feedback memories)
- `src/rocky/learning/policies.py` L14, L196-234 (diagnosed surface for F1)
- `git log --since="2026-04-16 07:18" -- src/rocky/learning/policies.py src/rocky/core/context.py src/rocky/learning/` (git-recency check)

## Likely scope of "work on next steps"

Three candidates grounded from the repo:

| Candidate | Source | Load-bearing? |
|-----------|--------|---------------|
| A. Commit prior run's uncommitted work | Standing instruction in global `CLAUDE.md`; prior run-20260416-161631 left the tree dirty at GREEN / APPROVE-WITH-NOTES-FIXED | Housekeeping; one conventional commit |
| B. F1 — `WEAK_MATCH_TOKENS` principled review | `/tmp/205534-follow-ups.md` L78-106; sole OPEN follow-up after prior run; medium-effort empirical analysis | **The single open engineering task** |
| C. Something else | No evidence in the repo or ledger points to other open work | n/a |

Working hypothesis: "next steps" = A + B. Commit the prior run's complete work, then ship F1. Intent phase finalizes.

## Strong matches (relevant carry-forward)

### CF-L10 — route-upgrade ↔ context-retrieval consistency

Prior run 20260416-205534 codified: retrieval scoring must not silently drop the policy that DROVE a route upgrade. Today's F1 inquiry is exactly in the same retrieval-scoring path (`LearnedPolicyRetriever.retrieve`, `src/rocky/learning/policies.py:203` `strong_token_matches = token_matches - WEAK_MATCH_TOKENS`). Any proposed change to `WEAK_MATCH_TOKENS` behavior must preserve the carry-field fix (agent.py:538 set-site, agent.py:3188 inject-site) — the carry-field is the safety net for exactly this class of retrieval drop.

**How to apply:** if F1 lands a scoring-path change, re-run `tests/test_route_upgrade_driving_policy.py` (the new regression suite) and the full route-upgrade test set before declaring GREEN.

### CF-L11 — git-log-since-baseline

Prior run's durable lesson: before trusting a memory-recall, run git-log on the diagnosed surface since the last STABLE baseline. **Applied this recall:**

- Diagnosed surface for F1: `src/rocky/learning/policies.py`, `src/rocky/core/context.py`, `src/rocky/learning/` tree.
- Baseline: commit 06cc767 (2026-04-16 07:17:01).
- Check: `git log --since="2026-04-16 07:18" -- <surface>` → **empty result** (no new commits).
- Note: prior run-20260416-205534's fix shipped uncommitted changes to `src/rocky/core/agent.py`; that file is NOT in F1's diagnosed surface (F1 is about retrieval scoring in `policies.py`). The F1 diagnosis from run 205534 (timestamp 20:55:34 on 2026-04-16) is current.

Recall status: **not stale**. F1 can be treated as an established hypothesis grounded in the prior-run empirical observation.

### CF-feedback: test generalization, not instruction-following

From user's auto-memory `feedback_test_generalization_not_instruction_following.md`. For F1: if a test teaches on P1 and reuses on lexically-different P2 within the same domain, it witnesses retrieval robustness, not instruction-following. Ground test scenarios in the actual failing prompt from run 205534 ("What command should I use to add axios?") or lexically-diverse variants in the same task family.

### CF-feedback: assert on real output, not a proxy field

From `feedback_assert_output_not_proxy.md`. If F1 ships a scoring change, behavioral validation should assert on the final `response["text"]` quality or on `trace.selected_policies` pass-through to the answer, not only on internal retrieval scoring numbers.

### CF-feedback: fixtures must match production paths

From `feedback_test_fixtures_must_match_production_paths.md`. Any F1 regression test must use production path helpers (e.g. `_find_repo_root`, existing `_build_runtime_with_policy` pattern from `test_route_intersection.py` / `test_route_upgrade_driving_policy.py`), not contrived fixture paths.

### CF-sensitivity: witnesses must bite

From `feedback_sensitivity_checks.md` + durable lesson L8. Any F1 code change must carry a sensitivity witness (revert the change → test fails → restore → test passes) before declaring GREEN. Vacuous tests that pass without the fix are a release blocker.

### CF-L12 — source fix + harness retry on autonomous-promotion paths

If F1 changes scoring and a live-LLM test needs to re-witness policy reuse, prefer source fix + bounded `_run_rocky_until` retry wrapper over single-layer approaches. This is specifically for live autonomous-promotion tests; deterministic unit tests should not need retry.

### CF-L14 — external-commit detection for cross-repo tasks

If F1 delegates any work to the sibling xlfg plugin (unlikely but possible — recall-phase SKILL.md already has a git-recency guardrail as of external commit c4e4f38), check `git -C /Users/flrngel/project/personal/xlfg status` before the implementer edits. Prior run discovered this class of lane leak.

## Rejected near-matches

- **CF-8 refined (teach over-tagging intersection allowlist)** — same retrieval subsystem, but F1 is about the scoring gate (`strong_token_matches`), not the upgrade gate (`_maybe_upgrade_route_from_project_context`). Different mechanism. Not carry-forward, but useful context: the original CF-8 attempt went wrong because it broke legitimate cross-family upgrades; F1 has a similar trap — naive "drop WEAK_MATCH_TOKENS entirely" could over-match and reintroduce the teach over-tagging problem CF-8 was trying to solve.
- **L16 YAML frontmatter on non-markdown artifacts** — sibling-plugin issue, not Rocky-side. Not actionable here.
- **OBS-4 (test_agent_testing_specs.py 2-level walk)** — acceptable while tests/ is top-level; no action until tests/ moves.

## No-hit statements (explicit)

- No prior run has empirically reviewed `WEAK_MATCH_TOKENS` on live corpora. This is the first F1-class run.
- No prior memory suggests that `command` should weigh differently for `task_family == "repo"` vs `task_family == "conversation"`. The open question in `/tmp/205534-follow-ups.md` L97-101 is genuinely open.
- No prior run has measured retrieval-outcome distributions across >1 live-LLM run; F1 as scoped requires ~10 runs or a deterministic proxy.

## Carry-forward rules adopted for this run

1. **CF-L10** — preserve driving-policy carry-field invariant across any retrieval-scoring change. Re-run `tests/test_route_upgrade_driving_policy.py` on any scoring change.
2. **CF-feedback/generalization** — if F1 adds tests, test on lexically-diverse prompts within the same task family.
3. **CF-feedback/assert-real-output** — behavioral claims ground on `trace.selected_policies` or `response["text"]`, not only on scoring numbers.
4. **CF-feedback/production-paths** — any new test uses `_build_runtime_with_policy` + `RockyRuntime.load_from(workspace)` patterns, not contrived paths.
5. **CF-sensitivity** — every F1 code change carries a sensitivity witness file under `evidence/`.
6. **CF-L11 applied** — git-recency checked and clean; F1 can proceed as an established hypothesis.

No `HYPOTHESIS-ONLY` markers required — git-recency was clean.
