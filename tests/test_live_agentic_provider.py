from __future__ import annotations

import json
import os

import pytest

from rocky.app import RockyRuntime
from test_agentic_contracts import SCENARIOS, Scenario, _prepare_workspace


LIVE_PROVIDER = "ollama"
LIVE_BASE_URL = "http://ainbr-research-fast:11434/v1"
LIVE_MODEL = "qwen3.5:4b"


if os.getenv("ROCKY_RUN_LIVE_AGENTIC") != "1":
    pytest.skip(
        "set ROCKY_RUN_LIVE_AGENTIC=1 to run live LLM agentic tests",
        allow_module_level=True,
    )


def _live_cli_overrides() -> dict[str, object]:
    return {
        "active_provider": LIVE_PROVIDER,
        "providers": {
            LIVE_PROVIDER: {
                "style": "openai_chat",
                "base_url": LIVE_BASE_URL,
                "model": LIVE_MODEL,
                "store": False,
            }
        },
        "permissions": {"mode": "bypass"},
    }


def _tool_result_events(response, *, successes_only: bool = False) -> list[dict]:
    events = [
        event
        for event in response.trace["tool_events"]
        if event.get("type") == "tool_result"
    ]
    if successes_only:
        return [event for event in events if event.get("success", True)]
    return events


def _tool_result_names(response, *, successes_only: bool = False) -> list[str]:
    return [event["name"] for event in _tool_result_events(response, successes_only=successes_only)]


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _phase1_anchor_tools(scenario: Scenario) -> set[str]:
    if scenario.task_signature == "repo/shell_execution":
        return {"run_shell_command", "inspect_shell_environment"}
    if scenario.task_signature == "repo/shell_inspection":
        return {"inspect_shell_environment", "read_shell_history", "run_shell_command"}
    if scenario.task_signature == "local/runtime_inspection":
        return {"inspect_runtime_versions"}
    if scenario.task_signature == "repo/general":
        return {"git_status", "git_recent_commits", "git_diff", "grep_files", "read_file", "list_files"}
    if scenario.task_signature == "data/spreadsheet/analysis":
        return {"inspect_spreadsheet", "read_sheet_range", "stat_path"}
    if scenario.task_signature == "extract/general":
        return {"glob_paths", "stat_path", "read_file", "run_python", "list_files"}
    if scenario.task_signature == "automation/general":
        return {"write_file", "read_file", "run_shell_command"}
    raise AssertionError(f"Unhandled task signature: {scenario.task_signature}")


def _assert_phase1(scenario: Scenario, response) -> None:
    trace_path = response.trace.get("trace_path", "unknown-trace")
    successful_names = _tool_result_names(response, successes_only=True)
    assert successful_names, f"phase1-step1: no successful tool results; trace={trace_path}"
    assert successful_names[0] in _phase1_anchor_tools(scenario), (
        f"phase1-step1: first tool {successful_names[0]!r} not in "
        f"{sorted(_phase1_anchor_tools(scenario))}; trace={trace_path}"
    )


def _assert_phase2(scenario: Scenario, response) -> None:
    trace_path = response.trace.get("trace_path", "unknown-trace")
    first_three = _tool_result_names(response, successes_only=True)[:3]
    first_five = _tool_result_names(response, successes_only=True)[:5]
    assert first_three, f"phase2-step2-3: no successful tool results; trace={trace_path}"

    if scenario.task_signature == "repo/shell_execution":
        assert "run_shell_command" in first_three[:2], (
            f"phase2-step2-3: expected `run_shell_command` by step 2 within {first_three}; trace={trace_path}"
        )
        return
    if scenario.task_signature == "repo/shell_inspection":
        observed = set(first_three)
        assert len(observed & {"inspect_shell_environment", "read_shell_history", "run_shell_command"}) >= 2, (
            f"phase2-step2-3: expected at least two shell inspection steps within {first_three}; trace={trace_path}"
        )
        return
    if scenario.task_signature == "local/runtime_inspection":
        assert "inspect_runtime_versions" in first_three, (
            f"phase2-step2-3: expected `inspect_runtime_versions` within {first_three}; trace={trace_path}"
        )
        assert set(first_five) & {"run_shell_command", "inspect_shell_environment"}, (
            f"phase2-step2-3: expected a confirming shell step within {first_five}; trace={trace_path}"
        )
        return
    if scenario.task_signature == "repo/general":
        observed = set(first_three)
        assert len(observed & {"git_status", "git_recent_commits", "git_diff", "grep_files", "read_file", "list_files"}) >= 2, (
            f"phase2-step2-3: expected two repo inspection steps within {first_three}; trace={trace_path}"
        )
        return
    if scenario.task_signature == "data/spreadsheet/analysis":
        observed = set(first_three)
        assert observed & {"inspect_spreadsheet", "read_sheet_range"}, (
            f"phase2-step2-3: expected spreadsheet inspection within {first_three}; trace={trace_path}"
        )
        assert len(first_five) >= 2, (
            f"phase2-step2-3: expected at least two spreadsheet-analysis steps within {first_five}; trace={trace_path}"
        )
        assert set(first_five) & {"read_sheet_range", "run_python"}, (
            f"phase2-step2-3: expected a follow-up spreadsheet detail step within {first_five}; trace={trace_path}"
        )
        return
    if scenario.task_signature == "extract/general":
        observed = set(first_three)
        assert observed & {"glob_paths", "stat_path", "read_file", "list_files"}, (
            f"phase2-step2-3: expected extraction discovery within {first_three}; trace={trace_path}"
        )
        assert len(first_five) >= 2, (
            f"phase2-step2-3: expected at least two extraction steps within {first_five}; trace={trace_path}"
        )
        return
    if scenario.task_signature == "automation/general":
        observed = set(first_five)
        assert "write_file" in observed, (
            f"phase2-step2-3: expected `write_file` within {first_five}; trace={trace_path}"
        )
        assert "run_shell_command" in observed, (
            f"phase2-step2-3: expected verification by `run_shell_command` within {first_five}; trace={trace_path}"
        )
        return
    raise AssertionError(f"Unhandled task signature: {scenario.task_signature}")


def _assert_phase3_behavior(scenario: Scenario, response) -> None:
    trace_path = response.trace.get("trace_path", "unknown-trace")
    tool_names = _tool_result_names(response)
    unique_tool_names = set(tool_names)
    verification = response.verification

    assert response.route.task_class == scenario.task_class, trace_path
    assert response.route.task_signature == scenario.task_signature, trace_path
    assert response.trace["provider"] == "OpenAIChatProvider", trace_path
    assert response.text.strip(), trace_path
    assert "Provider request failed:" not in response.text, trace_path
    assert "Tool loop ended without a final assistant response." not in response.text, trace_path

    minimums = {
        "repo/shell_execution": 2,
        "repo/shell_inspection": 1,
        "local/runtime_inspection": 2,
        "repo/general": 2,
        "data/spreadsheet/analysis": 2,
        "extract/general": 2,
        "automation/general": 3,
    }
    assert len(tool_names) >= minimums[scenario.task_signature], (
        f"{scenario.name} only used {len(tool_names)} tools; trace={trace_path}"
    )

    if scenario.task_signature == "repo/shell_execution":
        assert "run_shell_command" in unique_tool_names, trace_path
    elif scenario.task_signature == "repo/shell_inspection":
        assert unique_tool_names & {"inspect_shell_environment", "read_shell_history"}, trace_path
    elif scenario.task_signature == "local/runtime_inspection":
        assert "inspect_runtime_versions" in unique_tool_names, trace_path
        if "command paths do they use" in scenario.prompt or "confirm one with a shell command" in scenario.prompt:
            assert unique_tool_names & {"run_shell_command", "inspect_shell_environment"}, trace_path
    elif scenario.task_signature == "repo/general":
        assert unique_tool_names & {
            "grep_files",
            "read_file",
            "list_files",
            "git_status",
            "git_recent_commits",
            "git_diff",
        }, trace_path
    elif scenario.task_signature == "data/spreadsheet/analysis":
        assert unique_tool_names & {"inspect_spreadsheet", "read_sheet_range"}, trace_path
        assert unique_tool_names & {"read_sheet_range", "run_python"}, trace_path
        assert "write_file" not in unique_tool_names, trace_path
    elif scenario.task_signature == "extract/general":
        assert "write_file" not in unique_tool_names, trace_path
        assert unique_tool_names & {"read_file", "run_python"}, trace_path
        payload = json.loads(_strip_fences(response.text))
        assert isinstance(payload, (dict, list)), trace_path
    elif scenario.task_signature == "automation/general":
        assert "write_file" in unique_tool_names, trace_path
        assert "run_shell_command" in unique_tool_names, trace_path

    if verification["status"] == "pass":
        return

    assert verification["status"] == "warn", trace_path
    assert verification["name"] == "tool_failure_v1", trace_path


@pytest.fixture(scope="session", params=SCENARIOS, ids=[scenario.name for scenario in SCENARIOS])
def live_run(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
):
    scenario: Scenario = request.param
    tmp_path = tmp_path_factory.mktemp(f"live_{scenario.name}")
    monkeypatch = pytest.MonkeyPatch()
    try:
        workspace = _prepare_workspace(tmp_path, monkeypatch)
        runtime = RockyRuntime.load_from(workspace, cli_overrides=_live_cli_overrides())
        runtime.permissions.config.mode = "bypass"
        response = runtime.run_prompt(scenario.prompt, continue_session=False)
        return scenario, response
    finally:
        monkeypatch.undo()


def test_live_llm_phase1_step1_verification(live_run) -> None:
    scenario, response = live_run
    _assert_phase1(scenario, response)


def test_live_llm_phase2_step2_3_verification(live_run) -> None:
    scenario, response = live_run
    _assert_phase2(scenario, response)


def test_live_llm_phase3_multi_step_verification(live_run) -> None:
    scenario, response = live_run
    _assert_phase3_behavior(scenario, response)
