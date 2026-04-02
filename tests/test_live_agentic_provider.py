from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from rocky.app import RockyRuntime
from test_agentic_contracts import SCENARIOS, Scenario, _prepare_workspace


if os.getenv("ROCKY_RUN_LIVE_AGENTIC") != "1":
    pytest.skip(
        "set ROCKY_RUN_LIVE_AGENTIC=1 to run live LLM agentic tests",
        allow_module_level=True,
    )


def _live_cli_overrides() -> dict[str, object]:
    bootstrap = RockyRuntime.load_from()
    provider_name = os.getenv("ROCKY_LIVE_PROVIDER", bootstrap.config.active_provider)
    provider = bootstrap.config.provider(provider_name)
    return {
        "active_provider": provider_name,
        "providers": {
            provider_name: {
                "style": provider.style.value,
                "base_url": os.getenv("ROCKY_LIVE_BASE_URL", provider.base_url),
                "model": os.getenv("ROCKY_LIVE_MODEL", provider.model),
                "store": False,
            }
        },
        "permissions": {"mode": "bypass"},
    }


def _tool_result_names(response) -> list[str]:
    return [
        event["name"]
        for event in response.trace["tool_events"]
        if event.get("type") == "tool_result"
    ]


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


def _assert_live_behavior(scenario: Scenario, response) -> None:
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
        "local/runtime_inspection": 1,
        "repo/general": 2,
        "data/spreadsheet/analysis": 1,
        "extract/general": 2,
        "automation/general": 2,
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
        assert "write_file" not in unique_tool_names, trace_path
    elif scenario.task_signature == "extract/general":
        assert "write_file" not in unique_tool_names, trace_path
        payload = json.loads(_strip_fences(response.text))
        assert isinstance(payload, (dict, list)), trace_path
    elif scenario.task_signature == "automation/general":
        assert "write_file" in unique_tool_names, trace_path
        assert "run_shell_command" in unique_tool_names, trace_path

    if verification["status"] == "pass":
        return

    assert verification["status"] == "warn", trace_path
    assert verification["name"] == "tool_failure_v1", trace_path


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[scenario.name for scenario in SCENARIOS])
def test_live_llm_executes_agentic_scenarios(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: Scenario,
) -> None:
    workspace = _prepare_workspace(tmp_path, monkeypatch)
    runtime = RockyRuntime.load_from(workspace, cli_overrides=_live_cli_overrides())
    runtime.permissions.config.mode = "bypass"

    response = runtime.run_prompt(scenario.prompt, continue_session=False)

    _assert_live_behavior(scenario, response)
