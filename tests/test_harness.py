from __future__ import annotations

from pathlib import Path

from rocky.app import RockyRuntime
from rocky.harness import (
    DEFAULT_PHASES,
    HarnessResultStore,
    HarnessRunRecord,
    WorkspaceContinuityScenario,
    default_scenarios,
    phase4_mini_projects,
    scenarios_by_phase,
    workspace_continuity_scenarios,
)


def test_default_harness_phases_cover_v020_contract() -> None:
    slugs = [phase.slug for phase in DEFAULT_PHASES]
    assert slugs == [
        "phase1_route_anchor",
        "phase2_followup_evidence",
        "phase3_end_to_end_contract",
        "phase4_exact_output_build",
        "phase5_workspace_continuity",
    ]


def test_harness_result_store_writes_and_lists_phase_records(tmp_path: Path) -> None:
    store = HarnessResultStore(tmp_path / "eval" / "harness")
    record = HarnessRunRecord(
        scenario_name="workspace_memory_resume",
        phase="phase5_workspace_continuity",
        prompt="continue the work",
        route="repo/general",
        verification_status="pass",
        notes="loaded prior handoff",
    )
    path = store.write(record)
    assert path.exists()
    rows = store.list_recent(phase="phase5_workspace_continuity")
    assert rows
    assert rows[0]["scenario_name"] == "workspace_memory_resume"


def test_workspace_continuity_scenario_defaults_phase5() -> None:
    scenario = WorkspaceContinuityScenario(
        name="resume",
        seed_prompt="Build parser",
        seed_answer="Built parser in src/parser.py",
        follow_up_prompt="continue the work",
        expected_markers=("src/parser.py",),
    )
    assert scenario.phase_targets == ("phase5_workspace_continuity",)
    assert "workspace_continuity" in scenario.tags


def test_harness_catalog_has_phase_specific_scenarios() -> None:
    assert len(default_scenarios()) >= 24
    assert len(phase4_mini_projects()) >= 4
    assert len(workspace_continuity_scenarios()) >= 3
    assert scenarios_by_phase("phase5_workspace_continuity")


def test_runtime_harness_command_exposes_inventory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    runtime = RockyRuntime.load_from(tmp_path / "workspace")

    result = runtime.commands.handle("/harness")

    assert result.name == "harness"
    assert result.data["version"] == "1.0.1"
    assert result.data["execution_cwd"] == "."
    assert result.data["phases"][0]["slug"] == "phase1_route_anchor"
    assert result.data["generation"]["strategy"] == "on_demand_generators_without_static_catalog"
