from __future__ import annotations

from rocky.harness.models import HarnessPhase


DEFAULT_PHASES: tuple[HarnessPhase, ...] = (
    HarnessPhase(
        slug="phase1_route_anchor",
        title="Phase 1 — Route and anchor tool",
        description="Route the prompt correctly and make the first successful tool call land in the right family.",
        success_signals=("correct_task_signature", "correct_anchor_tool"),
    ),
    HarnessPhase(
        slug="phase2_followup_evidence",
        title="Phase 2 — Follow-up evidence",
        description="Continue after the first tool result and gather enough evidence to support every requested claim.",
        success_signals=("multi_step_follow_up", "route_specific_second_step"),
    ),
    HarnessPhase(
        slug="phase3_end_to_end_contract",
        title="Phase 3 — End-to-end task contract",
        description="Finish the scenario with a valid answer, tool trace, and verifier outcome instead of stopping early.",
        success_signals=("non_empty_answer", "trace_complete", "verification_not_fail"),
    ),
    HarnessPhase(
        slug="phase4_exact_output_build",
        title="Phase 4 — Exact-output build verification",
        description="For mini-projects and automations, create files, execute them, and compare observed output with the requested behavior.",
        success_signals=("files_created", "verified_command_run", "exact_output_or_json"),
    ),
    HarnessPhase(
        slug="phase5_workspace_continuity",
        title="Phase 5 — Workspace continuity and handoff",
        description="Carry project intent, paths, and recent successful work into a fresh session without pretending to remember unavailable chat turns.",
        success_signals=("execution_directory_focus", "recent_workspace_handoff", "project_memory_loaded"),
    ),
)

PHASES_BY_SLUG = {phase.slug: phase for phase in DEFAULT_PHASES}


def phase_titles() -> list[str]:
    return [phase.title for phase in DEFAULT_PHASES]
