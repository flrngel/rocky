from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
class PhaseExpectations:
    anchor_tools: tuple[str, ...] = ()
    min_successful_tools: int = 1
    phase2_required_tools: tuple[str, ...] = ()
    phase2_required_any: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    response_markers: tuple[str, ...] = ()
    requires_json_output: bool = False
    requires_non_shell_follow_up: bool = False


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    prompt: str
    task_class: TaskClass
    task_signature: str
    tool_families: tuple[str, ...]
    output_kind: str = "plain"
    fixture_seed: int = 0
    workspace_profile: str = "generated_workspace"
    phase_expectations: PhaseExpectations = field(default_factory=PhaseExpectations)
    phase_targets: tuple[str, ...] = (
        "phase1_route_anchor",
        "phase2_followup_evidence",
        "phase3_end_to_end_contract",
    )
    tags: tuple[str, ...] = ()
    oracle: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MiniProjectScenario:
    name: str
    prompt: str
    expected_files: tuple[str, ...]
    verify_command: tuple[str, ...]
    expected_output: object
    output_kind: str = "text"
    response_snippets: tuple[str, ...] = ()
    fixture_seed: int = 0
    workspace_profile: str = "mini_project"
    task_class: TaskClass = TaskClass.AUTOMATION
    task_signature: str = "automation/general"
    phase_expectations: PhaseExpectations = field(default_factory=PhaseExpectations)
    phase_targets: tuple[str, ...] = ("phase4_exact_output_build",)
    tags: tuple[str, ...] = ("mini_project",)
    oracle: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkspaceContinuityScenario:
    name: str
    seed_prompt: str
    seed_answer: str
    follow_up_prompt: str
    expected_markers: tuple[str, ...]
    fixture_seed: int = 0
    phase_targets: tuple[str, ...] = ("phase5_workspace_continuity",)
    tags: tuple[str, ...] = ("workspace_continuity",)
