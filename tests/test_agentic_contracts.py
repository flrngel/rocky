from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from rocky.core.router import Router
from rocky.harness import (
    MiniProjectScenario,
    Scenario,
    default_scenarios,
    harness_inventory,
    materialize_mini_project_workspace,
    materialize_scenario_workspace,
    phase4_mini_projects,
    workspace_continuity_scenarios,
)


def _prepare_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: Scenario,
) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()

    monkeypatch.chdir(workspace)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setenv("USER", "rockytester")
    bin_dir = materialize_scenario_workspace(workspace, home, scenario)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return workspace, home


def _default_scenarios() -> tuple[Scenario, ...]:
    return default_scenarios()


def _phase4_scenarios() -> tuple[MiniProjectScenario, ...]:
    return phase4_mini_projects()


def _continuity_scenarios():
    return workspace_continuity_scenarios()


def _prepare_clean_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()

    monkeypatch.chdir(workspace)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setenv("USER", "rockytester")
    return workspace


def _catalog_decisions(payload: dict[str, object]) -> dict[str, object]:
    products = []
    for product in payload["products"]:
        product = dict(product)
        candidates = [dict(candidate) for candidate in product["candidates"]]
        merge = [
            candidate["candidate_id"]
            for candidate in candidates
            if candidate["name"] == product["name"] and candidate["sku"] == product["sku"]
        ]
        skip = [
            candidate["candidate_id"]
            for candidate in candidates
            if candidate["candidate_id"] not in merge
        ]
        products.append(
            {
                "product_id": product["product_id"],
                "merge": merge,
                "skip": skip,
            }
        )
    return {"products": products}


def test_generated_harness_inventory_is_template_driven() -> None:
    inventory = harness_inventory()
    scenarios = _default_scenarios()
    mini_projects = _phase4_scenarios()
    continuity = _continuity_scenarios()

    assert len(scenarios) >= 24
    assert len(mini_projects) >= 4
    assert len(continuity) >= 3
    assert inventory["generation"]["strategy"] == "on_demand_generators_without_static_catalog"
    assert inventory["phase1_3_scenarios"] == len(scenarios)


def test_generated_scenarios_are_diverse_and_not_fixed_cases() -> None:
    scenarios = _default_scenarios()
    signatures = {scenario.task_signature for scenario in scenarios}
    seed_values = {scenario.fixture_seed for scenario in scenarios}

    assert signatures == {
        "repo/shell_execution",
        "repo/shell_inspection",
        "local/runtime_inspection",
        "repo/general",
        "data/spreadsheet/analysis",
        "extract/general",
        "automation/general",
    }
    assert seed_values == {11, 17, 29, 41}
    assert len({scenario.prompt for scenario in scenarios}) == len(scenarios)
    assert all(scenario.tool_families for scenario in scenarios)
    assert all(scenario.phase_expectations.anchor_tools for scenario in scenarios)
    assert all(scenario.phase_expectations.min_successful_tools >= 1 for scenario in scenarios)


def test_generated_catalog_assets_are_not_fixed_names() -> None:
    scenarios = _default_scenarios()
    shell_execution = [
        scenario
        for scenario in scenarios
        if scenario.task_signature == "repo/shell_execution"
    ]
    script_names = [str(scenario.oracle["script_name"]) for scenario in shell_execution]
    product_ids = [
        str(scenario.oracle["expected_decisions"]["products"][0]["product_id"])
        for scenario in shell_execution
    ]
    phase4_catalog = next(
        scenario
        for scenario in _phase4_scenarios()
        if str(scenario.oracle.get("family")) == "catalog_review"
    )

    assert len(set(script_names)) == len(script_names)
    assert "x.sh" not in script_names
    assert phase4_catalog.expected_files[0] != "x.sh"
    assert phase4_catalog.expected_files[1] != "merge_decisions.json"
    assert len(set(product_ids)) == len(product_ids)


@pytest.mark.parametrize("scenario", default_scenarios(), ids=[scenario.name for scenario in default_scenarios()])
def test_router_contract_for_generated_scenarios(scenario: Scenario) -> None:
    route = Router().route(scenario.prompt)

    assert route.task_class == scenario.task_class
    assert route.task_signature == scenario.task_signature
    for family in scenario.tool_families:
        assert family in route.tool_families


@pytest.mark.parametrize("seed", sorted({scenario.fixture_seed for scenario in default_scenarios()}))
def test_materialized_workspace_matches_generated_oracles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed: int,
) -> None:
    grouped = [scenario for scenario in _default_scenarios() if scenario.fixture_seed == seed]
    workspace, _home = _prepare_workspace(tmp_path, monkeypatch, grouped[0])

    for scenario in grouped:
        for relative_path in scenario.oracle.get("referenced_paths", ()):
            assert (workspace / str(relative_path)).exists(), (scenario.name, relative_path)
        for relative_path in scenario.oracle.get("created_paths", ()):
            assert not (workspace / str(relative_path)).exists(), (scenario.name, relative_path)

    catalog_scenario = next(
        scenario
        for scenario in grouped
        if scenario.task_signature == "repo/shell_execution"
    )
    result = subprocess.run(
        ["sh", str(catalog_scenario.oracle["script_name"])],
        cwd=str(workspace),
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert _catalog_decisions(payload) == catalog_scenario.oracle["expected_decisions"]

    runtime_result = subprocess.run(
        ["python3", "--version"],
        cwd=str(workspace),
        check=True,
        capture_output=True,
        text=True,
    )
    assert runtime_result.stdout.strip() == "Python 3.14.3"


@pytest.mark.parametrize("scenario", phase4_mini_projects(), ids=[scenario.name for scenario in phase4_mini_projects()])
def test_phase4_materialization_only_seeds_preexisting_assets_when_needed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: MiniProjectScenario,
) -> None:
    workspace = _prepare_clean_workspace(tmp_path, monkeypatch)
    materialize_mini_project_workspace(workspace, scenario)

    family = str(scenario.oracle["family"])
    if family == "catalog_review":
        script_name = str(scenario.oracle["script_name"])
        output_name = str(scenario.oracle["output_file"])
        assert (workspace / script_name).is_file()
        assert not (workspace / output_name).exists()
        return

    assert list(workspace.iterdir()) == []


def test_phase4_catalog_review_oracle_matches_generated_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = next(
        scenario
        for scenario in _phase4_scenarios()
        if str(scenario.oracle.get("family")) == "catalog_review"
    )
    workspace = _prepare_clean_workspace(tmp_path, monkeypatch)
    materialize_mini_project_workspace(workspace, scenario)

    result = subprocess.run(
        ["sh", str(scenario.oracle["script_name"])],
        cwd=str(workspace),
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert _catalog_decisions(payload) == scenario.expected_output


def test_workspace_continuity_scenarios_are_generated_from_workspace_tokens() -> None:
    scenarios = _continuity_scenarios()
    assert len(scenarios) >= 3
    assert all(scenario.expected_markers for scenario in scenarios)
    assert all(scenario.fixture_seed in {11, 17, 29, 41} for scenario in scenarios)
    assert all("workspace_continuity" in scenario.tags for scenario in scenarios)
