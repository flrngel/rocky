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

    assert names[:2] == ["inspect_runtime_versions", "run_shell_command"]


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
    assert "copy_path" not in names
    assert "list_files" not in names


def test_data_tasks_prefer_spreadsheet_tools_and_hide_writers(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)

    names = [
        tool.name
        for tool in runtime.tool_registry.select_for_task(
            ["filesystem", "data", "python"],
            "data/spreadsheet/analysis",
            "analyze data/sales.csv",
        )
    ]

    assert names[:3] == ["inspect_spreadsheet", "read_sheet_range", "run_python"]
    assert "stat_path" not in names
    assert "write_file" not in names
    assert "replace_in_file" not in names


def test_extraction_tasks_are_read_only_and_keep_python(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)

    names = [
        tool.name
        for tool in runtime.tool_registry.select_for_task(
            ["filesystem", "python", "data"],
            "extract/general",
            "normalize the people dataset into json with row count and fields",
        )
    ]

    assert names[:3] == ["glob_paths", "stat_path", "run_python"]
    assert "run_python" in names
    assert "read_file" in names
    assert "inspect_spreadsheet" not in names
    assert "write_file" not in names
    assert "delete_path" not in names


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

    assert names[:3] == ["write_file", "read_file", "run_shell_command"]
    assert "glob_paths" not in names



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
