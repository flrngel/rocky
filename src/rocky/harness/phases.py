from __future__ import annotations

from rocky.harness.models import HarnessPhase


DEFAULT_PHASES: tuple[HarnessPhase, ...] = (
    HarnessPhase(
        slug="prepare_workspace",
        title="Phase 1 — Prepare workspace",
        description="Create an isolated temporary workspace and seed only the files needed for the scenario.",
        success_signals=("workspace_created", "fixtures_materialized"),
    ),
    HarnessPhase(
        slug="install_and_baseline",
        title="Phase 2 — Install and baseline run",
        description="Install the current Rocky package, run the installed CLI with ollama, and record the initial trace and answer.",
        success_signals=("pipx_install", "baseline_trace", "baseline_answer"),
    ),
    HarnessPhase(
        slug="teach",
        title="Phase 3 — Teach Rocky",
        description="Send explicit `/learn` feedback when the scenario calls for it and confirm Rocky persisted a reusable lesson.",
        success_signals=("feedback_recorded", "skill_published_or_skipped_honestly"),
    ),
    HarnessPhase(
        slug="retry_with_learning",
        title="Phase 4 — Retry with learning",
        description="Re-run the follow-up task in a fresh Rocky process and verify whether the learned guidance is loaded and used.",
        success_signals=("fresh_process_retry", "learned_skill_selected", "improved_behavior"),
    ),
    HarnessPhase(
        slug="grade_results",
        title="Phase 5 — Grade behavior and result",
        description="Grade the real tool trace, route, artifacts, and final answer together instead of relying on mock provider assertions.",
        success_signals=("behavior_checked", "result_checked", "report_written"),
    ),
)

PHASES_BY_SLUG = {phase.slug: phase for phase in DEFAULT_PHASES}


def phase_titles() -> list[str]:
    return [phase.title for phase in DEFAULT_PHASES]
