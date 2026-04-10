from __future__ import annotations

from pathlib import Path

from rocky.app import RockyRuntime
from rocky.tools.base import Tool


def test_runtime_inspection_prefers_runtime_tools_first(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)

    names = [
        tool.name
        for tool in runtime.tool_registry.select_for_task(
            ["shell"],
            "local/runtime_inspection",
            "what python versions do i have",
        )
    ]

    assert names[:1] == ["run_shell_command"]


def test_shell_execution_tools_focus_on_shell_not_filesystem_shortcuts(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)

    names = [
        tool.name
        for tool in runtime.tool_registry.select_for_task(
            ["filesystem", "shell", "python", "git"],
            "repo/shell_execution",
            "execute ls and count the entries",
        )
    ]

    assert names[0] == "run_shell_command"
    assert names[1:] == ["read_file", "write_file"]


def test_data_tasks_prefer_spreadsheet_tools_and_hide_writers(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)

    names = [
        tool.name
        for tool in runtime.tool_registry.select_for_task(
            ["filesystem", "data", "python", "shell"],
            "data/spreadsheet/analysis",
            "analyze data/sales.csv",
        )
    ]

    assert names[:2] == ["run_shell_command", "read_file"]
    assert "write_file" not in names


def test_extraction_tasks_are_read_only_and_keep_python(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)

    names = [
        tool.name
        for tool in runtime.tool_registry.select_for_task(
            ["filesystem", "python", "data", "shell"],
            "extract/general",
            "normalize the people dataset into json with row count and fields",
        )
    ]

    assert names[:2] == ["read_file", "run_shell_command"]
    assert "read_file" in names
    assert "write_file" not in names


def test_automation_tools_keep_write_and_verify_path(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)

    names = [
        tool.name
        for tool in runtime.tool_registry.select_for_task(
            ["filesystem", "shell", "python"],
            "automation/general",
            "create a repeatable cleanup script for tmp artifacts and verify it",
        )
    ]

    assert names == ["write_file", "read_file", "run_shell_command"]


def test_research_route_exposes_web_tools_and_hides_shell_tools(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)

    names = [
        tool.name
        for tool in runtime.tool_registry.select_for_task(
            ["web", "browser"],
            "research/live_compare/general",
            "find trending openweight llm models under 12B and show me a list",
        )
    ]

    assert names[:3] == ["search_web", "fetch_url", "agent_browser"]
    assert "run_shell_command" not in names
    assert "write_file" not in names
    assert "read_file" not in names



def test_openai_tool_schema_defaults_to_closed_object_properties() -> None:
    tool = Tool(
        name="demo",
        description="demo",
        input_schema={
            "properties": {
                "path": {"type": "string"},
                "options": {
                    "properties": {
                        "recursive": {"type": "boolean"},
                    }
                },
            },
            "required": ["path"],
        },
        family="filesystem",
        handler=lambda ctx, args: None,  # type: ignore[arg-type]
    )

    schema = tool.openai_schema()["function"]["parameters"]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["options"]["type"] == "object"
    assert schema["properties"]["options"]["additionalProperties"] is False
