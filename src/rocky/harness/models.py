from __future__ import annotations

from dataclasses import dataclass

from rocky.core.router import TaskClass


@dataclass(frozen=True, slots=True)
class ToolStep:
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class HarnessPhase:
    slug: str
    title: str
    description: str
    success_signals: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    prompt: str
    task_class: TaskClass
    task_signature: str
    tool_families: tuple[str, ...]
    plan: tuple[ToolStep, ...]
    output_kind: str = "plain"
    phase_targets: tuple[str, ...] = (
        "phase1_route_anchor",
        "phase2_followup_evidence",
        "phase3_end_to_end_contract",
    )
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MiniProjectScenario:
    name: str
    prompt: str
    expected_files: tuple[str, ...]
    verify_command: tuple[str, ...]
    expected_output: object
    output_kind: str = "text"
    response_snippets: tuple[str, ...] = ()
    phase_targets: tuple[str, ...] = ("phase4_exact_output_build",)
    tags: tuple[str, ...] = ("mini_project",)


@dataclass(frozen=True, slots=True)
class WorkspaceContinuityScenario:
    name: str
    seed_prompt: str
    seed_answer: str
    follow_up_prompt: str
    expected_markers: tuple[str, ...]
    phase_targets: tuple[str, ...] = ("phase5_workspace_continuity",)
    tags: tuple[str, ...] = ("workspace_continuity",)
